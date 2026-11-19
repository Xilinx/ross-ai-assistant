# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
###########################################################################
## detect_srl_boundary.tcl
## SRL BOUNDARY OPTIMIZATION ADVISOR - find TIMING-UNFRIENDLY SRL boundaries on a
## POST-OPT / PRE-PLACE Versal checkpoint (estimated pre-route timing) and recommend
## converting them into REGISTER-to-REGISTER boundaries by PULLING an input/output
## register out of the SRL.
##
## An SRL (SRL16E/SRLC32E, PRIMITIVE_TYPE CLB.SRL.*) is a poor timing boundary when it
## acts as a path STARTPOINT (Q launches a path) or ENDPOINT (D captures a path) -
## retiming, register balancing, placement and replication all work best when a path
## is bounded by real FLOPS, not by an SRL. This advisor emits
## SRL_STAGES_TO_REG_{OUTPUT,INPUT} tags.
##
## DETECTION PRIORITIES (highest first):
##   P1  SRL <-> HARD BLOCK  (STRUCTURAL - NO TIMING GATE): an SRL Q reaches a hard-
##       block input (SRL -> N*logic -> RAMB, URAM, DSP, GT, MRMAC, DCMAC, PCIe, CPM, HSC) OR a
##       hard-block output reaches an SRL D (HB -> N*logic -> SRL). Hard blocks are
##       fixed-placement / low routing flex, so ANY SRL<->HB boundary is pulled
##       regardless of slack. Rec: SRL_STAGES_TO_REG_OUTPUT (SRL->HB) /
##       SRL_STAGES_TO_REG_INPUT (HB->SRL).
##   P2  CRITICAL SRL STARTPOINT (TIMING): SRL -> N*LUT -> FF and the path is FAILING or
##       NEAR-MEETING (slack <= SLACK_MAX, default +0.3ns). Rec: SRL_STAGES_TO_REG_OUTPUT.
##   P3  CRITICAL SRL ENDPOINT   (TIMING): FF -> N*LUT -> SRL D, slack <= SLACK_MAX.
##       Rec: SRL_STAGES_TO_REG_INPUT.
##   P5  DFX PARTITION BOUNDARY  : SRL and its path neighbor sit in different DFX
##       partitions (RP<->Static). Rec: register the partition interface.
##   P6  HIGH-FANOUT SRL OUTPUT  : SRL Q net FLAT_PIN_COUNT >= HIGH_FANOUT.
##       Rec: SRL_STAGES_TO_REG_OUTPUT (register for replication / fanout mgmt).
##   P7  GENERAL CLEANUP         : non-critical SRL->FF / FF->SRL (summarized only).
##   P8  SHALLOW SRL (depth<=SHALLOW_DEPTH, default 2): a shift register this short is
##       better as discrete flops - detected via the "_srl<N>" cell-name suffix or by
##       constant address-pin value (depth = addr+1). Emits set_property SRL_TO_REG 1
##       on the whole SRL cell (supersedes the side-based tags for that cell).
##   (P4 cross-SLR is N/A on a single-die post-opt DCP and is skipped.)
##
## Public entry:  run_srl_boundary_analysis <outdir>
## READ-ONLY on the design (reads props/connectivity/timing; emits files only).
###########################################################################
namespace eval ::srlb {
    # ---- tunables (env-overridable via SRLB_<NAME>) ----
    variable SLACK_MAX    0.300   ;# P2/P3 gate: flag SRL<->FF paths with slack <= this (failing + near-meeting)
    variable HB_MAX_LEVELS 0      ;# P1 SRL<->HB logic-level bound (0 = unlimited: pull ALL SRL<->HB)
    variable MAX_PATHS    20000   ;# cap on -from/-to timing path queries
    variable HIGH_FANOUT  1000    ;# P6 SRL-Q fanout threshold
    variable MED_FANOUT   100     ;# near-fanout note threshold
    variable SCOPE        ""      ;# hier-prefix: only SRLs under it (fast portion run; ""=whole design)
    variable DO_PARTITION 1       ;# 1 = flag cross-DFX-partition boundaries (P5)
    variable WARMUP       1       ;# 1 = run a throwaway get_timing_paths to trigger update_timing
    variable DO_SHALLOW   1       ;# P8: 1 = flag shallow SRLs (depth <= SHALLOW_DEPTH) -> SRL_TO_REG
    variable SHALLOW_DEPTH 2      ;# P8: max SRL depth treated as "shallow" (convert whole SRL to registers)
    # hard-block primitive ref patterns (matched with IS_PRIMITIVE to avoid module names)
    variable HB_PATTERNS {RAMB* URAM* DSP* GT*QUAD GT*CHANNEL *MRMAC* *DCMAC* *PCIE* CPM5* *HSC*}

    proc env_or {name def} {
        set e "SRLB_$name"
        if {[info exists ::env($e)] && $::env($e) ne ""} { return $::env($e) }
        return $def
    }
}

## ---- strip a pin name to its owning cell name (drop trailing /<pin>) ----
proc ::srlb::pin_cell {pin} {
    regsub {/[^/]+$} $pin {} c
    return $c
}

## ---- hard-block class of a ref name, or "" if not a hard block ----
proc ::srlb::hb_class {ref} {
    if {[string match RAMB* $ref]}   { return BRAM }
    if {[string match URAM* $ref]}   { return URAM }
    if {[string match DSP* $ref]}    { return DSP }
    if {[string match *MRMAC* $ref]} { return MRMAC }
    if {[string match *DCMAC* $ref]} { return DCMAC }
    if {[string match *PCIE* $ref]}  { return PCIE }
    if {[string match CPM5* $ref]}   { return CPM }
    if {[string match *HSC* $ref]}   { return HSC }
    if {[string match GT* $ref] && ([string match *QUAD* $ref] || [string match *CHANNEL* $ref])} { return GT }
    return ""
}

## ---- DFX partition id of a cell name (name-prefix vs ::srlb::RPS) ----
proc ::srlb::partition_of {name} {
    variable RPS
    foreach rp $RPS { if {$name eq $rp || [string match "$rp/*" $name]} { return $rp } }
    return TOP
}

## ---- cached ref-name lookup for arbitrary neighbor cells ----
proc ::srlb::ref_of {name} {
    variable REFCACHE
    variable HBREF
    variable SRLREF
    if {[info exists SRLREF($name)]} { return $SRLREF($name) }
    if {[info exists HBREF($name)]}  { return $HBREF($name) }
    if {[info exists REFCACHE($name)]} { return $REFCACHE($name) }
    set r [get_property -quiet REF_NAME [get_cells -quiet $name]]
    set REFCACHE($name) $r
    return $r
}

## ---- neighbor classification label from a cell name ----
proc ::srlb::classify {name} {
    set ref [ref_of $name]
    set hb  [hb_class $ref]
    if {$hb ne ""} { return "HB:$ref" }
    if {[string match SRL* $ref]}    { return "SRL:$ref" }
    if {[string match FD* $ref] || [string match *LATCH* $ref]} { return "FF:$ref" }
    if {[string match LUT* $ref] || [string match MUXF* $ref] || [string match CARRY* $ref]} { return "LOGIC:$ref" }
    if {$ref eq ""} { return "PORT/UNKNOWN" }
    return "OTHER:$ref"
}

## ---- SRL depth from the cell-name suffix "_srl<N>" (e.g. _srl2 -> 2), or "" ----
proc ::srlb::srl_depth_by_name {name} {
    if {[regexp {_srl(\d+)$} $name -> n]} { return $n }
    return ""
}

## ---- SRL depth from constant address pins (A0=LSB): depth = addr_value + 1 ----
##      returns "" if any address pin is NOT tied to a constant (dynamic/addressable SRL)
proc ::srlb::srl_depth_by_addr {name} {
    set apins [get_pins -quiet -of [get_cells -quiet $name] -filter {REF_PIN_NAME =~ A*}]
    if {![llength $apins]} { return "" }
    set val 0
    foreach ap $apins {
        if {![regexp {A(\d+)} [get_property -quiet REF_PIN_NAME $ap] -> bit]} { continue }
        set net [get_nets -quiet -of $ap]
        if {$net eq ""} { return "" }
        set nt [get_property -quiet TYPE $net]
        if {$nt eq "POWER"} {
            set val [expr {$val | (1 << $bit)}]
        } elseif {$nt eq "GROUND"} {
            # bit stays 0
        } else {
            return ""   ;# driven by real logic -> addressable, not a fixed shallow SRL
        }
    }
    return [expr {$val + 1}]
}

## ---- SRL depth: prefer the fast name suffix, else compute from address pins ----
proc ::srlb::srl_depth {name} {
    set d [srl_depth_by_name $name]
    if {$d ne ""} { return $d }
    return [srl_depth_by_addr $name]
}

###########################################################################
## main
###########################################################################
proc ::srlb::run_srl_boundary_analysis {outdir} {
    variable SLACK_MAX ; variable HB_MAX_LEVELS ; variable MAX_PATHS
    variable HIGH_FANOUT ; variable MED_FANOUT ; variable SCOPE
    variable DO_PARTITION ; variable WARMUP ; variable HB_PATTERNS
    variable DO_SHALLOW ; variable SHALLOW_DEPTH
    variable RPS ; variable HBREF ; variable SRLREF ; variable REFCACHE

    # apply env overrides (literal defaults -> idempotent across re-runs in one session)
    set SLACK_MAX   [env_or SLACK_MAX   0.300]
    set HB_MAX_LEVELS [env_or HB_MAX_LEVELS 0]
    set MAX_PATHS   [env_or MAX_PATHS   20000]
    set HIGH_FANOUT [env_or HIGH_FANOUT 1000]
    set MED_FANOUT  [env_or MED_FANOUT  100]
    set SCOPE       [env_or SCOPE       ""]
    set DO_PARTITION [env_or DO_PARTITION 1]
    set WARMUP      [env_or WARMUP      1]
    set DO_SHALLOW  [env_or DO_SHALLOW  1]
    set SHALLOW_DEPTH [env_or SHALLOW_DEPTH 2]

    file mkdir $outdir
    set t0 [clock milliseconds]
    array unset REFCACHE ; array unset HBREF ; array unset SRLREF

    puts "\[srlb\] collecting SRL primitives..."
    set srlFilter {IS_PRIMITIVE && PRIMITIVE_TYPE =~ CLB.SRL.*}
    set srls [get_cells -hier -quiet -filter $srlFilter]
    if {$SCOPE ne ""} {
        set kept {}
        foreach n [get_property -quiet NAME $srls] {
            if {$n eq $SCOPE || [string match "${SCOPE}*" $n]} { lappend kept $n }
        }
        set srls [get_cells -quiet $kept]
    }
    set nSRL [llength $srls]
    puts "\[srlb\] SRL primitives = $nSRL"
    if {!$nSRL} { error "no SRL primitives found (SCOPE='$SCOPE')" }

    # bulk ref map of SRLs
    foreach n [get_property -quiet NAME $srls] r [get_property -quiet REF_NAME $srls] { set SRLREF($n) $r }

    # Q/Q31 (out) and D (in) pins
    set qpins [get_pins -quiet -of $srls -filter {REF_PIN_NAME==Q || REF_PIN_NAME==Q31}]
    set dpins [get_pins -quiet -of $srls -filter {REF_PIN_NAME==D}]
    puts "\[srlb\] SRL Q/Q31 pins=[llength $qpins]  D pins=[llength $dpins]"

    # hard-block primitive ref map (name -> ref) for fast neighbor lookup
    set hbFilterParts {}
    foreach p $HB_PATTERNS { lappend hbFilterParts "REF_NAME=~$p" }
    set hbs [get_cells -hier -quiet -filter "IS_PRIMITIVE && ([join $hbFilterParts { || }])"]
    foreach n [get_property -quiet NAME $hbs] r [get_property -quiet REF_NAME $hbs] {
        if {[hb_class $r] ne ""} { set HBREF($n) $r }
    }
    puts "\[srlb\] hard-block primitives = [array size HBREF]"
    # hard-block IN/OUT pins (used to bound the structural SRL<->HB timing queries)
    set hbIn  [get_pins -quiet -of $hbs -filter {DIRECTION==IN}]
    set hbOut [get_pins -quiet -of $hbs -filter {DIRECTION==OUT}]
    puts "\[srlb\]   hbIn pins=[llength $hbIn]  hbOut pins=[llength $hbOut]"

    # DFX partitions (for P5)
    set RPS {}
    if {$DO_PARTITION} {
        set rpc [get_cells -hier -quiet -filter {HD.RECONFIGURABLE}]
        foreach n [get_property -quiet NAME $rpc] { lappend RPS $n }
        set RPS [lsort -command {apply {{a b} {expr {[string length $b]-[string length $a]}}}} $RPS]
        puts "\[srlb\] DFX reconfigurable partitions = [llength $RPS]  ($RPS)"
    }

    # per-SRL accumulators (one field-array per boundary kind):
    #   P1 SRL<->HARD BLOCK (structural, no slack gate)
    array set P1O_HB {} ; array set P1O_SLK {} ; array set P1O_LL {}   ;# SRL -> HB  (pull OUTPUT reg)
    array set P1I_HB {} ; array set P1I_SLK {} ; array set P1I_LL {}   ;# HB  -> SRL (pull INPUT  reg)
    #   P2/P3 SRL<->FF (timing-gated, slack <= SLACK_MAX)
    array set P2_NBR {} ; array set P2_SLK {} ; array set P2_LL {}     ;# SRL -> N*LUT -> FF (pull OUTPUT)
    array set P3_NBR {} ; array set P3_SLK {} ; array set P3_LL {}     ;# FF  -> N*LUT -> SRL (pull INPUT)
    array set FANOUT    {}

    # warmup timing engine (first call triggers update_timing ~ minutes on big designs)
    if {$WARMUP} {
        puts "\[srlb\] warming up timing engine (update_timing)..."
        set tw [clock milliseconds]
        catch { get_timing_paths -quiet -max_paths 1 -delay_type max }
        puts "\[srlb\]   warmup dt=[expr {[clock milliseconds]-$tw}]ms"
    }

    # =====================================================================
    # P1 (STRUCTURAL, NO TIMING GATE): SRL <-> HARD BLOCK
    #   The -from/-to constraint IS the structure - any SRL<->HB path qualifies.
    # =====================================================================
    # --- P1 SRL -> HB : SRL Q reaches a hard-block input pin ---
    puts "\[srlb\] P1 SRL->HB : -from Q -to hbIn (no slack gate)..."
    set tt [clock milliseconds]
    set p1o {}
    if {[llength $qpins] && [llength $hbIn]} {
        set p1o [get_timing_paths -quiet -from $qpins -to $hbIn -nworst 1 -max_paths $MAX_PATHS -delay_type max]
    }
    foreach p $p1o {
        set srl [pin_cell [get_property -quiet STARTPOINT_PIN $p]]
        if {![info exists SRLREF($srl)]} continue
        set ll [get_property -quiet LOGIC_LEVELS $p]
        if {$HB_MAX_LEVELS > 0 && $ll ne "" && $ll > $HB_MAX_LEVELS} continue
        set slk [get_property -quiet SLACK $p]
        if {![info exists P1O_SLK($srl)] || ($slk ne "" && $slk < $P1O_SLK($srl))} {
            set P1O_SLK($srl) $slk ; set P1O_HB($srl) [pin_cell [get_property -quiet ENDPOINT_PIN $p]] ; set P1O_LL($srl) $ll
        }
    }
    puts "\[srlb\]   [llength $p1o] paths -> [array size P1O_HB] distinct SRLs  dt=[expr {[clock milliseconds]-$tt}]ms"

    # --- P1 HB -> SRL : a hard-block output reaches SRL D ---
    puts "\[srlb\] P1 HB->SRL : -from hbOut -to D (no slack gate)..."
    set tt [clock milliseconds]
    set p1i {}
    if {[llength $dpins] && [llength $hbOut]} {
        set p1i [get_timing_paths -quiet -from $hbOut -to $dpins -nworst 1 -max_paths $MAX_PATHS -delay_type max]
    }
    foreach p $p1i {
        set srl [pin_cell [get_property -quiet ENDPOINT_PIN $p]]
        if {![info exists SRLREF($srl)]} continue
        set ll [get_property -quiet LOGIC_LEVELS $p]
        if {$HB_MAX_LEVELS > 0 && $ll ne "" && $ll > $HB_MAX_LEVELS} continue
        set slk [get_property -quiet SLACK $p]
        if {![info exists P1I_SLK($srl)] || ($slk ne "" && $slk < $P1I_SLK($srl))} {
            set P1I_SLK($srl) $slk ; set P1I_HB($srl) [pin_cell [get_property -quiet STARTPOINT_PIN $p]] ; set P1I_LL($srl) $ll
        }
    }
    puts "\[srlb\]   [llength $p1i] paths -> [array size P1I_HB] distinct SRLs  dt=[expr {[clock milliseconds]-$tt}]ms"

    # =====================================================================
    # P2 / P3 (TIMING-GATED): SRL <-> FF, slack <= SLACK_MAX (failing + near-meeting)
    #   HB-neighbor paths are excluded here (already covered by P1); SRL-neighbor skipped.
    # =====================================================================
    # --- P2 SRL -> N*LUT -> FF ---
    puts "\[srlb\] P2 SRL->FF : -from Q slack<=$SLACK_MAX ..."
    set tt [clock milliseconds]
    set p2 {}
    if {[llength $qpins]} {
        set p2 [get_timing_paths -quiet -from $qpins -nworst 1 -max_paths $MAX_PATHS \
                  -slack_lesser_than [expr {$SLACK_MAX + 1e-6}] -delay_type max]
    }
    foreach p $p2 {
        set slk [get_property -quiet SLACK $p]
        if {$slk eq "" || $slk > $SLACK_MAX} continue
        set srl [pin_cell [get_property -quiet STARTPOINT_PIN $p]]
        if {![info exists SRLREF($srl)]} continue
        set nbr [pin_cell [get_property -quiet ENDPOINT_PIN $p]]
        if {[info exists HBREF($nbr)] || [info exists SRLREF($nbr)]} continue  ;# HB=>P1, SRL=>skip
        if {![info exists P2_SLK($srl)] || $slk < $P2_SLK($srl)} {
            set P2_SLK($srl) $slk ; set P2_NBR($srl) $nbr ; set P2_LL($srl) [get_property -quiet LOGIC_LEVELS $p]
        }
    }
    puts "\[srlb\]   [array size P2_NBR] SRL->FF SRLs (slack<=$SLACK_MAX)  dt=[expr {[clock milliseconds]-$tt}]ms"

    # --- P3 FF -> N*LUT -> SRL ---
    puts "\[srlb\] P3 FF->SRL : -to D slack<=$SLACK_MAX ..."
    set tt [clock milliseconds]
    set p3 {}
    if {[llength $dpins]} {
        set p3 [get_timing_paths -quiet -to $dpins -nworst 1 -max_paths $MAX_PATHS \
                  -slack_lesser_than [expr {$SLACK_MAX + 1e-6}] -delay_type max]
    }
    foreach p $p3 {
        set slk [get_property -quiet SLACK $p]
        if {$slk eq "" || $slk > $SLACK_MAX} continue
        set srl [pin_cell [get_property -quiet ENDPOINT_PIN $p]]
        if {![info exists SRLREF($srl)]} continue
        set nbr [pin_cell [get_property -quiet STARTPOINT_PIN $p]]
        if {[info exists HBREF($nbr)] || [info exists SRLREF($nbr)]} continue  ;# HB=>P1, SRL=>skip
        if {![info exists P3_SLK($srl)] || $slk < $P3_SLK($srl)} {
            set P3_SLK($srl) $slk ; set P3_NBR($srl) $nbr ; set P3_LL($srl) [get_property -quiet LOGIC_LEVELS $p]
        }
    }
    puts "\[srlb\]   [array size P3_NBR] FF->SRL SRLs (slack<=$SLACK_MAX)  dt=[expr {[clock milliseconds]-$tt}]ms"

    # ---- P6 : high-fanout SRL Q nets ----
    puts "\[srlb\] measuring SRL-Q fanout..."
    set tt [clock milliseconds]
    set qnets [get_nets -quiet -of $qpins]
    set fos   [get_property -quiet FLAT_PIN_COUNT $qnets]
    set medNets {}
    foreach net $qnets fo $fos {
        if {$fo eq "" || $fo < $MED_FANOUT} continue
        lappend medNets [list $net $fo]
    }
    # map high/med-fanout net -> driver SRL (only a handful, cheap)
    foreach item $medNets {
        lassign $item net fo
        set drv [get_cells -quiet -of [get_pins -quiet -of [get_nets -quiet $net] -filter {DIRECTION==OUT}]]
        foreach d [get_property -quiet NAME $drv] {
            if {[info exists SRLREF($d)]} {
                if {![info exists FANOUT($d)] || $fo > $FANOUT($d)} { set FANOUT($d) $fo }
            }
        }
    }
    puts "\[srlb\]   nets>=MED($MED_FANOUT)=[llength $medNets]  dt=[expr {[clock milliseconds]-$tt}]ms"

    # count med/high fanout SRLs for the summary (P6 rows = high only)
    variable NHIGH ; variable NMED
    set NHIGH 0 ; set NMED 0
    foreach d [array names FANOUT] {
        if {$FANOUT($d) >= $HIGH_FANOUT} { incr NHIGH } elseif {$FANOUT($d) >= $MED_FANOUT} { incr NMED }
    }

    # ---- P8 : shallow SRLs (depth <= SHALLOW_DEPTH) -> convert whole SRL to registers ----
    array set SHALLOW {}
    if {$DO_SHALLOW} {
        foreach n [get_property -quiet NAME $srls] {
            set d [srl_depth $n]
            if {$d ne "" && $d <= $SHALLOW_DEPTH} { set SHALLOW($n) $d }
        }
        puts "\[srlb\] P8 shallow SRLs (depth<=$SHALLOW_DEPTH) = [array size SHALLOW]"
    }

    # ---- assemble flagged set ----
    array set FLAG {}
    foreach a [array names P1O_HB]  { set FLAG($a) 1 }
    foreach a [array names P1I_HB]  { set FLAG($a) 1 }
    foreach a [array names P2_NBR]  { set FLAG($a) 1 }
    foreach a [array names P3_NBR]  { set FLAG($a) 1 }
    foreach a [array names FANOUT]  { set FLAG($a) 1 }

    # rows: {srl ref priority prio_rank side slack ll nbr nbr_class fanout part cross rec tagprop}
    set rows {}
    array set PCOUNT {P1 0 P2 0 P3 0 P5 0 P6 0 P8 0}

    # P8 rows first: a shallow SRL is converted as a WHOLE cell (SRL_TO_REG) and is
    # therefore excluded from the side-based (SRL_STAGES_TO_REG_*) priorities below.
    foreach srl [lsort [array names SHALLOW]] {
        set ref $SRLREF($srl)
        set part [expr {$DO_PARTITION ? [partition_of $srl] : "-"}]
        set d $SHALLOW($srl)
        lappend rows [list $srl $ref P8 4 "shallow-srl" "" "" "-" "depth=$d" 0 $part "no" [shallow_rec $d] SRL_TO_REG]
        incr PCOUNT(P8)
    }

    foreach srl [array names FLAG] {
        if {[info exists SHALLOW($srl)]} continue   ;# depth<=SHALLOW_DEPTH -> handled by P8 (SRL_TO_REG)
        set ref $SRLREF($srl)
        set part [expr {$DO_PARTITION ? [partition_of $srl] : "-"}]
        set fan  [expr {[info exists FANOUT($srl)] ? $FANOUT($srl) : 0}]

        # ============ OUTPUT side (Q) : P1 SRL->HB > P2 SRL->FF > P6 fanout ============
        set prio "" ; set rank 99 ; set side "" ; set slk "" ; set ll "" ; set nbr ""
        if {[info exists P1O_HB($srl)]} {
            set prio P1 ; set rank 1 ; set side "SRL->HB" ; set slk $P1O_SLK($srl) ; set ll $P1O_LL($srl) ; set nbr $P1O_HB($srl)
        } elseif {[info exists P2_NBR($srl)]} {
            set prio P2 ; set rank 2 ; set side "SRL->FF" ; set slk $P2_SLK($srl) ; set ll $P2_LL($srl) ; set nbr $P2_NBR($srl)
        } elseif {$fan >= $HIGH_FANOUT} {
            set prio P6 ; set rank 6 ; set side "high-fanout" ; set nbr ""
        }
        if {$prio ne ""} {
            set ncls [expr {$nbr ne "" ? [classify $nbr] : "-"}]
            set cross [expr {($nbr ne "" && $DO_PARTITION && [partition_of $nbr] ne $part) ? "yes" : "no"}]
            lappend rows [list $srl $ref $prio $rank $side $slk $ll $nbr $ncls $fan $part $cross [output_rec $prio $ncls $fan] SRL_STAGES_TO_REG_OUTPUT]
            incr PCOUNT($prio)
        }

        # ============ INPUT side (D) : P1 HB->SRL > P3 FF->SRL ============
        set prio "" ; set rank 99 ; set side "" ; set slk "" ; set ll "" ; set nbr ""
        if {[info exists P1I_HB($srl)]} {
            set prio P1 ; set rank 1 ; set side "HB->SRL" ; set slk $P1I_SLK($srl) ; set ll $P1I_LL($srl) ; set nbr $P1I_HB($srl)
        } elseif {[info exists P3_NBR($srl)]} {
            set prio P3 ; set rank 3 ; set side "FF->SRL" ; set slk $P3_SLK($srl) ; set ll $P3_LL($srl) ; set nbr $P3_NBR($srl)
        }
        if {$prio ne ""} {
            set ncls [expr {$nbr ne "" ? [classify $nbr] : "-"}]
            set cross [expr {($nbr ne "" && $DO_PARTITION && [partition_of $nbr] ne $part) ? "yes" : "no"}]
            lappend rows [list $srl $ref $prio $rank $side $slk $ll $nbr $ncls $fan $part $cross [input_rec $prio $ncls] SRL_STAGES_TO_REG_INPUT]
            incr PCOUNT($prio)
        }
    }

    write_reports $outdir $rows $nSRL PCOUNT [expr {[clock milliseconds]-$t0}]
    return [llength $rows]
}

## recommendation text builders
proc ::srlb::output_rec {prio ecls fan} {
    switch -glob -- $prio {
        P1 { return "SRL drives hard block ($ecls) - structural boundary, pull register regardless of slack; register SRL output (SRL_STAGES_TO_REG_OUTPUT) or use the block's native input pipeline register" }
        P2 { return "failing/near-meeting SRL -> N*LUT -> FF; register SRL output so path becomes Register->Register (SRL_STAGES_TO_REG_OUTPUT)" }
        P5 { return "SRL output crosses a DFX partition boundary; register the partition interface (SRL_STAGES_TO_REG_OUTPUT)" }
        P6 { return "high-fanout SRL output ($fan loads); register output for replication / fanout management (SRL_STAGES_TO_REG_OUTPUT)" }
        default { return "register SRL output" }
    }
}
proc ::srlb::input_rec {prio scls} {
    switch -glob -- $prio {
        P1 { return "hard block ($scls) drives SRL - structural boundary, pull register regardless of slack; register SRL input (SRL_STAGES_TO_REG_INPUT) or use the block's native output pipeline register" }
        P3 { return "failing/near-meeting FF -> N*LUT -> SRL; register SRL input so path becomes Register->Register (SRL_STAGES_TO_REG_INPUT)" }
        P5 { return "SRL input crosses a DFX partition boundary; register the partition interface (SRL_STAGES_TO_REG_INPUT)" }
        default { return "register SRL input" }
    }
}
proc ::srlb::shallow_rec {d} {
    return "shallow SRL (depth=$d) - convert the whole SRL to registers (SRL_TO_REG 1); a depth<=2 shift register is cheaper/faster as discrete flops and frees retiming / register balancing / placement"
}

###########################################################################
## report writers
###########################################################################
proc ::srlb::write_reports {outdir rows nSRL pcountVar totms} {
    upvar 1 $pcountVar PCOUNT
    variable SLACK_MAX ; variable HB_MAX_LEVELS ; variable HIGH_FANOUT ; variable MED_FANOUT
    variable SCOPE ; variable MAX_PATHS ; variable NHIGH ; variable NMED ; variable SHALLOW_DEPTH

    # sort rows by priority rank then slack asc
    set rows [lsort -command ::srlb::row_cmp $rows]

    # ---- CSV ----
    set csv [open [file join $outdir srl_boundaries.csv] w]
    puts $csv "srl_cell,srl_ref,priority,side,slack_ns,logic_levels,neighbor_cell,neighbor_class,fanout,partition,cross_partition,tag_property,recommendation"
    foreach r $rows {
        lassign $r srl ref prio rank side slk ll nbr ncls fan part cross rec tagp
        puts $csv "\"$srl\",$ref,$prio,$side,$slk,$ll,\"$nbr\",$ncls,$fan,\"$part\",$cross,$tagp,\"$rec\""
    }
    close $csv

    # ---- apply-tags Tcl ----
    set tcl [open [file join $outdir apply_srl_boundary_tags.tcl] w]
    puts $tcl "## apply_srl_boundary_tags.tcl - AUTO-GENERATED by detect_srl_boundary.tcl"
    puts $tcl "## Pull a register out of each flagged SRL so the path becomes Register->Register."
    puts $tcl "##   SRL_STAGES_TO_REG_OUTPUT 1 : register the SRL OUTPUT (Q) side"
    puts $tcl "##   SRL_STAGES_TO_REG_INPUT  1 : register the SRL INPUT  (D) side"
    puts $tcl "##   SRL_TO_REG 1              : convert a SHALLOW SRL (depth<=$SHALLOW_DEPTH) entirely to registers (P8)"
    puts $tcl "## Review before applying; then re-run opt/place/route.\n"
    foreach pr {P1 P2 P3 P5 P6 P8} {
        set any 0
        foreach r $rows {
            lassign $r srl ref prio rank side slk ll nbr ncls fan part cross rec tagp
            if {$prio ne $pr} continue
            if {!$any} { puts $tcl "\n## ---- $pr ----" ; set any 1 }
            puts $tcl "set_property $tagp 1 \[get_cells \{$srl\}\]  ;# $side slack=$slk $ncls"
        }
    }
    close $tcl

    # ---- summary ----
    set rpt [open [file join $outdir srl_boundary_summary.rpt] w]
    puts $rpt "############################################################"
    puts $rpt "## SRL BOUNDARY OPTIMIZATION ADVISOR"
    puts $rpt "############################################################"
    puts $rpt "SRL primitives analyzed : $nSRL   (SCOPE='$SCOPE')"
    puts $rpt "post-opt/pre-place DCP - timing is ESTIMATED (pre-route)"
    set hbLvl [expr {$HB_MAX_LEVELS>0 ? "<=$HB_MAX_LEVELS" : "unbounded"}]
    puts $rpt "thresholds : P2/P3 SLACK_MAX<=$SLACK_MAX (failing + near-meeting) ; P1 SRL<->HB structural (no slack gate, logic-levels $hbLvl) ; HIGH_FANOUT>=$HIGH_FANOUT ; MAX_PATHS=$MAX_PATHS"
    puts $rpt "flagged boundary rows   : [llength $rows]"
    puts $rpt ""
    puts $rpt "by priority:"
    puts $rpt "  P1 SRL <-> HARD BLOCK (structural) : $PCOUNT(P1)"
    puts $rpt "  P2 SRL->N*LUT->FF  (slack<=$SLACK_MAX) : $PCOUNT(P2)"
    puts $rpt "  P3 FF->N*LUT->SRL  (slack<=$SLACK_MAX) : $PCOUNT(P3)"
    puts $rpt "  P5 DFX PARTITION BOUNDARY          : $PCOUNT(P5)"
    puts $rpt "  P6 HIGH-FANOUT SRL OUTPUT          : $PCOUNT(P6)   (>= $HIGH_FANOUT loads; also $NMED SRLs in \[$MED_FANOUT,$HIGH_FANOUT) not individually flagged)"
    puts $rpt "  P8 SHALLOW SRL (depth<=$SHALLOW_DEPTH) -> SRL_TO_REG : $PCOUNT(P8)"
    set flagged [llength $rows]
    puts $rpt "  P7 GENERAL (non-critical) : ~[expr {$nSRL - [llength [lsort -unique [srl_names $rows]]]}] SRLs not flagged (cleanup candidates)"
    puts $rpt ""
    puts $rpt "analysis time : [format %.1f [expr {$totms/1000.0}]]s"
    puts $rpt ""
    puts $rpt "top flagged boundaries (priority, slack asc):"
    puts $rpt [format "  %-4s %-11s %8s %3s  %-16s %s" PRI SIDE SLACK LL NEIGHBOR SRL]
    set shown 0
    foreach r $rows {
        lassign $r srl ref prio rank side slk ll nbr ncls fan part cross rec tagp
        puts $rpt [format "  %-4s %-11s %8s %3s  %-16s %s" $prio $side $slk $ll $ncls $srl]
        if {[incr shown] >= 60} { puts $rpt "  ... ([expr {[llength $rows]-60}] more in srl_boundaries.csv)" ; break }
    }
    close $rpt

    puts "\[srlb\] wrote: srl_boundary_summary.rpt  srl_boundaries.csv  apply_srl_boundary_tags.tcl"
    puts "\[srlb\] flagged=$flagged  P1=$PCOUNT(P1) P2=$PCOUNT(P2) P3=$PCOUNT(P3) P5=$PCOUNT(P5) P6=$PCOUNT(P6) P8=$PCOUNT(P8)"
}

proc ::srlb::srl_names {rows} {
    set l {}
    foreach r $rows { lappend l [lindex $r 0] }
    return $l
}
proc ::srlb::row_cmp {a b} {
    set ra [lindex $a 3] ; set rb [lindex $b 3]
    if {$ra != $rb} { return [expr {$ra - $rb}] }
    set sa [lindex $a 5] ; set sb [lindex $b 5]
    if {$sa eq ""} { set sa 9999 } ; if {$sb eq ""} { set sb 9999 }
    if {$sa < $sb} { return -1 } ; if {$sa > $sb} { return 1 } ; return 0
}
