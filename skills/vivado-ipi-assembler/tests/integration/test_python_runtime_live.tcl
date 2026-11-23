# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

set test_root [file normalize [file join [file dirname [info script]] ../../../../..]]
if {[current_project -quiet] ne ""} {error "Refusing to use a session with an open project"}
if {[llength [info commands ipcfg::python_runtime]]} {error "Use a fresh disposable session"}
set evidence_dir [file join $test_root tests evidence workflow_benchmark native_python_[clock microseconds]]
file mkdir $evidence_dir
set current_library [file join $test_root .claude skills vivado-ip-configurator lib ipcfg ipcfg.tcl]
set baseline_library [file join $test_root tests evidence workflow_benchmark 20260925-baseline sources .claude skills vivado-ip-configurator lib ipcfg ipcfg.tcl]
set libraries [dict create baseline $baseline_library current $current_library]
if {![file isfile $baseline_library]} {dict unset libraries baseline}
source -notrace $current_library
set python_script [file join $evidence_dir {argument check.py}]
set channel [open $python_script {WRONLY CREAT EXCL}]
puts $channel {import sys
print(sys.argv[1])}
close $channel
set original_environment [array get ::env]
set workload {
    set runtime_probes 0
    trace add execution ipcfg::_python_probe enter {apply {{args} {incr ::runtime_probes}}}
    unset -nocomplain ::env(IPCFG_PYTHON) ::env(IPCFG_REQUIRE_BUNDLED)
    foreach name {PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT LD_LIBRARY_PATH} {
        set ::env($name) /nonexistent/inherited
    }
    set inherited [lsort -stride 2 -index 0 [array get ::env]]
    set started [clock microseconds]
    set status [ipcfg::python_status]
    if {![string match {PYTHON:OK *} $status]} {error $status}
    set runtime [ipcfg::python_runtime]
    if {[dict get $runtime source] ne "vivado"} {error "Did not automatically select Vivado Python"}
    set imports [ipcfg::python_exec -c {import argparse, base64, hashlib, json, pathlib, re, sys, typing; print("PYTHON_IMPORTS:OK")}]
    if {$imports ne "PYTHON_IMPORTS:OK"} {error "Import preflight failed"}
    set cold_us [expr {[clock microseconds] - $started}]
    set cold_probes $runtime_probes
    set warm_us {}
    for {set iteration 0} {$iteration < 3} {incr iteration} {
        set started [clock microseconds]
        set result [ipcfg::python_exec $python_script {spaces ; $literal [brackets]}]
        lappend warm_us [expr {[clock microseconds] - $started}]
        if {$result ne {spaces ; $literal [brackets]}} {error "Argument boundary lost"}
    }
    set warm_probes [expr {$runtime_probes - $cold_probes}]
    if {$warm_probes != 0} {error "Warm calls repeated probes"}
    set failures {}
    foreach {name program expected} {
        dependency {import ipcfg_intentionally_missing_dependency} 1
        nonzero {raise SystemExit(7)} 7
    } {
        set code [catch {ipcfg::python_exec -c $program} message options]
        if {!$code || [lindex [dict get $options -errorcode] 0] ne "CHILDSTATUS" ||
            [lindex [dict get $options -errorcode] 2] != $expected} {error "Child failure hidden: $name"}
        if {$name eq "dependency" && ![string match *ModuleNotFoundError* $message]} {error "Lost import diagnostic"}
        dict set failures $name [dict create code $code message $message options $options]
    }
    if {[lsort -stride 2 -index 0 [array get ::env]] ne $inherited} {error "Inherited environment changed"}
    foreach name {PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT LD_LIBRARY_PATH} {unset ::env($name)}
    ipcfg::python_exec -c {print("clean")}
    catch {ipcfg::python_exec -c {raise SystemExit(7)}}
    foreach name {PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT LD_LIBRARY_PATH} {
        if {[info exists ::env($name)]} {error "Absent environment variable leaked: $name"}
    }
    if {$runtime_probes != $cold_probes} {error "Child failures invalidated runtime cache"}
    set ::env(IPCFG_PYTHON) [dict get $runtime executable]
    set override [ipcfg::python_runtime]
    if {[dict get $override source] ne "override" || $runtime_probes != $cold_probes + 1} {error "Override/key invalidation failed"}
    set ::env(IPCFG_PYTHON) /nonexistent/python
    set invalid [ipcfg::python_status]
    if {![string match PYTHON_RUNTIME_FAIL:* $invalid]} {error "Invalid override silently fell back"}
    unset ::env(IPCFG_PYTHON)
    foreach name {IPCFG_VIVADO_ROOT XILINX_VIVADO VIVADO_PATH} {set ::env($name) /nonexistent/vivado}
    set ::env(PATH) ""
    set unavailable [ipcfg::python_status]
    if {![string match PYTHON_RUNTIME_FAIL:* $unavailable]} {error "Unavailable runtime accepted"}
    foreach name {IPCFG_VIVADO_ROOT XILINX_VIVADO VIVADO_PATH} {unset ::env($name)}
    set vivado_root [file dirname [file dirname [file dirname [dict get $runtime root]]]]
    set ::env(PATH) [file join $vivado_root bin]
    set path_only [ipcfg::python_status]
    dict create status PASS runtime $runtime imports $imports cold_us $cold_us warm_us $warm_us \
        cold_probes $cold_probes warm_probes $warm_probes failures $failures \
        invalid_override $invalid unavailable $unavailable path_only $path_only environment_restored 1
}
set samples {}
set shell_samples {}
try {
    for {set trial 1} {$trial <= 3} {incr trial} {
        set arms [dict keys $libraries]
        if {$trial % 2 == 0} {set arms [lreverse $arms]}
        foreach arm $arms {
            array unset ::env *
            array set ::env $original_environment
            set child [interp create]
            try {
                set library [dict get $libraries $arm]
                interp eval $child [list source $library]
                interp eval $child [list set python_script $python_script]
                set result [interp eval $child $workload]
                set expected [expr {$arm eq "current" ? {PYTHON:OK *} : {PYTHON_RUNTIME_FAIL:*}}]
                if {![string match $expected [dict get $result path_only]]} {error "Unexpected PATH-only result for $arm"}
                dict set result trial $trial
                dict lappend samples $arm $result
            } finally {interp delete $child}
        }
    }
    array unset ::env *
    array set ::env $original_environment
    set runtime [dict get [lindex [dict get $samples current] 0] runtime]
    set vivado_root [file dirname [file dirname [file dirname [dict get $runtime root]]]]
    foreach name {IPCFG_PYTHON IPCFG_REQUIRE_BUNDLED IPCFG_VIVADO_ROOT XILINX_VIVADO VIVADO_PATH} {
        unset -nocomplain ::env($name)
    }
    set ::env(PATH) [file join $vivado_root bin]
    foreach name {PYTHONHOME PYTHONPATH PYTHONSTARTUP LD_LIBRARY_PATH} {set ::env($name) /nonexistent/inherited}
    set ::env(PYTHONINSPECT) 1
    for {set trial 1} {$trial <= 3} {incr trial} {
        set started [clock microseconds]
        set result [exec /bin/sh [file join [file dirname $current_library] vivado_python.sh] \
            $python_script {spaces ; $literal [brackets]} < /dev/null]
        if {$result ne {spaces ; $literal [brackets]}} {error "Offline PATH-only invocation failed"}
        lappend shell_samples [dict create trial $trial status PASS elapsed_us [expr {[clock microseconds] - $started}]]
    }
} finally {
    array unset ::env *
    array set ::env $original_environment
}
set source_paths [concat [dict values $libraries] [list [info script] [file join [file dirname $current_library] vivado_python.sh]]]
set hashes [ipcfg::python_exec -c {import hashlib, pathlib, sys; print(" ".join(hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() for path in sys.argv[1:]))} {*}$source_paths]
set receipt [ipcfg::write_evidence [file join $evidence_dir results.tcldict] \
    [dict create status PASS vivado [version -short] samples $samples shell_path_only $shell_samples \
        source_paths $source_paths sha256 $hashes]]
puts "PYTHON_RUNTIME_LIVE:PASS evidence=[dict get $receipt evidence_path]"