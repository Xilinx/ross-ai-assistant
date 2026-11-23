# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# The IP-configuration workflow through the stock Vivado MCP. Source this file once,
# then send every workflow op with its JSON written once:
#     ipcfg_op {"seq":1,"op":"begin", ...}
# through vivado_execute. The workflow itself is scripts/ipcfg_bridge.py, run as a
# helper process of this Vivado with Vivado's own bundled Python; every Tcl command
# it needs comes back over the pipe and is evaluated here. The model never writes
# workflow Tcl, and nothing besides the stock MCP has to be installed.
#
# Vivado under the MCP does not service the pipe between commands, so each ipcfg_op
# serves the helper while it is called. A client abandons an MCP call after about a
# minute (Claude Code: 60 s) while Vivado keeps working, so a step that runs longer
# would finish with nobody to read its reply. ipcfg_op therefore serves its step for at
# most `budget` ms, then returns stage "running" with the step still in flight; the
# next `ipcfg_op {"seq":N,"op":"status"}` resumes serving it and, when it finishes,
# returns that step's own reply. The helper lives as long as this Vivado, so one
# design is built over many cells and prompts.

namespace eval ipcfg_op {
    variable skill [file dirname [file dirname [file normalize [info script]]]]
    # Sourcing again (a later prompt, the same Vivado) keeps the running helper.
    variable pipe
    if {![info exists pipe]} {set pipe ""}
    # The op still in flight after its budget ran out, or "".
    variable busy
    if {![info exists busy]} {set busy ""}
    variable budget 40000
}

# Vivado's working directory holds the MCP's .vivado-ai session log; the run root
# holding evidence/ is derived from it exactly as lite::run_root does.
#
# The working directory is meant to be the project directory <root>/proj, so the
# root is its parent -- but only when the working directory is that `proj` folder.
# Started anywhere else, its parent is not ours to write in: on a test host it is
# the directory holding every case's workspace.
proc ipcfg_op::root {} {
    set workdir [file normalize [pwd]]
    if {[file tail $workdir] eq "proj"} {set workdir [file dirname $workdir]}
    return $workdir
}

proc ipcfg_op::log_file {} {
    set logs [glob -nocomplain -types f [file join [pwd] .vivado-ai *.log]]
    if {![llength $logs]} {
        error "IPCFG_OP_FAIL: no session log under [pwd]/.vivado-ai; start Vivado with vivado_start and its working_dir"
    }
    return [lindex [lsort -command {apply {{a b} {expr {[file mtime $a] - [file mtime $b]}}}} $logs] end]
}

# Vivado's own Python, never a host one: IPCFG_REQUIRE_BUNDLED makes the locator
# fail rather than fall back to python3 on PATH.
proc ipcfg_op::runtime {} {
    variable skill
    if {![llength [info commands ::ipcfg::python_runtime]]} {
        uplevel #0 [list source -notrace [file join $skill lib ipcfg ipcfg.tcl]]
    }
    set had [info exists ::env(IPCFG_REQUIRE_BUNDLED)]
    if {$had} {set old $::env(IPCFG_REQUIRE_BUNDLED)}
    set ::env(IPCFG_REQUIRE_BUNDLED) 1
    try {
        set runtime [::ipcfg::python_runtime]
    } finally {
        if {$had} {set ::env(IPCFG_REQUIRE_BUNDLED) $old} else {unset ::env(IPCFG_REQUIRE_BUNDLED)}
    }
    if {[dict get $runtime source] ne "vivado"} {
        error "IPCFG_OP_FAIL: expected Vivado's bundled Python, found [dict get $runtime executable]"
    }
    return $runtime
}

proc ipcfg_op::start {} {
    variable skill
    variable pipe
    set runtime [ipcfg_op::runtime]
    set root [ipcfg_op::root]
    file mkdir [file join $root evidence]
    set command [list [dict get $runtime executable] -I -u [file join $skill scripts ipcfg_bridge.py] \
        --log-file [ipcfg_op::log_file]]
    # The same environment the library gives its own Python calls (ipcfg::_python_invoke).
    set saved [array get ::env]
    try {
        foreach name {PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT} {unset -nocomplain ::env($name)}
        set lib [file join [dict get $runtime root] lib]
        set ::env(LD_LIBRARY_PATH) [expr {[info exists ::env(LD_LIBRARY_PATH)] && $::env(LD_LIBRARY_PATH) ne ""
                                          ? "$lib:$::env(LD_LIBRARY_PATH)" : $lib}]
        set pipe [open |[concat $command [list 2>> [file join $root evidence ipcfg_bridge.stderr.log]]] r+]
    } finally {
        foreach name [array names ::env] {if {![dict exists $saved $name]} {unset ::env($name)}}
        foreach {name value} $saved {set ::env($name) $value}
    }
    fconfigure $pipe -buffering line -translation lf -encoding ascii -blocking 1
}

proc ipcfg_op::encode {text} {
    return [binary encode base64 [encoding convertto utf-8 $text]]
}

proc ipcfg_op::decode {text} {
    return [encoding convertfrom utf-8 [binary decode base64 $text]]
}

proc ipcfg_op::running {} {
    variable busy
    return [format {{"stage":"running","running_step":"%s","next_op":"status","note":"Vivado is still working on this step. Send {\"seq\":<next>,\"op\":\"status\"} until it returns this step's reply; never send the step again."}} $busy]
}

proc ipcfg_op {json} {
    variable ::ipcfg_op::pipe
    variable ::ipcfg_op::busy
    variable ::ipcfg_op::budget
    if {$pipe eq ""} {ipcfg_op::start}
    # `ipcfg_op {<json>}` with the step's JSON written once: Tcl strips the braces around the
    # word, so they are put back here. The older `ipcfg_op {{<json>}}` form arrives whole.
    # Counting the extra pair is what models get wrong: twelve selects ending in a closing bracket lost
    # their last brace, and Vivado waited for the rest of an incomplete command until the
    # client gave up (ip-configurator-test-kit_02, _20, _21, _24, _26, _29, issue-674_02).
    if {[string index [string trimleft $json] 0] ne "\{"} {set json "\{$json\}"}
    if {$busy ne ""} {
        # Only status reaches a step in flight; anything else would start a second op the
        # helper is not reading yet.
        if {![regexp {"op"\s*:\s*"status"} $json]} {
            error "STRUCTURED_ERROR: Vivado is still working on the $busy step. Send {\"seq\":<next>,\"op\":\"status\"} until it returns that step's reply; never send the step again."
        }
    } else {
        set busy request
        regexp {"op"\s*:\s*"([^"]+)"} $json -> busy
        puts $pipe "OP [ipcfg_op::encode $json]"
    }
    set started [clock milliseconds]
    while {[gets $pipe line] >= 0} {
        set kind [lindex [split $line] 0]
        set body [ipcfg_op::decode [lindex [split $line] 1]]
        switch -- $kind {
            DONE {set busy ""; return $body}
            FAIL {set busy ""; error $body}
            TCL {
                set rc [catch {uplevel #0 $body} result]
                puts $pipe "[expr {$rc == 1 ? {ERR} : {OK}}] [ipcfg_op::encode $result]"
                if {[clock milliseconds] - $started >= $budget} {return [ipcfg_op::running]}
            }
            default {set busy ""; error "IPCFG_OP_FAIL: unexpected helper line $kind"}
        }
    }
    catch {close $pipe}
    set pipe ""
    set busy ""
    error "IPCFG_OP_FAIL: the workflow helper exited; see [file join [ipcfg_op::root] evidence ipcfg_bridge.stderr.log]"
}
