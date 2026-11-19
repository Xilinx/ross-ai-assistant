# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

set test_root [file normalize [file join [file dirname [info script]] ../../../../..]]
set evidence_dir [file join $test_root tests evidence issue-closure]
if {[current_project -quiet] ne ""} {error "Refusing to change an open project"}
file mkdir $evidence_dir
source [file join $test_root .claude/skills/vivado-ip-configurator/lib/ipcfg/ipcfg.tcl]
source [file join $test_root .claude/skills/vivado-ipi-assembler/lib/ipiasm.tcl]
namespace eval procedure_trace {}
set timing_channel [open [file join $evidence_dir live_procedure_timings.log] w]
set ::procedure_trace::output $timing_channel
source [file join $test_root .claude/skills/vivado-ipi-assembler/tests/support/procedure_trace.tcl]
namespace eval live_test {variable hits {}; variable originals {}; variable results {}}
proc live_test::entered {name command operation} {
    if {[info body $name] eq [dict get $::live_test::originals $name]} {
        dict incr ::live_test::hits $name
    }
}
foreach pattern {::ipcfg::* ::ipiasm::*} {
    foreach name [info procs $pattern] {
        dict set ::live_test::originals $name [info body $name]
        trace add execution $name enter [list live_test::entered $name]
    }
}
proc live_test::assert {expression} {
    if {![uplevel 1 [list expr $expression]]} {error "assertion failed: $expression"}
}
proc live_test::success {result} {
    if {![string match SUCCESS:* $result]} {error $result}
}
proc live_test::count {cell} {
    set channel [open [file join [get_property DIRECTORY [current_project]] .ipcfg attempts.tcldict]]
    set state [read $channel]
    close $channel
    return [dict get $state [list [current_bd_design] $cell] count]
}
proc live_test::case {name script} {
    set ::procedure_trace::label $name
    set code [catch {uplevel 1 $script} result options]
    dict set ::live_test::results $name [dict create code $code result $result]
    puts "LIVE_TEST:$name code=$code result=$result"
}
set_param board.repoPaths [list [file join $test_root inputs/rave2_board/board_files]]
set project_dir [file join $evidence_dir live_attempts_[clock microseconds]]
create_project live_attempts $project_dir -part xc2ve3558-sfva1440-2MP-e-S
set_property board_part amd.com:rave2:part0:1.2 [current_project]
create_bd_design retry_tests
ipiasm::init
live_test::case gpio {
    live_test::success [ipcfg::create_cell_cfg xilinx.com:ip:axi_gpio gpio {CONFIG.C_GPIO_WIDTH 8}]
    live_test::assert {[get_property CONFIG.C_GPIO_WIDTH [get_bd_cells gpio]] == 8}
    set rejected [ipcfg::apply_dict gpio {CONFIG.C_GPIO_WIDTH 33}]
    live_test::assert {[string match CONFIGURE_FAIL:* $rejected]}
    live_test::assert {[get_property CONFIG.C_GPIO_WIDTH [get_bd_cells gpio]] == 8}
    while {[live_test::count /gpio] < 10} {
        live_test::success [ipcfg::apply_dict gpio {CONFIG.C_GPIO_WIDTH 8}]
    }
    set blocked [ipcfg::apply_dict /gpio {CONFIG.C_GPIO_WIDTH 16}]
    live_test::assert {[string match *ATTEMPT_LIMIT* $blocked]}
    live_test::assert {[get_property CONFIG.C_GPIO_WIDTH [get_bd_cells gpio]] == 8}
    list status PASS rejected $rejected count [live_test::count /gpio] width 8
}
live_test::case interrupts {
    live_test::success [ipcfg::create_cell xilinx.com:ip:axi_intc irq_controller]
    set baseline [get_property CONFIG.C_NUM_INTR_INPUTS [get_bd_cells irq_controller]]
    live_test::success [ipcfg::create_cell_cfg xilinx.com:ip:xlconcat irq_concat {CONFIG.NUM_PORTS 22}]
    set rejected [ipcfg::apply_dict irq_concat {CONFIG.NUM_PORTS 0}]
    live_test::assert {[string match CONFIGURE_FAIL:* $rejected]}
    live_test::success [ipcfg::apply_dict irq_concat {CONFIG.NUM_PORTS 22}]
    live_test::success [ipcfg::create_cell_cfg xilinx.com:ip:xlconstant irq_zero {CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 0}]
    foreach slot {0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21} {
        connect_bd_net [get_bd_pins irq_zero/dout] [get_bd_pins irq_concat/In$slot]
    }
    connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins irq_controller/intr]
    set clock_port [create_bd_port -dir I -type clk probe_clk]
    set reset_port [create_bd_port -dir I -type rst probe_resetn]
    set_property -dict {CONFIG.FREQ_HZ 100000000 CONFIG.ASSOCIATED_BUSIF GPIO_AXI:IRQ_AXI CONFIG.ASSOCIATED_RESET probe_resetn} $clock_port
    set_property CONFIG.POLARITY ACTIVE_LOW $reset_port
    foreach {cell port interface} {gpio GPIO_AXI S_AXI irq_controller IRQ_AXI s_axi} {
        connect_bd_net $clock_port [get_bd_pins $cell/s_axi_aclk]
        connect_bd_net $reset_port [get_bd_pins $cell/s_axi_aresetn]
        set boundary [create_bd_intf_port -mode Slave -vlnv xilinx.com:interface:aximm_rtl:1.0 $port]
        set_property -dict {CONFIG.PROTOCOL AXI4LITE CONFIG.ADDR_WIDTH 32 CONFIG.DATA_WIDTH 32 CONFIG.FREQ_HZ 100000000} $boundary
        connect_bd_intf_net $boundary [get_bd_intf_pins $cell/$interface]
        assign_bd_address -offset 0 -range 0x10000 -target_address_space [get_bd_addr_spaces $port] [get_bd_addr_segs $cell/$interface/Reg]
    }
    validate_bd_design
    set count [get_property CONFIG.C_NUM_INTR_INPUTS [get_bd_cells irq_controller]]
    set width [expr {[get_property LEFT [get_bd_pins irq_controller/intr]] - [get_property RIGHT [get_bd_pins irq_controller/intr]] + 1}]
    live_test::assert {$count == 22 && $width == 22}
    live_test::assert {[live_test::count /irq_concat] == 3}
    list status PASS baseline $baseline derived $count width $width concat_attempts 3
}
live_test::case clock {
    set rejected [ipcfg::create_cell_cfg xilinx.com:ip:clkx5_wiz test_clock {CONFIG.PRIM_IN_FREQ -1}]
    live_test::assert {[string match CONFIGURE_FAIL:* $rejected]}
    set before [live_test::count /test_clock]
    live_test::assert {$before >= 2}
    live_test::success [ipcfg::create_cell_cfg xilinx.com:ip:clkx5_wiz test_clock {CONFIG.PRIM_IN_FREQ 100}]
    live_test::assert {[get_property CONFIG.PRIM_IN_FREQ [get_bd_cells test_clock]] == 100}
    live_test::assert {[live_test::count /test_clock] == $before + 1}
    list status PASS failed_creation_attempts $before recovered_frequency 100 attempts [live_test::count /test_clock]
}
live_test::case processing_system {
    live_test::success [ipcfg::create_cell xilinx.com:ip:ps_wizard processing_system]
    set hooks [ipcfg::board_hooks processing_system]
    live_test::assert {[llength $hooks] == 1}
    set bound [ipcfg::board_bind processing_system [list [lindex $hooks 0] ps_pmc_fixed_io]]
    live_test::assert {[string match BOARD:bound* $bound]}
    live_test::assert {[get_property [lindex $hooks 0] [get_bd_cells processing_system]] eq "ps_pmc_fixed_io"}
    set baseline [get_property CONFIG.PS11_CONFIG [get_bd_cells processing_system]]
    live_test::assert {[dict size $baseline] > 0}
    set result [ipcfg::resolve_subkeys processing_system PS11_CONFIG $baseline PS11_CONFIG_INTERNAL {PS_USE_PMCPL_CLK0}]
    live_test::assert {[string match RSK:* $result]}
    set probe [ipcfg::probe_subkey_range processing_system PS11_CONFIG PS_USE_PMCPL_CLK0]
    live_test::assert {[string match PSR_OUT_OF_RANGE:* $probe]}
    set after [get_property CONFIG.PS11_CONFIG [get_bd_cells processing_system]]
    live_test::assert {$baseline eq $after}
    live_test::assert {[live_test::count /processing_system] == 4}
    list status PASS board_hook $hooks nested_readback $result range_probe $probe attempts 4
}
set channel [open [file join $evidence_dir live_attempt_results.tcldict] w]
puts $channel [dict create version [version -short] project $project_dir results $::live_test::results]
close $channel
set channel [open [file join $evidence_dir live_procedure_coverage.tsv] w]
foreach name [lsort [dict keys $::live_test::originals]] {
    set hits 0
    if {[dict exists $::live_test::hits $name]} {set hits [dict get $::live_test::hits $name]}
    puts $channel "$name\t$hits"
    trace remove execution $name enter [list live_test::entered $name]
}
close $channel
save_bd_design
procedure_trace::uninstall
close $timing_channel
set ::procedure_trace::output stdout
set failures 0
dict for {name result} $::live_test::results {
    if {[dict get $result code] != 0} {incr failures}
}
puts "LIVE_SUMMARY:cases=[dict size $::live_test::results] failed=$failures project=$project_dir"
if {$failures} {error "$failures live test(s) failed; see live_attempt_results.tcldict"}