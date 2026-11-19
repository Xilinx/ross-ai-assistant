# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# The CED workflow through the stock Vivado MCP. Source this file once, then send
# every workflow op as
#     ced_op {{"op":"begin", ...}}
# through vivado_execute. The workflow itself is scripts/ced_bridge.py, run as a
# helper process of this Vivado with Vivado's own bundled Python; every Tcl command
# it needs comes back over the pipe and is evaluated here. The model never writes
# workflow Tcl, and nothing besides the stock MCP has to be installed.
#
# The call is synchronous: Vivado under the MCP does not service the pipe between
# commands, so each ced_op serves the helper until its op is done.

namespace eval ced_op {
    variable skill [file dirname [file dirname [file normalize [info script]]]]
    variable pipe ""
}

# The run root holding evidence/, snapshots/ and proj/, exactly as
# cedcfg::run_root derives it. Vivado's working directory is meant to be
# <run>/work, so the root is its parent -- but only when the working directory
# is that `work` folder. Started anywhere else, its parent is not ours to write
# in: on a test host it is the directory holding every case's workspace.
proc ced_op::root {} {
    set workdir [file normalize [pwd]]
    if {[file tail $workdir] eq "work"} {set workdir [file dirname $workdir]}
    return $workdir
}

# The MCP gives each session one log under <working_dir>/.vivado-ai.
proc ced_op::log_file {} {
    set logs [glob -nocomplain -types f [file join [pwd] .vivado-ai *.log]]
    if {![llength $logs]} {
        error "CED_OP_FAIL: no session log under [pwd]/.vivado-ai; start Vivado with vivado_start and its working_dir"
    }
    return [lindex [lsort -command {apply {{a b} {expr {[file mtime $a] - [file mtime $b]}}}} $logs] end]
}

# Vivado's own Python, never a host one: IPCFG_REQUIRE_BUNDLED makes the engine's
# locator fail rather than fall back to python3 on PATH.
proc ced_op::runtime {} {
    variable skill
    if {![llength [info commands ::ipcfg_ced::python_runtime]]} {
        uplevel #0 [list source -notrace [file join $skill engine lib ipcfg ipcfg.tcl]]
    }
    set had [info exists ::env(IPCFG_REQUIRE_BUNDLED)]
    if {$had} {set old $::env(IPCFG_REQUIRE_BUNDLED)}
    set ::env(IPCFG_REQUIRE_BUNDLED) 1
    try {
        set runtime [::ipcfg_ced::python_runtime]
    } finally {
        if {$had} {set ::env(IPCFG_REQUIRE_BUNDLED) $old} else {unset ::env(IPCFG_REQUIRE_BUNDLED)}
    }
    if {[dict get $runtime source] ne "vivado"} {
        error "CED_OP_FAIL: expected Vivado's bundled Python, found [dict get $runtime executable]"
    }
    return $runtime
}

proc ced_op::start {} {
    variable skill
    variable pipe
    set runtime [ced_op::runtime]
    set root [ced_op::root]
    file mkdir [file join $root evidence]
    set cache [expr {[info exists ::env(CED_MIRROR)] && $::env(CED_MIRROR) ne ""
                     ? $::env(CED_MIRROR) : [file join $root ced-mirror]}]
    set command [list [dict get $runtime executable] -I -u [file join $skill scripts ced_bridge.py] \
        --log-file [ced_op::log_file] --ced-cache $cache]
    # The same environment the engine gives its own Python calls (ipcfg_ced::_python_invoke).
    set saved [array get ::env]
    try {
        foreach name {PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT} {unset -nocomplain ::env($name)}
        set lib [file join [dict get $runtime root] lib]
        set ::env(LD_LIBRARY_PATH) [expr {[info exists ::env(LD_LIBRARY_PATH)] && $::env(LD_LIBRARY_PATH) ne ""
                                          ? "$lib:$::env(LD_LIBRARY_PATH)" : $lib}]
        set pipe [open |[concat $command [list 2>> [file join $root evidence ced_bridge.stderr.log]]] r+]
    } finally {
        foreach name [array names ::env] {if {![dict exists $saved $name]} {unset ::env($name)}}
        foreach {name value} $saved {set ::env($name) $value}
    }
    fconfigure $pipe -buffering line -translation lf -encoding ascii -blocking 1
}

proc ced_op::encode {text} {
    return [binary encode base64 [encoding convertto utf-8 $text]]
}

proc ced_op::decode {text} {
    return [encoding convertfrom utf-8 [binary decode base64 $text]]
}

proc ced_op {json} {
    variable ::ced_op::pipe
    if {$pipe eq ""} {ced_op::start}
    puts $pipe "OP [ced_op::encode $json]"
    while {[gets $pipe line] >= 0} {
        set kind [lindex [split $line] 0]
        set body [ced_op::decode [lindex [split $line] 1]]
        switch -- $kind {
            DONE {return $body}
            FAIL {error $body}
            TCL {
                set rc [catch {uplevel #0 $body} result]
                puts $pipe "[expr {$rc == 1 ? {ERR} : {OK}}] [ced_op::encode $result]"
            }
            default {error "CED_OP_FAIL: unexpected helper line $kind"}
        }
    }
    catch {close $pipe}
    set pipe ""
    error "CED_OP_FAIL: the workflow helper exited; see [file join [ced_op::root] evidence ced_bridge.stderr.log]"
}
