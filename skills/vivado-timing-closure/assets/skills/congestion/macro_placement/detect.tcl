# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Congestion / Macro Placement Detection
# =============================================================================
# Detects BRAM/DSP clusters causing local routing congestion.
#
# Output:
#   <run_dir>/macro_placement_detected.flag  (if problem found)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Checking for macro placement congestion..."

# Check if BRAMs or DSPs are clustered in congested clock regions
set congested_crs {}

# Parse congestion from report_design_analysis if available
set da_file [file join $run_dir "postroute_physopt_design_analysis.rpt"]
if {![file exists $da_file]} {
    puts "\[DETECT\] No design_analysis report found — skipping macro check."
    return
}

# Get clock regions with high BRAM/DSP density
set all_brams [get_cells -hierarchical -filter {PRIMITIVE_TYPE =~ BMEM.*}]
set all_dsps [get_cells -hierarchical -filter {PRIMITIVE_TYPE =~ ARITHMETIC.*}]

puts "\[DETECT\] Total BRAMs: [llength $all_brams], DSPs: [llength $all_dsps]"

# Group by clock region
set cr_macros {}
foreach cell [concat $all_brams $all_dsps] {
    set cr [get_clock_regions -of_objects $cell]
    if {$cr != ""} {
        dict incr cr_macros $cr
    }
}

# Find CRs with excessive macro density
set threshold 50
set hot_crs {}
dict for {cr count} $cr_macros {
    if {$count > $threshold} {
        lappend hot_crs "$cr ($count macros)"
    }
}

if {[llength $hot_crs] > 0} {
    puts "\[DETECT\] Macro clustering detected in [llength $hot_crs] clock regions!"
    foreach cr $hot_crs {
        puts "\[DETECT\]   $cr"
    }
    
    set flag_file [file join $run_dir "macro_placement_detected.flag"]
    set fp [open $flag_file w]
    puts $fp "MACRO_PLACEMENT_DETECTED"
    foreach cr $hot_crs {
        puts $fp $cr
    }
    close $fp

    # ---- additive, structured result.json (does not affect detection/fix logic) ----
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
        set _rj_items {}
        dict for {cr count} $cr_macros {
            if {$count > $threshold} {
                lappend _rj_items [_rj_obj [list region s $cr macro_count n $count]]
            }
        }
        set _rj_counts [_rj_obj [list hot_regions n [llength $hot_crs] total_brams n [llength $all_brams] \
            total_dsps n [llength $all_dsps] threshold n $threshold]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "[llength $hot_crs] clock region(s) have >$threshold BRAM/DSP macros clustered (of [llength $all_brams] BRAM + [llength $all_dsps] DSP total)"
        set _rj_changes "No direct design mutation; flag file lists hot clock regions for downstream placement guidance"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "macro_placement" category s "congestion" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts,\n  \"items\": \[[join $_rj_items ", "]\]\n\}\n"
        set _rj_fh [open [file join $run_dir "macro_placement_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write macro_placement_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] Macro placement balanced — no action needed."
}
