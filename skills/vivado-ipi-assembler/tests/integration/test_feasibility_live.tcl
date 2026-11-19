# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

namespace eval feasibility_live {
    variable root [file normalize [file join [file dirname [info script]] ../../../../..]]
    variable results {}
    variable assertions 0
}
proc feasibility_live::assert {expression message} {
    if {![uplevel 1 [list expr $expression]]} {error $message}
    incr ::feasibility_live::assertions
}
proc feasibility_live::accept {value {prefix SUCCESS:}} {
    feasibility_live::assert {[string first $prefix $value] == 0} $value
}
proc feasibility_live::read_bytes {path} {
    set channel [open $path rb]
    try {return [read $channel]} finally {close $channel}
}
proc feasibility_live::irq_setup {} {
    feasibility_live::accept [ipcfg::create_cell_cfg xilinx.com:ip:axi_intc:4.1 irq_controller {CONFIG.C_KIND_OF_INTR 0x00000000}]
    feasibility_live::accept [ipcfg::create_cell_cfg xilinx.com:ip:xlconstant:1.1 irq_zero \
        {CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 0}]
    feasibility_live::accept [ipcfg::create_cell_cfg xilinx.com:ip:xlconcat:2.1 irq_concat \
        [list CONFIG.NUM_PORTS $::feasibility_live::width]]
    ipcfg::attempt_call irq_concat connect_inputs {
        for {set slot 0} {$slot < $::feasibility_live::width} {incr slot} {
            connect_bd_net [get_bd_pins irq_zero/dout] [get_bd_pins irq_concat/In$slot]
        }
        connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins irq_controller/intr]
    }
    set boundary [create_bd_intf_port -mode Slave -vlnv xilinx.com:interface:aximm_rtl:1.0 IRQ_AXI]
    set clock_port [create_bd_port -dir I -type clk -freq_hz 100000000 probe_clk]
    set reset_port [create_bd_port -dir I -type rst probe_resetn]
    set_property -dict {CONFIG.FREQ_HZ 100000000 CONFIG.ASSOCIATED_BUSIF IRQ_AXI CONFIG.ASSOCIATED_RESET probe_resetn} $clock_port
    set_property CONFIG.POLARITY ACTIVE_LOW $reset_port
    connect_bd_net $clock_port [get_bd_pins irq_controller/s_axi_aclk]
    connect_bd_net $reset_port [get_bd_pins irq_controller/s_axi_aresetn]
    set_property -dict {CONFIG.PROTOCOL AXI4LITE CONFIG.ADDR_WIDTH 32 CONFIG.DATA_WIDTH 32} $boundary
    connect_bd_intf_net $boundary [get_bd_intf_pins irq_controller/s_axi]
    assign_bd_address -offset 0 -range 0x10000 -target_address_space [get_bd_addr_spaces IRQ_AXI] [get_bd_addr_segs irq_controller/s_axi/Reg]
    validate_bd_design
    return TEST:PASS
}
proc feasibility_live::noc_setup {} {
    feasibility_live::accept [ipcfg::create_cell xilinx.com:ip:ps_wizard:1.0 processing_system]
    feasibility_live::accept [ipcfg::board_bind processing_system {CONFIG.PS_BOARD_INTERFACE ps_pmc_fixed_io} {} {RAVE2 PS preset}] BOARD:bound
    feasibility_live::accept [ipcfg::create_cell xilinx.com:ip:axi_noc2:1.1 memory_noc]
    feasibility_live::accept [ipiasm::attach_board_memory memory_noc 4 {MC_CHAN_REGION0 DDR_CH0_LEGACY MC_CHAN_REGION1 DDR_CH0_MED}]
    feasibility_live::accept [ipiasm::gate_inventory {ps_wizard 1 axi_noc2 1}] INVENTORY:OK
    feasibility_live::accept [ipiasm::add_noc_si memory_noc MC_0 processing_system/FPD_AXI_NOC0 1000 1000]
    return TEST:PASS
}
proc feasibility_live::checks {kind} {
    if {$kind eq "irq"} {
        set source {outputs/design_spec.json#/blocks/interrupt/parameters/fabric_controller_inputs/width}
        return [list \
            [dict create id irq.count kind config subject /irq_controller property CONFIG.C_NUM_INTR_INPUTS type integer expected $::feasibility_live::width source $source stage connect] \
            [dict create id irq.width kind pin_width subject /irq_controller/intr expected $::feasibility_live::width source $source stage connect] \
            [dict create id irq.interface kind interface subject /irq_controller/s_axi expected {VLNV xilinx.com:interface:aximm_rtl:1.0 MODE Slave} source $source stage connect]]
    }
    return [list \
        [dict create id noc.legacy kind aperture_capacity subject /memory_noc/DDR_MC_PORTS/DDR_CH0_LEGACYx4 expected $::feasibility_live::legacy_size source {outputs/design_spec.json#/blocks/lpddr5/parameters/regions/legacy} stage connect selector {cell /memory_noc property CONFIG.MC_CHAN_REGION0 value DDR_CH0_LEGACY}] \
        [dict create id noc.medium kind aperture_capacity subject /memory_noc/DDR_MC_PORTS/DDR_CH0_MEDx4 expected $::feasibility_live::medium_size source {outputs/design_spec.json#/blocks/lpddr5/parameters/regions/medium} stage connect selector {cell /memory_noc property CONFIG.MC_CHAN_REGION1 value DDR_CH0_MED}]]
}
proc feasibility_live::managed_check {label checks sources fingerprint} {
    set plan [file join $::feasibility_live::evidence $label.json]
    ipcfg::python_exec -c {import json, pathlib, sys; pathlib.Path(sys.argv[1]).write_text(json.dumps({"schema_version":1,"stages":[{"name":"connect","checkpoint":False,"operations":[{"id":"boundary","op":"call","proc":"ipiasm::check_feasibility","args":[sys.argv[2],"connect",sys.argv[3],sys.argv[4]],"expect":["FEASIBILITY:OK"]}]}]}))} $plan $checks $sources $fingerprint
    set result [ipiasm::assemble_from_plan $plan]
    set status [ipiasm::stage_execution_status]
    dict set ::feasibility_live::results $label [dict create result $result status $status]
    return [dict get $status status]
}
proc feasibility_live::exercise {kind phase} {
    set checks [feasibility_live::checks $kind]
    set sources [dict create spec [list [file join $::feasibility_live::root outputs design_spec.json]] \
        board [glob [file join $::feasibility_live::root inputs rave2_board board_files rave2 A.0 1.3 *.xml]]]
    set definitions {}
    foreach cell [get_bd_cells -quiet -filter {TYPE == ip}] {
        foreach definition [get_ipdefs -all [get_property VLNV $cell]] {
            lappend definitions [get_property XML_FILE_NAME $definition]
        }
    }
    dict set sources ip [lsort -unique $definitions]
    set fingerprint [ipiasm::feasibility_fingerprint $sources]
    if {$phase eq "scratch"} {dict set ::feasibility_live::fingerprints $kind $fingerprint}
    set expected [dict get $::feasibility_live::fingerprints $kind]
    set before [feasibility_live::read_bytes [file join [get_property DIRECTORY [current_project]] .ipcfg attempts.tcldict]]
    set receipt [ipcfg::capture [file join $::feasibility_live::evidence $kind.$phase.check.tcldict] \
        [list ipiasm::check_feasibility $checks [expr {$phase eq "scratch" ? "scratch" : "connect"}] $sources $expected] {FEASIBILITY:OK}]
    dict set ::feasibility_live::results $kind.$phase.check [dict create receipt $receipt fingerprint $fingerprint checks $checks context [ipiasm::context_identity]]
    feasibility_live::assert {[dict get $receipt status] eq "OK"} "Boundary readback failed: $receipt"
    if {$phase eq "delivery"} {
        feasibility_live::assert {[feasibility_live::managed_check $kind.delivery $checks $sources $expected] eq "PASS"} "Managed delivery boundary failed"
        set bad [lindex $checks 0]
        dict set bad expected [expr {$kind eq "irq" ? 23 : 18446744073709551615}]
        feasibility_live::assert {[feasibility_live::managed_check $kind.negative [list $bad] $sources $expected] eq "FAIL"} "Incorrect requirement accepted"
        set stale $expected
        dict set stale part wrong_part
        feasibility_live::assert {[feasibility_live::managed_check $kind.stale $checks $sources $stale] eq "FAIL"} "Stale fingerprint accepted"
        feasibility_live::assert {[feasibility_live::managed_check $kind.restored $checks $sources $expected] eq "PASS"} "Correct requirement did not recover"
    }
    set after [feasibility_live::read_bytes [file join [get_property DIRECTORY [current_project]] .ipcfg attempts.tcldict]]
    feasibility_live::assert {$before eq $after} "Read-only checks changed attempt ledger"
    dict set ::feasibility_live::results $kind.$phase.ledger $after
    if {$kind eq "noc"} {
        set spaces [get_bd_addr_spaces /processing_system/ps11_0_cortexa78_0]
        feasibility_live::assert {[llength $spaces] > 0} "Functional PS master has no address space"
        foreach {region base size} [list LEGACY $::feasibility_live::legacy_base $::feasibility_live::legacy_size MED $::feasibility_live::medium_base $::feasibility_live::medium_size] {
            set target [get_bd_addr_segs /memory_noc/DDR_MC_PORTS/DDR_CH0_${region}x4]
            foreach space $spaces {
                assign_bd_address -offset $base -range $size -target_address_space $space $target
            }
            set check [ipiasm::check_window_scope $target $spaces $base $size]
            dict set ::feasibility_live::results $kind.$phase.window.$region $check
            feasibility_live::accept $check WINDOW_SCOPE:OK
        }
    }
    return TEST:PASS
}
if {[info exists feasibility_check_only] && $feasibility_check_only} {return STRUCTURE_ONLY}
if {![info exists feasibility_session_id] || $feasibility_session_id eq "vivado-20260925-134546-f2f5e4cc"} {error "Supply own disposable MCP session ID"}
if {[current_project -quiet] ne ""} {error "Refusing to replace an open project"}
set ::feasibility_live::evidence [file join $::feasibility_live::root tests evidence workflow_benchmark feasibility_native_[pid]_[clock microseconds]]
file mkdir $::feasibility_live::evidence
set ::feasibility_live::fingerprints {}
set run_code [catch {
    set_param board.repoPaths [list [file join $::feasibility_live::root inputs rave2_board board_files]]
    foreach kind {irq noc} {
        foreach phase {scratch delivery} {
            create_project ${kind}_${phase} [file join $::feasibility_live::evidence ${kind}_${phase}] -part xc2ve3558-sfva1440-2MP-e-S
            set_property BOARD_PART amd.com:rave2:part0:1.3 [current_project]
            create_bd_design boundary
            if {![info exists ::feasibility_live::width]} {
                source -notrace [file join $::feasibility_live::root .claude skills vivado-ip-configurator lib ipcfg ipcfg.tcl]
                source -notrace [file join $::feasibility_live::root .claude skills vivado-ipi-assembler lib ipiasm.tcl]
                if {[info exists test_native_log]} {set ::ipcfg::native_log_path $test_native_log}
                set python_status [ipcfg::python_status]
                feasibility_live::accept $python_status PYTHON:OK
                set expected [ipcfg::python_exec -c {import json, pathlib, sys; data=json.loads(pathlib.Path(sys.argv[1]).read_text()); blocks={block['name']:block for block in data['blocks']}; irq=blocks['interrupt']['parameters']['fabric_controller_inputs']['width']; regions=blocks['lpddr5']['parameters']['regions']; print(irq, *[int(regions[region][key],0) for region in ('legacy','medium') for key in ('base','size')])} [file join $::feasibility_live::root outputs design_spec.json]]
                lassign $expected ::feasibility_live::width ::feasibility_live::legacy_base ::feasibility_live::legacy_size ::feasibility_live::medium_base ::feasibility_live::medium_size
            }
            feasibility_live::accept [ipiasm::begin_design]
            feasibility_live::accept [ipiasm::require_board_preset amd.com:rave2:part0:1.3] READY:
            set receipt [ipcfg::capture [file join $::feasibility_live::evidence $kind.$phase.setup.tcldict] [list feasibility_live::${kind}_setup] {TEST:PASS}]
            dict set ::feasibility_live::results $kind.$phase.setup $receipt
            feasibility_live::assert {[dict get $receipt status] eq "OK"} "Setup failed: $receipt"
            feasibility_live::exercise $kind $phase
            if {$kind eq "irq" && $phase eq "delivery"} {
                set receipt [ipcfg::capture [file join $::feasibility_live::evidence disappearing_warning.tcldict] {
                    ipcfg::attempt_call irq_controller readonly_probe {
                        set_property CONFIG.FREQ_HZ 100000000 [get_bd_intf_ports IRQ_AXI]
                        validate_bd_design -force
                    }
                    set outcome TEST:PASS
                } {TEST:PASS}]
                dict set ::feasibility_live::results disappearing_warning $receipt
                feasibility_live::assert {[dict get $receipt status] eq "FAIL" && [dict get $receipt code] == 0 && [dict get $receipt critical_count] > 0} "Disappearing warning was not rejected"
                feasibility_live::assert {[dict get $receipt native_counts_after {CRITICAL WARNING}] > [dict get $receipt native_counts_before {CRITICAL WARNING}]} "Probe emitted no critical warning"
            }
            save_bd_design
            close_project
        }
    }
} run_result run_options]
set result [dict create status [expr {$run_code ? "FAIL" : "PASS"}] session $feasibility_session_id vivado [version -short] assertions $::feasibility_live::assertions code $run_code result $run_result options $run_options tests $::feasibility_live::results]
puts [ipcfg::write_evidence [file join $::feasibility_live::evidence results.tcldict] $result]
puts "FEASIBILITY_LIVE:code=$run_code assertions=$::feasibility_live::assertions evidence=$::feasibility_live::evidence"
if {$run_code} {return -options $run_options $run_result}