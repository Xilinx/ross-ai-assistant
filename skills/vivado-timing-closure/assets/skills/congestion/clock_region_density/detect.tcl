# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Congestion / Clock Region Density Detection
# =============================================================================
# Detects over-packed clock regions causing routing congestion.
#
# Output:
#   <run_dir>/clock_region_density_detected.flag  (if problem found)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Checking for clock region density issues..."

# Get per-clock-region utilization
set clock_regions [get_clock_regions]
set hot_crs {}
set hot_crs_struct {}

foreach cr $clock_regions {
    set sites [get_sites -of_objects $cr -filter {SITE_TYPE =~ SLICE*}]
    set total [llength $sites]
    set used [llength [get_sites -of_objects $cr -filter {SITE_TYPE =~ SLICE* && IS_USED}]]

    if {$total > 0} {
        set pct [expr {100.0 * $used / $total}]
        if {$pct > 90.0} {
            lappend hot_crs "$cr ([format %.1f $pct]%)"
            lappend hot_crs_struct [list $cr $pct]
        }
    }
}

puts "\[DETECT\] Clock regions > 90% utilized: [llength $hot_crs]"

if {[llength $hot_crs] >= 2} {
    puts "\[DETECT\] Clock region density problem detected!"
    foreach cr $hot_crs {
        puts "\[DETECT\]   $cr"
    }
    
    set flag_file [file join $run_dir "clock_region_density_detected.flag"]
    set fp [open $flag_file w]
    puts $fp "CLOCK_REGION_DENSITY_DETECTED"
    puts $fp "hot_regions=[llength $hot_crs]"
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
        foreach pair $hot_crs_struct {
            lassign $pair _rj_cr _rj_pct
            lappend _rj_items [_rj_obj [list region s $_rj_cr util_pct n [format %.2f $_rj_pct]]]
        }
        set _rj_counts [_rj_obj [list hot_regions n [llength $hot_crs]]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "[llength $hot_crs] clock region(s) exceed 90% SLICE utilization (threshold: >=2 regions)"
        set _rj_changes "No direct design mutation by this script; flags congestion category so a downstream placer subdirective (e.g. ReduceCongestion) is selected"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "clock_region_density" category s "congestion" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts,\n  \"items\": \[[join $_rj_items ", "]\]\n\}\n"
        set _rj_fh [open [file join $run_dir "clock_region_density_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write clock_region_density_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] Clock region density within limits — no action needed."
}
