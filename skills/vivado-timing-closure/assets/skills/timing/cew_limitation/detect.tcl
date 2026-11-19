# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Timing / CEW Limitation Detection
# =============================================================================
# Runs CEW timing analysis on failing paths to detect if Clock Expansion Window
# limitations are causing timing failures.
#
# Sources analyze_cew_timing.tcl and calls run_cew_analysis.
# If CEW-limited paths found, writes flag + generates fix XDC.
#
# Output:
#   <run_dir>/cew_limitation_detected.flag  (if problem found)
#   <run_dir>/cew_timing_report.rpt         (detailed analysis)
#   <run_dir>/cew_timing_report.xdc         (USER_CLOCK_EXPANSION_WINDOW fix)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Running CEW limitation analysis..."

# Source the CEW analysis script
set cew_script [file join [file dirname [info script]] "analyze_cew_timing.tcl"]

if {![file exists $cew_script]} {
    puts "\[DETECT\] CEW analysis script not found: $cew_script"
    return
}

if {[catch {source $cew_script} _cew_src_err]} {
    puts "\[DETECT\] WARNING: could not source $cew_script ($_cew_src_err) -- skipping CEW analysis."
    return
}

# Run analysis with defaults. Wrapped in catch: analyze_cew_timing.tcl's
# run_cew_analysis does ~500 lines of clock-region-name parsing across
# multiple device-family naming formats and CR-distance arithmetic with
# almost no internal error guarding -- an uncaught exception here would
# otherwise propagate out of this detect.tcl (which is source'd mid-flow)
# and abort the entire remaining Vivado run for this run, the same class
# of real incident already root-caused elsewhere in this framework this
# session (an uncaught Tcl error mid-script silently aborting everything
# after it while Vivado still exits "cleanly").
set output_file [file join $run_dir "cew_timing_report.rpt"]
if {[catch {
    run_cew_analysis -max_paths 5 -cr_threshold 2 -min_overlap 2 -stage router -output $output_file
} _cew_run_err]} {
    puts "\[DETECT\] WARNING: CEW analysis failed ($_cew_run_err) -- skipping."
    return
}

# Check if XDC was generated (indicates CEW problem found)
set xdc_file [regsub {\.[^.]+$} $output_file ""].xdc
if {[file exists $xdc_file]} {
    # Verify it has actual constraints
    set fp [open $xdc_file r]
    set content [read $fp]
    close $fp

    if {[regexp {set_property USER_CLOCK_EXPANSION_WINDOW} $content]} {
        puts "\[DETECT\] CEW limitation detected! Fix XDC generated."

        set flag_file [file join $run_dir "cew_limitation_detected.flag"]
        set fp [open $flag_file w]
        puts $fp "CEW_LIMITATION_DETECTED"
        puts $fp "fix_xdc=$xdc_file"
        puts $fp "constrains=nets:clock_buffer_nets"
        puts $fp "compatible_with=all"
        close $fp
    } else {
        puts "\[DETECT\] CEW analysis ran but no actionable fixes needed."
    }
} else {
    puts "\[DETECT\] No CEW limitation issues found."
}
