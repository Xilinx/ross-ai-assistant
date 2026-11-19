# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# ila_probes.tcl: ila::write_probes, the one place the skill writes a probes file. Sourced by ila_insert.tcl.
# It runs only when the user asks for the file, to the path the user names, and never over an existing file unless the user says so (-force 1).

namespace eval ila {
    # 1 when every net of the design is routed (report_route_status counts, read the way ila_eco.tcl reads them). Any failure, or a design that is
    # not placed, answers 0: the caller then says the hub id may still change, which is the safe side.
    proc probes_design_routed {} {
        if {[catch {report_route_status -return_string} rep]} { return 0 }
        if {![regexp {# of routable nets[ .]*:\s*([0-9]+)} $rep -> routable] || ![regexp {# of fully routed nets[ .]*:\s*([0-9]+)} $rep -> full]} { return 0 }
        set err 0
        regexp {# of nets with routing errors[ .]*:\s*([0-9]+)} $rep -> err
        return [expr {$routable > 0 && $full == $routable && $err == 0}]
    }

    # The debug cores whose cell is still a black box: Vivado has not built them (opt_design builds them), so write_debug_probes
    # cannot write the file. A core whose cell cannot be found says nothing and is left out.
    proc probes_unbuilt_cores {} {
        set out {}
        # the cores of this session's apply, or, in a session that reopened a saved checkpoint, every debug core of the open design
        set cores [created_list]
        if {[llength $cores] == 0} { set cores [get_debug_cores -quiet] }
        foreach c $cores {
            set cell [get_cells -quiet $c]
            if {[llength $cell] == 0} continue
            if {[get_property IS_BLACKBOX $cell]} { lappend out $c }
        }
        return $out
    }

    # ila::write_probes <file> ?-force 1?: write the probes file of the open design with write_debug_probes. Returns the path of the file.
    # Vivado puts two of its messages only in the log (12-5829 before opt_design, 16-19 for a missing folder) and the Tcl result is only
    # Common 17-39, so the state is read from the cores (a black box means opt_design has not run) and the folder from file isdirectory.
    proc write_probes {args} {
        if {[llength $args] == 0 || [string match -* [lindex $args 0]]} {
            return -code error "ILA_BAD_OPTION: the probes file name must come first (AI only: ila::write_probes <file> ?-force 1?). Nothing was written."
        }
        set path [lindex $args 0]
        check_options ila::write_probes ILA_BAD_OPTION {-force} [lrange $args 1 end]
        array set o {-force 0}
        array set o [lrange $args 1 end]
        if {![string is boolean -strict $o(-force)]} { return -code error "ILA_BAD_OPTION: the overwrite setting must be 0 or 1 (AI only: ila::write_probes -force got $o(-force)). Nothing was written." }
        set unbuilt [probes_unbuilt_cores]
        if {[llength $unbuilt] > 0} {
            return -code error "ILA_PROBES_NOT_READY: Vivado writes the probes file only after opt_design (Vivado 12-5829: the debug core(s) [join $unbuilt {, }] are not built yet, so they have no ID). Nothing was written (AI only: ila::write_probes <file> works once opt_design has run)."
        }
        # Vivado adds .ltx to a name with no extension, so that is the file that would be made or replaced
        set tail [file tail $path]
        set target [expr {[regexp {\.[^./\\]*$} $tail] ? $path : "$path.ltx"}]
        set dir [string trimright [string range $target 0 [expr {[string length $target] - [string length [file tail $target]] - 1}]] /\\]
        if {$dir eq "" && [string length $target] == [string length [file tail $target]]} { set dir . }
        if {$dir ne "" && ![file isdirectory $dir]} {
            return -code error "ILA_PROBES_NO_DIRECTORY: the folder $dir does not exist, so Vivado cannot open $target for writing (Chipscope 16-19: Could not open file). Nothing was written. Make the folder, then ask again for the probes file (AI only: ila::write_probes <file>)."
        }
        set force [string is true -strict $o(-force)]
        if {[file exists $target] && !$force} {
            return -code error "ILA_PROBES_EXISTS: $target already exists (Vivado 12-1574: you must use the -force option to overwrite). Nothing was overwritten. Pick another name, or pass -force 1 if the user asked to replace it."
        }
        if {[catch {if {$force} { write_debug_probes -force $path } else { write_debug_probes $path }} err]} {
            return -code error "ILA_PROBES_FAILED: write_debug_probes did not write $target. Vivado said: $err"
        }
        if {![file exists $target]} {
            return -code error "ILA_PROBES_FAILED: write_debug_probes reported no error, but $target does not exist."
        }
        say "probes file written: $target ([file size $target] bytes)"
        # the hub unique_id in the file is provisional until route_design (e49, Versal 2026.2); a routed design already holds the final one
        set fh [open $target]; set txt [read $fh]; close $fh
        if {[regexp {"unique_id"\s*:} $txt] && ![probes_design_routed]} {
            say "WARNING: $target holds a hub unique_id, and that id is provisional until after route_design (Vivado 2026.2): Hardware Manager finds no ILA with a provisional id (AI only: a probes file written from the routed design holds the final id: write_debug_probes -force [list $target])."
        }
        return $target
    }
}
