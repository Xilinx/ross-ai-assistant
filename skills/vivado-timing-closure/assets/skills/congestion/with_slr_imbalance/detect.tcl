# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Congestion / With SLR Imbalance Detection
# =============================================================================
# Detects congestion combined with SLR utilization imbalance.
# This is a compound condition: routing congestion exists AND SLRs are
# unevenly loaded (one SLR is significantly more utilized than others).
#
# Output:
#   <run_dir>/with_slr_imbalance_detected.flag  (if problem found)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Checking for congestion with SLR imbalance..."

# Check if congestion was detected (congestion_only always writes its flag)
set has_congestion 0
if {[file exists [file join $run_dir "congestion_only_detected.flag"]]} {
    set has_congestion 1
}

if {!$has_congestion} {
    puts "\[DETECT\] No congestion sub-problem detected — skipping imbalance check."
    return
}

# Check SLR utilization imbalance
set slrs [get_slrs]
set num_slrs [llength $slrs]

if {$num_slrs <= 1} {
    puts "\[DETECT\] Single-SLR device — skipping."
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
}

set max_util [lindex [lsort -real -decreasing $utils] 0]
set min_util [lindex [lsort -real $utils] 0]
set diff [expr {$max_util - $min_util}]

if {$diff > 30.0} {
    puts "\[DETECT\] Congestion + SLR imbalance detected (diff=[format %.1f $diff]%)!"

    set flag_file [file join $run_dir "with_slr_imbalance_detected.flag"]
    set fp [open $flag_file w]
    puts $fp "WITH_SLR_IMBALANCE_DETECTED"
    puts $fp "max_util=$max_util"
    puts $fp "min_util=$min_util"
    puts $fp "diff=$diff"
    puts $fp "fix_xdc=none"
    puts $fp "constrains=placement_directives"
    puts $fp "compatible_with=all"
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
        set _rj_counts [_rj_obj [list max_util n [format %.2f $max_util] min_util n [format %.2f $min_util] \
            diff n [format %.2f $diff] num_slrs n $num_slrs]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "Congestion already flagged AND SLR utilization imbalance [format %.1f $diff]% exceeds 30% threshold (max=[format %.1f $max_util]%, min=[format %.1f $min_util]% across $num_slrs SLRs)"
        set _rj_changes "No direct design mutation; flags compound congestion+imbalance condition for a placement-directive choice downstream"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "with_slr_imbalance" category s "congestion" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts\n\}\n"
        set _rj_fh [open [file join $run_dir "with_slr_imbalance_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write with_slr_imbalance_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] Congestion present but SLR utilization balanced — no compound issue."
}
