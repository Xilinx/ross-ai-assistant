# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Run only in an authorized disposable project with fresh helper libraries.
# Caller supplies qualification_dir and an unconfigured clkx5_wiz named generator.
# This no-board fixture qualifies BD metadata/connectivity, not physical timing.
namespace eval clock_contracts_live {
    variable script [file normalize [info script]]
    variable results {}
}

proc clock_contracts_live::expect {label result prefix} {
    dict set ::clock_contracts_live::results $label $result
    if {![string match ${prefix}* $result]} {error "$label: $result"}
}

proc clock_contracts_live::capture {label body prefix} {
    set path [file join $::qualification_dir $label.tcldict]
    set receipt [ipcfg::capture $path $body [list $prefix]]
    if {[dict get $receipt status] ne "OK"} {error "$label: $receipt"}
}

proc clock_contracts_live::setup {} {
    # Parameter shape was discovered on the live IP. No alternate guessed writes.
    clock_contracts_live::expect configure [ipcfg::require generator {
        CONFIG.PRIM_IN_FREQ 100.000
        CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY 50.000,100.000,100.000,100.000,100.000,100.000,100.000
        CONFIG.CLKOUT_USED true,false,false,false,false,false,false
        CONFIG.USE_LOCKED true
    } {Synthetic 100 MHz reference and 50 MHz clock qualification}] REQ:pinned+applied
    clock_contracts_live::expect reset_create [ipcfg::create_cell xilinx.com:ip:proc_sys_reset reset_domain] SUCCESS:
    clock_contracts_live::expect zero_create [ipcfg::create_cell_cfg xilinx.com:ip:xlconstant zero {
        CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 0
    }] SUCCESS:
    create_bd_port -dir I -type clk -freq_hz 100000000 reference
    create_bd_port -dir I -type rst reset_in
    set_property CONFIG.POLARITY ACTIVE_LOW [get_bd_ports reset_in]
    create_bd_port -dir O -type rst reset_out
    set_property CONFIG.POLARITY ACTIVE_LOW [get_bd_ports reset_out]
    connect_bd_net [get_bd_ports reference] [get_bd_pins generator/clk_in1]
    connect_bd_net [get_bd_pins generator/clk_out1] [get_bd_pins reset_domain/slowest_sync_clk]
    connect_bd_net [get_bd_ports reset_in] [get_bd_pins reset_domain/ext_reset_in]
    connect_bd_net [get_bd_pins generator/locked] [get_bd_pins reset_domain/dcm_locked]
    connect_bd_net [get_bd_pins zero/dout] [get_bd_pins reset_domain/aux_reset_in] [get_bd_pins reset_domain/mb_debug_sys_rst]
    connect_bd_net [get_bd_pins reset_domain/peripheral_aresetn] [get_bd_ports reset_out]
    return TEST:PASS
}

proc clock_contracts_live::validate {} {
    # Validation clears some native message history, so it must not span an
    # append-only capture window. Keep the MCP session log and exact counters.
    set before [ipcfg::_message_counts]
    set code [catch {validate_bd_design} result options]
    set after [ipcfg::_message_counts]
    set receipt [ipcfg::write_evidence [file join $::qualification_dir validation.tcldict] \
        [dict create code $code result $result options $options before $before after $after native_log $::ipcfg::native_log_path]]
    if {$code} {return -options $options $result}
    foreach severity {ERROR {CRITICAL WARNING} WARNING} {
        if {[dict get $after $severity] != [dict get $before $severity]} {error "Native validation emitted $severity; see $receipt"}
    }
}

proc clock_contracts_live::exercise {} {
    variable script
    set definitions {}
    foreach cell [get_bd_cells -hier -filter {TYPE == ip}] {
        foreach definition [get_ipdefs -all [get_property VLNV $cell]] {
            lappend definitions [get_property XML_FILE_NAME $definition]
        }
    }
    # The test script is the independent synthetic requirement and no-board policy.
    set sources [dict create spec [list $script] board [list $script] ip [lsort -unique $definitions]]
    set fingerprint [ipiasm::feasibility_fingerprint $sources]
    set check {id generated.clock kind clock_frequency subject /reset_domain/slowest_sync_clk
        expected 50000000 source synthetic-test#/clock stage clocks
        driver /generator/clk_out1 input /generator/clk_in1 reference /reference
        reference_hz 100000000 tolerance_hz 0}
    set reset {cell /reset_domain clock /generator/clk_out1 source /reset_in
        source_polarity ACTIVE_LOW lock /generator/locked
        sinks {{/reset_out peripheral_aresetn}}}
    clock_contracts_live::expect registered [ipiasm::set_clock_contracts [list $check] [list $reset] $sources $fingerprint] CLOCK_CONTRACTS:SET
    clock_contracts_live::expect positive [ipiasm::check_clock_contracts] CLOCK_CONTRACTS:OK
    set wrong $check
    dict set wrong expected 50000001
    clock_contracts_live::expect wrong_frequency [ipiasm::check_feasibility [list $wrong] clocks $sources $fingerprint] FEASIBILITY_FAIL:
    set wrong $check
    dict set wrong driver /reference
    clock_contracts_live::expect wrong_driver [ipiasm::check_feasibility [list $wrong] clocks $sources $fingerprint] FEASIBILITY_FAIL:
    set stale $fingerprint
    dict set stale build synthetic-other-build
    clock_contracts_live::expect stale_build [ipiasm::check_feasibility [list $check] clocks $sources $stale] FEASIBILITY_FAIL:
    set weaker $check
    dict set weaker tolerance_hz 100
    clock_contracts_live::expect immutable [ipiasm::set_clock_contracts [list $weaker] [list $reset] $sources $fingerprint] CLOCK_CONTRACT_FAIL:
    # Snapshot and restore the actual lock edge, even when the assertion fails.
    # Reserve one transaction for the temporary experiment and its restoration.
    ipcfg::attempt_call reset_domain lock_regression {
        set lock_net [get_bd_nets -of_objects [get_bd_pins reset_domain/dcm_locked]]
        try {
            disconnect_bd_net $lock_net [get_bd_pins reset_domain/dcm_locked]
            clock_contracts_live::expect missing_lock [ipiasm::check_clock_contracts] CLOCK_CONTRACT_FAIL:
        } finally {
            connect_bd_net [get_bd_pins generator/locked] [get_bd_pins reset_domain/dcm_locked]
        }
    }
    clock_contracts_live::expect restored [ipiasm::check_clock_contracts] CLOCK_CONTRACTS:OK
    clock_contracts_live::expect resume [ipiasm::init] SUCCESS:ipiasm
    clock_contracts_live::expect after_resume [ipiasm::check_clock_contracts] CLOCK_CONTRACTS:OK
    ipcfg::write_evidence [file join $::qualification_dir clock_contract_results.tcldict] \
        [dict create results $::clock_contracts_live::results sources $sources fingerprint $fingerprint checks [list $check] resets [list $reset]]
    return TEST:PASS
}

if {![info exists ::qualification_dir] || ![string match */clock_qualification_* $::qualification_dir] ||
    [get_property DIRECTORY [current_project]] ne [file join $::qualification_dir project] ||
    [llength [get_bd_cells -quiet]] != 1 || [get_bd_cells -quiet generator] eq ""} {
    error "Fresh disposable clock qualification fixture required"
}
clock_contracts_live::capture setup {clock_contracts_live::setup} TEST:PASS
clock_contracts_live::validate
clock_contracts_live::capture contracts {clock_contracts_live::exercise} TEST:PASS
puts "CLOCK_NATIVE:PASS evidence=$::qualification_dir checks=[dict size $::clock_contracts_live::results]"