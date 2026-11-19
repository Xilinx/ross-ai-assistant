# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Congestion / High Fanout Detection
# =============================================================================
# Detects high-fanout nets in congested regions that cause routing pressure.
#
# Output:
#   <run_dir>/high_fanout_detected.flag  (if problem found)
#   <run_dir>/congestion_high_fanout_fix.xdc (optional: buffer insertion hints)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Checking for high-fanout nets in congested regions..."

# Find nets with fanout > 1000 that are also on failing timing paths
set failing_paths [get_timing_paths -max_paths 20 -delay_type max -slack_lesser_than 0]
set high_fanout_nets {}

foreach path $failing_paths {
    set nets [get_nets -of_objects $path]
    foreach net $nets {
        set fanout [get_property FLAT_PIN_COUNT $net]
        if {$fanout > 1000} {
            dict set high_fanout_nets [get_property NAME $net] $fanout
        }
    }
}

set num_hf_nets [dict size $high_fanout_nets]
puts "\[DETECT\] High-fanout nets (>1000) on failing paths: $num_hf_nets"

if {$num_hf_nets >= 3} {
    puts "\[DETECT\] High-fanout congestion detected!"
    
    set flag_file [file join $run_dir "high_fanout_detected.flag"]
    set fp [open $flag_file w]
    puts $fp "HIGH_FANOUT_DETECTED"
    puts $fp "count=$num_hf_nets"
    dict for {net fanout} $high_fanout_nets {
        puts $fp "$net fanout=$fanout"
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
        set _rj_n 0
        set _rj_truncated 0
        dict for {net fanout} $high_fanout_nets {
            if {$_rj_n >= 200} { set _rj_truncated 1; break }
            lappend _rj_items [_rj_obj [list net s $net fanout n $fanout]]
            incr _rj_n
        }
        set _rj_counts [_rj_obj [list num_hf_nets n $num_hf_nets items_truncated b $_rj_truncated]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "$num_hf_nets net(s) with fanout > 1000 found on failing setup paths (threshold: >=3 nets)"
        set _rj_changes "No direct design mutation; flag file lists offending nets for downstream handling"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "high_fanout" category s "congestion" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts,\n  \"items\": \[[join $_rj_items ", "]\]\n\}\n"
        set _rj_fh [open [file join $run_dir "high_fanout_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write high_fanout_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] High-fanout not significant — no action needed."
}
