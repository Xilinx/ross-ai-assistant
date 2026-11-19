# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# gt_mrmac_early.tcl — GT-MRMAC pre-place SLR assignment (helper)
# Sourced by early_flow_fix.tcl. Sets $_gtmr_moves variable.
# =============================================================================

set _gt_cells [get_cells -hier -quiet -filter "REF_NAME =~ GT*"]
set _mrmac_cells [get_cells -hier -quiet -filter "REF_NAME =~ MRMAC* || REF_NAME =~ DCMAC*"]

if {[llength $_gt_cells] == 0 || [llength $_mrmac_cells] == 0} {
    set _gtmr_moves 0
    return
}

# Build GT lookup
array unset _isgt
array set _isgt {}
foreach _c $_gt_cells { set _isgt([get_property NAME $_c]) 1 }

# Helpers
proc _gtmr_early_datapins {cells dir} {
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

proc _gtmr_early_hop {pins dir} {
    if {[llength $pins] == 0} { return {} }
    if {$dir eq "fanin"} {
        return [all_fanin -startpoints_only -flat -only_cells $pins]
    }
    return [all_fanout -endpoints_only -flat -only_cells $pins]
}

# BFS search from MRMAC to find connected GT
proc _gtmr_early_search {start dir cap} {
    variable _isgt
    array set _seen {}
    set _seen($start) 1
    set sinkpins [_gtmr_early_datapins [list $start] $dir]
    for {set stage 1} {$stage <= $cap} {incr stage} {
        if {[llength $sinkpins] == 0} { break }
        set sps [_gtmr_early_hop $sinkpins $dir]
        if {[llength $sps] == 0} { break }
        foreach c $sps {
            if {[info exists _isgt($c)]} { return [list [expr {$stage - 1}] $c] }
        }
        set newregs {}
        foreach c $sps {
            if {![info exists _seen($c)]} { set _seen($c) 1; lappend newregs $c }
        }
        if {[llength $newregs] == 0} { break }
        set sinkpins [_gtmr_early_datapins $newregs $dir]
    }
    return {}
}

proc _gtmr_early_cr_xy {cr} {
    if {[regexp {X(\d+)Y(\d+)} $cr -> x y]} { return [list $x $y] }
    return {}
}

# Main logic
set _gtmr_moves 0
array set _assigned_sites {}

foreach _mr $_mrmac_cells {
    set _mr_name [get_property NAME $_mr]
    set _mr_loc [get_property -quiet LOC $_mr]
    set _mr_fixed [get_property -quiet IS_LOC_FIXED $_mr]

    if {$_mr_loc ne "" || $_mr_fixed eq "1"} { continue }

    # Search for connected GT
    set _gt_found ""
    foreach _dir {fanin fanout} {
        set _res [_gtmr_early_search $_mr_name $_dir 6]
        if {[llength $_res] > 0} {
            set _depth [lindex $_res 0]
            set _gtc [lindex $_res 1]
            if {$_depth <= 3} {
                set _gt_found $_gtc
                break
            }
        }
    }
    if {$_gt_found eq ""} { continue }

    # Get GT's SLR
    set _gt_slr [get_slrs -quiet -of_objects [get_cells -quiet $_gt_found]]
    if {[llength $_gt_slr] == 0} { continue }
    set _gt_slr_name [get_property NAME $_gt_slr]

    # Get GT's clock region for distance
    set _gt_site [get_sites -quiet -of_objects [get_cells -quiet $_gt_found]]
    set _gt_cr ""
    if {[llength $_gt_site] > 0} { set _gt_cr [get_property -quiet CLOCK_REGION $_gt_site] }
    set _gxy [_gtmr_early_cr_xy $_gt_cr]

    # Find MRMAC site type
    set _mr_ref [get_property REF_NAME $_mr]
    set _stype ""
    if {[string match "MRMAC*" $_mr_ref]} { set _stype "MRMAC" }
    if {[string match "DCMAC*" $_mr_ref]} { set _stype "DCMAC" }
    if {$_stype eq ""} { continue }

    # Find vacant site in GT's SLR
    set _vac [list]
    foreach _st [get_sites -quiet -filter "SITE_TYPE =~ ${_stype}*"] {
        if {[get_property -quiet IS_USED $_st] eq "1"} { continue }
        if {[info exists _assigned_sites($_st)]} { continue }
        set _stslr [get_slrs -quiet -of_objects $_st]
        if {[llength $_stslr] == 0} { continue }
        if {[get_property -quiet NAME $_stslr] ne $_gt_slr_name} { continue }
        set _cr [get_property -quiet CLOCK_REGION $_st]
        set _cxy [_gtmr_early_cr_xy $_cr]
        set _d -1
        if {[llength $_gxy] == 2 && [llength $_cxy] == 2} {
            set _d [expr {abs([lindex $_gxy 0]-[lindex $_cxy 0]) + abs([lindex $_gxy 1]-[lindex $_cxy 1])}]
        }
        lappend _vac [list $_d $_st]
    }

    if {[llength $_vac] == 0} { continue }
    set _vac [lsort -integer -index 0 $_vac]
    set _best_site [lindex [lindex $_vac 0] 1]

    # Only count/mark this site as used if the LOC assignment actually
    # succeeded -- `catch` alone swallows the result and used to fall
    # through unconditionally, so a rejected site (invalid/occupied/DRC
    # conflict) still got counted in _gtmr_moves and permanently marked in
    # _assigned_sites, silently blocking any other MRMAC candidate from
    # trying that same site even though it was never actually used.
    if {![catch { set_property LOC $_best_site [get_cells $_mr] }]} {
        set _assigned_sites($_best_site) $_mr_name
        incr _gtmr_moves
    }
}
