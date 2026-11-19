# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Early Resolution / GT-MRMAC Pre-Place SLR Assignment
# =============================================================================
# Detects GT <-> MRMAC/DCMAC connectivity and constrains un-LOC'd MRMACs to
# the same SLR as their connected GT, BEFORE placement.
# This prevents SLR crossing issues that would cause timing failures.
#
# Logic:
#   1. Find GT ↔ MRMAC/DCMAC connections (structural BFS, N flop stages)
#   2. GT always has LOC (fixed hard block) → we know GT's SLR
#   3. If connected MRMAC has no LOC → assign to GT's SLR (nearest vacant site)
#
# Based on find_gt_mrmac_paths.tcl (post-place version), adapted for pre-place.
# Uses same BFS approach but skips timing checks (not available pre-place).
#
# IMPORTANT: Source BEFORE place_design (after open_checkpoint / opt_design).
#
# Output:
#   <run_dir>/gt_mrmac_preplace_detected.flag  (if relocations suggested)
#   <run_dir>/gt_mrmac_preplace_fix.xdc        (LOC constraints)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] GT-MRMAC pre-place SLR assignment..."

# --- Configuration ---
set max_levels 3
set gt_pat     {GT*}
set mrmac_pat  {MRMAC*}
set dcmac_pat  {DCMAC*}

# --- Find GT and MRMAC/DCMAC cells ---
set gt_cells    [get_cells -hier -quiet -filter "REF_NAME =~ $gt_pat"]
set mrmac_cells [get_cells -hier -quiet -filter "REF_NAME =~ $mrmac_pat || REF_NAME =~ $dcmac_pat"]

if {[llength $gt_cells] == 0} {
    puts "\[DETECT\] No GT cells found. Skipping."
    return
}
if {[llength $mrmac_cells] == 0} {
    puts "\[DETECT\] No MRMAC/DCMAC cells found. Skipping."
    return
}

puts "\[DETECT\] Found [llength $gt_cells] GT cell(s), [llength $mrmac_cells] MRMAC/DCMAC cell(s)"

# --- Build lookup sets ---
array set _isgt {}
array set _ismr {}
foreach c $gt_cells    { set _isgt([get_property NAME $c]) 1 }
foreach c $mrmac_cells { set _ismr([get_property NAME $c]) 1 }

# --- Helper: get data pins (exclude clock pins) ---
proc _preplace_datapins {cells dir} {
    if {[llength $cells] == 0} { return {} }
    if {$dir eq "fanin"} {
        set pins [get_pins -quiet -of [get_cells -quiet $cells] -filter {DIRECTION == IN}]
    } else {
        set pins [get_pins -quiet -of [get_cells -quiet $cells] -filter {DIRECTION == OUT}]
    }
    if {[llength $pins] == 0} { return {} }
    set flags [get_property -quiet IS_CLOCK $pins]
    set out {}
    foreach p $pins f $flags { if {$f ne "1"} { lappend out $p } }
    return $out
}

# --- Helper: one timing-stage hop (batched) ---
proc _preplace_hop {pins dir} {
    if {[llength $pins] == 0} { return {} }
    if {$dir eq "fanin"} {
        return [all_fanin  -startpoints_only -flat -only_cells $pins]
    }
    return [all_fanout -endpoints_only -flat -only_cells $pins]
}

# --- Helper: BFS search from MRMAC to find connected GT ---
proc _preplace_search {start dir cap} {
    variable _isgt
    variable _ismr
    variable gt_pat
    variable mrmac_pat
    variable dcmac_pat

    array set seen {}
    set seen($start) 1
    set sinkpins [_preplace_datapins [list $start] $dir]
    for {set stage 1} {$stage <= [expr {$cap + 1}]} {incr stage} {
        if {[llength $sinkpins] == 0} { break }
        set sps [_preplace_hop $sinkpins $dir]
        if {[llength $sps] == 0} { break }
        set refs [get_property -quiet REF_NAME $sps]

        # GT reached?
        foreach c $sps r $refs {
            if {[info exists _isgt($c)] || [string match $gt_pat $r]} {
                return [list [expr {$stage - 1}] $c]
            }
        }
        # Collect new register frontier
        set newregs {}
        foreach c $sps r $refs {
            if {[string match $mrmac_pat $r] || [string match $dcmac_pat $r] || [info exists _ismr($c)]} { continue }
            if {![info exists seen($c)]} { set seen($c) 1; lappend newregs $c }
        }
        if {[llength $newregs] == 0} { break }
        set sinkpins [_preplace_datapins $newregs $dir]
    }
    return {}
}

# --- Helper: get SLR of a cell (works for GT which always has LOC) ---
proc _preplace_get_slr {cell} {
    set s [get_slrs -quiet -of_objects [get_cells -quiet $cell]]
    if {[llength $s] == 0} { return "" }
    return [get_property -quiet NAME $s]
}

# --- Helper: get clock region XY ---
proc _preplace_cr_xy {cr} {
    if {[regexp {X(\d+)Y(\d+)} $cr -> x y]} { return [list $x $y] }
    return {}
}

# --- Main: find connectivity and suggest LOC for un-LOC'd MRMACs ---
set xdc_lines [list]
set move_count 0
set deep_cap [expr {$max_levels + 3}]

# Track which MRMACs we've already processed
array set processed {}
# Track which sites have been assigned (avoid assigning same site to multiple MRMACs)
array set assigned_sites {}

foreach mr $mrmac_cells {
    set mr_name [get_property NAME $mr]
    if {[info exists processed($mr_name)]} { continue }

    # Check if MRMAC already has LOC
    set mr_loc [get_property -quiet LOC $mr]
    set mr_fixed [get_property -quiet IS_LOC_FIXED $mr]

    # If already LOC'd / fixed, skip (no need to constrain)
    if {$mr_loc ne "" || $mr_fixed eq "1"} {
        puts "\[DETECT\]   $mr_name: already has LOC=$mr_loc (skip)"
        set processed($mr_name) 1
        continue
    }

    # Search for connected GT in both directions
    set gt_found ""
    set gt_depth -1
    foreach dir {fanin fanout} {
        set res [_preplace_search $mr_name $dir $deep_cap]
        if {[llength $res] > 0} {
            set depth [lindex $res 0]
            set gtc   [lindex $res 1]
            if {$depth <= $max_levels} {
                set gt_found $gtc
                set gt_depth $depth
                break
            }
        }
    }

    if {$gt_found eq ""} {
        # No GT connected within max_levels
        continue
    }

    # Get GT's SLR
    set gt_slr [_preplace_get_slr $gt_found]
    if {$gt_slr eq ""} {
        puts "\[DETECT\]   $mr_name: connected GT $gt_found has no SLR info (skip)"
        continue
    }

    # Find nearest vacant MRMAC/DCMAC site in GT's SLR
    set mr_ref [get_property REF_NAME $mr]
    set gt_site [get_sites -quiet -of_objects [get_cells -quiet $gt_found]]
    set gt_cr ""
    if {[llength $gt_site] > 0} {
        set gt_cr [get_property -quiet CLOCK_REGION $gt_site]
    }
    set gxy [_preplace_cr_xy $gt_cr]

    # Determine the site type of the MRMAC
    # For unplaced cells, use the compatible site type from the architecture
    set stype ""
    if {[string match "MRMAC*" $mr_ref]} {
        set stype "MRMAC"
    } elseif {[string match "DCMAC*" $mr_ref]} {
        set stype "DCMAC"
    }

    if {$stype eq ""} {
        puts "\[DETECT\]   $mr_name: unknown site type for REF=$mr_ref (skip)"
        continue
    }

    # Find vacant sites of this type in GT's SLR (exclude already-assigned sites)
    set vac [list]
    foreach st [get_sites -quiet -filter "SITE_TYPE =~ ${stype}*"] {
        if {[get_property -quiet IS_USED $st] eq "1"} { continue }
        if {[info exists assigned_sites($st)]} { continue }
        set stslr [get_slrs -quiet -of_objects $st]
        if {[llength $stslr] == 0} { continue }
        if {[get_property -quiet NAME $stslr] ne $gt_slr} { continue }
        set cr  [get_property -quiet CLOCK_REGION $st]
        set cxy [_preplace_cr_xy $cr]
        set d -1
        if {[llength $gxy] == 2 && [llength $cxy] == 2} {
            set d [expr {abs([lindex $gxy 0]-[lindex $cxy 0]) + \
                         abs([lindex $gxy 1]-[lindex $cxy 1])}]
        }
        lappend vac [list $d $st $cr]
    }

    if {[llength $vac] == 0} {
        puts "\[DETECT\]   $mr_name: no vacant $stype site in GT SLR $gt_slr"
        lappend xdc_lines "# $mr_name: connected to GT $gt_found ($gt_depth stages) but no vacant site in SLR $gt_slr"
        continue
    }

    # Sort by distance to GT, pick nearest
    set vac [lsort -integer -index 0 $vac]
    set best_site [lindex [lindex $vac 0] 1]
    set best_cr   [lindex [lindex $vac 0] 2]

    puts "\[DETECT\]   $mr_name ($mr_ref): connected to GT $gt_found ($gt_depth flop stages)"
    puts "\[DETECT\]     GT SLR=$gt_slr, CR=$gt_cr -> assigning LOC=$best_site (CR=$best_cr)"

    lappend xdc_lines "# $mr_name ($mr_ref): connected to GT $gt_found ($gt_depth stages), assign to GT SLR $gt_slr"
    lappend xdc_lines "catch { set_property LOC $best_site \[get_cells \{$mr_name\}\] }"
    lappend xdc_lines ""
    incr move_count
    set processed($mr_name) 1
    # Mark this site as assigned so it won't be reused for another MRMAC
    set assigned_sites($best_site) $mr_name
}

# --- Write output ---
if {$move_count > 0} {
    puts "\[DETECT\] Suggested $move_count MRMAC/DCMAC LOC assignment(s) to avoid SLR crossings"

    # Write XDC
    set xdc_path [file join $run_dir "gt_mrmac_preplace_fix.xdc"]
    set fh [open $xdc_path w]
    puts $fh "# ============================================="
    puts $fh "# GT-MRMAC Pre-Place SLR Assignment - Auto-generated"
    puts $fh "# Constrains un-LOC'd MRMACs to same SLR as connected GT"
    puts $fh "# Source BEFORE place_design"
    puts $fh "# ============================================="
    puts $fh ""
    foreach line $xdc_lines { puts $fh $line }
    close $fh
    puts "\[DETECT\] Written XDC: $xdc_path"

    # Write flag
    set flag_file [file join $run_dir "gt_mrmac_preplace_detected.flag"]
    set fh [open $flag_file w]
    puts $fh "GT_MRMAC_PREPLACE_DETECTED"
    puts $fh "relocations=$move_count"
    close $fh
    puts "\[DETECT\] Written flag: $flag_file"

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
        set _rj_counts [_rj_obj [list move_count n $move_count gt_cells n [llength $gt_cells] mrmac_cells n [llength $mrmac_cells]]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "$move_count un-LOC'd MRMAC/DCMAC cell(s) found connected (<=3 flop stages) to a GT in a different/vacant SLR site (of [llength $gt_cells] GT, [llength $mrmac_cells] MRMAC/DCMAC cells)"
        set _rj_changes "Wrote gt_mrmac_preplace_fix.xdc assigning LOC on $move_count MRMAC/DCMAC cell(s) to match their connected GT's SLR"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "gt_mrmac_preplace" category s "early_resolution" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts\n\}\n"
        set _rj_fh [open [file join $run_dir "gt_mrmac_preplace_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write gt_mrmac_preplace_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] No MRMAC/DCMAC LOC assignments needed (all already LOC'd or no GT connectivity)"
}
