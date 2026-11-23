# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

if {![info exists replay_session_id] || $replay_session_id in {
    vivado-20260925-134546-f2f5e4cc
    vivado-20260925-192118-39cf7164
    vivado-20260925-192131-4a430de6
}} {error "Supply replay_session_id for your own fresh disposable Tcl session"}
if {[current_project -quiet] ne "" || [llength [info commands ::ipcfg::require]] ||
    [llength [info commands ::ipiasm::init]]} {
    error "Requires a fresh session with no open project or loaded helpers"
}
namespace eval replay_live {
    variable root [file normalize [file join [file dirname [info script]] ../../../../..]]
    variable test_source [file normalize [info script]]
    variable events {}
    variable results {}
    variable target /fixture
}
proc replay_live::assert {expression message} {
    if {![uplevel 1 [list expr $expression]]} {error $message}
}
proc replay_live::bytes {path} {
    set channel [open $path rb]
    try {return [read $channel]} finally {close $channel}
}
proc replay_live::ledger {} {
    return [file join [get_property DIRECTORY [current_project]] .ipcfg attempts.tcldict]
}
proc replay_live::count {} {
    set path [replay_live::ledger]
    if {![file exists $path]} {return 0}
    set state [replay_live::bytes $path]
    set key [list [current_bd_design] $::replay_live::target]
    if {![dict exists $state $key]} {return 0}
    return [dict get $state $key count]
}
proc replay_live::trace_write {command code result operation} {
    lappend ::replay_live::events [dict create command $command code $code result $result]
}
proc replay_live::values {} {
    set object [get_bd_cells -quiet $::replay_live::target]
    if {![llength $object]} {return {}}
    return [dict create CONFIG.CONST_WIDTH [get_property CONFIG.CONST_WIDTH $object] \
        CONFIG.CONST_VAL [get_property CONFIG.CONST_VAL $object]]
}
proc replay_live::measure {label script expected_attempts expected_writes} {
    set before [replay_live::count]
    set values_before [replay_live::values]
    set ::replay_live::events {}
    set started [clock microseconds]
    set code [catch {uplevel 1 $script} result options]
    set after [replay_live::count]
    set successes 0
    foreach event $::replay_live::events {
        if {[dict get $event code] == 0} {incr successes}
    }
    set record [dict create code $code result $result options $options \
        elapsed_us [expr {[clock microseconds] - $started}] attempts_before $before \
        attempts_after $after attempt_delta [expr {$after - $before}] \
        native_write_calls [llength $::replay_live::events] successful_write_calls $successes \
        values_before $values_before values_after [replay_live::values] trace $::replay_live::events]
    dict set ::replay_live::results $::replay_live::trial $label $record
    replay_live::assert {!$code} "$label raised: $result"
    replay_live::assert {$after - $before == $expected_attempts} "$label attempt delta mismatch"
    replay_live::assert {[llength $::replay_live::events] == $expected_writes && $successes == $expected_writes} "$label native write count mismatch"
    return $result
}
proc replay_live::state {} {
    set state {}
    foreach name {
        ipcfg::req ipcfg::board_owned ipcfg::board_override ipcfg::bd_file
        ipiasm::initialized ipiasm::design_context ipiasm::gates ipiasm::qos_sub
        ipiasm::stage_t ipiasm::stage_log ipiasm::delivery_fp ipiasm::forced_fp
        ipiasm::executed_plan ipiasm::interface_contracts ipiasm::stage_completion
        ipiasm::stage_result ipiasm::plan_request ipiasm::unused_qos_routes
    } {
        if {[info exists ::$name]} {dict set state $name [set ::$name]}
    }
    return $state
}
proc replay_live::trial_body {} {
    set created [replay_live::measure creation {
        ipcfg::create_cell xilinx.com:ip:xlconstant:1.1 /fixture
    } 1 1]
    replay_live::assert {[string match SUCCESS:* $created]} $created
    set initial [get_property CONFIG.CONST_VAL [get_bd_cells /fixture]]
    replay_live::assert {$initial in {0 1}} "Unexpected one-bit constant default"
    set desired [dict create CONFIG.CONST_VAL [expr {1 - $initial}]]
    set first [replay_live::measure initial_require {
        ipcfg::require /fixture $desired initial
    } 1 1]
    replay_live::assert {[string match REQ:pinned+applied* $first]} $first
    set baseline [dict get [lindex [ipcfg::requirements_data /fixture] 0] base]
    set ledger_before [replay_live::bytes [replay_live::ledger]]
    replay_live::measure identical_replay {
        for {set repeat 0} {$repeat < 3} {incr repeat} {
            set required [ipcfg::require /fixture $desired replay]
            set applied [ipcfg::apply_dict /fixture $desired]
            set created [ipcfg::create_cell xilinx.com:ip:xlconstant:1.1 /fixture]
            replay_live::assert {[string match REQ:pinned+applied* $required]} $required
            replay_live::assert {[string match SUCCESS:* $applied] && [string match SUCCESS:* $created]} "Replay refused"
        }
    } 0 0
    replay_live::assert {$ledger_before eq [replay_live::bytes [replay_live::ledger]]} "Replay changed ledger bytes"
    replay_live::assert {$baseline eq [dict get [lindex [ipcfg::requirements_data /fixture] 0] base]} "Replay lost original base"
    set desired [dict create CONFIG.CONST_VAL $initial]
    set drift [replay_live::measure one_value_drift {
        ipcfg::require /fixture $desired drift
    } 1 1]
    replay_live::assert {[string match REQ:pinned+applied* $drift]} $drift
    set change [dict get $::replay_live::results $::replay_live::trial one_value_drift]
    replay_live::assert {[dict get $change values_before CONFIG.CONST_WIDTH] eq [dict get $change values_after CONFIG.CONST_WIDTH]} "Drift changed unrelated width"
    replay_live::assert {[dict get $change values_after CONFIG.CONST_VAL] == $initial} "Drift did not stick"
    set event [lindex [dict get $change trace] 0]
    replay_live::assert {[lindex [dict get $event command] 1] eq "-dict" && [lindex [dict get $event command] 2] eq $desired} "Drift wrote unexpected keys"
    replay_live::measure exhaust_budget {
        while {[replay_live::count] < 10} {
            set current [get_property CONFIG.CONST_VAL [get_bd_cells /fixture]]
            set desired [dict create CONFIG.CONST_VAL [expr {1 - $current}]]
            set applied [ipcfg::apply_dict /fixture $desired]
            replay_live::assert {[string match SUCCESS:* $applied]} $applied
            replay_live::assert {[get_property CONFIG.CONST_VAL [get_bd_cells /fixture]] == [dict get $desired CONFIG.CONST_VAL]} "Budget write did not stick"
        }
    } 7 7
    set ledger_before [replay_live::bytes [replay_live::ledger]]
    replay_live::measure exhausted_satisfied {
        set required [ipcfg::require /fixture $desired exhausted_satisfied]
        set applied [ipcfg::apply_dict /fixture $desired]
        replay_live::assert {[string match REQ:pinned+applied* $required] && [string match SUCCESS:* $applied]} "Satisfied exhausted check refused"
    } 0 0
    set forbidden [dict create CONFIG.CONST_VAL [expr {1 - [dict get $desired CONFIG.CONST_VAL]}]]
    replay_live::measure exhausted_drift {
        set applied [ipcfg::apply_dict /fixture $forbidden]
        set required [ipcfg::require /fixture $forbidden exhausted_drift]
        replay_live::assert {[string match *ATTEMPT_LIMIT* $applied] && [string match *ATTEMPT_LIMIT* $required]} "Exhausted drift was not refused"
        replay_live::assert {[get_property CONFIG.CONST_VAL [get_bd_cells /fixture]] == [dict get $desired CONFIG.CONST_VAL]} "Exhausted drift changed value"
        list apply_dict $applied require $required
    } 0 0
    replay_live::assert {$ledger_before eq [replay_live::bytes [replay_live::ledger]]} "Exhausted checks changed ledger bytes"
    set created [ipcfg::create_cell xilinx.com:ip:axi_gpio:2.0 /board_probe]
    replay_live::assert {[string match SUCCESS:* $created]} $created
    set hooks [ipcfg::board_hooks /board_probe]
    if {[llength $hooks]} {
        set hook [lindex $hooks 0]
        set binding [list $hook [get_property $hook [get_bd_cells /board_probe]]]
        set before [replay_live::bytes [replay_live::ledger]]
        set ::replay_live::events {}
        set refused [ipcfg::board_bind /board_probe $binding]
        dict set ::replay_live::results $::replay_live::trial board_unknown [dict create \
            result $refused binding $binding trace $::replay_live::events \
            ledger_unchanged [expr {$before eq [replay_live::bytes [replay_live::ledger]]}]]
        replay_live::assert {[string match BOARD_FAIL:OWNERSHIP_UNKNOWN* $refused] && ![llength $::replay_live::events] && $before eq [replay_live::bytes [replay_live::ledger]]} "Unknown board ownership guard failed"
    } else {
        dict set ::replay_live::results $::replay_live::trial board_unknown [dict create status SKIP reason "Native AXI GPIO exposes no board hooks without a board part"]
    }
    return TEST:PASS
}
proc replay_live::transition {} {
    set ::ipcfg::board_owned {/fixture {synthetic_lifecycle_sentinel fixture}}
    set ::ipcfg::board_override {/fixture synthetic_lifecycle_sentinel}
    set ::ipcfg::bd_file synthetic_lifecycle_sentinel
    set ::ipiasm::gates {board 1 inventory 1 qos 1}
    set ::ipiasm::qos_sub {qos 1 noc 1}
    foreach name {delivery_fp forced_fp executed_plan interface_contracts stage_completion stage_result plan_request stage_log} {
        set ::ipiasm::$name synthetic_lifecycle_sentinel
    }
    set ::ipiasm::unused_qos_routes {context synthetic_lifecycle_sentinel routes {}}
    set before [replay_live::state]
    set old_identity [ipiasm::context_identity]
    set directory [get_property DIRECTORY [current_project]]
    set ledger [replay_live::ledger]
    set ledger_bytes [replay_live::bytes $ledger]
    save_bd_design
    close_project
    create_project replay_second $directory -part $::replay_live::part
    create_bd_design replay
    set new_identity [ipiasm::context_identity]
    replay_live::assert {[lindex $old_identity 0] eq [lindex $new_identity 0] && [lindex $old_identity 2] eq [lindex $new_identity 2] && [lindex $old_identity 1] ne [lindex $new_identity 1]} "Project-name-only transition not achieved"
    set refused [ipiasm::init]
    set after_refusal [replay_live::state]
    set transition [ipiasm::begin_design]
    set after_transition [replay_live::state]
    dict set ::replay_live::results $::replay_live::trial transition [dict create \
        old_identity $old_identity new_identity $new_identity init_result $refused \
        begin_design_result $transition before $before after_refusal $after_refusal \
        after_transition $after_transition ledger_path $ledger ledger_before $ledger_bytes \
        ledger_after [replay_live::bytes $ledger]]
    replay_live::assert {[string match ASM_FAIL:* $refused] && $before eq $after_refusal} "Implicit project transition did not preserve state"
    replay_live::assert {[string match SUCCESS:* $transition]} $transition
    foreach name {req board_owned board_override bd_file} {
        replay_live::assert {[set ::ipcfg::$name] eq {}} "Configurator state leaked: $name"
    }
    foreach name {stage_completion stage_result executed_plan interface_contracts delivery_fp forced_fp plan_request stage_log} {
        replay_live::assert {[set ::ipiasm::$name] eq {}} "Assembler state leaked: $name"
    }
    foreach name {board inventory qos} {replay_live::assert {![dict get $::ipiasm::gates $name]} "Gate leaked: $name"}
    foreach name {qos noc} {replay_live::assert {![dict get $::ipiasm::qos_sub $name]} "QoS subgate leaked: $name"}
    replay_live::assert {![info exists ::ipiasm::unused_qos_routes]} "Unused QoS intent leaked"
    replay_live::assert {$::ipiasm::design_context eq $new_identity && $ledger_bytes eq [replay_live::bytes $ledger]} "Transition identity or persistent ledger mismatch"
    return TEST:PASS
}
set ::replay_live::part xc2ve3558-sfva1440-2MP-e-S
set ::replay_live::evidence [file join $::replay_live::root tests evidence workflow_benchmark replay_native_[clock microseconds]]
file mkdir $::replay_live::evidence
set run_code [catch {
    for {set ::replay_live::trial 1} {$::replay_live::trial <= 3} {incr ::replay_live::trial} {
        set directory [file join $::replay_live::evidence trial_$::replay_live::trial]
        create_project replay_first $directory -part $::replay_live::part
        create_bd_design replay
        if {$::replay_live::trial == 1} {
            set configurator [file join $::replay_live::root .claude skills vivado-ip-configurator lib ipcfg ipcfg.tcl]
            set assembler [file join $::replay_live::root .claude skills vivado-ipi-assembler lib ipiasm.tcl]
            source -notrace $configurator
            source -notrace $assembler
            set python_status [ipcfg::python_status]
            replay_live::assert {[string match {PYTHON:OK *} $python_status]} $python_status
            set imports [ipcfg::python_exec -c {import argparse, base64, hashlib, json, pathlib, re, sys, typing; print("PYTHON_IMPORTS:OK")}]
            set hashes [ipcfg::python_exec -c {import hashlib, pathlib, sys; print(" ".join(hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() for path in sys.argv[1:]))} $configurator $assembler $::replay_live::test_source]
            set provenance [dict create session_id $replay_session_id vivado [version -short] \
                source_paths [list $configurator $assembler $::replay_live::test_source] sha256 $hashes \
                python_status $python_status imports $imports]
            foreach command {set_property create_bd_cell} {
                trace add execution $command leave replay_live::trace_write
            }
        }
        set begun [ipiasm::begin_design]
        replay_live::assert {[string match SUCCESS:* $begun]} $begun
        set receipt [ipcfg::capture [file join $::replay_live::evidence trial_$::replay_live::trial.tcldict] \
            {replay_live::trial_body} {TEST:PASS}]
        dict set ::replay_live::results $::replay_live::trial capture $receipt
        replay_live::assert {[dict get $receipt status] eq "OK"} "Native trial failed: $receipt"
        replay_live::transition
        close_project
    }
} run_result run_options]
foreach command {set_property create_bd_cell} {
    catch {trace remove execution $command leave replay_live::trace_write}
}
if {[current_project -quiet] ne ""} {set cleanup_code [catch {close_project} cleanup_result]} else {set cleanup_code 0; set cleanup_result already_closed}
if {![info exists provenance]} {set provenance [dict create session_id $replay_session_id]}
set channel [open [file join $::replay_live::evidence results.tcldict] {WRONLY CREAT EXCL}]
puts $channel [dict create schema_version 1 status [expr {$run_code || $cleanup_code ? "FAIL" : "PASS"}] \
    provenance $provenance code $run_code result $run_result options $run_options \
    trials $::replay_live::results cleanup [dict create code $cleanup_code result $cleanup_result] \
    session_stop NOT_PERFORMED_BY_HARNESS]
close $channel
puts "REPLAY_LIFECYCLE:code=$run_code evidence=[file join $::replay_live::evidence results.tcldict]"
if {$run_code} {return -options $run_options $run_result}
if {$cleanup_code} {error "Disposable project cleanup failed: $cleanup_result"}