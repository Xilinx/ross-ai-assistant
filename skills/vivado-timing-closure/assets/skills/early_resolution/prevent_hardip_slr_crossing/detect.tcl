# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
###########################################################################
## detect.tcl
## HARD-IP LOCALITY CONSTRAINT GENERATOR - a PRE-PLACEMENT advisor.
##
## Automatically identifies locality-sensitive interfaces on BRAM / URAM / DSP
## primitives and emits USER_CROSSING_SLR=0 on the datapath nets incident on them so
## the placer/router keeps each datapath within a single SLR (minimizing unnecessary
## cross-SLR routing that hurts timing closure, congestion and repeatability).
##
## Scope = BRAM/URAM/DSP ONLY (movable primitives). USER_CROSSING_SLR 0 is HONORABLE
## here because BOTH endpoints can move, so the placer just co-locates them. The large
## fixed hard IP (GT, MRMAC, DCMAC, NoC, CPM, PCIe, PS, DDRMC, HBM, AIE) is intentionally OUT OF
## SCOPE: their wide, immovable interface buses over-constrain and the tools cannot
## honor per-net SLR hints on them - floorplan those with PBLOCKS instead.
##
## This is PRE-PLACE: selection is TOPOLOGY-DRIVEN (structural), not based on
## actual routed SLR crossings. A best-effort cross-SLR RISK check is done
## only for endpoints that already carry a fixed LOC (many hard IPs do).
##
## Flow (per the spec):
##   1. Discover hard-IP instances (IS_PRIMITIVE + ref patterns -> hb_class).
##   2. Identify critical interface pins (per-class pin-name patterns).
##   3. Trace the fan-in / fan-out net incident on each interface pin.
##   4. Classify (skip power/ground; separate clock pins; cross-SLR risk).
##   5. Generate USER_CROSSING_SLR=0 constraints (XDC).
##   6. Produce locality + cross-SLR risk + coverage reports.
##
## Public entry:  ::hipl::run_hardip_locality_analysis <outdir>
## READ-ONLY on the design (reads props/connectivity; writes report files only).
##
## Env tunables (HIPL_<NAME>):
##   HIPL_MODE=all|crossing   all = constrain every locality-sensitive net (default);
##                            crossing = only nets whose FIXED (LOC'd) endpoints span >1 SLR
##   HIPL_CONSTRAIN_CLK=0|1   also constrain hard-IP clock nets (default 0 = report only)
##   HIPL_ALL_PINS=0|1        ignore per-class interface patterns; use ALL non-clock
##                            signal pins of each hard IP (default 0)
##   HIPL_DO_SLR=0|1          best-effort fixed-endpoint cross-SLR risk check (default 1)
##   HIPL_SLR_FANOUT_CAP=<N>  skip the SLR check on nets with fanout > N (default 2000)
##   HIPL_SCOPE=<hier-prefix> only hard IPs under this hierarchy (fast portion run)
##   HIPL_CLASSES=<list>      restrict to these classes (e.g. "BRAM DSP"); default all
##   HIPL_MAX_NETS=<N>        safety cap on constrained nets (default 500000)
###########################################################################
namespace eval ::hipl {
    # broad ref-name superset for the initial get_cells (hb_class is the source of truth)
    variable HB_REFPATS {RAMB* URAM* URAM288* DSP* DSP48* DSP58*}
    # clock-ish interface pins: reported, NOT constrained unless HIPL_CONSTRAIN_CLK=1
    variable CLK_PATS {*USERCLK* *ACLK* *CLK* *CLK}
    # per-class CRITICAL interface pin-name patterns (globs on REF_PIN_NAME).
    # BRAM/URAM: the fabric data + address buses (not the narrow/shared control pins).
    # DSP: the A, B, C, D data inputs and P output buses (bare name OR indexed A[..]);
    #      the `[[]` idiom matches a literal '[' so ALUMODE[*]/CEx/CARRYIN are excluded.
    variable IFPAT
    array set IFPAT {
        BRAM {*DIN* *DOUT* *ADDR*}
        URAM {*DIN* *DOUT* *ADDR*}
        DSP  {A A[[]* B B[[]* C C[[]* D D[[]* P P[[]*}
    }

    # ---- tunables (env-overridable) ----
    variable MODE            all
    variable CONSTRAIN_CLK   0
    variable ALL_PINS        0
    variable DO_SLR          1
    variable SLR_FANOUT_CAP  2000
    variable SCOPE           ""
    variable CLASSES         ""
    variable MAX_NETS        500000

    proc env_or {name def} {
        set e "HIPL_$name"
        if {[info exists ::env($e)] && $::env($e) ne ""} { return $::env($e) }
        return $def
    }
}

## ---- hard-IP class of a ref name, or "" if not a supported primitive ----
proc ::hipl::hb_class {ref} {
    if {[string match RAMB* $ref]} { return BRAM }
    if {[string match URAM* $ref]} { return URAM }
    if {[string match DSP* $ref]}  { return DSP }
    return ""
}

## ---- SLR name of a cell IF it is LOC-fixed to a site, else "" (pre-place safe) ----
proc ::hipl::cell_slr {cell} {
    set loc [get_property -quiet LOC [get_cells -quiet $cell]]
    if {$loc eq ""} { return "" }
    set site [get_sites -quiet $loc]
    if {$site eq ""} { return "" }
    set slr [get_slrs -quiet -of_objects $site]
    if {$slr eq ""} { return "" }
    return [get_property -quiet NAME $slr]
}

## ---- best-effort cross-SLR risk of a net from its FIXED (LOC'd) endpoints ----
##   returns {crosses(0/1) slr_list}. Pre-place: only LOC-anchored cells contribute.
proc ::hipl::net_fixed_slrs {net fanoutCap} {
    set fo [get_property -quiet FLAT_PIN_COUNT [get_nets -quiet $net]]
    if {$fo ne "" && $fo > $fanoutCap} { return [list 0 {}] }
    set pins [get_pins -quiet -leaf -of [get_nets -quiet $net]]
    if {![llength $pins]} { return [list 0 {}] }
    set slrs {}
    foreach c [lsort -unique [get_property -quiet PARENT_CELL $pins]] {
        set s [cell_slr $c]
        if {$s ne ""} { lappend slrs $s }
    }
    set slrs [lsort -unique $slrs]
    return [list [expr {[llength $slrs] > 1 ? 1 : 0}] $slrs]
}

## ---- is this pin a clock-ish interface pin? ----
proc ::hipl::is_clock_pin {refpin} {
    variable CLK_PATS
    foreach p $CLK_PATS { if {[string match $p $refpin]} { return 1 } }
    return 0
}

## ---- resolve a pin's incident net to its TOP-LEVEL hierarchical segment ----
## USER_CROSSING_SLR is a per-net-OBJECT property. When a primitive output passes through a
## preserved IP boundary (e.g. a FIFO / DSP-macro / memory wrapper), the physical net exists as several
## segment objects - one per hierarchy level. get_nets -of returns the INNERMOST (pin-scope)
## alias, whose scope stays inside the IP, so a constraint on it is silently ignored by the
## placer. The placer keys SLR-locality off the TOP-LEVEL segment that owns the inter-SLR
## route. Pick the shallowest-hierarchy segment (fewest '/') so the crossing net is tagged.
proc ::hipl::pin_top_net {pin} {
    set segs [get_nets -quiet -segments -of $pin]
    if {[llength $segs] <= 1} { return [get_nets -quiet -of $pin] }
    set best "" ; set bestd 999999
    foreach n $segs {
        set d [llength [split [get_property -quiet NAME $n] /]]
        if {$d < $bestd} { set bestd $d ; set best $n }
    }
    return $best
}

###########################################################################
## main
###########################################################################
proc ::hipl::run_hardip_locality_analysis {outdir} {
    variable HB_REFPATS ; variable IFPAT ; variable CLK_PATS
    variable MODE ; variable CONSTRAIN_CLK ; variable ALL_PINS ; variable DO_SLR
    variable SLR_FANOUT_CAP ; variable SCOPE ; variable CLASSES ; variable MAX_NETS

    # env overrides
    set MODE           [env_or MODE           all]
    set CONSTRAIN_CLK  [env_or CONSTRAIN_CLK  0]
    set ALL_PINS       [env_or ALL_PINS       0]
    set DO_SLR         [env_or DO_SLR         1]
    set SLR_FANOUT_CAP [env_or SLR_FANOUT_CAP 2000]
    set SCOPE          [env_or SCOPE          ""]
    set CLASSES        [env_or CLASSES        ""]
    set MAX_NETS       [env_or MAX_NETS       500000]

    file mkdir $outdir
    set t0 [clock milliseconds]

    # -------- 1. discover hard-IP instances --------
    puts "\[hipl\] discovering hard-IP primitives..."
    set parts {}
    foreach p $HB_REFPATS { lappend parts "REF_NAME=~$p" }
    set cand [get_cells -hier -quiet -filter "IS_PRIMITIVE && ([join $parts { || }])"]
    # classify + optional class/scope filter
    array set HIPCLASS {}
    set nHIP 0
    foreach n [get_property -quiet NAME $cand] r [get_property -quiet REF_NAME $cand] {
        set cls [hb_class $r]
        if {$cls eq ""} continue
        if {$CLASSES ne "" && [lsearch -exact $CLASSES $cls] < 0} continue
        if {$SCOPE ne "" && !($n eq $SCOPE || [string match "${SCOPE}*" $n])} continue
        set HIPCLASS($n) $cls
        incr nHIP
    }
    puts "\[hipl\] hard-IP instances = $nHIP  (classes: [lsort -unique [array_vals HIPCLASS]])"
    if {!$nHIP} { error "no supported hard-IP instances found (SCOPE='$SCOPE' CLASSES='$CLASSES')" }

    # per-class instance tally
    array set CLSCNT {}
    foreach n [array names HIPCLASS] { incr CLSCNT($HIPCLASS($n)) }

    # -------- 2/3/4. interface pins -> incident nets --------
    # net-keyed info: NETCLASS NETCELL NETPIN NETDIR NETFO ; clock nets kept separate
    array set NETCLASS {} ; array set NETCELL {} ; array set NETPIN {} ; array set NETDIR {} ; array set NETFO {}
    array set CLKNET {}   ;# clock-ish interface nets (reported; constrained only if CONSTRAIN_CLK)
    set nPins 0
    foreach cell [array names HIPCLASS] {
        set cls $HIPCLASS($cell)
        # build the interface-pin filter for this class (or all non-clock pins)
        if {$ALL_PINS || ![info exists IFPAT($cls)]} {
            set ifpins [get_pins -quiet -of [get_cells -quiet $cell]]
        } else {
            set fp {}
            foreach p $IFPAT($cls) { lappend fp "REF_PIN_NAME =~ $p" }
            set ifpins [get_pins -quiet -of [get_cells -quiet $cell] -filter [join $fp { || }]]
        }
        foreach pin $ifpins {
            set refpin [get_property -quiet REF_PIN_NAME $pin]
            set net [pin_top_net $pin]
            if {$net eq ""} continue
            set nm [get_property -quiet NAME $net]
            set tp [get_property -quiet TYPE $net]
            if {$tp eq "POWER" || $tp eq "GROUND"} continue
            incr nPins
            if {[is_clock_pin $refpin]} {
                if {!$CONSTRAIN_CLK} { set CLKNET($nm) [list $cls $cell $refpin] ; continue }
            }
            if {[info exists NETCLASS($nm)]} continue   ;# already recorded (dedup)
            if {[array size NETCLASS] >= $MAX_NETS} continue
            set NETCLASS($nm) $cls ; set NETCELL($nm) $cell ; set NETPIN($nm) $refpin
            set NETDIR($nm) [get_property -quiet DIRECTION $pin]
            set fo [get_property -quiet FLAT_PIN_COUNT $net]
            set NETFO($nm) [expr {$fo eq "" ? 0 : $fo}]
        }
    }
    puts "\[hipl\] scanned interface pins=$nPins  candidate signal nets=[array size NETCLASS]  clock nets=[array size CLKNET]"

    # -------- best-effort cross-SLR risk (fixed endpoints only) --------
    array set XSLR {} ; array set XSLRLIST {}
    set nCross 0
    if {$DO_SLR} {
        puts "\[hipl\] best-effort fixed-endpoint cross-SLR risk check..."
        foreach nm [array names NETCLASS] {
            lassign [net_fixed_slrs $nm $SLR_FANOUT_CAP] cx slrs
            set XSLR($nm) $cx ; set XSLRLIST($nm) $slrs
            if {$cx} { incr nCross }
        }
        puts "\[hipl\]   nets crossing SLR by fixed endpoints = $nCross"
    }

    # -------- 5. select nets to constrain per MODE --------
    set constrained {}
    foreach nm [array names NETCLASS] {
        if {$MODE eq "crossing"} {
            if {![info exists XSLR($nm)] || !$XSLR($nm)} continue
        }
        lappend constrained $nm
    }
    puts "\[hipl\] MODE=$MODE -> nets to constrain = [llength $constrained]"

    # -------- 6. write outputs --------
    write_outputs $outdir constrained NETCLASS NETCELL NETPIN NETDIR NETFO XSLR XSLRLIST \
                  CLKNET CLSCNT $nHIP $nCross [expr {[clock milliseconds]-$t0}]
    return [llength $constrained]
}

## small helper: values of an array as a list
proc ::hipl::array_vals {arrName} {
    upvar 1 $arrName A
    set v {}
    foreach k [array names A] { lappend v $A($k) }
    return $v
}

###########################################################################
## report writers
###########################################################################
proc ::hipl::write_outputs {outdir consVar clsVar cellVar pinVar dirVar foVar xslrVar xlistVar \
                            clknetVar clscntVar nHIP nCross totms} {
    upvar 1 $consVar constrained $clsVar NETCLASS $cellVar NETCELL $pinVar NETPIN \
            $dirVar NETDIR $foVar NETFO $xslrVar XSLR $xlistVar XSLRLIST \
            $clknetVar CLKNET $clscntVar CLSCNT
    variable MODE ; variable CONSTRAIN_CLK ; variable ALL_PINS ; variable DO_SLR ; variable SCOPE

    # sort constrained nets by class then fanout desc
    set constrained [lsort -command [list ::hipl::net_cmp NETCLASS NETFO] $constrained]

    # ---- fix.xdc (persistent constraints) + fix.tcl (sourceable, self-applying) ----
    set xdc  [open [file join $outdir fix.xdc] w]
    set tclf [open [file join $outdir fix.tcl] w]
    puts $xdc "# fix.xdc - AUTO-GENERATED by detect.tcl"
    puts $xdc "# PRE-PLACEMENT locality hints: keep hard-IP interface nets within one SLR."
    puts $xdc "#   set_property -quiet USER_CROSSING_SLR 0 \[get_nets {<net>}\]"
    puts $xdc "# Add to the constraint set (read_xdc / add_files -fileset constrs_1) BEFORE place_design.\n"
    puts $tclf "## fix.tcl - AUTO-GENERATED by detect.tcl (sourceable form of fix.xdc)."
    puts $tclf "## Source this to APPLY the USER_CROSSING_SLR 0 locality hints to the open design"
    puts $tclf "## (before place_design). Same assignments as fix.xdc.\n"
    set ntag 0
    set lastcls ""
    foreach nm $constrained {
        if {$NETCLASS($nm) ne $lastcls} {
            puts $xdc  "\n## ---- $NETCLASS($nm) ----"
            puts $tclf "\n# ---- $NETCLASS($nm) ----"
            set lastcls $NETCLASS($nm)
        }
        set risk [expr {([info exists XSLR($nm)] && $XSLR($nm)) ? " ;# CROSS-SLR risk (fixed endpoints: $XSLRLIST($nm))" : ""}]
        puts $xdc  "set_property -quiet USER_CROSSING_SLR 0 \[get_nets {$nm}\]$risk"
        puts $tclf "set_property -quiet USER_CROSSING_SLR 0 \[get_nets {$nm}\]$risk"
        incr ntag
    }
    if {$CONSTRAIN_CLK} {
        foreach nm [array names CLKNET] {
            lassign $CLKNET($nm) cls cell refpin
            puts $xdc  "set_property -quiet USER_CROSSING_SLR 0 \[get_nets {$nm}\]  ;# $cls clock $refpin"
            puts $tclf "set_property -quiet USER_CROSSING_SLR 0 \[get_nets {$nm}\]  ;# $cls clock $refpin"
            incr ntag
        }
    }
    puts $tclf "\nputs \"\\\[hipl\\\] applied USER_CROSSING_SLR 0 on $ntag hard-IP interface nets\""
    close $xdc
    close $tclf

    # ---- CSV ----
    set csv [open [file join $outdir hardip_locality.csv] w]
    puts $csv "net,hardip_class,hardip_cell,interface_pin,direction,fanout,cross_slr,slr_list,constrained"
    foreach nm $constrained {
        set cx [expr {[info exists XSLR($nm)] ? $XSLR($nm) : ""}]
        set sl [expr {[info exists XSLRLIST($nm)] ? $XSLRLIST($nm) : ""}]
        puts $csv "\"$nm\",$NETCLASS($nm),\"$NETCELL($nm)\",$NETPIN($nm),$NETDIR($nm),$NETFO($nm),$cx,\"$sl\",1"
    }
    close $csv

    # ---- hard-IP locality report (per class) ----
    set rpt [open [file join $outdir hardip_locality.rpt] w]
    puts $rpt "############################################################"
    puts $rpt "## HARD-IP LOCALITY REPORT (pre-placement)"
    puts $rpt "############################################################"
    puts $rpt "hard-IP instances : $nHIP    (SCOPE='$SCOPE')"
    puts $rpt "mode              : $MODE   (all=constrain every interface net; crossing=fixed-endpoint SLR span only)"
    puts $rpt "constrain clocks  : $CONSTRAIN_CLK    all-pins mode : $ALL_PINS    slr-check : $DO_SLR"
    puts $rpt ""
    puts $rpt "hard-IP instances by class:"
    foreach c [lsort [array names CLSCNT]] { puts $rpt [format "  %-6s : %d" $c $CLSCNT($c)] }
    puts $rpt ""
    # constrained-net count per class
    array set PERCLS {}
    foreach nm $constrained { incr PERCLS($NETCLASS($nm)) }
    puts $rpt "constrained interface nets by class (USER_CROSSING_SLR 0):"
    foreach c [lsort [array names PERCLS]] { puts $rpt [format "  %-6s : %d" $c $PERCLS($c)] }
    close $rpt

    # ---- cross-SLR risk report ----
    set risk [open [file join $outdir crossslr_risk.rpt] w]
    puts $risk "############################################################"
    puts $risk "## CROSS-SLR RISK (best-effort, fixed LOC endpoints only; pre-place)"
    puts $risk "############################################################"
    if {!$DO_SLR} {
        puts $risk "SLR check disabled (HIPL_DO_SLR=0)."
    } else {
        puts $risk "nets whose FIXED (LOC-anchored) endpoints already span >1 SLR : $nCross"
        puts $risk ""
        puts $risk [format "  %-6s %-10s %-40s %s" CLASS SLRS PIN NET]
        set shown 0
        foreach nm $constrained {
            if {![info exists XSLR($nm)] || !$XSLR($nm)} continue
            puts $risk [format "  %-6s %-10s %-40s %s" $NETCLASS($nm) $XSLRLIST($nm) $NETPIN($nm) $nm]
            if {[incr shown] >= 200} { puts $risk "  ... (more in hardip_locality.csv)" ; break }
        }
    }
    close $risk

    # ---- coverage summary + recommendations ----
    set sum [open [file join $outdir hardip_locality_summary.rpt] w]
    puts $sum "############################################################"
    puts $sum "## HARD-IP LOCALITY CONSTRAINT GENERATOR - SUMMARY"
    puts $sum "############################################################"
    puts $sum "hard-IP instances analyzed : $nHIP"
    puts $sum "constrained interface nets : [llength $constrained]   (mode=$MODE)"
    puts $sum "clock interface nets       : [array size CLKNET]   (constrained=$CONSTRAIN_CLK)"
    puts $sum "fixed-endpoint cross-SLR   : $nCross"
    puts $sum "analysis time              : [format %.1f [expr {$totms/1000.0}]]s"
    puts $sum ""
    puts $sum "coverage by class (instances | constrained nets):"
    foreach c [lsort [array names CLSCNT]] {
        set cn [expr {[info exists PERCLS($c)] ? $PERCLS($c) : 0}]
        puts $sum [format "  %-6s : %4d instances | %6d nets" $c $CLSCNT($c) $cn]
    }
    puts $sum ""
    puts $sum "recommendations:"
    puts $sum "  * source fix.tcl to apply now, or add fix.xdc to the constraint set (read_xdc),"
    puts $sum "    BEFORE place_design (USER_CROSSING_SLR is a pre-place locality hint); re-run P&R."
    puts $sum "  * Start with MODE=crossing on a placed reference to target only real SLR spans,"
    puts $sum "    or MODE=all pre-place to keep all hard-IP interfaces local."
    puts $sum "  * If a class is over-constrained (e.g. a wide DSP cascade), use HIPL_CLASSES / HIPL_SCOPE"
    puts $sum "    to scope, or tighten the per-class interface patterns."
    puts $sum "  * Clock nets are reported but not constrained by default (HIPL_CONSTRAIN_CLK=1"
    puts $sum "    to include them)."
    close $sum

    puts "\[hipl\] wrote: fix.tcl  fix.xdc  hardip_locality.csv  hardip_locality.rpt  crossslr_risk.rpt  hardip_locality_summary.rpt"
    puts "\[hipl\] constrained=[llength $constrained]  clock_nets=[array size CLKNET]  cross_slr=$nCross"
    puts "\[hipl\] to apply now:  source [file join $outdir fix.tcl]   |   for future runs add [file join $outdir fix.xdc] to the constraint set"
}

proc ::hipl::net_cmp {clsVar foVar a b} {
    upvar 1 $clsVar NETCLASS $foVar NETFO
    set ca $NETCLASS($a) ; set cb $NETCLASS($b)
    if {$ca ne $cb} { return [string compare $ca $cb] }
    set fa $NETFO($a) ; set fb $NETFO($b)
    if {$fa > $fb} { return -1 } ; if {$fa < $fb} { return 1 } ; return 0
}

## ---------------------------------------------------------------------------
## Auto-run on `source`: analyze the CURRENTLY OPEN design and write the locality
## constraints to <OUTDIR>/fix.tcl + <OUTDIR>/fix.xdc (default HIPL_OUTDIR=".").
## Then:  source fix.tcl   to apply now, or add fix.xdc to the constraint set for
## future runs. Set env HIPL_NO_AUTORUN=1 to only load the procs (then call
## ::hipl::run_hardip_locality_analysis <outdir> yourself).
## ---------------------------------------------------------------------------
if {![info exists ::env(HIPL_NO_AUTORUN)] || $::env(HIPL_NO_AUTORUN) eq "0"} {
    if {[catch {current_design} ::hipl::_cd] || $::hipl::_cd eq ""} {
        puts "\[hipl\] no open design - open a checkpoint first, then: source detect.tcl"
    } else {
        ::hipl::run_hardip_locality_analysis [::hipl::env_or OUTDIR "."]
    }
}
