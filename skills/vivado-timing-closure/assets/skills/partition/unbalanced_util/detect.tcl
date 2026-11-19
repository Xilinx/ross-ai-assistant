# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Partition / Unbalanced SLR Utilization Detection
# =============================================================================
# Runs inside Vivado session after post-route physopt.
# Detects if SLR utilization is highly imbalanced (> 30% difference).
#
# Output:
#   <run_dir>/unbalanced_util_detected.flag  (if problem found)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Checking for SLR utilization imbalance..."

# Get per-SLR utilization
set slrs [get_slrs]
set num_slrs [llength $slrs]

if {$num_slrs <= 1} {
    puts "\[DETECT\] Single-SLR device — skipping utilization balance check."
    return
}

set utils {}
foreach slr $slrs {
    set slr_sites [get_sites -of_objects $slr -filter {SITE_TYPE =~ SLICE*}]
    set total [llength $slr_sites]
    set used [llength [get_sites -of_objects $slr -filter {SITE_TYPE =~ SLICE* && IS_USED}]]
    if {$total > 0} {
        set pct [expr {100.0 * $used / $total}]
    } else {
        set pct 0.0
    }
    lappend utils $pct
    puts "\[DETECT\]   $slr: [format %.1f $pct]% SLICE utilization"
}

set max_util [lindex [lsort -real -decreasing $utils] 0]
set min_util [lindex [lsort -real $utils] 0]
set diff [expr {$max_util - $min_util}]

puts "\[DETECT\] SLR utilization range: [format %.1f $min_util]% - [format %.1f $max_util]% (diff=[format %.1f $diff]%)"

if {$diff > 30.0} {
    puts "\[DETECT\] SLR imbalance detected (> 30%)!"
    
    set flag_file [file join $run_dir "unbalanced_util_detected.flag"]
    set fp [open $flag_file w]
    puts $fp "UNBALANCED_UTIL_DETECTED"
    puts $fp "max_util=$max_util"
    puts $fp "min_util=$min_util"
    puts $fp "diff=$diff"
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
        foreach _rj_slr $slrs _rj_u $utils {
            lappend _rj_items [_rj_obj [list slr s $_rj_slr util_pct n [format %.2f $_rj_u]]]
        }
        set _rj_counts [_rj_obj [list max_util n [format %.2f $max_util] min_util n [format %.2f $min_util] \
            diff n [format %.2f $diff] num_slrs n $num_slrs]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "SLR utilization imbalance [format %.1f $diff]% exceeds 30% threshold across $num_slrs SLRs (max=[format %.1f $max_util]%, min=[format %.1f $min_util]%)"
        set _rj_changes "No direct design mutation; flag file records imbalance for downstream placement-directive selection"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "unbalanced_util" category s "partition" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts,\n  \"items\": \[[join $_rj_items ", "]\]\n\}\n"
        set _rj_fh [open [file join $run_dir "unbalanced_util_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write unbalanced_util_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] SLR utilization balanced — no action needed."
}
