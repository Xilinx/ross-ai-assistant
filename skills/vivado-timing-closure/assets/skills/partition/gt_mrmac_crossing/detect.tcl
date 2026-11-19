# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Partition / GT-MRMAC SLR Crossing Detection
# =============================================================================
# Detects GT <-> MRMAC connectivity crossing SLR boundaries with negative slack.
# If found, generates mrmac_slr_suggestions.xdc with LOC constraints to
# relocate MRMAC into the same SLR as its connected GT.
#
# Output:
#   <run_dir>/gt_mrmac_crossing_detected.flag  (if problem found)
#   <run_dir>/partition_gt_mrmac_crossing_fix.xdc  (LOC fix)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Checking for GT <-> MRMAC SLR crossing problems..."

# Source the GT/MRMAC analysis script
set gt_mrmac_script [file join [file dirname [info script]] "find_gt_mrmac_paths.tcl"]

if {![file exists $gt_mrmac_script]} {
    puts "\[DETECT\] GT/MRMAC script not found: $gt_mrmac_script"
    return
}

source $gt_mrmac_script

# Run the detection (up to 3 flop stages). Defense-in-depth: this file's
# own JSON-dump block below is already catch-wrapped; wrap this main call
# too, consistent with that convention -- find_gt_mrmac_paths.tcl's own
# internals are almost entirely -quiet-guarded, but its `open $xdc_file w`
# is not, so a disk-full/permission-denied failure there would otherwise
# propagate uncaught through this unguarded call site.
set xdc_output [file join $run_dir "mrmac_slr_suggestions.xdc"]
set _gtmr_levels 3
if {[catch {find_gt_mrmac_paths -levels $_gtmr_levels -xdc $xdc_output} _gtmr_find_err]} {
    puts "\[DETECT\] WARNING: find_gt_mrmac_paths failed: $_gtmr_find_err"
}

# Check if XDC was generated with actual fixes
if {[file exists $xdc_output]} {
    set fp [open $xdc_output r]
    set content [read $fp]
    close $fp

    # Check if it has actual LOC constraints (not just comments)
    if {[regexp {set_property LOC} $content]} {
        puts "\[DETECT\] GT-MRMAC SLR crossing problem detected! Fix XDC generated."

        set flag_file [file join $run_dir "gt_mrmac_crossing_detected.flag"]
        set fp [open $flag_file w]
        puts $fp "GT_MRMAC_CROSSING_DETECTED"
        close $fp

        # ---- additive, structured result.json (does not affect detection/fix logic) ----
        # Only ::gtmr_moves and the ::gtmr_cross array are truly global-qualified
        # inside find_gt_mrmac_paths.tcl's proc; finer counts (n_lock/n_mrmac/n_dcmac)
        # are proc-local and not readable from here.
        if {[catch {
            proc _rj_esc {s} { return [string map {\\ \\\\ \" \\\" \n \\n \r \\r \t \\t} $s] }
            proc _rj_str {v} { return "\"[_rj_esc $v]\"" }
            proc _rj_bool {v} { return [expr {$v ? "true" : "false"}] }
            proc _rj_obj {pairs} {
                set m {}
                foreach {k t v} $pairs {
                    set vv [expr {$t eq "s" ? [_rj_str $v] : ($t eq "b" ? [_rj_bool $v] : $v)}]
                    lappend m "\"$k\": $vv"
                }
                return "\{[join $m ", "]\}"
            }
            set _rj_relocations [expr {[info exists ::gtmr_moves] ? $::gtmr_moves : 0}]
            set _rj_crossing [expr {[array exists ::gtmr_cross] ? [array size ::gtmr_cross] : 0}]
            set _rj_counts [_rj_obj [list relocations n $_rj_relocations crossing_mrmacs n $_rj_crossing levels_searched n $_gtmr_levels]]
            set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
            set _rj_cond "$_rj_crossing MRMAC/GT crossing(s) found within $_gtmr_levels flop stages requiring relocation ($_rj_relocations LOC suggestion(s))"
            set _rj_changes "Wrote mrmac_slr_suggestions.xdc with $_rj_relocations LOC constraint(s)"
            set _rj_top [_rj_obj [list schema_version n 1 skill s "gt_mrmac_crossing" category s "partition" \
                detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
            set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts\n\}\n"
            set _rj_fh [open [file join $run_dir "gt_mrmac_crossing_result.json"] w]
            puts -nonewline $_rj_fh $_rj_json
            close $_rj_fh
        } _rj_err]} {
            puts "\[DETECT\] WARNING: could not write gt_mrmac_crossing_result.json: $_rj_err"
        }
    } else {
        puts "\[DETECT\] GT-MRMAC analysis ran but no actionable fixes needed."
    }
} else {
    puts "\[DETECT\] No GT-MRMAC crossing issues found."
}
