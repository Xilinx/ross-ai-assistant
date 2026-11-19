# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Congestion / Congestion Only Detection
# =============================================================================
# Always-on detection: writes flag whenever Congestion category is detected.
# This script is only sourced when Layer 1 has already confirmed congestion,
# so the flag is unconditionally written to trigger the ReduceCongestion fix.
#
# Output:
#   <run_dir>/congestion_only_detected.flag  (always written)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Congestion detected — marking congestion_only for ReduceCongestion fix."

set flag_file [file join $run_dir "congestion_only_detected.flag"]
set fh [open $flag_file w]
puts $fh "Congestion detected by Layer 1. ReduceCongestion subdirective applicable."
close $fh

puts "\[DETECT\] Written: $flag_file"

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
    set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
    set _rj_cond "Congestion category already flagged by the Layer 1 checker upstream (always-on trigger, no local threshold)"
    set _rj_changes "No direct design mutation by this script; writes only a detection flag so the ReduceCongestion subdirective is selected downstream"
    set _rj_top [_rj_obj [list schema_version n 1 skill s "congestion_only" category s "congestion" \
        detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
    set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": \{\}\n\}\n"
    set _rj_fh [open [file join $run_dir "congestion_only_result.json"] w]
    puts -nonewline $_rj_fh $_rj_json
    close $_rj_fh
} _rj_err]} {
    puts "\[DETECT\] WARNING: could not write congestion_only_result.json: $_rj_err"
}
