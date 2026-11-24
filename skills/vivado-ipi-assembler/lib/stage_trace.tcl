# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Build timing trace. ipiasm.tcl starts it when it loads inside Vivado (IPIASM_TRACE=off
# disables it, IPIASM_TRACE=<abs-path.jsonl> picks the log), so every build records where
# its time went; summarise with scripts/timing_report.py <log-or-directory>.
#
# One JSON line per event, labelled with the build-flow stage ipiasm::stage last entered:
#   kind tcl     a completed ::ipcfg::* / ::ipiasm::* call (inclusive and exclusive time)
#   kind python  a completed ipcfg::python_exec call, named after its script
#   kind native  a heavy Vivado command called outside the library (validate_bd_design,
#                apply_bd_automation, assign_bd_address, write_hw_platform, source, ...):
#                the agent's own commands and scripts
#   kind start/stage/stop  markers
# Time not covered by any event is time Vivado spent idle between commands (the agent
# reasoning, offline scripts) or in quick untraced commands.
# IPIASM_TRACE_PYTHON=1 also runs each Python script under cProfile (inflates its time).

namespace eval ipiasm::trace {
    variable here [file dirname [file normalize [info script]]]
    variable channel {}
    variable path {}
    variable records 0
    variable native_depth 0
    # Heavy or design-changing Vivado commands worth timing when the agent calls them
    # directly. Frequent cheap commands (get_*/set_property) are left out: tracing them
    # would slow every library call that uses them.
    variable native_commands {
        source validate_bd_design apply_bd_automation assign_bd_address save_bd_design
        write_hw_platform write_bd_tcl generate_target make_wrapper open_project
        create_project close_project open_bd_design create_bd_design create_bd_cell
        launch_runs wait_on_run synth_design
    }
}

proc ipiasm::trace::json {value} {
    return "\"[string map {\\ \\\\ \" \\\" \n \\n \r \\r \t \\t} $value]\""
}

proc ipiasm::trace::write {fields} {
    variable channel
    variable records
    if {$channel eq {}} {return}
    set pairs {}
    foreach {key value} $fields {
        lappend pairs "[json $key]:[expr {[string is entier -strict $value] ? $value : [json $value]}]"
    }
    puts $channel "\{[join $pairs ,]\}"
    incr records
}

# procedure_trace sink: one line per completed call.
proc ipiasm::trace::call {name inclusive exclusive code stage command depth} {
    set fields [list v 1 ts [clock milliseconds] stage $stage kind tcl name $name \
        incl_us $inclusive excl_us $exclusive rc $code depth $depth]
    if {$command ne {}} {
        # ipcfg::python_exec script ?args...? or ipcfg::python_exec -c code
        set first [lindex $command 1]
        set script [expr {$first eq "-c" ? "-c" : [file tail $first]}]
        lappend fields script $script
        lset fields 7 python
        lset fields 9 py:$script
    }
    write $fields
}

# Execution trace on a native command: time only the outermost call made outside the
# library (no traced ipcfg/ipiasm call open, no other traced native command open).
proc ipiasm::trace::native {name args} {
    variable native_depth
    variable starts
    if {[lindex $args end] eq "enter"} {
        if {$native_depth == 0 && ![llength $::procedure_trace::frames]} {
            set starts [list $name [clock clicks -microseconds] [lindex $args 0]]
        }
        incr native_depth
        return
    }
    if {$native_depth > 0} {incr native_depth -1}
    if {$native_depth != 0 || ![llength $starts]} {return}
    lassign $starts started_name started command
    set starts {}
    if {$started_name ne $name} {return}
    set elapsed [expr {max(0, [clock clicks -microseconds] - $started)}]
    set fields [list v 1 ts [clock milliseconds] stage $::procedure_trace::label kind native \
        name $name incl_us $elapsed excl_us $elapsed rc [lindex $args 1] depth 0]
    if {$name eq "source"} {lappend fields script [file tail [lindex $command end]]}
    write $fields
}

proc ipiasm::trace_start {{path ""}} {
    variable ::ipiasm::trace::channel
    variable ::ipiasm::trace::here
    variable ::ipiasm::trace::native_commands
    if {$channel ne {}} {return "TRACE_FAIL:already tracing to $::ipiasm::trace::path"}
    if {$path eq "" && [info exists ::env(IPIASM_TRACE)] && [file pathtype $::env(IPIASM_TRACE)] eq "absolute"} {
        set path $::env(IPIASM_TRACE)
    }
    if {$path eq ""} {
        set path [file join [pwd] .ipiasm trace build.[pid].[clock seconds].jsonl]
    }
    if {[file pathtype $path] ne "absolute"} {return "TRACE_FAIL:absolute path required: $path"}
    set path [file normalize $path]
    # Runs and tests never write into the lab inputs.
    if {"inputs" in [file split $path]} {return "TRACE_FAIL:refusing to write under inputs/: $path"}
    file mkdir [file dirname $path]
    set channel [open $path a]
    fconfigure $channel -buffering line -encoding utf-8 -translation lf
    set ::ipiasm::trace::path $path
    set ::ipiasm::trace::records 0
    set ::ipiasm::trace::native_depth 0
    set ::ipiasm::trace::starts {}
    set stage [expr {[dict exists $::ipiasm::stage_t name] ? [dict get $::ipiasm::stage_t name] : "pre"}]
    namespace eval ::procedure_trace {}
    set ::procedure_trace::patterns {::ipcfg::* ::ipiasm::* ::ipiasm::feasibility::*}
    set ::procedure_trace::sink ::ipiasm::trace::call
    set ::procedure_trace::detail_procs {::ipcfg::python_exec}
    # Global level: its relative namespace names must not land under ::ipiasm.
    uplevel #0 [list source [file join $here procedure_trace.tcl]]
    # The tracer's own procs are not measured.
    foreach name [info procs ::ipiasm::trace*] {
        catch {trace remove execution $name {enter leave} [list procedure_trace::event $name]}
        dict unset ::procedure_trace::originals $name
    }
    procedure_trace::set_label $stage
    set native {}
    foreach command $native_commands {
        if {[llength [info commands ::$command]]} {
            trace add execution ::$command {enter leave} [list ::ipiasm::trace::native $command]
            lappend native $command
        }
    }
    set ::ipiasm::trace::traced_native $native
    if {[info exists ::env(IPIASM_TRACE_PYTHON)] && $::env(IPIASM_TRACE_PYTHON) eq "1"} {
        set ::ipcfg::python_wrapper [list [file join $here trace_python.py] "[file rootname $path].py.jsonl"]
    }
    ipiasm::trace::write [list v 1 ts [clock milliseconds] stage $stage kind start pid [pid]]
    return "TRACE:ON path=$path traced=[dict size $::procedure_trace::originals] native=[llength $native] stage=$stage"
}

proc ipiasm::trace_stop {} {
    variable ::ipiasm::trace::channel
    if {$channel eq {}} {return "TRACE:OFF records=0"}
    procedure_trace::uninstall
    foreach command $::ipiasm::trace::traced_native {
        catch {trace remove execution ::$command {enter leave} [list ::ipiasm::trace::native $command]}
    }
    set ::ipcfg::python_wrapper {}
    ipiasm::trace::write [list v 1 ts [clock milliseconds] stage $::procedure_trace::label kind stop]
    close $channel
    set channel {}
    return "TRACE:OFF records=$::ipiasm::trace::records path=$::ipiasm::trace::path"
}

# Called by ipiasm::stage when this file is loaded; safe to call directly.
proc ipiasm::trace_label {stage} {
    if {$::ipiasm::trace::channel eq {}} {return}
    procedure_trace::set_label $stage
    ipiasm::trace::write [list v 1 ts [clock milliseconds] stage $stage kind stage]
}

# Whether the build timing trace is on, and where it writes.
proc ipiasm::trace_status {} {
    if {$::ipiasm::trace::channel eq {}} {return "TRACE:OFF"}
    return "TRACE:ON path=$::ipiasm::trace::path stage=$::procedure_trace::label records=$::ipiasm::trace::records"
}
