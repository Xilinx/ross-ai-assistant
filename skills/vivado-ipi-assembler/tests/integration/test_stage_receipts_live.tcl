# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Plan stage completion carries across separate Vivado processes through the receipts
# assemble_from_plan writes. Run each phase in its OWN fresh Vivado process, in order,
# against the same evidence directory:
#   set receipts_live_phase one|two|three
#   set receipts_live_evidence <lab>/tests/evidence/workflow_benchmark/<dir>
#   source tests/integration/test_stage_receipts_live.tcl

namespace eval receipts_live {
    variable root [file normalize [file join [file dirname [info script]] ../../../../..]]
    variable assertions 0
    variable target /fixture
}
proc receipts_live::assert {expression message} {
    if {![uplevel 1 [list expr $expression]]} {error "ASSERT:$message"}
    incr ::receipts_live::assertions
}
proc receipts_live::bytes {path} {
    set channel [open $path rb]
    try {return [read $channel]} finally {close $channel}
}
proc receipts_live::plan_json {} {
    return {{
  "schema_version": 1,
  "stages": [
    {"name": "prepare", "operations": [
      {"id": "prepare_check", "op": "call", "proc": "ipiasm::stage", "args": ["prepare"], "expect": ["STAGE:"]}
    ]},
    {"name": "configure", "operations": [
      {"id": "configure_write", "op": "call", "proc": "ipcfg::apply_dict", "args": ["/fixture", {"CONFIG.CONST_WIDTH": 8, "CONFIG.CONST_VAL": 3}], "expect": ["SUCCESS:"]}
    ]},
    {"name": "finish", "operations": [
      {"id": "finish_check", "op": "call", "proc": "ipiasm::stage", "args": ["finish"], "expect": ["STAGE:"]}
    ]}
  ]
}}
}
# Not named "load": Vivado's IPI init scripts run in the caller's namespace and call Tcl's load.
# Sourced at global level so the libraries create ::ipcfg and ::ipiasm, not nested namespaces.
proc receipts_live::load_skills {} {
    foreach library {{vivado-ip-configurator lib ipcfg ipcfg.tcl} {vivado-ipi-assembler lib ipiasm.tcl}} {
        uplevel #0 [list source -notrace [file join $::receipts_live::root .claude skills {*}$library]]
    }
}
proc receipts_live::attempts {} {
    set path [file join [get_property DIRECTORY [current_project]] .ipcfg attempts.tcldict]
    if {![file exists $path]} {return 0}
    set state [receipts_live::bytes $path]
    set key [list [current_bd_design] $::receipts_live::target]
    if {![dict exists $state $key]} {return 0}
    return [dict get $state $key count]
}
proc receipts_live::values {} {
    set cell [get_bd_cells $::receipts_live::target]
    return [list [get_property CONFIG.CONST_WIDTH $cell] [get_property CONFIG.CONST_VAL $cell]]
}
proc receipts_live::reopen {} {
    open_project [file join $::receipts_live::evidence scratch receipts.xpr]
    open_bd_design [get_files resume_fixture.bd]
    receipts_live::load_skills
    set init [ipiasm::init]
    receipts_live::assert {[string match SUCCESS:* $init]} "init: $init"
    ipiasm::policy unattended
}
proc receipts_live::invalidations {} {
    return [glob -nocomplain -directory $::receipts_live::evidence .plan.*.invalidated.result.tcldict]
}

proc receipts_live::one {} {
    create_project receipts [file join $::receipts_live::evidence scratch] -part xc2ve3558-sfva1440-2MP-e-S
    create_bd_design resume_fixture
    receipts_live::load_skills
    receipts_live::assert {[string match SUCCESS:* [ipiasm::begin_design]]} "begin_design"
    ipiasm::policy unattended
    set created [ipcfg::capture [file join $::receipts_live::evidence create_target.tcldict] \
        {ipcfg::create_cell xilinx.com:ip:xlconstant:1.1 /fixture} {SUCCESS:}]
    receipts_live::assert {[dict get $created status] eq "OK"} "create /fixture: $created"
    set channel [open $::receipts_live::plan {WRONLY CREAT EXCL}]
    puts $channel [receipts_live::plan_json]
    close $channel
    set first [ipiasm::assemble_from_plan $::receipts_live::plan prepare]
    receipts_live::assert {[string match ASSEMBLY_PLAN:PARTIAL* $first]} "prepare: $first"
    set second [ipiasm::assemble_from_plan $::receipts_live::plan configure]
    receipts_live::assert {[string match ASSEMBLY_PLAN:PARTIAL* $second]} "configure: $second"
    receipts_live::assert {[receipts_live::values] eq {8 3}} "configure readback [receipts_live::values]"
    set status [ipiasm::stage_execution_status]
    receipts_live::assert {[dict get $status completed_stages] eq {prepare configure}} "completed [dict get $status completed_stages]"
    set provenance [ipiasm::plan_execution_status $::receipts_live::plan]
    receipts_live::assert {[string match "PLAN_EXECUTION_FAIL:stage receipts show {prepare configure} of {prepare configure finish}*run {finish}*" $provenance]} "incomplete provenance: $provenance"
    save_bd_design
    set result [dict create attempts [receipts_live::attempts] values [receipts_live::values] \
        prepare $first configure $second provenance $provenance]
    close_project
    return $result
}

proc receipts_live::two {} {
    receipts_live::reopen
    set attempts_before [receipts_live::attempts]
    set values_before [receipts_live::values]
    set carried [ipiasm::_staged_completion $::receipts_live::plan {}]
    receipts_live::assert {$carried eq {prepare configure}} "carried completion: $carried"
    set result [ipiasm::assemble_from_plan $::receipts_live::plan finish]
    set status [ipiasm::stage_execution_status]
    receipts_live::assert {[string match "ASSEMBLY_PLAN:PASS stages=3 *" $result]} "finish: $result"
    receipts_live::assert {[dict keys [dict get $status operations]] eq {finish_check}} "replayed: [dict keys [dict get $status operations]]"
    receipts_live::assert {[receipts_live::attempts] == $attempts_before} "guarded write replayed"
    receipts_live::assert {[receipts_live::values] eq $values_before} "values changed"
    set provenance [ipiasm::plan_execution_status $::receipts_live::plan]
    receipts_live::assert {[string match "PLAN_EXECUTION:OK *" $provenance] && ![string match "*staged=1*" $provenance]} "in-process provenance: $provenance"
    save_bd_design
    close_project
    return [dict create attempts_before $attempts_before attempts_after $attempts_before \
        carried $carried finish $result operations [dict keys [dict get $status operations]] provenance $provenance]
}

proc receipts_live::three {} {
    receipts_live::reopen
    set staged [ipiasm::plan_execution_status $::receipts_live::plan]
    receipts_live::assert {[string match "PLAN_EXECUTION:OK *staged=1 receipt=.plan.*" $staged]} "staged provenance: $staged"
    receipts_live::assert {![llength [receipts_live::invalidations]]} "invalidated too early"
    create_bd_design other_bd
    set drift [ipiasm::plan_execution_status $::receipts_live::plan]
    receipts_live::assert {[string match "PLAN_EXECUTION_FAIL:project/BD/environment changed*" $drift]} "drift: $drift"
    receipts_live::assert {[llength [receipts_live::invalidations]] == 1} "no invalidation receipt"
    current_bd_design resume_fixture
    set after [ipiasm::plan_execution_status $::receipts_live::plan]
    receipts_live::assert {[string match "PLAN_EXECUTION_FAIL:the newest receipt *carries no stage completion*" $after]} "after drift: $after"
    set resume [ipiasm::assemble_from_plan $::receipts_live::plan finish]
    receipts_live::assert {[string match ASSEMBLY_PLAN_FAIL:* $resume] &&
        [string first PLAN_PREREQUISITE_FAIL [dict get [ipiasm::stage_execution_status] error_info]] >= 0} "invalidated completion resumed: $resume"
    close_project
    return [dict create staged $staged drift $drift after $after resume $resume \
        invalidation [file tail [lindex [receipts_live::invalidations] 0]]]
}

if {![info exists receipts_live_phase] || $receipts_live_phase ni {one two three}} {
    error "set receipts_live_phase to one, two or three"
}
if {![info exists receipts_live_evidence]} {error "set receipts_live_evidence"}
if {[version -short] ne "2026.1"} {error "Requires native Vivado 2026.1"}
if {[current_project -quiet] ne "" || [llength [info commands ::ipiasm::init]]} {
    error "Each phase needs a fresh Vivado process with no project or loaded helpers"
}
set ::receipts_live::evidence [file normalize $receipts_live_evidence]
set ::receipts_live::plan [file join $::receipts_live::evidence plan.json]
file mkdir $::receipts_live::evidence
set phase_code [catch {receipts_live::$receipts_live_phase} phase_result phase_options]
if {[current_project -quiet] ne ""} {catch {close_project}}
set channel [open [file join $::receipts_live::evidence phase_$receipts_live_phase.tcldict] {WRONLY CREAT EXCL}]
puts $channel [dict create schema_version 1 phase $receipts_live_phase pid [pid] code $phase_code \
    result $phase_result assertions $::receipts_live::assertions]
close $channel
puts "RECEIPTS_LIVE:phase=$receipts_live_phase pid=[pid] code=$phase_code assertions=$::receipts_live::assertions"
if {$phase_code} {return -options $phase_options $phase_result}
