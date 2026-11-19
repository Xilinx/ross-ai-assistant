# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

namespace eval stage_resume_live {
    variable root [file normalize [file join [file dirname [info script]] ../../../../..]]
    variable source_path [file normalize [info script]]
    variable results {}
    variable assertions 0
    variable compiles 0
    variable compiler_events {}
    variable writes {}
    variable evidence_paths {}
    variable target /fixture
}
proc stage_resume_live::assert {expression message} {
    if {![uplevel 1 [list expr $expression]]} {error $message}
    incr ::stage_resume_live::assertions
}
proc stage_resume_live::bytes {path} {
    set channel [open $path rb]
    try {return [read $channel]} finally {close $channel}
}
proc stage_resume_live::plan_json {} {
    return {{
  "schema_version": 1,
  "stages": [
    {"name": "prepare", "operations": [
    {"id": "prepare_check", "op": "call", "proc": "ipiasm::stage", "args": ["prepare"], "expect": ["STAGE:"]}
    ]},
    {"name": "cached", "operations": [
    {"id": "cached_check", "op": "call", "proc": "ipiasm::stage", "args": ["cached"], "expect": ["STAGE:"]}
    ]},
    {"name": "configure", "operations": [
      {"id": "early_write", "op": "call", "proc": "ipcfg::apply_dict", "args": ["/fixture", {"CONFIG.CONST_WIDTH": 8, "CONFIG.CONST_VAL": 3}], "expect": ["SUCCESS:"]},
      {"id": "late_write", "op": "call", "proc": "ipcfg::apply_dict", "args": ["/late_missing", {"CONFIG.CONST_WIDTH": 8, "CONFIG.CONST_VAL": 3}], "expect": ["SUCCESS:"]},
    {"id": "configure_sentinel", "op": "call", "proc": "ipiasm::stage", "args": ["configure_done"], "expect": ["STAGE:"]}
    ]},
    {"name": "finish", "operations": [
    {"id": "finish_check", "op": "call", "proc": "ipiasm::stage", "args": ["finish"], "expect": ["STAGE:"]}
    ]}
  ]
}}
}
proc stage_resume_live::ledger {} {
    return [file join [get_property DIRECTORY [current_project]] .ipcfg attempts.tcldict]
}
proc stage_resume_live::count {} {
    set path [stage_resume_live::ledger]
    if {![file exists $path]} {return 0}
    set state [stage_resume_live::bytes $path]
    set key [list [current_bd_design] $::stage_resume_live::target]
    if {![dict exists $state $key]} {return 0}
    return [dict get $state $key count]
}
proc stage_resume_live::values {} {
    set object [get_bd_cells -quiet $::stage_resume_live::target]
    if {![llength $object]} {return {}}
    return [dict create CONFIG.CONST_WIDTH [get_property CONFIG.CONST_WIDTH $object] \
        CONFIG.CONST_VAL [get_property CONFIG.CONST_VAL $object]]
}
proc stage_resume_live::trace_compile {command operation} {
    if {[file tail [lindex $command 1]] eq "compile_assembly_plan.py"} {
        incr ::stage_resume_live::compiles
        lappend ::stage_resume_live::compiler_events $command
    }
}
proc stage_resume_live::trace_write {command code result operation} {
    if {[lsearch -exact [lindex $command end] $::stage_resume_live::target] >= 0} {
        lappend ::stage_resume_live::writes [dict create command $command code $code result $result]
    }
}
proc stage_resume_live::run_stage {label stage expected_status expected_attempts expected_writes expected_skips} {
    set before [stage_resume_live::count]
    set before_values [stage_resume_live::values]
    set ledger_before [stage_resume_live::bytes [stage_resume_live::ledger]]
    set ::stage_resume_live::writes {}
    set compile_before $::stage_resume_live::compiles
    set started [clock microseconds]
    set code [catch {ipiasm::assemble_from_plan $::stage_resume_live::plan $stage} result options]
    set status [ipiasm::stage_execution_status]
    set after [stage_resume_live::count]
    set record [dict create code $code result $result options $options status $status \
        elapsed_us [expr {[clock microseconds] - $started}] \
        attempts_before $before attempts_after $after attempt_delta [expr {$after - $before}] \
        compile_delta [expr {$::stage_resume_live::compiles - $compile_before}] \
        compile_total $::stage_resume_live::compiles native_target_writes $::stage_resume_live::writes \
        values_before $before_values values_after [stage_resume_live::values] \
        ledger_before $ledger_before ledger_after [stage_resume_live::bytes [stage_resume_live::ledger]]]
    dict set ::stage_resume_live::results $label $record
    stage_resume_live::assert {!$code} "$label raised unexpectedly: $result"
    stage_resume_live::assert {[dict get $status status] eq $expected_status} "$label unexpected stage status: $result"
    stage_resume_live::assert {$after - $before == $expected_attempts} "$label target attempt delta mismatch"
    stage_resume_live::assert {[llength $::stage_resume_live::writes] == $expected_writes} "$label target native write count mismatch"
    foreach event $::stage_resume_live::writes {
        stage_resume_live::assert {[dict get $event code] == 0} "$label target native write failed"
    }
    stage_resume_live::assert {[dict get $status skipped_operations] == $expected_skips} "$label skip count mismatch"
    set evidence [dict get $status evidence_path]
    stage_resume_live::assert {$evidence ni $::stage_resume_live::evidence_paths && [file isfile $evidence]} "$label missing or reused evidence"
    lappend ::stage_resume_live::evidence_paths $evidence
    set saved [dict get [stage_resume_live::bytes $evidence] data]
    stage_resume_live::assert {$saved eq $status} "$label incomplete durable stage evidence"
    dict for {operation details} [dict get $status operations] {
        set capture [dict get $details capture]
        stage_resume_live::assert {[dict exists $capture evidence_path] && [file isfile [dict get $capture evidence_path]]} "$label/$operation missing operation capture"
        stage_resume_live::assert {[file isfile [dict get $capture log_path]]} "$label/$operation missing native log"
    }
    return $status
}
proc stage_resume_live::assert_late_failure {status} {
    stage_resume_live::assert {[dict get $status operations early_write status] eq "PASS"} "Earlier configuration did not succeed"
    stage_resume_live::assert {[dict get $status operations late_write status] eq "FAIL"} "Later operation did not fail"
    stage_resume_live::assert {![dict exists $status operations configure_sentinel]} "Failure allowed a later operation"
    stage_resume_live::assert {[dict get $status completed_stages] eq {prepare cached}} "Failed stage completed or lost prerequisites"
    stage_resume_live::assert {[dict get $status error_info] ne {}} "Failure diagnostic missing"
    stage_resume_live::assert {[dict get [stage_resume_live::values] CONFIG.CONST_WIDTH] == 8 && [dict get [stage_resume_live::values] CONFIG.CONST_VAL] == 3} "Early write readback mismatch"
}
proc stage_resume_live::body {} {
    set first [stage_resume_live::run_stage first prepare PARTIAL 0 0 0]
    set second [stage_resume_live::run_stage distinct_stage cached PARTIAL 0 0 0]
    stage_resume_live::assert {$::stage_resume_live::compiles == 1} "Distinct stages did not share one compilation"
    stage_resume_live::assert {![dict get $first compilation_cache_hit] && [dict get $second compilation_cache_hit]} "Cache hit flags incorrect"
    stage_resume_live::assert {[dict get $first compiled_artifact] eq [dict get $second compiled_artifact]} "Stages used different compiled artifacts"
    set channel [open [dict get $second compiled_artifact] a]
    puts $channel {set ::stage_resume_live::tampered_executed 1; error STAGE_RESUME_TAMPERED}
    close $channel
    set repaired [stage_resume_live::run_stage corrupt_artifact cached PARTIAL 0 0 0]
    stage_resume_live::assert {$::stage_resume_live::compiles == 2 && ![dict get $repaired compilation_cache_hit] && ![info exists ::stage_resume_live::tampered_executed]} "Corrupt artifact was executed or not recompiled"
    stage_resume_live::assert {[dict get $repaired compiled_artifact] ne [dict get $second compiled_artifact]} "Corruption evidence overwritten"
    file delete [dict get $repaired compiled_artifact]
    set rebuilt [stage_resume_live::run_stage missing_artifact cached PARTIAL 0 0 0]
    stage_resume_live::assert {$::stage_resume_live::compiles == 3 && ![dict get $rebuilt compilation_cache_hit]} "Missing artifact not recompiled"
    set failed [stage_resume_live::run_stage later_failure configure FAIL 1 1 0]
    stage_resume_live::assert_late_failure $failed
    set retry [stage_resume_live::run_stage unchanged_retry configure FAIL 0 0 1]
    stage_resume_live::assert_late_failure $retry
    set before [stage_resume_live::count]
    set ::stage_resume_live::writes {}
    set receipt [ipcfg::capture [file join $::stage_resume_live::evidence drift.tcldict] \
        {ipcfg::apply_dict /fixture {CONFIG.CONST_VAL 2}} {SUCCESS:}]
    dict set ::stage_resume_live::results drift [dict create capture $receipt \
        attempt_delta [expr {[stage_resume_live::count] - $before}] \
        writes $::stage_resume_live::writes values [stage_resume_live::values]]
    stage_resume_live::assert {[dict get $receipt status] eq "OK" && [stage_resume_live::count] - $before == 1 && [llength $::stage_resume_live::writes] == 1} "Drift injection was not a guarded write"
    stage_resume_live::assert {[dict get [stage_resume_live::values] CONFIG.CONST_VAL] == 2} "Drift injection did not stick"
    set repaired [stage_resume_live::run_stage drift_repair configure FAIL 1 1 0]
    stage_resume_live::assert_late_failure $repaired
    set receipt [ipcfg::capture [file join $::stage_resume_live::evidence create_late.tcldict] \
        {ipcfg::create_cell xilinx.com:ip:xlconstant:1.1 /late_missing} {SUCCESS:}]
    dict set ::stage_resume_live::results create_late $receipt
    stage_resume_live::assert {[dict get $receipt status] eq "OK"} "Could not create late scratch cell"
    stage_resume_live::run_stage recovered configure PARTIAL 0 0 1
    set late [get_bd_cells /late_missing]
    stage_resume_live::assert {[get_property CONFIG.CONST_WIDTH $late] == 8 && [get_property CONFIG.CONST_VAL $late] == 3} "Late cell recovery readback mismatch"
    stage_resume_live::run_stage completed finish PASS 0 0 0
    stage_resume_live::assert {[string match PLAN_EXECUTION:OK* [ipiasm::plan_execution_status $::stage_resume_live::plan]]} "Full-chain provenance missing"
    stage_resume_live::assert {[stage_resume_live::count] == 4 && $::stage_resume_live::compiles == 3} "Unexpected total target attempts or compilations"
    set old_context [ipiasm::context_identity]
    set ledger_before [stage_resume_live::bytes [stage_resume_live::ledger]]
    create_bd_design changed_context
    set refused [stage_resume_live::run_stage implicit_context_change configure FAIL 0 0 0]
    stage_resume_live::assert {[dict get $refused completed_stages] eq {} && [dict get $refused operations] eq {}} "Changed context reused completion or ran operations"
    stage_resume_live::assert {[string match PLAN_EXECUTION_FAIL:* [ipiasm::plan_execution_status $::stage_resume_live::plan]]} "Changed context retained full-plan provenance"
    set begun [ipiasm::begin_design]
    stage_resume_live::assert {[string match SUCCESS:* $begun]} $begun
    set refused [stage_resume_live::run_stage explicit_context_change configure FAIL 0 0 0]
    stage_resume_live::assert {[string first PLAN_PREREQUISITE_FAIL [dict get $refused error_info]] >= 0 && [dict get $refused completed_stages] eq {} && [dict get $refused operations] eq {}} "New context inherited prerequisites"
    stage_resume_live::assert {$ledger_before eq [stage_resume_live::bytes [stage_resume_live::ledger]]} "Context transition altered persistent ledger"
    dict set ::stage_resume_live::results context [dict create before $old_context after [ipiasm::context_identity] begin_design $begun ledger_unchanged 1]
    return TEST:PASS
}
if {[info exists stage_resume_check_only] && $stage_resume_check_only} {
    unset stage_resume_check_only
    return STRUCTURE_ONLY
}
if {![info exists stage_resume_session_id] || $stage_resume_session_id eq "" || $stage_resume_session_id in {
    vivado-20260925-134546-f2f5e4cc vivado-20260925-203730-09fb1ad7
}} {error "Supply stage_resume_session_id from your OWN fresh Tcl MCP session"}
if {![info exists stage_resume_authorized_executable] || $stage_resume_authorized_executable ne "/opt/tools/2026.1/2026.1/Vivado/bin/vivado"} {
    error "Requires MCP startup receipt for /opt/tools/2026.1/2026.1/Vivado/bin/vivado"
}
if {[current_project -quiet] ne "" || [llength [info commands ::ipcfg::require]] || [llength [info commands ::ipiasm::init]]} {
    error "Requires a fresh session with no project or loaded helpers"
}
if {[version -short] ne "2026.1"} {error "Requires native Vivado 2026.1"}
set ::stage_resume_live::evidence [file join $::stage_resume_live::root tests evidence workflow_benchmark stage_resume_native_[pid]_[clock microseconds]]
file mkdir $::stage_resume_live::evidence
set provenance [dict create session_id $stage_resume_session_id authorized_executable $stage_resume_authorized_executable vivado [version -short]]
set run_code [catch {
    set part xc2ve3558-sfva1440-2MP-e-S
    create_project stage_resume [file join $::stage_resume_live::evidence scratch] -part $part
    create_bd_design resume_fixture
    source -notrace [file join $::stage_resume_live::root .claude skills vivado-ip-configurator lib ipcfg ipcfg.tcl]
    source -notrace [file join $::stage_resume_live::root .claude skills vivado-ipi-assembler lib ipiasm.tcl]
    if {[info exists test_native_log]} {set ::ipcfg::native_log_path $test_native_log}
    set python_status [ipcfg::python_status]
    dict set provenance python_status $python_status
    stage_resume_live::assert {[string match {PYTHON:OK *} $python_status]} $python_status
    dict set provenance imports [ipcfg::python_exec -c {import argparse, base64, hashlib, json, pathlib, re, sys, typing; print("PYTHON_IMPORTS:OK")}]
    dict set provenance context [ipiasm::context_identity]
    dict set provenance source_sha256 [ipiasm::_file_sha256 $::stage_resume_live::source_path]
    dict set provenance library_signatures [list $::ipcfg::loaded_signature $::ipiasm::loaded_signature]
    set begun [ipiasm::begin_design]
    stage_resume_live::assert {[string match SUCCESS:* $begun]} $begun
    set policy [ipiasm::policy unattended]
    dict set provenance policy $policy
    set receipt [ipcfg::capture [file join $::stage_resume_live::evidence create_target.tcldict] \
        {ipcfg::create_cell xilinx.com:ip:xlconstant:1.1 /fixture} {SUCCESS:}]
    dict set ::stage_resume_live::results create_target $receipt
    stage_resume_live::assert {[dict get $receipt status] eq "OK" && [stage_resume_live::count] == 1} "Guarded target creation failed"
    set ::stage_resume_live::plan [file join $::stage_resume_live::evidence plan.json]
    set channel [open $::stage_resume_live::plan {WRONLY CREAT EXCL}]
    puts $channel [stage_resume_live::plan_json]
    close $channel
    trace add execution ipcfg::python_script enter stage_resume_live::trace_compile
    trace add execution set_property leave stage_resume_live::trace_write
    stage_resume_live::body
} run_result run_options]
catch {trace remove execution ipcfg::python_script enter stage_resume_live::trace_compile}
catch {trace remove execution set_property leave stage_resume_live::trace_write}
set cleanup_code 0
set cleanup_result already_closed
if {[current_project -quiet] ne ""} {set cleanup_code [catch {close_project} cleanup_result]}
set result_path [file join $::stage_resume_live::evidence results.tcldict]
set channel [open $result_path {WRONLY CREAT EXCL}]
puts $channel [dict create schema_version 1 status [expr {$run_code || $cleanup_code ? "FAIL" : "PASS"}] \
    provenance $provenance code $run_code result $run_result options $run_options \
    assertions $::stage_resume_live::assertions compiles $::stage_resume_live::compiles \
    compiler_events $::stage_resume_live::compiler_events evidence_paths $::stage_resume_live::evidence_paths \
    tests $::stage_resume_live::results cleanup [dict create code $cleanup_code result $cleanup_result] \
    session_stop NOT_PERFORMED_BY_HARNESS]
close $channel
puts "STAGE_RESUME:code=$run_code cleanup_code=$cleanup_code assertions=$::stage_resume_live::assertions compiles=$::stage_resume_live::compiles evidence=$result_path"
if {$run_code} {return -options $run_options $run_result}
if {$cleanup_code} {error "Scratch project cleanup failed: $cleanup_result"}