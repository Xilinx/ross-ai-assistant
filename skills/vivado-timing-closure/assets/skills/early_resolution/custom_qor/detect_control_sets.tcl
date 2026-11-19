# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
###########################################################################
## detect_control_sets.tcl   (READ-ONLY - no design mutation)
##
## Control-set fragmentation analysis, per module.
##
## A CONTROL SET = unique combination of { Clock net, Clock-Enable net,
## Set/Reset net }. FFs pack into a SLICE only if they share a control set, so
## more control sets => the FF population is fragmented into smaller packable
## groups => worse placement/packing density.
##
## Per module (hierarchy prefix at DEPTH levels) and overall we compute:
##   FF count, control-set count, FFs/control-set, largest CS, median CS,
##   control-set-size histogram, Fragmentation Index = 1 - largest_CS/total_FFs,
##   Control-set density = CS/FFs, Priority = FF_count * density  (= CS count).
##
## EFFICIENCY: the {clk,ce,sr} net of every FF is resolved by PIVOTING ON THE
## CONTROL NETS (a few thousand) - for each net, one get_pins -leaf and one
## vectorized get_property PARENT_CELL assigns the net to all its FFs. This is
## thousands of Vivado calls, not the millions a per-FF loop would need.
##
## Usage (design already open):
##   source detect_control_sets.tcl
##   run_control_set_analysis <outdir>            ;# whole design
##   run_control_set_analysis <outdir> $ffSubset  ;# restrict to a FF list (debug)
## Env tunables:
##   DEPTH    module = first DEPTH '/'-separated segments of the FF name (default 8)
##   MIN_FF   modules with < MIN_FF FFs are dropped from the ranked report (default 200)
###########################################################################

proc _cs_env {name def} {
    if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
    return $def
}

# interpret a Vivado boolean-ish property value (TRUE/1/yes/on) as "set"
proc _cs_boolset {v} {
    return [expr {[string tolower $v] in {1 true yes on}}]
}

# fanout-histogram bucket for a control signal (1,2,3,4 exact; then 5-16, >16)
proc _cs_fobin {n} {
    if {$n <= 4}  { return $n }
    if {$n <= 16} { return "5-16" }
    return ">16"
}

# worst (min) numeric D-pin setup slack over a list of FFs; "" if none known
proc _cs_worstslack {ffs slkVar} {
    upvar 1 $slkVar DSLACK
    set w ""
    foreach f $ffs {
        if {![info exists DSLACK($f)]} continue
        set s $DSLACK($f)
        if {![string is double -strict $s]} continue
        if {$w eq "" || $s < $w} { set w $s }
    }
    return $w
}

# net name normalized: POWER->1, GROUND->0, else the net NAME
proc _cs_netnorm {n} {
    if {$n eq ""} { return "-" }
    set t [get_property -quiet TYPE $n]
    if {$t eq "POWER"}  { return 1 }
    if {$t eq "GROUND"} { return 0 }
    return [get_property -quiet NAME $n]
}

# assign OF(ff)=netName for every FF driving one of $pinFilter, via net-pivot.
# POWER/GROUND (const) nets are SKIPPED - those FFs keep the caller's default
# (CE tied to VCC = "always enabled"; SR tied to GND = "no reset"). Skipping them
# avoids a huge get_pins -leaf over the near-universal VCC/GND fanout.
proc _cs_assign {ffs pinFilter arrName} {
    upvar 1 $arrName OF
    set pins [get_pins -quiet -of $ffs -filter $pinFilter]
    set nets [get_nets -quiet -of $pins]
    foreach n $nets {
        set t [get_property -quiet TYPE $n]
        if {$t eq "POWER" || $t eq "GROUND"} { continue }
        set nm [get_property -quiet NAME $n]
        set lp [get_pins -quiet -leaf -of $n -filter $pinFilter]
        foreach par [get_property -quiet PARENT_CELL $lp] { set OF($par) $nm }
    }
}

proc _cs_median {sorted} {
    set k [llength $sorted]
    if {$k == 0} { return 0 }
    set m [expr {$k/2}]
    if {$k % 2} { return [lindex $sorted $m] }
    return [expr {([lindex $sorted [expr {$m-1}]] + [lindex $sorted $m]) / 2.0}]
}

proc _cs_bin {n} {
    if {$n <= 1}    { return "1" }
    if {$n <= 4}    { return "2-4" }
    if {$n <= 16}   { return "5-16" }
    if {$n <= 64}   { return "17-64" }
    if {$n <= 256}  { return "65-256" }
    if {$n <= 1024} { return "257-1024" }
    return ">1024"
}

# LUT count per hierarchical prefix (every ancestor instance), from LUT leaves.
proc _cs_build_lutcnt {arrName} {
    upvar 1 $arrName LUTCNT
    array unset LUTCNT
    foreach l [get_cells -hierarchical -quiet -filter {REF_NAME =~ LUT*}] {
        set segs [split $l /]
        set n [llength $segs]
        set p ""
        for {set i 0} {$i < $n-1} {incr i} {
            if {$i} { append p / }
            append p [lindex $segs $i]
            incr LUTCNT($p)
        }
    }
}

# module = SHALLOWEST ancestor prefix whose LUT count <= maxlut (the largest
# hierarchy instance still under the ~5K cap). Counts are monotone-decreasing
# with depth, so the first prefix under the cap is that partition boundary. If
# none (FF sits directly in a >cap block), fall back to the immediate parent.
proc _cs_module {ff maxlut lutcntArr} {
    upvar 1 $lutcntArr LUTCNT
    set segs [split $ff /]
    set n [llength $segs]
    set p ""
    for {set i 0} {$i < $n-1} {incr i} {
        if {$i} { append p / }
        append p [lindex $segs $i]
        if {[info exists LUTCNT($p)] && $LUTCNT($p) <= $maxlut} { return $p }
    }
    return [join [lrange $segs 0 end-1] /]
}

proc run_control_set_analysis {outdir {ffOverride ""}} {
    file mkdir $outdir
    set t0 [clock seconds]
    set MAX_LUT [_cs_env MAX_LUT 5000]
    set MIN_LUT [_cs_env MIN_LUT 500]
    set MIN_FF  [_cs_env MIN_FF 200]
    set FANOUT_MAX [_cs_env FANOUT_MAX 4] ;# a control signal is fragmenting/actionable if it drives <= this many FFs in the module
    set TOPN       [_cs_env TOPN 30]      ;# how many top modules to rank and to detail control signals for
    set SLACK_MIN  [_cs_env SLACK_MIN 0.5] ;# min D-pin setup slack (ns) to call a control signal safely movable into datapath
    set CDCRE      [_cs_env CDC_REGEX {(cdc|synchroniz|resync|_sync|sync_|_meta|xpm_cdc)}] ;# do-not-touch CDC logic (FF or control-net name, case-insensitive)
    set SR {REF_PIN_NAME == R || REF_PIN_NAME == S || REF_PIN_NAME == CLR || REF_PIN_NAME == PRE}

    if {$ffOverride ne ""} {
        set ffs $ffOverride
    } else {
        set ffs [get_cells -hierarchical -quiet -filter {IS_SEQUENTIAL && REF_NAME =~ FD*}]
    }
    set total [llength $ffs]
    puts "\[CS\] FFs = $total  (MAX_LUT=$MAX_LUT MIN_LUT=$MIN_LUT)"

    # ---- 1. resolve clk / ce / sr net per FF --------------------------
    array unset CLKOF; array unset CEOF; array unset SROF
    puts "\[CS\] resolving clocks (all_registers per clock) ..."
    foreach clk [get_clocks -quiet] {
        set nm [get_property -quiet NAME $clk]
        foreach r [all_registers -quiet -clock $clk] { set CLKOF($r) $nm }
    }
    puts "\[CS\] resolving CE nets ..."
    _cs_assign $ffs {REF_PIN_NAME == CE} CEOF
    puts "\[CS\] resolving set/reset nets ..."
    _cs_assign $ffs $SR SROF

    # ---- already-placed FFs (FF has a LOC = committed to a site) --------
    # This is a mostly-LOGICAL netlist; only a PORTION is placed/routed. A FF
    # is "already placed" iff it has a non-empty LOC property. (Do NOT expand
    # IS_ROUTE_FIXED nets to cells: clock/reset nets are route-fixed and would
    # pull in every FF on the clock, hugely over-counting.) Modules whose FFs
    # are mostly placed are already committed and are dropped from the ranked
    # candidate tables.
    puts "\[CS\] flagging already-placed FFs (non-empty LOC property) ..."
    array unset PLACED
    set locs [get_property -quiet LOC $ffs]
    foreach f $ffs l $locs { if {$l ne ""} { set PLACED($f) 1 } }
    puts "\[CS\]   already-placed FFs (have LOC) = [array size PLACED] / $total"

    # ---- protected FFs: ASYNC_REG / DONT_TOUCH / CDC must NOT be restructured -
    # CDC synchronizers (ASYNC_REG), do-not-touch regs, and any cell/net whose name
    # matches the CDC regex (e.g. *_cdc, *sync*, xpm_cdc) are intentional clock-
    # domain-crossing logic; their control signals are excluded from candidates.
    puts "\[CS\] flagging protected FFs (ASYNC_REG / DONT_TOUCH / CDC-name) ..."
    array unset PROT
    set aregs [get_property -quiet ASYNC_REG $ffs]
    set dtchs [get_property -quiet DONT_TOUCH $ffs]
    foreach f $ffs a $aregs d $dtchs {
        if {[_cs_boolset $a] || [_cs_boolset $d] || [regexp -nocase $CDCRE $f]} { set PROT($f) 1 }
    }
    puts "\[CS\]   protected FFs (ASYNC_REG/DONT_TOUCH/CDC-name) = [array size PROT]"

    # ---- 2. LUT count per hierarchy prefix (module cap) ----------------
    puts "\[CS\] counting LUTs per hierarchy prefix (module cap ~$MAX_LUT LUTs) ..."
    array unset LUTCNT
    _cs_build_lutcnt LUTCNT

    # ---- 3. per-FF signature; module = maximal ancestor <= MAX_LUT LUTs
    puts "\[CS\] building signatures + per-module tallies ..."
    array unset MODCS      ;# "module\x00sig" -> count   (CS size within module)
    array unset MODFF      ;# module -> FF count
    array unset MODPLACED  ;# module -> # already-placed FFs (have LOC)
    array unset MODOF      ;# FF -> its module (for later control-signal drill-down)
    array unset ALLCS      ;# sig -> global count
    foreach f $ffs {
        set clk [expr {[info exists CLKOF($f)] ? $CLKOF($f) : "-"}]
        set ce  [expr {[info exists CEOF($f)]  ? $CEOF($f)  : 1}]
        set sr  [expr {[info exists SROF($f)]  ? $SROF($f)  : 0}]
        set sig "$clk|$ce|$sr"
        incr ALLCS($sig)
        set mod [_cs_module $f $MAX_LUT LUTCNT]
        if {$mod eq ""} { set mod "<top>" }
        set MODOF($f) $mod
        incr MODFF($mod)
        incr MODCS($mod\x00$sig)
        if {[info exists PLACED($f)]} { incr MODPLACED($mod) }
    }

    # ---- 3. overall metrics + global CS-size histogram -----------------
    set uniqCS [array size ALLCS]
    set sizes {}
    array set HIST {}
    foreach b {1 2-4 5-16 17-64 65-256 257-1024 >1024} { set HIST($b) 0 }
    set largest 0
    foreach {sig c} [array get ALLCS] {
        lappend sizes $c
        incr HIST([_cs_bin $c])
        if {$c > $largest} { set largest $c }
    }
    set sizes [lsort -integer $sizes]
    set medianAll [_cs_median $sizes]
    set ffPerCsAll [expr {$uniqCS ? double($total)/$uniqCS : 0}]
    set fragAll [expr {$total ? 1.0 - double($largest)/$total : 0}]

    # ---- 4. per-module metrics ----------------------------------------
    # gather per-module list of CS sizes
    array unset MSIZES
    foreach key [array names MODCS] {
        set mod [lindex [split $key \x00] 0]
        lappend MSIZES($mod) $MODCS($key)
    }
    set rows {}
    foreach mod [array names MODFF] {
        set ff $MODFF($mod)
        set lut [expr {[info exists LUTCNT($mod)] ? $LUTCNT($mod) : 0}]
        set szs [lsort -integer $MSIZES($mod)]
        set cs [llength $szs]
        set lg [lindex $szs end]
        set med [_cs_median $szs]
        set ffpercs [expr {$cs ? double($ff)/$cs : 0}]
        set frag [expr {$ff ? 1.0 - double($lg)/$ff : 0}]
        set dens [expr {$ff ? double($cs)/$ff : 0}]
        set prio [expr {$ff * $dens}]
        set l2  [expr {$ff > 1 ? log($ff)/log(2) : 0}]
        set score [expr {$frag * $dens * $l2}]
        set pl [expr {[info exists MODPLACED($mod)] ? $MODPLACED($mod) : 0}]
        set plfrac [expr {$ff ? double($pl)/$ff : 0}]
        lappend rows [list $mod $ff $lut $cs $ffpercs $lg $med $frag $dens $prio $score $pl $plfrac]
    }
    # rank by SCORE = frag * density * log2(FF)  (desc)
    set ranked [lsort -real -decreasing -index 10 $rows]

    # ---- 5. write outputs ---------------------------------------------
    set csv [open $outdir/control_set_by_module.csv w]
    puts $csv "module,ff_count,lut_count,cs_count,ff_per_cs,largest_cs,median_cs,frag_index,cs_density,priority,score,placed_ff,placed_frac"
    foreach r $ranked {
        lassign $r mod ff lut cs ffpercs lg med frag dens prio score pl plfrac
        puts $csv [format "%s,%d,%d,%d,%.2f,%d,%.1f,%.3f,%.5f,%.1f,%.3f,%d,%.3f" $mod $ff $lut $cs $ffpercs $lg $med $frag $dens $prio $score $pl $plfrac]
    }
    close $csv

    set rpt [open $outdir/control_set_summary.rpt w]
    puts $rpt "########################################################################"
    puts $rpt "# Control-set fragmentation analysis"
    puts $rpt "# control set = { clock net | clock-enable net | set/reset net }"
    puts $rpt "# module = maximal hierarchy instance with <= MAX_LUT=$MAX_LUT LUTs"
    puts $rpt "########################################################################"
    puts $rpt ""
    puts $rpt "== OVERALL =="
    puts $rpt [format "  total FFs                 : %d" $total]
    puts $rpt [format "  unique control sets       : %d" $uniqCS]
    puts $rpt [format "  FFs per control set       : %.2f" $ffPerCsAll]
    puts $rpt [format "  largest control set (FFs) : %d" $largest]
    puts $rpt [format "  median control set (FFs)  : %.1f" $medianAll]
    puts $rpt [format "  fragmentation index       : %.3f  (1 - largest/total)" $fragAll]
    puts $rpt ""
    puts $rpt "== control-set SIZE histogram (how many control sets have N FFs) =="
    puts $rpt [format "  %-10s %10s" "bucket" "#ctrl-sets"]
    foreach b {1 2-4 5-16 17-64 65-256 257-1024 >1024} {
        puts $rpt [format "  %-10s %10d" $b $HIST($b)]
    }
    puts $rpt ""
    # right-sized partitions only, EXCLUDING any module that has LOC'd (already-placed) cells
    set sized {}
    foreach r $ranked { if {[lindex $r 2] >= $MIN_LUT && [lindex $r 2] <= $MAX_LUT && [lindex $r 1] >= $MIN_FF && [lindex $r 11] == 0} { lappend sized $r } }

    puts $rpt "== TOP $TOPN right-sized modules by SCORE = frag x density x log2(FF)  (LUTs $MIN_LUT..$MAX_LUT, FF >= $MIN_FF, NO LOC'd cells) =="
    puts $rpt "   -> strongest control->datapath candidates (fragmented AND large enough to matter, still fully LOGICAL)"
    puts $rpt [format "  %-9s %-7s %-8s %-7s %-9s %-9s %-9s  %s" "SCORE" "#LUT" "#FF" "#CS" "FF/CS" "frag" "density" "module"]
    set byscore [lsort -real -decreasing -index 10 $sized]
    set n 0
    foreach r $byscore {
        lassign $r mod ff lut cs ffpercs lg med frag dens prio score pl plfrac
        puts $rpt [format "  %-9.3f %-7d %-8d %-7d %-9.1f %-9.3f %-9.5f  %s" $score $lut $ff $cs $ffpercs $frag $dens $mod]
        incr n; if {$n >= $TOPN} break
    }
    puts $rpt ""
    puts $rpt "== also: MOST FRAGMENTED right-sized modules (lowest FF/CS first) =="
    puts $rpt [format "  %-7s %-8s %-7s %-9s %-9s %-9s  %s" "#LUT" "#FF" "#CS" "FF/CS" "frag" "density" "module"]
    set fragged [lsort -real -increasing -index 4 $sized]
    set n 0
    foreach r $fragged {
        lassign $r mod ff lut cs ffpercs lg med frag dens prio score pl plfrac
        puts $rpt [format "  %-7d %-8d %-7d %-9.1f %-9.3f %-9.5f  %s" $lut $ff $cs $ffpercs $frag $dens $mod]
        incr n; if {$n >= $TOPN} break
    }

    # ---- 6. control-signal fanout INSIDE the top candidate modules ------
    # Within each identified module, CE/SR nets driving <= FANOUT_MAX FFs are the
    # fragmenting control signals (each spawns a tiny control set) - the ones to
    # push into the datapath. Signals whose target FFs carry ASYNC_REG/DONT_TOUCH
    # are EXCLUDED (protected: CDC syncs / do-not-restructure).
    puts "\[CS\] control-signal fanout within top $TOPN modules ..."
    array unset TOPSET
    set k 0
    foreach r $byscore { set TOPSET([lindex $r 0]) 1; incr k; if {$k >= $TOPN} break }
    array unset CEF ; array unset SRF ; array unset NETPROT
    array unset CEFF ; array unset SRFF
    foreach f $ffs {
        set mod $MODOF($f)
        if {![info exists TOPSET($mod)]} continue
        if {[info exists CEOF($f)]} {
            set ky $mod\x00$CEOF($f)
            incr CEF($ky) ; lappend CEFF($ky) $f
            if {[info exists PROT($f)]} { set NETPROT($CEOF($f)) 1 }
        }
        if {[info exists SROF($f)]} {
            set ky $mod\x00$SROF($f)
            incr SRF($ky) ; lappend SRFF($ky) $f
            if {[info exists PROT($f)]} { set NETPROT($SROF($f)) 1 }
        }
    }
    array unset MODCE ; array unset MODSR
    foreach key [array names CEF] {
        set i [string first \x00 $key]
        lappend MODCE([string range $key 0 [expr {$i-1}]]) [list [string range $key [expr {$i+1}] end] $CEF($key)]
    }
    foreach key [array names SRF] {
        set i [string first \x00 $key]
        lappend MODSR([string range $key 0 [expr {$i-1}]]) [list [string range $key [expr {$i+1}] end] $SRF($key)]
    }

    # ---- also treat CDC-named control NETS as protected (do not touch CDC) ----
    foreach ky [array names CEF] {
        set net [string range $ky [expr {[string first \x00 $ky]+1}] end]
        if {[regexp -nocase $CDCRE $net]} { set NETPROT($net) 1 }
    }
    foreach ky [array names SRF] {
        set net [string range $ky [expr {[string first \x00 $ky]+1}] end]
        if {[regexp -nocase $CDCRE $net]} { set NETPROT($net) 1 }
    }

    # ---- D-pin setup slack: headroom to fold the control into the datapath ----
    # Moving a CE/SR into the datapath adds a mux delay on the FF's D path, so it
    # is only safe where the D-pin has setup slack to spare. Collect the D pins of
    # the low-fanout (non-protected) candidate FFs and measure worst setup slack.
    puts "\[CS\] measuring D-pin setup slack on low-fanout candidate FFs ..."
    array unset CANDFF
    foreach ky [array names CEF] {
        if {$CEF($ky) <= $FANOUT_MAX} {
            set net [string range $ky [expr {[string first \x00 $ky]+1}] end]
            if {![info exists NETPROT($net)]} { foreach f $CEFF($ky) { set CANDFF($f) 1 } }
        }
    }
    foreach ky [array names SRF] {
        if {$SRF($ky) <= $FANOUT_MAX} {
            set net [string range $ky [expr {[string first \x00 $ky]+1}] end]
            if {![info exists NETPROT($net)]} { foreach f $SRFF($ky) { set CANDFF($f) 1 } }
        }
    }
    array unset DSLACK
    set cffs [array names CANDFF]
    puts "\[CS\]   candidate FFs (low-fanout, unprotected) = [llength $cffs]"
    if {[llength $cffs]} {
        set dpins [get_pins -quiet -filter {REF_PIN_NAME == D} -of [get_cells -quiet $cffs]]
        if {[llength $dpins]} {
            set paths [get_timing_paths -quiet -delay_type max -nworst 1 -max_paths [expr {[llength $dpins]+16}] -to $dpins]
            foreach p $paths {
                set ep [get_property -quiet ENDPOINT_PIN $p]
                if {$ep eq ""} continue
                set pc [get_property -quiet PARENT_CELL $ep]
                if {$pc ne ""} { set DSLACK($pc) [get_property -quiet SLACK $p] }
            }
        }
    }
    puts "\[CS\]   D-pin slacks resolved = [array size DSLACK]"

    set sigcsv [open $outdir/control_signals_lowfanout.csv w]
    puts $sigcsv "module,kind,net,fanout,protected,worst_dpin_slack_ns,movable"

    puts $rpt ""
    puts $rpt "== PER-MODULE CONTROL-SIGNAL FANOUT  (top $TOPN candidate modules) =="
    puts $rpt "   control signal = a CE or SR net; fanout = # FFs it drives INSIDE the module."
    puts $rpt "   loFO_* = # signals with fanout <= $FANOUT_MAX (fragmenting); mov* = those whose WORST target"
    puts $rpt "     D-pin setup slack > $SLACK_MIN ns (room to add a datapath mux => SAFE to fold into datapath);"
    puts $rpt "   gov_FF = FFs the low-fanout signals govern; prot_sig = signals SKIPPED (ASYNC_REG/DONT_TOUCH/CDC);"
    puts $rpt "   histFO1-4 = signal count at fanout 1/2/3/4.  Per-signal slack -> control_signals_lowfanout.csv"
    puts $rpt [format "  %-8s %-8s %-7s %-7s %-8s %-9s %-11s  %s" "loFO_CE" "loFO_SR" "movCE" "movSR" "gov_FF" "prot_sig" "histFO1-4" "module"]
    array unset MOVCE ; array unset MOVSR
    foreach r $byscore {
        set mod [lindex $r 0]
        array unset H ; foreach b {1 2 3 4 5-16 >16} { set H($b) 0 }
        set loCE 0; set loSR 0; set movCE 0; set movSR 0; set govFF 0; set protSig 0
        set cel {} ; if {[info exists MODCE($mod)]} { set cel $MODCE($mod) }
        set srl {} ; if {[info exists MODSR($mod)]} { set srl $MODSR($mod) }
        foreach pair $cel {
            lassign $pair net fo
            incr H([_cs_fobin $fo])
            if {$fo <= $FANOUT_MAX} {
                if {[info exists NETPROT($net)]} {
                    incr protSig ; puts $sigcsv "$mod,CE,$net,$fo,1,,0"
                } else {
                    set w [_cs_worstslack $CEFF($mod\x00$net) DSLACK]
                    set wtxt "NA" ; if {$w ne ""} { set wtxt [format %.3f $w] }
                    set mv [expr {($w ne "" && $w > $SLACK_MIN) ? 1 : 0}]
                    incr loCE ; incr govFF $fo
                    if {$mv} { incr movCE ; foreach f $CEFF($mod\x00$net) { set MOVCE($f) 1 } }
                    puts $sigcsv "$mod,CE,$net,$fo,0,$wtxt,$mv"
                }
            }
        }
        foreach pair $srl {
            lassign $pair net fo
            incr H([_cs_fobin $fo])
            if {$fo <= $FANOUT_MAX} {
                if {[info exists NETPROT($net)]} {
                    incr protSig ; puts $sigcsv "$mod,SR,$net,$fo,1,,0"
                } else {
                    set w [_cs_worstslack $SRFF($mod\x00$net) DSLACK]
                    set wtxt "NA" ; if {$w ne ""} { set wtxt [format %.3f $w] }
                    set mv [expr {($w ne "" && $w > $SLACK_MIN) ? 1 : 0}]
                    incr loSR ; incr govFF $fo
                    if {$mv} { incr movSR ; foreach f $SRFF($mod\x00$net) { set MOVSR($f) 1 } }
                    puts $sigcsv "$mod,SR,$net,$fo,0,$wtxt,$mv"
                }
            }
        }
        puts $rpt [format "  %-8d %-8d %-7d %-7d %-8d %-9d %-11s  %s" $loCE $loSR $movCE $movSR $govFF $protSig "$H(1)/$H(2)/$H(3)/$H(4)" $mod]
    }
    close $sigcsv

    # ---- emit CONTROL_SET_REMAP tags for the movable candidate FFs ------------
    # ENABLE = move clock-enable into datapath; RESET = move set/reset; ALL = both.
    # A FF is tagged by which of its control signals are movable (low-fanout,
    # unprotected, worst D-pin setup slack > SLACK_MIN).
    array unset TAG
    foreach f [array names MOVCE] { set TAG($f) [expr {[info exists MOVSR($f)] ? "ALL" : "ENABLE"}] }
    foreach f [array names MOVSR] { if {![info exists MOVCE($f)]} { set TAG($f) "RESET" } }
    array unset NTAG ; set NTAG(ENABLE) 0 ; set NTAG(RESET) 0 ; set NTAG(ALL) 0
    set apf [open $outdir/apply_control_set_remap.tcl w]
    puts $apf "# CONTROL_SET_REMAP - move fragmenting control logic into the datapath."
    puts $apf "#   ENABLE = clock-enable only | RESET = set/reset only | ALL = both."
    puts $apf "# Candidates: low-fanout (<= $FANOUT_MAX) CE/SR signals in the top $TOPN modules,"
    puts $apf "# excluding already-placed (LOC'd), ASYNC_REG/DONT_TOUCH/CDC cells, and requiring"
    puts $apf "# worst D-pin setup slack > $SLACK_MIN ns. Review before applying, then re-run place."
    puts $apf ""
    foreach f [lsort [array names TAG]] {
        set fesc [string map [list {[} {\[} {]} {\]}] $f]
        puts $apf "set_property CONTROL_SET_REMAP $TAG($f) \[get_cells {$fesc}\]"
        incr NTAG($TAG($f))
    }
    close $apf
    puts "\[CS\]   CONTROL_SET_REMAP: ENABLE=$NTAG(ENABLE) RESET=$NTAG(RESET) ALL=$NTAG(ALL) -> apply_control_set_remap.tcl"

    puts $rpt ""
    puts $rpt [format "  CONTROL_SET_REMAP candidates (movable FFs): ENABLE=%d  RESET=%d  ALL=%d  -> apply_control_set_remap.tcl" $NTAG(ENABLE) $NTAG(RESET) $NTAG(ALL)]

    close $rpt

    set dt [expr {[clock seconds]-$t0}]
    puts "\[CS\] DONE in ${dt}s -> $outdir/control_set_summary.rpt (+ control_set_by_module.csv, control_signals_lowfanout.csv, apply_control_set_remap.tcl)"
    set fh [open $outdir/control_set_summary.rpt r]; puts [read $fh]; close $fh
}
