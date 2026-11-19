# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#==============================================================================
# find_gt_mrmac_paths.tcl   (POST-PLACE, STRUCTURAL - NO TIMING CHECK)
#
# Detect GT <-> MRMAC/DCMAC connectivity, counting SEQUENTIAL (flip-flop)
# stages, and - whenever a connected GT and MAC sit in DIFFERENT SLRs - write
# a LOC constraint that relocates the (movable) MAC into the GT's SLR.
#
# This version is TIMING-AGNOSTIC: it does NOT run get_timing_paths and does
# NOT gate on slack.  A constraint is emitted for EVERY qualifying scenario:
#     - GT reachable from MRMAC/DCMAC within N sequential level(s) (default 3,
#       which also covers the 2-level case), in either direction (GT->MAC or
#       MAC->GT / fanin or fanout),
#     - the MAC is NOT user-locked (IS_LOC_FIXED != 1), and
#     - GT and MAC are in DIFFERENT SLRs, and
#     - a vacant compatible site is available near the GT in the GT's SLR.
#
# A "level" = one flip-flop stage between GT and MRMAC.  Combinational logic
# (LUT / CARRY / MUX) between registers is traversed but does NOT add a level.
# So the structure  GT -> LUTs -> FF -> FF -> MRMAC  is 2 levels (2 flop stages).
#
#   -levels 0  : GT connects to MRMAC through pure combinational logic (no FF)
#   -levels 1  : GT -> (comb) -> FF -> (comb) -> MRMAC
#   -levels 2  : GT -> (comb) -> FF -> (comb) -> FF -> (comb) -> MRMAC
#   ...
#
# Usage (in Vivado Tcl console, design open - sourceable skill):
#     source find_gt_mrmac_paths.tcl
#     find_gt_mrmac_paths                 ;# up to 3 flop stages (default)
#     find_gt_mrmac_paths -levels 2
#     find_gt_mrmac_paths -levels 3 -xdc gt_mrmac_slr_suggestions.xdc
#     find_gt_mrmac_paths -levels 4 -gt_ref {GTY*} -mrmac_ref {MRMAC*}
#==============================================================================

proc find_gt_mrmac_paths {args} {
    set max_levels 3
    set gt_pat     {GT*}
    set mrmac_pat  {MRMAC*}
    set dcmac_pat  {DCMAC*}
    set xdc_file   "gt_mrmac_slr_suggestions.xdc"
    set ::gtmr_debug 0

    for {set i 0} {$i < [llength $args]} {incr i} {
        set opt [lindex $args $i]
        switch -- $opt {
            -levels    { incr i; set max_levels [lindex $args $i] }
            -gt_ref    { incr i; set gt_pat     [lindex $args $i] }
            -mrmac_ref { incr i; set mrmac_pat  [lindex $args $i] }
            -dcmac_ref { incr i; set dcmac_pat  [lindex $args $i] }
            -xdc       { incr i; set xdc_file   [lindex $args $i] }
            -debug     { set ::gtmr_debug 1 }
            default    { puts "WARNING: unknown option '$opt' ignored" }
        }
    }

    set gt_cells    [get_cells -hier -quiet -filter "REF_NAME =~ $gt_pat"]
    set mrmac_cells [get_cells -hier -quiet -filter "REF_NAME =~ $mrmac_pat || REF_NAME =~ $dcmac_pat"]

    if {[llength $gt_cells] == 0} {
        puts "ERROR: no GT cells found matching REF_NAME =~ $gt_pat"; return }
    if {[llength $mrmac_cells] == 0} {
        puts "ERROR: no MRMAC/DCMAC cells found matching REF_NAME =~ $mrmac_pat or $dcmac_pat"; return }

    puts "INFO: [llength $gt_cells] GT cell(s), [llength $mrmac_cells] MRMAC/DCMAC cell(s)"
    puts "INFO: searching up to $max_levels sequential level(s) (flop stages), both directions"
    puts "-----------------------------------------------------------------"

    # Reset the movable-MAC current-location map BEFORE the info loop populates it.
    catch {array unset ::gtmr_movable_loc}; array set ::gtmr_movable_loc {}

    # ---- MRMAC/DCMAC placement info (IS_LOC_FIXED is honored: a fixed cell
    #      must NOT be moved/relocated; this block only reports the status) ---
    puts "MRMAC/DCMAC placement info (IS_LOC_FIXED honored - fixed cells are not movable):"
    set n_mrmac 0 ; set n_dcmac 0
    set n_mrmac_fixed 0 ; set n_dcmac_fixed 0
    set n_mrmac_nofix 0 ; set n_dcmac_nofix 0
    set n_mrmac_noloc 0 ; set n_dcmac_noloc 0
    foreach mr $mrmac_cells {
        set nm    [get_property -quiet NAME $mr]
        set ref   [get_property -quiet REF_NAME $mr]
        set fixed [get_property -quiet IS_LOC_FIXED $mr]
        set loc   [get_property -quiet LOC $mr]
        set site  [get_sites -quiet -of_objects $mr]
        set slr   "-"
        if {[llength $site] > 0} {
            set sl [get_slrs -quiet -of_objects $site]
            if {[llength $sl] > 0} { set slr [get_property -quiet NAME $sl] }
        }
        set is_dc [string match $dcmac_pat $ref]
        if {$is_dc} { incr n_dcmac } else { incr n_mrmac }
        if {$fixed eq "1"} {
            if {$is_dc} { incr n_dcmac_fixed } else { incr n_mrmac_fixed }
            set ftxt "IS_LOC_FIXED=1  -> user-fixed placement (HONORED: do NOT move/relocate)"
        } else {
            if {$is_dc} { incr n_dcmac_nofix } else { incr n_mrmac_nofix }
            set ftxt "IS_LOC_FIXED=0  -> movable (no fixed-placement constraint)"
            if {$loc ne ""} { set ::gtmr_movable_loc($nm) $loc }
        }
        if {$loc eq ""} { if {$is_dc} { incr n_dcmac_noloc } else { incr n_mrmac_noloc } }
        puts "   $nm ($ref)"
        puts "        $ftxt ; LOC=$loc ; SLR=$slr"
    }
    puts ""
    puts "Breakdown (MRMAC vs DCMAC):"
    puts "   MRMAC : total $n_mrmac  | LOC-fixed (IS_LOC_FIXED=1): $n_mrmac_fixed  | not fixed: $n_mrmac_nofix  | no LOC (unplaced): $n_mrmac_noloc"
    puts "   DCMAC : total $n_dcmac  | LOC-fixed (IS_LOC_FIXED=1): $n_dcmac_fixed  | not fixed: $n_dcmac_nofix  | no LOC (unplaced): $n_dcmac_noloc"
    puts "   TOTAL : [expr {$n_mrmac + $n_dcmac}] MAC cell(s)  | LOC-fixed: [expr {$n_mrmac_fixed + $n_dcmac_fixed}]  | not fixed: [expr {$n_mrmac_nofix + $n_dcmac_nofix}]  | no LOC: [expr {$n_mrmac_noloc + $n_dcmac_noloc}]"
    puts "-----------------------------------------------------------------"

    # Publish endpoint sets + property cache as globals for the helpers.
    catch {array unset ::gtmr_isgt}
    catch {array unset ::gtmr_ismr}
    catch {array unset ::gtmr_cls}
    array set ::gtmr_isgt {}
    array set ::gtmr_ismr {}
    set ::gtmr_gtpat $gt_pat
    set ::gtmr_mrpat $mrmac_pat
    set ::gtmr_mrpat2 $dcmac_pat
    foreach c $gt_cells    { set ::gtmr_isgt([get_property NAME $c]) 1 }
    foreach c $mrmac_cells { set ::gtmr_ismr([get_property NAME $c]) 1 }

    # SLR-crossing suggestion accumulators (consolidated per MAC cell).
    catch {array unset ::gtmr_cross};    array set ::gtmr_cross {}
    catch {array unset ::gtmr_gtc};      array set ::gtmr_gtc {}
    catch {array unset ::gtmr_gtslr};    array set ::gtmr_gtslr {}
    catch {array unset ::gtmr_xdc_seen}; array set ::gtmr_xdc_seen {}
    set ::gtmr_xdc_lines {}
    set ::gtmr_moves 0

    set deep_cap [expr {$max_levels + 3}]

    set found_any 0
    foreach mr $mrmac_cells {
        set mr_name [get_property NAME $mr]
        foreach dir {fanin fanout} {
            set res [_gtmr_search $mr_name $dir $deep_cap]
            if {[llength $res] == 0} { continue }
            set depth  [lindex $res 0]
            set gt     [lindex $res 1]
            set fronts [lindex $res 2]
            set chain  [_gtmr_reconstruct $mr_name $dir $depth $gt $fronts]
            if {$dir eq "fanin"} { set ordered [lreverse $chain] } else { set ordered $chain }
            if {$depth <= $max_levels} {
                set found_any 1
                puts "MATCH (dir=$dir): $depth sequential level(s) / flop stage(s)"
                puts "   [_gtmr_fmt $ordered]"
                _gtmr_slr_analysis $ordered
                puts ""
            } else {
                puts "NEARBY (dir=$dir): GT reachable at $depth flop stage(s) (> requested $max_levels)."
                puts "   [_gtmr_fmt $ordered]"
                _gtmr_slr_analysis $ordered
                puts "   -> re-run with:  find_gt_mrmac_paths -levels $depth"
                puts ""
            }
        }
    }
    if {!$found_any} {
        puts "No GT <-> MRMAC path found within the requested $max_levels sequential level(s)."
        puts "(Any NEARBY lines above show the real flop-stage distance, up to $deep_cap.)"
    }

    # One consolidated placement suggestion per MAC (across both directions).
    _gtmr_emit_all_suggestions

    # For rerun stability: if we emitted any relocation, also LOCK every OTHER
    # movable (IS_LOC_FIXED=0) MAC at its CURRENT location, so the rerun placer
    # does not move them and create new crossings.  (The relocated MAC(s) keep
    # their new LOC from above; IS_LOC_FIXED=1 cells are already user-locked.)
    #
    # IMPORTANT: nothing is written unless at least one REAL move was suggested,
    # so a design with no move suggestions produces NO constraints / NO XDC.
    set n_lock 0
    if {$::gtmr_moves > 0} {
        set lock_lines {}
        foreach nm [lsort [array names ::gtmr_movable_loc]] {
            if {[info exists ::gtmr_xdc_seen($nm)]} { continue }  ;# already relocated
            set cl $::gtmr_movable_loc($nm)
            if {$cl eq ""} { continue }
            incr n_lock
            lappend lock_lines "catch { set_property LOC $cl \[get_cells \{$nm\}\] }"
        }
        if {$n_lock > 0} {
            lappend ::gtmr_xdc_lines "# --- keep remaining movable MACs at their CURRENT location (rerun stability) ---"
            foreach l $lock_lines { lappend ::gtmr_xdc_lines $l }
            lappend ::gtmr_xdc_lines ""
            puts "INFO: also locked $n_lock movable MAC(s) at current location for rerun stability"
        }
    }

    if {$::gtmr_moves > 0} {
        set fh [open $xdc_file w]
        puts $fh "# GT<->MRMAC/DCMAC cross-SLR LOC suggestions (find_gt_mrmac_paths)"
        puts $fh "# STRUCTURAL (no timing): every GT<->MAC SLR crossing within the requested"
        puts $fh "# sequential level(s) is relocated; IS_LOC_FIXED cells are skipped."
        puts $fh "# Movable (IS_LOC_FIXED=0) MACs not relocated are pinned at their current site for rerun stability."
        puts $fh "# Review before use."
        puts $fh ""
        foreach l $::gtmr_xdc_lines { puts $fh $l }
        close $fh
        puts "INFO: wrote $::gtmr_moves relocation(s) and $n_lock current-location lock(s) to $xdc_file"
    } else {
        puts "INFO: no move suggestions -> NO XDC / NO constraints written."
    }
    puts "-----------------------------------------------------------------"
    return
}

#------------------------------------------------------------------------------
# Data pins of one-or-more cells for tracing (clock pins excluded), batched.
#   fanin  -> data INPUT  pins (D, CE, R/S, ...)
#   fanout -> data OUTPUT pins (Q, ...)
#------------------------------------------------------------------------------
proc _gtmr_datapins {cells dir} {
    if {[llength $cells] == 0} { return {} }
    if {$dir eq "fanin"} {
        set pins [get_pins -quiet -of [get_cells -quiet $cells] -filter {DIRECTION == IN}]
    } else {
        set pins [get_pins -quiet -of [get_cells -quiet $cells] -filter {DIRECTION == OUT}]
    }
    if {[llength $pins] == 0} { return {} }
    # Drop clock pins with a single batched property query.
    set flags [get_property -quiet IS_CLOCK $pins]
    set out {}
    foreach p $pins f $flags { if {$f ne "1"} { lappend out $p } }
    return $out
}

#------------------------------------------------------------------------------
# One timing-stage hop for a set of pins (batched).
#------------------------------------------------------------------------------
proc _gtmr_hop {pins dir} {
    if {[llength $pins] == 0} { return {} }
    if {$dir eq "fanin"} {
        return [all_fanin  -startpoints_only -flat -only_cells $pins]
    }
    return [all_fanout -endpoints_only  -flat -only_cells $pins]
}

#------------------------------------------------------------------------------
# BATCHED sequential-stage search from $start (MRMAC).  ONE all_fanin/all_fanout
# per stage (not per flop), so it stays fast even on large fan-in cones.
#
# Returns {depth gt_cell frontiers} where:
#   depth     = number of flip-flop stages between GT and MRMAC (0 = direct)
#   gt_cell   = the GT cell reached
#   frontiers = list of register-name lists R_1..R_depth (R_1 nearest MRMAC)
# Returns {} if no GT within $cap stages.
#------------------------------------------------------------------------------
proc _gtmr_search {start dir cap} {
    array set seen {}
    set seen($start) 1
    set frontiers {}
    set sinkpins [_gtmr_datapins [list $start] $dir]
    for {set stage 1} {$stage <= [expr {$cap + 1}]} {incr stage} {
        if {[llength $sinkpins] == 0} { break }
        set sps [_gtmr_hop $sinkpins $dir]
        if {[llength $sps] == 0} { break }
        set refs [get_property -quiet REF_NAME $sps]

        # 1) GT reached at this stage?  -> depth = stage-1
        foreach c $sps r $refs {
            if {[info exists ::gtmr_isgt($c)] || [string match $::gtmr_gtpat $r]} {
                return [list [expr {$stage - 1}] $c $frontiers]
            }
        }
        # 2) Collect the new register frontier (skip GT/MRMAC & already-seen).
        set newregs {}
        foreach c $sps r $refs {
            if {[string match $::gtmr_mrpat $r] || [string match $::gtmr_mrpat2 $r] || [info exists ::gtmr_ismr($c)]} { continue }
            if {![info exists seen($c)]} { set seen($c) 1; lappend newregs $c }
        }
        if {[llength $newregs] == 0} { break }
        lappend frontiers $newregs
        set sinkpins [_gtmr_datapins $newregs $dir]
    }
    return {}
}

#------------------------------------------------------------------------------
# Reconstruct ONE representative chain start(MRMAC) .. GT from the recorded
# frontier sets.  Cheap: one hop per stage on a single cell.
#------------------------------------------------------------------------------
proc _gtmr_reconstruct {start dir depth gt frontiers} {
    set chain [list $start]
    set current $start
    for {set i 0} {$i < $depth} {incr i} {
        array unset inr
        array set inr {}
        foreach c [lindex $frontiers $i] { set inr($c) 1 }
        set pick ""
        foreach c [_gtmr_hop [_gtmr_datapins [list $current] $dir] $dir] {
            if {[info exists inr($c)]} { set pick $c; break }
        }
        if {$pick eq ""} { break }
        lappend chain $pick
        set current $pick
    }
    lappend chain $gt
    return $chain
}

# Pretty-print a cell chain as  name(REF)  -->  name(REF) ...
proc _gtmr_fmt {celllist} {
    set out {}
    foreach c $celllist {
        set r [get_property -quiet REF_NAME [get_cells -quiet $c]]
        lappend out "$c\($r\)"
    }
    return [join $out {  -->  }]
}

#------------------------------------------------------------------------------
# SLR of a placed cell ("-" if unplaced).
#------------------------------------------------------------------------------
proc _gtmr_slr {cell} {
    set s [get_slrs -quiet -of_objects [get_cells -quiet $cell]]
    if {[llength $s] == 0} { return "-" }
    return [get_property -quiet NAME $s]
}

proc _gtmr_is_gt {c} {
    if {[info exists ::gtmr_isgt($c)]} { return 1 }
    return [string match $::gtmr_gtpat [get_property -quiet REF_NAME [get_cells -quiet $c]]]
}

proc _gtmr_cr_xy {cr} {
    if {[regexp {X(\d+)Y(\d+)} $cr -> x y]} { return [list $x $y] }
    return {}
}

#------------------------------------------------------------------------------
# Report SLR placement of every cell in a GT..MAC chain; if GT and MAC are in
# different SLRs, print the crossing point(s) and record the crossing for a
# consolidated placement suggestion.  STRUCTURAL ONLY - no timing/slack.
#------------------------------------------------------------------------------
proc _gtmr_slr_analysis {ordered} {
    set n [llength $ordered]
    if {$n == 0} { return }
    set slrs {}
    foreach c $ordered { lappend slrs [_gtmr_slr $c] }
    puts "   SLR:  [join $slrs {  -->  }]"

    set c0 [lindex $ordered 0]
    set s0 [lindex $slrs 0]
    set sN [lindex $slrs end]
    if {[_gtmr_is_gt $c0]} {
        set gtc $c0 ; set macc [lindex $ordered end] ; set gts $s0 ; set mrs $sN
    } else {
        set gtc [lindex $ordered end] ; set macc $c0 ; set gts $sN ; set mrs $s0
    }

    if {$gts eq "-" || $mrs eq "-"} {
        puts "   GT in $gts, MAC in $mrs  (placement unavailable)"
        return
    }
    if {$gts eq $mrs} {
        puts "   GT in $gts, MAC in $mrs  (same SLR - no SLR crossing)"
        return
    }
    puts "   GT in $gts, MAC in $mrs  => CROSSES SLR boundary"

    puts "   SLR crossing point(s):"
    for {set i 0} {$i < [expr {$n - 1}]} {incr i} {
        set sa [lindex $slrs $i] ; set sb [lindex $slrs [expr {$i + 1}]]
        if {$sa eq $sb} { continue }
        set a [lindex $ordered $i] ; set b [lindex $ordered [expr {$i + 1}]]
        set aref [get_property -quiet REF_NAME [get_cells -quiet $a]]
        set bref [get_property -quiet REF_NAME [get_cells -quiet $b]]
        puts "     $a\($aref\)\[$sa\]  ->  $b\($bref\)\[$sb\]"
    }

    # record crossing for consolidated suggestion (structural: any crossing acts)
    set ::gtmr_cross($macc) 1
    if {![info exists ::gtmr_gtc($macc)]} {
        set ::gtmr_gtc($macc)   $gtc
        set ::gtmr_gtslr($macc) $gts
    }
}

#------------------------------------------------------------------------------
# One consolidated placement suggestion per crossing MAC.
#------------------------------------------------------------------------------
proc _gtmr_emit_all_suggestions {} {
    if {![array exists ::gtmr_cross] || [llength [array names ::gtmr_cross]] == 0} { return }
    puts "================  MRMAC/DCMAC placement suggestions  ================"
    foreach mrc [lsort [array names ::gtmr_cross]] {
        set gtc  $::gtmr_gtc($mrc)
        set gslr $::gtmr_gtslr($mrc)
        puts "MAC: $mrc"
        _gtmr_suggest_one $mrc $gtc $gslr
        puts ""
    }
}

#------------------------------------------------------------------------------
# Suggest relocating a MAC into the GT's SLR.  HONORS IS_LOC_FIXED (fixed cells
# are never moved).  STRUCTURAL: adds a catch-wrapped set_property LOC to the
# XDC buffer for EVERY cross-SLR MAC that has a vacant compatible site in the
# GT's SLR - irrespective of timing/slack.
#------------------------------------------------------------------------------
proc _gtmr_suggest_one {mrc gtc gt_slr} {
    set mc [get_cells -quiet $mrc]
    if {[llength $mc] == 0} { puts "   (MAC cell not found)"; return }

    # HONOR IS_LOC_FIXED - never move a user-fixed MAC.
    if {[get_property -quiet IS_LOC_FIXED $mc] eq "1"} {
        puts "   IS_LOC_FIXED=1 -> user-fixed placement HONORED; NOT moved (no suggestion, no XDC)."
        return
    }

    set gt_site [get_sites -quiet -of_objects [get_cells -quiet $gtc]]
    set mr_site [get_sites -quiet -of_objects $mc]
    if {[llength $gt_site] == 0 || [llength $mr_site] == 0} {
        puts "   (GT or MAC unplaced - skipped)"; return
    }
    set stype [get_property -quiet SITE_TYPE $mr_site]
    set gt_cr [get_property -quiet CLOCK_REGION $gt_site]
    set gxy   [_gtmr_cr_xy $gt_cr]

    set vac {}
    foreach st [get_sites -quiet -filter "SITE_TYPE == $stype"] {
        if {[get_property -quiet IS_USED $st] eq "1"} { continue }
        set stslr [get_slrs -quiet -of_objects $st]
        if {[llength $stslr] == 0} { continue }
        if {[get_property -quiet NAME $stslr] ne $gt_slr} { continue }
        set cr  [get_property -quiet CLOCK_REGION $st]
        set cxy [_gtmr_cr_xy $cr]
        set d   -1
        if {[llength $gxy] == 2 && [llength $cxy] == 2} {
            set d [expr {abs([lindex $gxy 0]-[lindex $cxy 0]) + \
                         abs([lindex $gxy 1]-[lindex $cxy 1])}]
        }
        lappend vac [list $d $st $cr]
    }

    puts "   GT $gtc in SLR $gt_slr; MAC crosses SLR (structural - timing not checked)."

    if {[llength $vac] == 0} {
        puts "   no vacant '$stype' site free in GT SLR $gt_slr."
        puts "   -> add SLR-crossing pipeline (SLL) registers, or relocate the GT usage."
        if {![info exists ::gtmr_xdc_seen($mrc)]} {
            set ::gtmr_xdc_seen($mrc) 1
            lappend ::gtmr_xdc_lines "# MAC $mrc : cross-SLR but NO vacant $stype site in GT SLR $gt_slr - manual action."
        }
        return
    }

    set vac  [lsort -integer -index 0 $vac]
    set best [lindex [lindex $vac 0] 1]
    puts "   place MAC in GT SLR $gt_slr (GT clock region $gt_cr); vacant site(s), nearest first:"
    set cnt 0
    foreach e $vac {
        set d [lindex $e 0] ; set st [lindex $e 1] ; set cr [lindex $e 2]
        set dtxt [expr {$d < 0 ? "?" : $d}]
        puts "      $st  (CLOCK_REGION $cr, dist $dtxt)"
        if {[incr cnt] >= 5} { break }
    }
    puts "   e.g.  set_property LOC $best \[get_cells $mrc\]"

    if {![info exists ::gtmr_xdc_seen($mrc)]} {
        set ::gtmr_xdc_seen($mrc) 1
        incr ::gtmr_moves
        lappend ::gtmr_xdc_lines "# MAC $mrc -> relocate into GT SLR $gt_slr (GT clock region $gt_cr); cross-SLR"
        lappend ::gtmr_xdc_lines "catch { set_property LOC $best \[get_cells \{$mrc\}\] }"
        lappend ::gtmr_xdc_lines ""
    }
    puts "   (added to XDC: cross-SLR crossing, structural)"
}
