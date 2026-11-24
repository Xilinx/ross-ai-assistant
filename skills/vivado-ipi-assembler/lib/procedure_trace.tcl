# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

namespace eval procedure_trace {
    variable originals {}
    variable frames {}
    variable label unnamed
    variable output
    if {![info exists output]} {set output stdout}
    # Set before sourcing to override. sink, when non-empty, receives each
    # completed call as {*}$sink name inclusive_us exclusive_us code label
    # command depth instead of the COV_* lines. detail_procs keep their full
    # command line in that call, e.g. to name the Python script that ran.
    variable patterns
    if {![info exists patterns]} {set patterns {::ipcfg::* ::ipiasm::*}}
    variable sink
    if {![info exists sink]} {set sink {}}
    variable detail_procs
    if {![info exists detail_procs]} {set detail_procs {}}
}

proc procedure_trace::event {name args} {
    variable originals
    variable frames
    variable label
    variable output
    variable sink
    variable detail_procs
    set operation [lindex $args end]
    if {$operation eq "enter"} {
        if {[info body $name] ne [dict get $originals $name]} {return}
        if {$sink eq {}} {puts $output "COV_HIT $name"}
        set command [expr {$name in $detail_procs ? [lindex $args 0] : {}}]
        lappend frames [list $name [clock clicks -microseconds] 0 $label $command]
        return
    }
    if {![llength $frames] || [lindex $frames end 0] ne $name} {return}
    lassign [lindex $frames end] ignored started children test command
    set elapsed [expr {max(0, [clock clicks -microseconds] - $started)}]
    set frames [lrange $frames 0 end-1]
    if {[llength $frames]} {
        lset frames end 2 [expr {[lindex $frames end 2] + $elapsed}]
    }
    set exclusive [expr {max(0, $elapsed - $children)}]
    if {$sink ne {}} {
        {*}$sink $name $elapsed $exclusive [lindex $args 1] $test $command [llength $frames]
        return
    }
    puts $output "COV_TIME $name $elapsed $exclusive [lindex $args 1] $test"
}

# Attribute calls that start from now on to name; returns the previous label.
proc procedure_trace::set_label {name} {
    variable label
    set previous $label
    set label $name
    return $previous
}

proc procedure_trace::defined {command code result operation} {
    variable originals
    set name [lindex $command 1]
    if {![string match ::* $name]} {set name ::$name}
    if {$code == 0 && [dict exists $originals $name] &&
        [info body $name] eq [dict get $originals $name]} {
        trace add execution $name {enter leave} [list procedure_trace::event $name]
    }
}

proc procedure_trace::install {} {
    variable originals
    variable output
    variable patterns
    variable sink
    foreach pattern $patterns {
        foreach name [info procs $pattern] {
            dict set originals $name [info body $name]
            if {$sink eq {}} {puts $output "COV_DECL $name"}
            trace add execution $name {enter leave} [list procedure_trace::event $name]
        }
    }
    trace add execution ::proc leave procedure_trace::defined
}

proc procedure_trace::uninstall {} {
    variable originals
    foreach name [dict keys $originals] {
        if {[llength [info procs $name]]} {
            trace remove execution $name {enter leave} [list procedure_trace::event $name]
        }
    }
    trace remove execution ::proc leave procedure_trace::defined
}

procedure_trace::install
