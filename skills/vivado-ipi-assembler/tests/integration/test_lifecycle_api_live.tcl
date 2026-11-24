# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

set test_root [file normalize [file join [file dirname [info script]] ../../../../..]]
if {[current_project -quiet] ne "" || [llength [info commands ipiasm::init]]} {
    error "Use a fresh disposable session with no project or helpers loaded"
}
if {![info exists lifecycle_arm]} {set lifecycle_arm current}
if {$lifecycle_arm ni {baseline current}} {error "Expected baseline or current arm"}
set skills [file join $test_root .claude skills]
if {$lifecycle_arm eq "baseline"} {
    set skills [file join $test_root tests evidence workflow_benchmark 20260925-baseline sources .claude skills]
}
set evidence_dir [file join $test_root tests evidence workflow_benchmark lifecycle_${lifecycle_arm}_[clock microseconds]]
file mkdir $evidence_dir
set configurator [file join $skills vivado-ip-configurator lib ipcfg ipcfg.tcl]
set assembler [file join $skills vivado-ipi-assembler lib ipiasm.tcl]
source -notrace $configurator
source -notrace $assembler
proc lifecycle_read {path} {
    set channel [open $path r]
    try {return [read $channel]} finally {close $channel}
}
proc lifecycle_assert {expression message} {
    if {![uplevel 1 [list expr $expression]]} {error $message}
}
proc lifecycle_state {} {
    set state {}
    foreach name {
        ipcfg::req ipcfg::board_owned ipcfg::board_override ipcfg::bd_file
        ipiasm::gates ipiasm::qos_sub ipiasm::stage_t ipiasm::stage_log
        ipiasm::delivery_fp ipiasm::executed_plan ipiasm::interface_contracts
        ipiasm::stage_completion ipiasm::stage_result
    } {dict set state $name [set ::$name]}
    return $state
}
proc lifecycle_seed {} {
    set ::ipcfg::req [dict create /fixture [list [list CONFIG.TEXT {spaces ; $literal [brackets]} before "reason\nwith newline"]]]
    set ::ipcfg::board_owned {/fixture {fixture ownership sentinel}}
    set ::ipcfg::board_override {/fixture {fixture override sentinel}}
    set ::ipcfg::bd_file fixture.bd
    set ::ipiasm::gates {board 1 inventory 1 qos 1}
    set ::ipiasm::qos_sub {qos 1 noc 1}
    set ::ipiasm::delivery_fp fixture
    set ::ipiasm::executed_plan {path fixture sha256 fixture}
    set ::ipiasm::interface_contracts {fixture contract}
    set ::ipiasm::stage_completion {fixture complete}
    set ::ipiasm::stage_result {status PASS}
    ipiasm::stage fixture
}
create_project lifecycle_test [file join $evidence_dir project] -part xc2ve3558-sfva1440-2MP-e-S
set results {}
for {set trial 1} {$trial <= 3} {incr trial} {
    create_bd_design scratch_$trial
    if {$lifecycle_arm eq "current"} {ipiasm::begin_design} else {ipiasm::init}
    # The fixture cell is typed directly, which the plan gate refuses after init.
    ipiasm::_gate_set plan 1 "test: fixture cell"
    create_bd_cell -type hier fixture
    for {set attempt 1} {$attempt <= 10} {incr attempt} {
        ipcfg::attempt_call /fixture fixture_port [list create_bd_port -dir I attempt_$attempt]
    }
    set ledger [file join [get_property DIRECTORY [current_project]] .ipcfg attempts.tcldict]
    set ledger_before [lifecycle_read $ledger]
    lifecycle_seed
    set before [lifecycle_state]
    set started [clock microseconds]
    set load_code [catch {source -notrace $configurator; source -notrace $assembler} load_result load_options]
    set reload_us [expr {[clock microseconds] - $started}]
    set reload_preserved [expr {$before eq [lifecycle_state]}]
    lifecycle_seed
    set before [lifecycle_state]
    set started [clock microseconds]
    set resume [ipiasm::init]
    set resume_us [expr {[clock microseconds] - $started}]
    set resume_preserved [expr {$before eq [lifecycle_state]}]
    set blocked [catch {ipcfg::attempt_call /fixture forbidden {create_bd_port -dir I forbidden}} diagnostic]
    lifecycle_assert {$blocked && [string match *ATTEMPT_LIMIT* $diagnostic]} "Exhaustion was bypassed"
    lifecycle_assert {[llength [get_bd_ports -quiet forbidden]] == 0} "Exhausted operation executed"
    lifecycle_assert {$ledger_before eq [lifecycle_read $ledger]} "Load/resume changed persistent ledger"
    if {$lifecycle_arm eq "current"} {
        lifecycle_assert {!$load_code && $reload_preserved && $resume_preserved} "Same-context state loss"
        set before [lifecycle_state]
        set started [clock microseconds]
        set inventory [ipiasm::api_inventory]
        set inventory_us [expr {[clock microseconds] - $started}]
        set allowed [ipcfg::python_exec -c {import runpy, sys; print(" ".join(sorted(runpy.run_path(sys.argv[1])["ALLOWED_PROCS"])))} [file join $skills vivado-ipi-assembler scripts compile_assembly_plan.py]]
        foreach name $allowed {lifecycle_assert {[dict exists $inventory $name]} "Missing whitelist helper $name"}
        set check [ipiasm::api_preflight [list [list ipcfg::require [list fixture {} reason]] [list ipiasm::gate_status {}]]]
        lifecycle_assert {[dict get $check status] eq "PASS"} "Valid API calls refused"
        set invalid [ipiasm::api_preflight {{ipiasm::missing {}} {ipiasm::gate_status {extra}}}]
        lifecycle_assert {[dict get $invalid status] eq "FAIL"} "Invalid API accepted"
        set record [lindex [ipcfg::requirements_data /fixture] 0]
        lifecycle_assert {[dict get $record expected] eq {spaces ; $literal [brackets]} && [dict get $record reason] eq "reason\nwith newline"} "Lossy requirements"
        lifecycle_assert {$before eq [lifecycle_state]} "API discovery changed state"
        set ::ipiasm::unused_qos_routes {context scratch routes {noc/S00_AXI/MC_0 unused}}
        save_bd_design
        create_bd_design delivery_$trial
        set refused [ipiasm::init]
        lifecycle_assert {[string match ASM_FAIL:* $refused] && $before eq [lifecycle_state]} "Implicit switch accepted or mutated state"
        set started [clock microseconds]
        set transition [ipiasm::begin_design]
        set transition_us [expr {[clock microseconds] - $started}]
        lifecycle_assert {[string match SUCCESS:* $transition]} "Explicit transition failed"
        foreach name {req board_owned board_override bd_file} {lifecycle_assert {[set ::ipcfg::$name] eq {}} "Scratch configurator state leaked"}
        foreach name {stage_completion stage_result executed_plan interface_contracts delivery_fp stage_log} {
            lifecycle_assert {[set ::ipiasm::$name] eq {}} "Scratch assembler state leaked: $name"
        }
        foreach gate {board inventory qos} {lifecycle_assert {![dict get $::ipiasm::gates $gate]} "Gate leaked"}
        foreach gate {qos noc} {lifecycle_assert {![dict get $::ipiasm::qos_sub $gate]} "QoS subgate leaked"}
        lifecycle_assert {![info exists ::ipiasm::unused_qos_routes]} "Unused routes leaked"
        lifecycle_assert {$ledger_before eq [lifecycle_read $ledger]} "Transition reset attempt ledger"
        dict set results api_$trial [dict create status PASS public_helpers [dict size $inventory] whitelist_helpers [llength $allowed] inventory_us $inventory_us transition_us $transition_us]
    } else {
        lifecycle_assert {!$reload_preserved && !$resume_preserved} "Baseline no longer reproduces state loss"
    }
    dict set results trial_$trial [dict create reload_code $load_code reload_result $load_result \
        reload_options $load_options reload_preserved $reload_preserved reload_us $reload_us \
        resume_preserved $resume_preserved resume_us $resume_us attempt_count 10 ledger_unchanged 1]
    save_bd_design
}
if {$lifecycle_arm eq "current"} {
    set before [lifecycle_state]
    foreach library [list $configurator $assembler] {
        set copy [file join $evidence_dir [file tail $library]]
        file copy $library $copy
        if {$library eq $assembler} {
            file copy [file join [file dirname $library] feasibility.tcl] [file join $evidence_dir feasibility.tcl]
        }
        set code [catch {source -notrace $copy} detail]
        lifecycle_assert {$code && [string match LIBRARY_FAIL:* $detail]} "Changed source path accepted"
        set child [interp create]
        try {
            interp eval $child [list source $copy]
            set channel [open $copy a]
            puts $channel "\n"
            close $channel
            set code [catch {interp eval $child [list source $copy]} detail]
            lifecycle_assert {$code && [string match LIBRARY_FAIL:* $detail]} "Changed source content accepted"
        } finally {interp delete $child}
    }
    lifecycle_assert {$before eq [lifecycle_state]} "Rejected reload changed state"
    dict set results reload_rejection PASS
}
set hashes [ipcfg::python_exec -c {import hashlib, pathlib, sys; print(" ".join(hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() for path in sys.argv[1:]))} $configurator $assembler [info script]]
set channel [open [file join $evidence_dir results.tcldict] {WRONLY CREAT EXCL}]
puts $channel [dict create schema_version 1 arm $lifecycle_arm status PASS vivado [version -short] results $results \
    source_paths [list $configurator $assembler [info script]] sha256 $hashes]
close $channel
puts "LIFECYCLE_API:PASS arm=$lifecycle_arm evidence=[file join $evidence_dir results.tcldict]"