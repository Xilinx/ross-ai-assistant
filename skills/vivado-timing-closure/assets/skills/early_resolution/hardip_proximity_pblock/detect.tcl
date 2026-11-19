# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
###########################################################################
## detect.tcl
## HARD-IP DATAPATH LOCALITY (PROXIMITY-PBLOCK) GENERATOR — a PRE-PLACEMENT advisor.
##
## PROBLEM this solves (that the SLR-locality skill cannot):
##   A fabric register that captures data DIRECTLY from a FIXED hard IP
##   (GT RXDATA/TXDATA, DCMAC/MRMAC AXIS ...) is placed by the
##   wirelength-driven placer at the *load centroid* of its downstream cone,
##   which can be on the OPPOSITE side of the SAME SLR from the hard-IP site.
##   The result is a 0-logic-level, route-dominated path (e.g. GT@CR X9Y6 ->
##   capture FF@CR X1Y5, ~4.2 ns pure interconnect) that NO logic skill
##   (retiming / SRL / control-set / BRAM-oreg / macro-cluster) can fix and
##   that USER_CROSSING_SLR does NOT catch (it never leaves the SLR).
##
## FIX: emit an RP-aware PROXIMITY PBLOCK per (partition, hard-IP clock region)
##   group that pulls the directly-connected capture/launch registers into the
##   fabric nearest the hard-IP's fixed site. In a DFX design the proximity
##   pblock is built ONLY from the reconfigurable partition's own ranges (a
##   valid nested sub-pblock); soft by default so it attracts without fencing.
##
## PRE-PLACE + TOPOLOGY-DRIVEN: selection uses connectivity + the hard-IP's
##   FIXED LOC (GT/DCMAC/MRMAC carry a fixed site before place_design). No routed
##   data required. READ-ONLY on the design (writes report + fix files only).
##
## Public entry:  ::hdpl::run_hardip_datapath_locality <outdir>
## Outputs:       fix.tcl  fix.xdc  hardip_datapath_locality.csv  *_summary.rpt
##   fix.tcl / fix.xdc create+resize the proximity pblocks and add_cells_to_pblock
##   the capture registers. Count token for the flow: "HARDIP_PROX_PBLOCK".
##
## Env tunables (HDPL_<NAME>):
##   HDPL_CLASSES   = subset of {GT DCMAC MRMAC} to target (default all)
##   HDPL_DIRS      = which sides to anchor: rx tx both  (default both)
##   HDPL_FANOUT_CAP= skip hard-IP data nets with fanout > N (default 64)
##   HDPL_MAX_CELLS = safety cap on total anchored registers (default 200000)
##   HDPL_SOFT      = 1 -> IS_SOFT pblocks (attract, don't fence) ; 0 -> hard (default 1)
##   HDPL_FF_PER_SLICE = capacity model for range sizing (default 12, headroom)
##   HDPL_SCOPE     = only hard IPs under this hierarchy prefix
##   HDPL_OUTDIR    = default outdir for auto-run on source (default ".")
##   HDPL_NO_AUTORUN= 1 -> only load procs (call the run proc yourself)
###########################################################################

namespace eval ::hdpl {
    # broad ref-name superset for the initial get_cells; hb_class is source of truth
    variable HB_REFPATS {GT*_QUAD GT*_CHANNEL GTM* GTY* GTH* GTF* GTYP*
                         *DCMAC* *MRMAC* PCIE* PCIE50E5 CPM* CPM5}
    # per-class DATA interface pin-name patterns (globs on REF_PIN_NAME). Clock
    # pins are excluded separately. Both directions are captured; DIRECTION of
    # the pin decides whether we anchor the LOADS (hard-IP output) or DRIVERS
    # (hard-IP input).
    variable IFPAT
    array unset IFPAT
    array set IFPAT {
        GT    {*RXDATA* *TXDATA* *RXCTRL* *TXCTRL* *RXHEADER* *TXHEADER* *RXMTY* *TXMTY*}
        DCMAC {*TDATA* *TKEEP* *TVALID* *TLAST* *TUSER* *RXDATA* *TXDATA*}
        MRMAC {*TDATA* *TKEEP* *TVALID* *TLAST* *TUSER* *RXDATA* *TXDATA*}
        PCIE  {*PIPERX*DATA* *PIPETX*DATA* *PIPERX* *PIPETX* *TDATA* *TKEEP* *TVALID* *TLAST* *TUSER* *AXIS*}
        CPM   {*TDATA* *TKEEP* *TVALID* *TLAST* *TUSER* *AXIS*}
    }
    variable CLK_PATS {*USERCLK* *ACLK* *CLK* *CLK}

    # tunables (env-overridable)
    variable CLASSES        ""
    variable DIRS           both
    variable FANOUT_CAP     64
    variable MAX_CELLS      200000
    variable SOFT           1
    variable FF_PER_SLICE   12
    variable SCOPE          ""

    proc env_or {name def} {
        set e "HDPL_$name"
        if {[info exists ::env($e)] && $::env($e) ne ""} { return $::env($e) }
        return $def
    }
}

## ---- hard-IP class of a ref name, or "" if not a supported fixed hard block ----
proc ::hdpl::hb_class {ref} {
    if {[string match *PCIE* $ref]}  { return PCIE }
    if {[string match *CPM* $ref]}   { return CPM }
    if {[string match *DCMAC* $ref]} { return DCMAC }
    if {[string match *MRMAC* $ref]} { return MRMAC }
    if {[string match GT*_QUAD $ref] || [string match GT*_CHANNEL $ref] || [string match GTM* $ref] \
        || [string match GTY* $ref] || [string match GTH* $ref] || [string match GTF* $ref] \
        || [string match GTYP* $ref]} { return GT }
    return ""
}

## ---- is this a register (a capture/launch cell we may anchor)? ----
proc ::hdpl::is_register {ref} {
    return [expr {[string match FD* $ref] || [string match LD* $ref] || [string match SRL* $ref]}]
}

## ---- clock-ish interface pin? (never anchor off a clock net) ----
proc ::hdpl::is_clock_pin {refpin} {
    variable CLK_PATS
    foreach p $CLK_PATS { if {[string match $p $refpin]} { return 1 } }
    return 0
}

## ---- clock region of a cell IF it is LOC-fixed to a site, else "" ----
proc ::hdpl::cell_anchor_cr {cell} {
    set loc [get_property -quiet LOC [get_cells -quiet $cell]]
    if {$loc eq ""} { return "" }
    set site [get_sites -quiet $loc]
    if {$site eq ""} { return "" }
    return [get_property -quiet CLOCK_REGION $site]
}

## ---- the pblock a (reconfigurable) cell currently belongs to, or "" ----
proc ::hdpl::cell_pblock {cell} {
    set pb [get_pblocks -quiet -of [get_cells -quiet $cell]]
    if {$pb eq ""} { return "" }
    return [get_property -quiet NAME [lindex $pb 0]]
}

## ---- parse "X<u>Y<v>" clock-region name -> {u v}, or {} ----
proc ::hdpl::parse_cr {cr} {
    if {[regexp {X([0-9]+)Y([0-9]+)} $cr -> x y]} { return [list $x $y] }
    return {}
}

## ---- resolve a pin's incident net to its TOP-LEVEL (shallowest) segment so a
## constraint on it is honoured across preserved IP boundaries. ----
proc ::hdpl::pin_top_net {pin} {
    set segs [get_nets -quiet -segments -of $pin]
    if {[llength $segs] <= 1} { return [get_nets -quiet -of $pin] }
    set best "" ; set bestd 999999
    foreach n $segs {
        set d [llength [split [get_property -quiet NAME $n] /]]
        if {$d < $bestd} { set bestd $d ; set best $n }
    }
    return $best
}

## ---- SLICE_* ranges of a pblock, as a list of {loX loY hiX hiY rangeText} ----
proc ::hdpl::rp_slice_ranges {pbname} {
    set out {}
    foreach r [get_property -quiet GRID_RANGES [get_pblocks -quiet $pbname]] {
        if {![string match SLICE_* $r]} continue
        if {[regexp {SLICE_X([0-9]+)Y([0-9]+):SLICE_X([0-9]+)Y([0-9]+)} $r -> ax ay bx by]} {
            lappend out [list $ax $ay $bx $by $r]
        } elseif {[regexp {SLICE_X([0-9]+)Y([0-9]+)} $r -> ax ay]} {
            lappend out [list $ax $ay $ax $ay $r]
        }
    }
    return $out
}

## ---- CR-grid Manhattan distance from a SLICE range's nearest corner to (crx,cry) ----
proc ::hdpl::range_cr_dist {rng crx cry} {
    lassign $rng ax ay bx by
    set best 1000000
    foreach c [list "SLICE_X${ax}Y${ay}" "SLICE_X${bx}Y${by}" "SLICE_X${ax}Y${by}" "SLICE_X${bx}Y${ay}"] {
        set s [get_sites -quiet $c]
        if {$s eq ""} continue
        set cr [parse_cr [get_property -quiet CLOCK_REGION $s]]
        if {![llength $cr]} continue
        set d [expr {abs([lindex $cr 0]-$crx) + abs([lindex $cr 1]-$cry)}]
        if {$d < $best} { set best $d }
    }
    return $best
}

## ---- SLICE capacity (site count) of a range ----
proc ::hdpl::range_capacity {rng} {
    lassign $rng ax ay bx by
    return [expr {($bx-$ax+1)*($by-$ay+1)}]
}

###########################################################################
## main
###########################################################################
proc ::hdpl::run_hardip_datapath_locality {outdir} {
    variable HB_REFPATS ; variable IFPAT
    variable CLASSES ; variable DIRS ; variable FANOUT_CAP ; variable MAX_CELLS
    variable SOFT ; variable FF_PER_SLICE ; variable SCOPE

    set CLASSES      [env_or CLASSES      "GT DCMAC MRMAC"]
    set DIRS         [string tolower [env_or DIRS both]]
    set FANOUT_CAP   [env_or FANOUT_CAP   64]
    set MAX_CELLS    [env_or MAX_CELLS    200000]
    set SOFT         [env_or SOFT         1]
    set FF_PER_SLICE [env_or FF_PER_SLICE 12]
    set SCOPE        [env_or SCOPE        ""]

    file mkdir $outdir
    set t0 [clock milliseconds]

    # -------- 1. discover fixed hard-IP instances --------
    puts "\[hdpl\] discovering fixed hard-IP primitives ..."
    set parts {}
    foreach p $HB_REFPATS { lappend parts "REF_NAME=~$p" }
    set cand [get_cells -hier -quiet -filter "IS_PRIMITIVE && ([join $parts { || }])"]
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
    puts "\[hdpl\] fixed hard-IP instances = $nHIP"
    if {!$nHIP} { error "no supported fixed hard-IP instances found (CLASSES='$CLASSES' SCOPE='$SCOPE')" }

    # -------- 2/3. data-interface pins -> directly-connected capture registers --------
    # group key = "<pblock>|<anchorCR>" ; value = list of {reg dir hipcell refpin}
    array set GROUP {}       ;# key -> reg name list (dedup via REGSEEN)
    array set REGSEEN {}
    array set GROUP_CR {}    ;# key -> anchorCR
    array set GROUP_PB {}    ;# key -> pblock (RP) name ("" = static)
    array set GROUP_HIP {}   ;# key -> {hip cells}
    array set GROUP_CLS {}   ;# key -> class
    set nAnchored 0 ; set nPins 0

    foreach hip [array names HIPCLASS] {
        set cls $HIPCLASS($hip)
        set acr [cell_anchor_cr $hip]
        if {$acr eq ""} continue    ;# no fixed site -> cannot anchor to it
        if {![info exists IFPAT($cls)]} continue
        set fp {}
        foreach p $IFPAT($cls) { lappend fp "REF_PIN_NAME =~ $p" }
        set ifpins [get_pins -quiet -of [get_cells -quiet $hip] -filter [join $fp { || }]]
        foreach pin $ifpins {
            set refpin [get_property -quiet REF_PIN_NAME $pin]
            if {[is_clock_pin $refpin]} continue
            set dir [get_property -quiet DIRECTION $pin]
            # rx = hard-IP OUTPUT feeding fabric ; tx = fabric feeding hard-IP INPUT
            set side [expr {$dir eq "OUT" ? "rx" : "tx"}]
            if {$DIRS ne "both" && $DIRS ne $side} continue
            set net [pin_top_net $pin]
            if {$net eq ""} continue
            set tp [get_property -quiet TYPE $net]
            if {$tp eq "POWER" || $tp eq "GROUND" || $tp eq "GLOBAL_CLOCK"} continue
            set fo [get_property -quiet FLAT_PIN_COUNT $net]
            if {$fo ne "" && $fo > $FANOUT_CAP} continue
            incr nPins
            # fabric-side pins: loads for an OUTPUT data pin, drivers for an INPUT one
            set want [expr {$dir eq "OUT" ? "IN" : "OUT"}]
            foreach fpin [get_pins -quiet -leaf -of $net -filter "DIRECTION == $want"] {
                set c [get_cells -quiet -of $fpin]
                if {$c eq ""} continue
                set cn [get_property -quiet NAME $c]
                if {[info exists HIPCLASS($cn)]} continue   ;# skip the hard IP itself
                if {![is_register [get_property -quiet REF_NAME $c]]} continue
                if {[info exists REGSEEN($cn)]} continue
                if {$nAnchored >= $MAX_CELLS} break
                set pb  [cell_pblock $c]
                set key "$pb|$acr"
                lappend GROUP($key) $cn
                set REGSEEN($cn) 1
                set GROUP_CR($key) $acr
                set GROUP_PB($key) $pb
                set GROUP_CLS($key) $cls
                lappend GROUP_HIP($key) $hip
                incr nAnchored
            }
        }
    }
    puts "\[hdpl\] scanned data pins=$nPins  capture registers to anchor=$nAnchored  groups=[array size GROUP]"
    if {!$nAnchored} { error "no directly-connected capture registers found to anchor" }

    # -------- 4. per-group proximity-pblock range selection --------
    # For each group pick the SLICE ranges of the group's RP that are NEAREST (in
    # clock-region grid) to the hard-IP anchor CR, accumulating enough capacity.
    array set GROUP_RANGES {}
    foreach key [array names GROUP] {
        set acr $GROUP_CR($key)
        lassign [parse_cr $acr] crx cry
        set need [llength $GROUP($key)]
        set pb   $GROUP_PB($key)
        if {$pb ne ""} {
            # DFX: build ONLY from the RP's own ranges (valid nested sub-pblock)
            set ranges [rp_slice_ranges $pb]
            set scored {}
            foreach rng $ranges { lappend scored [list [range_cr_dist $rng $crx $cry] $rng] }
            set scored [lsort -integer -index 0 $scored]
            set chosen {} ; set cap 0
            foreach s $scored {
                lappend chosen [lindex [lindex $s 1] 4]
                set cap [expr {$cap + [range_capacity [lindex $s 1]] * $FF_PER_SLICE}]
                if {$cap >= $need} break
            }
            set GROUP_RANGES($key) $chosen
        } else {
            # static / non-DFX: anchor straight to the hard-IP clock region
            set GROUP_RANGES($key) [list "CLOCKREGION_$acr"]
        }
    }

    # -------- 5. write outputs --------
    write_outputs $outdir GROUP GROUP_CR GROUP_PB GROUP_CLS GROUP_HIP GROUP_RANGES \
                  $nHIP $nPins $nAnchored [expr {[clock milliseconds]-$t0}]
    return [array size GROUP]
}

###########################################################################
## report / fix writers
###########################################################################
proc ::hdpl::grp_tag {key} {
    # stable, legal pblock name from "<pb>|<CR>"
    regsub -all {[^A-Za-z0-9]} $key {_} t
    return "hdpl_$t"
}

proc ::hdpl::write_outputs {outdir gVar crVar pbVar clsVar hipVar rngVar nHIP nPins nAnchored totms} {
    upvar 1 $gVar GROUP $crVar GROUP_CR $pbVar GROUP_PB $clsVar GROUP_CLS \
            $hipVar GROUP_HIP $rngVar GROUP_RANGES
    variable SOFT ; variable DIRS ; variable FANOUT_CAP ; variable FF_PER_SLICE

    set tclf [open [file join $outdir fix.tcl] w]
    set xdc  [open [file join $outdir fix.xdc] w]
    set csv  [open [file join $outdir hardip_datapath_locality.csv] w]
    set rpt  [open [file join $outdir hardip_datapath_locality_summary.rpt] w]

    puts $tclf "## fix.tcl — AUTO-GENERATED by hardip_datapath_locality/detect.tcl"
    puts $tclf "## RP-aware PROXIMITY pblocks that pull hard-IP capture/launch registers"
    puts $tclf "## next to their fixed hard-IP site. Source BEFORE place_design.\n"
    puts $xdc  "# fix.xdc — AUTO-GENERATED by hardip_datapath_locality/detect.tcl"
    puts $xdc  "# Add to the constraint set BEFORE place_design.\n"
    puts $csv  "group_pblock,class,anchor_clock_region,rp_pblock,n_registers,proximity_ranges"

    set nPB 0 ; set nCells 0
    foreach key [lsort [array names GROUP]] {
        set pbn   [grp_tag $key]
        set cls   $GROUP_CLS($key)
        set acr   $GROUP_CR($key)
        set rp    $GROUP_PB($key)
        set regs  [lsort -unique $GROUP($key)]
        set ranges $GROUP_RANGES($key)
        set nreg  [llength $regs]
        set softp [expr {$SOFT ? "TRUE" : "FALSE"}]
        incr nPB ; incr nCells $nreg

        puts $tclf "# HARDIP_PROX_PBLOCK $pbn  class=$cls  anchorCR=$acr  RP=[expr {$rp eq {} ? {<static>} : $rp}]  regs=$nreg"
        puts $tclf "if {\[llength \[get_pblocks -quiet $pbn\]\] == 0} { create_pblock $pbn }"
        puts $tclf "resize_pblock $pbn -add { $ranges }"
        puts $tclf "set_property IS_SOFT $softp \[get_pblocks $pbn\]"
        puts $tclf "add_cells_to_pblock $pbn \[get_cells -quiet {\\"
        foreach r $regs { puts $tclf "    $r \\" }
        puts $tclf "}\]"
        puts $tclf ""

        puts $xdc "# HARDIP_PROX_PBLOCK $pbn  class=$cls  anchorCR=$acr  regs=$nreg"
        puts $xdc "create_pblock $pbn"
        puts $xdc "resize_pblock $pbn -add { $ranges }"
        puts $xdc "set_property IS_SOFT $softp \[get_pblocks $pbn\]"
        puts $xdc "add_cells_to_pblock $pbn \[get_cells -quiet { [join $regs { }] }\]"
        puts $xdc ""

        puts $csv "$pbn,$cls,$acr,\"$rp\",$nreg,\"$ranges\""
    }
    close $tclf ; close $xdc ; close $csv

    puts $rpt "############################################################"
    puts $rpt "## HARD-IP DATAPATH LOCALITY — PROXIMITY PBLOCK PLAN"
    puts $rpt "############################################################"
    puts $rpt "fixed hard-IP instances scanned : $nHIP"
    puts $rpt "data interface nets examined     : $nPins"
    puts $rpt "capture/launch registers anchored: $nAnchored"
    puts $rpt "proximity pblocks emitted        : $nPB"
    puts $rpt "soft pblocks                      : [expr {$SOFT ? {yes (attract, not fence)} : {no (hard boundary)}}]"
    puts $rpt "directions                        : $DIRS    fanout cap : $FANOUT_CAP"
    puts $rpt "elapsed                           : [format %.1f [expr {$totms/1000.0}]] s"
    puts $rpt ""
    puts $rpt [format "%-40s %-6s %-10s %-14s %s" pblock class anchorCR n_regs rp_pblock]
    foreach key [lsort [array names GROUP]] {
        puts $rpt [format "%-40s %-6s %-10s %-14d %s" [grp_tag $key] $GROUP_CLS($key) \
            $GROUP_CR($key) [llength [lsort -unique $GROUP($key)]] \
            [expr {$GROUP_PB($key) eq {} ? {<static>} : $GROUP_PB($key)}]]
    }
    close $rpt

    puts "\[hdpl\] wrote $nPB proximity pblock(s) anchoring $nCells register(s)"
    puts "\[hdpl\] apply now:  source [file join $outdir fix.tcl]   |   or add [file join $outdir fix.xdc] to constrs before place_design"
}

## ---------------------------------------------------------------------------
## empty-range guard: never emit `resize_pblock -add {}` (that traps cells in a
## 0-site pblock and breaks place_design). Returns 1 if a pblock was written.
## ---------------------------------------------------------------------------
proc ::hdpl::emit_pblock_guarded {tclfh pbn ranges regs soft} {
    set clean {}
    foreach r $ranges { if {[string trim $r] ne ""} { lappend clean $r } }
    if {![llength $clean] || ![llength $regs]} { return 0 }
    puts $tclfh "if {\[llength \[get_pblocks -quiet $pbn\]\] == 0} { create_pblock $pbn }"
    puts $tclfh "resize_pblock $pbn -add { $clean }"
    puts $tclfh "set_property IS_SOFT [expr {$soft ? {TRUE} : {FALSE}}] \[get_pblocks $pbn\]"
    puts $tclfh "add_cells_to_pblock $pbn \[get_cells -quiet { [join $regs { }] }\]"
    puts $tclfh ""
    return 1
}

## ---------------------------------------------------------------------------
## Small-fanout clock-ENABLE / BUFGCE proximity.
##   Anchor the fanout<=CAP register driving a BUFGCE/BUFGCTRL/MBUFGCE CE pin
##   next to its clock region.  Anchor CR = the buffer's placed LOC region
##   (POST-place refinement), else the buffer output clock's
##   CLOCK_ROOT/USER_CLOCK_ROOT region (PRE-place best-effort). Count token in
##   fix.tcl: CKP_CLK_PROX.  Read-only (writes fix.tcl + summary only).
## Env: CKP_BUF_REFPATS  CKP_FANOUT_CAP(16)  CKP_SOFT(1)  CKP_SCOPE
## ---------------------------------------------------------------------------
proc ::hdpl::run_clkctrl_proximity {outdir} {
    file mkdir $outdir
    set REFPATS [expr {[info exists ::env(CKP_BUF_REFPATS)] && $::env(CKP_BUF_REFPATS) ne "" ? $::env(CKP_BUF_REFPATS) : {BUFGCE BUFGCE_DIV BUFGCTRL MBUFGCE MBUFGCE_DIV}}]
    set CAP     [expr {[info exists ::env(CKP_FANOUT_CAP)] && $::env(CKP_FANOUT_CAP) ne "" ? $::env(CKP_FANOUT_CAP) : 16}]
    set SOFT    [expr {[info exists ::env(CKP_SOFT)]       && $::env(CKP_SOFT) ne ""       ? $::env(CKP_SOFT)       : 1}]
    set SCOPE   [expr {[info exists ::env(CKP_SCOPE)]      && $::env(CKP_SCOPE) ne ""      ? $::env(CKP_SCOPE)      : ""}]

    set parts {}
    foreach p $REFPATS { lappend parts "REF_NAME=~$p" }
    set bufs [get_cells -hier -quiet -filter "IS_PRIMITIVE && ([join $parts { || }])"]

    array set GRP {} ; array set GRP_BUF {}
    set nscan 0 ; set nanch 0
    foreach b $bufs {
        set bn [get_property -quiet NAME $b]
        if {$SCOPE ne "" && ![string match "${SCOPE}*" $bn]} continue
        # anchor CR: buffer LOC clock region (post-place), else output clock root (pre-place)
        set acr ""
        set loc [get_property -quiet LOC $b]
        if {$loc ne ""} { set acr [get_property -quiet CLOCK_REGION [get_sites -quiet $loc]] }
        if {$acr eq ""} {
            set onet [get_nets -quiet -of [get_pins -quiet -of $b -filter {REF_PIN_NAME==O}]]
            if {$onet ne ""} {
                set acr [get_property -quiet CLOCK_ROOT $onet]
                if {$acr eq ""} { set acr [get_property -quiet USER_CLOCK_ROOT $onet] }
            }
        }
        if {$acr eq ""} continue
        foreach cepin [get_pins -quiet -of $b -filter {REF_PIN_NAME==CE || REF_PIN_NAME=~CE[0-9]*}] {
            incr nscan
            set net [get_nets -quiet -of $cepin]
            if {$net eq ""} continue
            set fo [get_property -quiet FLAT_PIN_COUNT $net]
            if {$fo eq "" || $fo > $CAP} continue
            foreach dp [get_pins -quiet -leaf -of $net -filter {DIRECTION==OUT}] {
                set c [get_cells -quiet -of $dp]
                if {$c eq ""} continue
                if {![::hdpl::is_register [get_property -quiet REF_NAME $c]]} continue
                lappend GRP($acr) [get_property -quiet NAME $c]
                lappend GRP_BUF($acr) $bn
                incr nanch
            }
        }
    }

    set tclf [open [file join $outdir fix.tcl] w]
    set rpt  [open [file join $outdir clkctrl_proximity_summary.rpt] w]
    puts $tclf "## fix.tcl - clock-enable/BUFGCE proximity (::hdpl::run_clkctrl_proximity)"
    puts $tclf "## Anchors small-fanout CE drivers next to their clock buffer's CR. Source before (re)place.\n"
    set nPB 0
    foreach acr [lsort [array names GRP]] {
        set regs [lsort -unique [concat $GRP($acr) $GRP_BUF($acr)]]
        set pbn "ckp_[regsub -all {[^A-Za-z0-9]} $acr _]"
        puts $tclf "# CKP_CLK_PROX $pbn  anchorCR=$acr  cells=[llength $regs]"
        if {[::hdpl::emit_pblock_guarded $tclf $pbn "CLOCKREGION_$acr" $regs $SOFT]} { incr nPB }
    }
    close $tclf
    puts $rpt "############################################################"
    puts $rpt "## CLOCK-ENABLE / BUFGCE PROXIMITY - SUMMARY"
    puts $rpt "############################################################"
    puts $rpt "clock buffers scanned (CE pins) : $nscan"
    puts $rpt "small-fanout CE drivers anchored: $nanch   (fanout cap=$CAP)"
    puts $rpt "proximity pblocks emitted        : $nPB    (soft=$SOFT)"
    close $rpt
    puts "\[ckp\] scanned=$nscan  anchored=$nanch  pblocks=$nPB -> $outdir/fix.tcl"
    return $nPB
}

## ---------------------------------------------------------------------------
## Auto-run on `source` unless HDPL_NO_AUTORUN=1 (mirrors the other skills).
## ---------------------------------------------------------------------------
if {![info exists ::env(HDPL_NO_AUTORUN)] || $::env(HDPL_NO_AUTORUN) eq "0"} {
    if {[catch {current_design} ::hdpl::_cd] || $::hdpl::_cd eq ""} {
        puts "\[hdpl\] no open design — open a checkpoint first, then: source detect.tcl"
    } else {
        ::hdpl::run_hardip_datapath_locality [::hdpl::env_or OUTDIR "."]
    }
}
