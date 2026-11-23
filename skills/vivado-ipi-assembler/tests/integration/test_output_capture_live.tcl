# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

set test_root [file normalize [file join [file dirname [info script]] ../../../../..]]
if {[current_project -quiet] ne ""} {error "Refusing to change an open project"}
set evidence_dir [file join $test_root tests evidence workflow_benchmark native_output_[clock microseconds]]
file mkdir $evidence_dir
source -notrace [file join $test_root .claude skills vivado-ip-configurator lib ipcfg ipcfg.tcl]
source -notrace [file join $test_root .claude skills vivado-ipi-assembler lib ipiasm.tcl]
create_project output_fixture [file join $evidence_dir project] -part xc2ve3558-sfva1440-2MP-e-S
create_bd_design output_fixture
set mode clean
ipiasm::begin_design
set calls {}
set payload "SUCCESS:[string repeat {CONFIG.FIXTURE {value with spaces} } 1000]"
proc ipiasm::stage {name} {
    lappend ::calls $name
    switch -- $::mode {
        warning {send_msg_id {IPCFG-100} WARNING {ordinary fixture warning}}
        critical {send_msg_id {IPCFG-101} {CRITICAL WARNING} {intentional native fixture}}
        ignored {send_msg_id {IPCFG-102} WARNING {disabled parameter ignored}}
        exception {error "fixture full exception" "original fixture stack" {FIXTURE ORIGINAL}}
    }
    return $::payload
}
proc output_assert {expression message} {
    if {![uplevel 1 [list expr $expression]]} {error $message}
}
set plan [file join $evidence_dir plan.json]
set channel [open $plan {WRONLY CREAT EXCL}]
puts $channel {{"schema_version":1,"stages":[{"name":"probe","operations":[{"id":"first","op":"call","proc":"ipiasm::stage","args":["first"],"expect":["SUCCESS:"]},{"id":"second","op":"call","proc":"ipiasm::stage","args":["second"],"expect":["SUCCESS:"]},{"id":"third","op":"call","proc":"ipiasm::stage","args":["third"],"expect":["SUCCESS:"]}]}]}}
close $channel
set samples {}
for {set trial 1} {$trial <= 3} {incr trial} {
    foreach arm {verbose compact} {
        set calls {}
        set before [ipcfg::_message_snapshot [file join $evidence_dir $arm.$trial.before.log]]
        set started [clock milliseconds]
        if {$arm eq "verbose"} {
            foreach name {first second third} {puts [ipiasm::stage $name]}
        } else {
            set result [ipiasm::assemble_from_plan $plan probe]
            output_assert {[string match ASSEMBLY_PLAN:PASS* $result]} $result
        }
        set elapsed [expr {[clock milliseconds] - $started}]
        set after [ipcfg::_message_snapshot [file join $evidence_dir $arm.$trial.after.log]]
        output_assert {[string first $before $after] == 0} "Changed native history"
        set delta [string range $after [string length $before] end]
        set bytes [string length [encoding convertto utf-8 $delta]]
        output_assert {$calls eq {first second third}} "Different stage workload"
        dict lappend samples $arm [dict create trial $trial bytes $bytes elapsed_ms $elapsed]
        if {$arm eq "verbose"} {set baseline_bytes $bytes} else {
            output_assert {$bytes < $baseline_bytes * 0.2} "Console reduction below 80 percent"
        }
    }
}
set payload SUCCESS:verified
set verdicts {}
foreach mode {warning critical ignored exception clean} {
    set calls {}
    set result [ipiasm::assemble_from_plan $plan probe]
    set state [ipiasm::stage_execution_status]
    set expected [expr {$mode in {warning clean} ? "PASS" : "FAIL"}]
    output_assert {[dict get $state status] eq $expected} "Incorrect $mode verdict: $result"
    if {$expected eq "FAIL"} {output_assert {$calls eq {first}} "Continued after failure"}
    set channel [open [dict get $state evidence_path] r]
    set saved [dict get [read $channel] data]
    close $channel
    output_assert {$saved eq $state} "Incomplete durable stage record"
    if {$mode eq "exception"} {
        output_assert {[dict get $state operations first options -errorcode] eq {FIXTURE ORIGINAL}} "Lost error code"
    }
    dict set verdicts $mode [dict create status $expected evidence [dict get $state evidence_path]]
}
set channel [open $plan a]
puts $channel invalid_json
close $channel
set result [ipiasm::assemble_from_plan $plan probe]
set state [ipiasm::stage_execution_status]
output_assert {[dict get $state status] eq "FAIL" && [file isfile [dict get $state evidence_path]]} "Compile failure not archived"
puts [ipcfg::write_evidence [file join $evidence_dir results.tcldict] [dict create \
    scope {native Vivado stage-control fixture; synthetic return payload, not a RAVE2 hardware acceptance run} \
    vivado [version -short] samples $samples verdicts $verdicts \
    successful_stage_calls 3 followup_diagnostic_tool_calls 0 status PASS]]
puts "OUTPUT_CAPTURE_LIVE:PASS evidence=$evidence_dir"