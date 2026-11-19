# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
###########################################################################
## detect.tcl   (READ-ONLY - no design mutation)
##
## FPGA Broadcast Control Network Analyzer (BCNA).
##
## Identifies PRE-PLACE timing-closure risks caused by broadcast control
## networks: high-fanout drivers, wide hierarchy spans, and mixed macro/fabric
## control distribution in a synthesis or opt_design netlist.
##
## For every SIGNAL net whose fanout >= MIN_FANOUT we resolve:
##   - the DRIVER cell and its type      (FF / LUT / DSP / RAM / OTHER)
##   - the FANOUT                        (# leaf load pins)
##   - the HIERARCHY SPAN                (# distinct modules that contain a load,
##                                        module = hierarchy prefix at MODULE_DEPTH)
##   - MACRO vs FABRIC load split        (DSP/BRAM/URAM loads vs LUT/FF/SRL loads)
##   - IMPACTED LOGIC fraction           (fabric-logic cells in the spanned modules
##                                        / total fabric-logic cells in the design)
##
## and classify it against the BCNA rules:
##   GLOBAL_BCAST     : fanout >= 1000 and span >= 10
##   LUT_BCAST        : LUT driver and fanout >= 500 and span >= 5
##   FF_REPL          : FF  driver and fanout >= 500                (replication candidate)
##   SHARED_DRIVER    : span >= 10 modules and impacts >= 20% design logic
##   MIXED_MACRO_FAB  : fanout >= 150 with BOTH macro and fabric loads
##   FORCE_MAX_FANOUT : fanout >= 200 and span >= 5
##
## Fanout risk = log2(fanout) x hierarchy_span x driver_weight x impacted_logic_weight
##   driver_weight          = DRW_LUT/FF/DSP/RAM/OTHER (LUT weighted highest -
##                            combinational broadcast is the worst / must be
##                            registered before replication)
##   impacted_logic_weight  = 1 + impacted_logic_fraction   (range 1..2)
##
## EFFICIENCY: the candidate high-fanout nets are pulled in ONE tool-side filtered
## query (get_nets ... -filter {FLAT_PIN_COUNT >= ...}); only those few thousand
## nets get the detailed per-net leaf-pin expansion. Per-module design-logic
## counts are built in a single pass over LUT+FF cells.
##
## Usage (design already open):
##   source detect.tcl
##   run_bcna_analysis <outdir>
## Env tunables (all optional): see _bcna_env defaults below.
###########################################################################

proc _bcna_env {name def} {
    if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
    return $def
}

# classify a cell REF_NAME into a coarse driver/load class.
proc _bcna_class {ref} {
    if {$ref eq ""}                     { return OTHER }
    if {[string match FD* $ref]}        { return FF }
    if {[string match LD* $ref]}        { return FF }
    if {[string match LUT* $ref]}       { return LUT }
    if {[string match SRL* $ref]}       { return LUT }
    if {[string match RAMD* $ref]}      { return LUT }
    if {[string match RAMS* $ref]}      { return LUT }
    if {[string match DSP* $ref]}       { return DSP }
    if {[string match RAMB* $ref]}      { return RAM }
    if {[string match URAM* $ref]}      { return RAM }
    if {[string match FIFO* $ref]}      { return RAM }
    return OTHER
}

# macro = hard block (DSP/BRAM/URAM); everything else counts as fabric.
proc _bcna_is_macro {cls} {
    return [expr {$cls eq "DSP" || $cls eq "RAM"}]
}

# a driver we intentionally SKIP: global clock/reset buffers are supposed to be
# high-fanout on dedicated global routing - not a fabric broadcast problem.
proc _bcna_is_global_buf {ref} {
    foreach p {BUFG* BUFGCE* BUFGCTRL* BUFCE* BUFDIV* BUFGCE_DIV* BUFG_GT* GCLK*} {
        if {[string match $p $ref]} { return 1 }
    }
    return 0
}

# module = first min(depth, N-1) '/'-separated segments of a cell name.
proc _bcna_modprefix {name depth} {
    set segs [split $name /]
    set n [llength $segs]
    set take [expr {($n - 1) < $depth ? ($n - 1) : $depth}]
    if {$take <= 0} { return "<top>" }
    return [join [lrange $segs 0 [expr {$take - 1}]] /]
}

proc _bcna_dweight {cls} {
    switch -- $cls {
        LUT     { return [_bcna_env DRW_LUT   1.3] }
        FF      { return [_bcna_env DRW_FF    1.0] }
        DSP     { return [_bcna_env DRW_DSP   0.8] }
        RAM     { return [_bcna_env DRW_RAM   0.8] }
        default { return [_bcna_env DRW_OTHER 0.6] }
    }
}

# Tiered FORCE_MAX_FANOUT target as a function of net fanout (# loads). Bigger
# nets get a larger per-replica cap so replica count (~fanout/target) stays sane
# under MAX_FANOUT_MODE (~1 replica per clock region for the largest nets).
# Tiers "hi:val ..." evaluated low->high; first tier with fanout <= hi wins
# ("inf" = top). Env FMF_TIERS overrides. Clamped to < fanout (never disables
# replication).
proc _bcna_fmf_target {fanout} {
    set tiers [_bcna_env FMF_TIERS {1000:128 4000:256 10000:512 64000:1024 inf:2048}]
    set target ""
    foreach t $tiers {
        lassign [split $t :] hi val
        if {$hi eq "inf" || $fanout <= $hi} { set target $val ; break }
    }
    if {$target eq ""} { set target 2048 }
    if {$target >= $fanout} { set target [expr {$fanout > 2 ? $fanout - 1 : 2}] }
    return $target
}

proc run_bcna_analysis {outdir} {
    file mkdir $outdir
    set t0 [clock seconds]

    # ---- thresholds / tunables ----------------------------------------
    set MIN_FANOUT   [_bcna_env MIN_FANOUT   150]  ;# smallest fanout worth scanning (Mixed rule floor)
    set MODULE_DEPTH [_bcna_env MODULE_DEPTH 4]    ;# hierarchy depth used for span / impacted-logic modules
    set TOPN         [_bcna_env TOPN         50]   ;# how many top-risk nets to table + constrain
    set TARGET_FANOUT [_bcna_env TARGET_FANOUT ""]  ;# non-empty forces ONE FMF value for all nets (A/B); empty = tiered _bcna_fmf_target

    set GLOBAL_FANOUT [_bcna_env GLOBAL_FANOUT 1000]
    set GLOBAL_SPAN   [_bcna_env GLOBAL_SPAN   10]
    set LUT_FANOUT    [_bcna_env LUT_FANOUT    500]
    set LUT_SPAN      [_bcna_env LUT_SPAN      5]
    set FF_REPL_FANOUT [_bcna_env FF_REPL_FANOUT 500]
    set SHARED_MODULES [_bcna_env SHARED_MODULES 10]
    set SHARED_FRAC    [_bcna_env SHARED_FRAC    0.20]
    set MIXED_FANOUT   [_bcna_env MIXED_FANOUT   150]
    set FMF_FANOUT     [_bcna_env FMF_FANOUT     200]
    set FMF_SPAN       [_bcna_env FMF_SPAN       5]

    puts "\[BCNA\] MIN_FANOUT=$MIN_FANOUT MODULE_DEPTH=$MODULE_DEPTH TOPN=$TOPN"

    # ---- 1. per-module design-logic (LUT+FF) cell counts --------------
    # "design logic" = fabric LUT + FF cells; per MODULE_DEPTH-prefix count feeds
    # the impacted-logic fraction. One pass per category.
    puts "\[BCNA\] counting design-logic cells per module (depth $MODULE_DEPTH) ..."
    array unset CELLCNT
    set TOTALLOGIC 0
    foreach patt {LUT* FD* LD* SRL*} {
        foreach c [get_cells -hierarchical -quiet -filter "REF_NAME =~ $patt"] {
            incr CELLCNT([_bcna_modprefix $c $MODULE_DEPTH])
            incr TOTALLOGIC
        }
    }
    puts "\[BCNA\]   total design-logic cells = $TOTALLOGIC  modules = [array size CELLCNT]"

    # ---- 2. candidate high-fanout SIGNAL nets (tool-side filtered) -----
    # FLAT_PIN_COUNT = leaf pins incl. driver, so fanout >= MIN_FANOUT means
    # FLAT_PIN_COUNT >= MIN_FANOUT+1. Filtering in get_nets keeps this cheap.
    set thr [expr {$MIN_FANOUT + 1}]
    puts "\[BCNA\] pulling candidate nets (TYPE==SIGNAL && FLAT_PIN_COUNT >= $thr) ..."
    set cand [get_nets -hierarchical -quiet -filter "TYPE == SIGNAL && FLAT_PIN_COUNT >= $thr"]
    puts "\[BCNA\]   candidate net segments = [llength $cand]"

    # ---- 3. per-net analysis ------------------------------------------
    array unset SEEN        ;# driver-pin -> 1  (dedup hierarchical net segments)
    set rows {}
    array unset RULECNT
    foreach rn {GLOBAL_BCAST LUT_BCAST FF_REPL SHARED_DRIVER MIXED_MACRO_FAB FORCE_MAX_FANOUT} { set RULECNT($rn) 0 }
    array unset CLSCNT
    foreach cl {FF LUT DSP RAM OTHER} { set CLSCNT($cl) 0 }
    set nproc 0

    foreach net $cand {
        # driver pin (leaf output). Dedup: skip if we've already handled this driver pin.
        set dpin [get_pins -quiet -leaf -of $net -filter {DIRECTION == OUT}]
        if {[llength $dpin] > 1} { set dpin [lindex $dpin 0] }
        if {$dpin ne ""} {
            set dpn [get_property -quiet NAME $dpin]
            if {[info exists SEEN($dpn)]} continue
            set SEEN($dpn) 1
            set dcell [get_property -quiet PARENT_CELL $dpin]
            set dref  [get_property -quiet REF_NAME $dcell]
        } else {
            # port / undriven: key on the net name so we still dedup
            set dpn "PORT:[get_property -quiet NAME $net]"
            if {[info exists SEEN($dpn)]} continue
            set SEEN($dpn) 1
            set dcell [get_property -quiet NAME $net]
            set dref  "PORT"
        }
        if {[_bcna_is_global_buf $dref]} continue   ;# global clock/reset buffer - intentional
        set dclass [_bcna_class $dref]

        # loads: leaf input pins -> distinct load cells
        set lpins [get_pins -quiet -leaf -of $net -filter {DIRECTION == IN}]
        set fanout [llength $lpins]
        if {$fanout < $MIN_FANOUT} continue
        set lcells [lsort -unique [get_cells -quiet -of $lpins]]
        set lrefs  [get_property -quiet REF_NAME $lcells]

        set macro 0 ; set fabric 0
        array unset IMPMOD
        foreach lc $lcells lr $lrefs {
            set lcls [_bcna_class $lr]
            if {[_bcna_is_macro $lcls]} { incr macro } else { incr fabric }
            set IMPMOD([_bcna_modprefix $lc $MODULE_DEPTH]) 1
        }
        set span [array size IMPMOD]
        set impcells 0
        foreach m [array names IMPMOD] { if {[info exists CELLCNT($m)]} { incr impcells $CELLCNT($m) } }
        set impfrac [expr {$TOTALLOGIC ? double($impcells)/$TOTALLOGIC : 0}]

        # ---- rule classification ----
        set rules {}
        if {$fanout >= $GLOBAL_FANOUT && $span >= $GLOBAL_SPAN} { lappend rules GLOBAL_BCAST }
        if {$dclass eq "LUT" && $fanout >= $LUT_FANOUT && $span >= $LUT_SPAN} { lappend rules LUT_BCAST }
        if {$dclass eq "FF"  && $fanout >= $FF_REPL_FANOUT} { lappend rules FF_REPL }
        if {$span >= $SHARED_MODULES && $impfrac >= $SHARED_FRAC} { lappend rules SHARED_DRIVER }
        if {$fanout >= $MIXED_FANOUT && $macro > 0 && $fabric > 0} { lappend rules MIXED_MACRO_FAB }
        if {$fanout >= $FMF_FANOUT && $span >= $FMF_SPAN} { lappend rules FORCE_MAX_FANOUT }
        if {![llength $rules]} continue   ;# high fanout but under every rule - not flagged

        foreach r $rules { incr RULECNT($r) }
        incr CLSCNT($dclass)

        # ---- risk score ----
        set l2 [expr {$fanout > 1 ? log($fanout)/log(2) : 0}]
        set dw [_bcna_dweight $dclass]
        set ilw [expr {1.0 + $impfrac}]
        set risk [expr {$l2 * $span * $dw * $ilw}]

        lappend rows [list $dcell $dref $dclass $fanout $span $macro $fabric [array size IMPMOD] $impfrac $risk [join $rules ";"] $dpn]
        incr nproc
    }
    puts "\[BCNA\] flagged broadcast nets = $nproc  (unique drivers = [array size SEEN])"

    set ranked [lsort -real -decreasing -index 9 $rows]

    # ---- 4. write CSV -------------------------------------------------
    set csv [open $outdir/broadcast_nets.csv w]
    puts $csv "driver_cell,driver_pin,driver_ref,driver_class,fanout,hier_span,macro_loads,fabric_loads,impacted_modules,impacted_logic_frac,risk_score,rules"
    foreach r $ranked {
        lassign $r dcell dref dclass fanout span macro fabric imod impfrac risk rules dpin
        puts $csv [format "%s,%s,%s,%s,%d,%d,%d,%d,%d,%.4f,%.3f,%s" $dcell $dpin $dref $dclass $fanout $span $macro $fabric $imod $impfrac $risk $rules]
    }
    close $csv

    # ---- 5. write summary report --------------------------------------
    set rpt [open $outdir/bcna_summary.rpt w]
    puts $rpt "########################################################################"
    puts $rpt "# FPGA Broadcast Control Network Analyzer (BCNA)"
    puts $rpt "# Pre-place timing-risk: high-fanout / wide-span / mixed macro-fabric"
    puts $rpt "# control distribution. READ-ONLY analysis."
    puts $rpt "########################################################################"
    puts $rpt ""
    puts $rpt "== SCAN =="
    puts $rpt [format "  min fanout scanned        : %d" $MIN_FANOUT]
    puts $rpt [format "  module hierarchy depth    : %d" $MODULE_DEPTH]
    puts $rpt [format "  candidate net segments    : %d" [llength $cand]]
    puts $rpt [format "  design-logic cells (LUT+FF): %d" $TOTALLOGIC]
    puts $rpt [format "  FLAGGED broadcast nets    : %d" $nproc]
    puts $rpt ""
    puts $rpt "== rule hits (a net can match several) =="
    foreach rn {GLOBAL_BCAST LUT_BCAST FF_REPL SHARED_DRIVER MIXED_MACRO_FAB FORCE_MAX_FANOUT} {
        puts $rpt [format "  %-18s %8d" $rn $RULECNT($rn)]
    }
    puts $rpt ""
    puts $rpt "== flagged driver class =="
    foreach cl {FF LUT DSP RAM OTHER} { puts $rpt [format "  %-6s %8d" $cl $CLSCNT($cl)] }
    puts $rpt ""
    puts $rpt "== TOP $TOPN broadcast nets by RISK = log2(fanout) x span x driver_wt x (1+impacted_frac) =="
    puts $rpt [format "  %-8s %-6s %-7s %-6s %-6s %-6s %-7s  %-28s %s" "RISK" "class" "fanout" "span" "macro" "fabric" "imp%" "rules" "driver"]
    set n 0
    foreach r $ranked {
        lassign $r dcell dref dclass fanout span macro fabric imod impfrac risk rules
        puts $rpt [format "  %-8.2f %-6s %-7d %-6d %-6d %-6d %-6.1f  %-28s %s" $risk $dclass $fanout $span $macro $fabric [expr {$impfrac*100}] $rules $dcell]
        incr n ; if {$n >= $TOPN} break
    }
    close $rpt

    # ---- 6. emit ready-to-source constraints --------------------------
    # For each top-risk actionable driver pick a MAX_FANOUT_MODE and a fanout
    # target. Review before applying, then re-run place.
    set apf [open $outdir/fix.tcl w]
    set axf [open $outdir/fix.xdc w]
    puts $apf "## fix.tcl - BCNA fanout constraints (from detect.tcl). Source to APPLY to the open design."
    puts $axf "# fix.xdc - BCNA fanout constraints (from detect.tcl). Add to the constraint set (read_xdc) for future runs."
    set fmf_desc [expr {$TARGET_FANOUT ne "" ? "forced $TARGET_FANOUT (all nets)" : "tiered by fanout: <=1K:128 <=4K:256 <=10K:512 <=64K:1024 >64K:2048"}]
    foreach fh2 [list $apf $axf] {
        puts $fh2 "# Reduce broadcast-control timing risk."
        puts $fh2 "#   FORCE_MAX_FANOUT target = $fmf_desc"
        puts $fh2 "#   MAX_FANOUT_MODE : GLOBAL_BCAST -> SLR ; MIXED_MACRO_FAB -> MACRO ; else CLOCK_REGION"
        puts $fh2 "#   LUT drivers: REGISTER the decode BEFORE replication (comment marked)."
        puts $fh2 "# Apply before place_design and re-run placement."
        puts $fh2 ""
    }
    set n 0 ; set ntag 0
    foreach r $ranked {
        lassign $r dcell dref dclass fanout span macro fabric imod impfrac risk rules dpin
        if {$dref eq "PORT"} { incr n ; if {$n >= $TOPN} break ; continue }
        set mode CLOCK_REGION
        if {[string first GLOBAL_BCAST $rules] >= 0}    { set mode SLR }
        if {[string first MIXED_MACRO_FAB $rules] >= 0} { set mode MACRO }
        # FORCE_MAX_FANOUT / MAX_FANOUT_MODE are NET properties: select the
        # broadcast net from the driver's OUTPUT PIN, not the driver cell.
        set sel "\[get_nets -of_objects \[get_pins {$dpin}\]\]"
        set fmf [expr {$TARGET_FANOUT ne "" ? $TARGET_FANOUT : [_bcna_fmf_target $fanout]}]
        foreach fh2 [list $apf $axf] {
            puts $fh2 "# risk=[format %.2f $risk] class=$dclass fanout=$fanout span=$span rules=$rules driver=$dcell"
            if {$dclass eq "LUT"} { puts $fh2 "#   NOTE: register this LUT decode before replicating." }
            puts $fh2 "set_property -quiet FORCE_MAX_FANOUT $fmf $sel"
            puts $fh2 "set_property -quiet MAX_FANOUT_MODE $mode $sel"
            puts $fh2 ""
        }
        incr ntag
        incr n ; if {$n >= $TOPN} break
    }
    puts $apf "puts \"\\\[BCNA\\\] applied fanout constraints on $ntag drivers\""
    close $apf
    close $axf

    set dt [expr {[clock seconds] - $t0}]
    puts "\[BCNA\] done in ${dt}s -> $outdir/{bcna_summary.rpt, broadcast_nets.csv, fix.tcl, fix.xdc}"
    if {$ntag > 0} {
        puts "\[BCNA\] to apply now:  source $outdir/fix.tcl   |   for future runs add $outdir/fix.xdc to the constraint set"
    }

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
        set _rj_counts [_rj_obj [list \
            elapsed_s              n $dt \
            nproc                  n $nproc \
            seen                   n [array size SEEN] \
            rule_global_bcast      n $RULECNT(GLOBAL_BCAST) \
            rule_lut_bcast         n $RULECNT(LUT_BCAST) \
            rule_ff_repl           n $RULECNT(FF_REPL) \
            rule_shared_driver     n $RULECNT(SHARED_DRIVER) \
            rule_mixed_macro_fab   n $RULECNT(MIXED_MACRO_FAB) \
            rule_force_max_fanout  n $RULECNT(FORCE_MAX_FANOUT) \
            cls_ff                 n $CLSCNT(FF) \
            cls_lut                n $CLSCNT(LUT) \
            cls_dsp                n $CLSCNT(DSP) \
            cls_ram                n $CLSCNT(RAM) \
            cls_other              n $CLSCNT(OTHER) \
            ntag                   n $ntag \
            totallogic             n $TOTALLOGIC \
        ]]
        set _rj_thresholds [_rj_obj [list min_fanout n $MIN_FANOUT module_depth n $MODULE_DEPTH topn n $TOPN target_fanout s [expr {$TARGET_FANOUT ne "" ? $TARGET_FANOUT : "tiered"}]]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "$nproc broadcast net(s) matched a BCNA rule (GLOBAL_BCAST=$RULECNT(GLOBAL_BCAST) LUT_BCAST=$RULECNT(LUT_BCAST) FF_REPL=$RULECNT(FF_REPL) SHARED_DRIVER=$RULECNT(SHARED_DRIVER) MIXED_MACRO_FAB=$RULECNT(MIXED_MACRO_FAB) FORCE_MAX_FANOUT=$RULECNT(FORCE_MAX_FANOUT))"
        set _rj_changes [expr {$ntag > 0 ? "Wrote fix.tcl/fix.xdc setting FORCE_MAX_FANOUT=[expr {$TARGET_FANOUT ne "" ? $TARGET_FANOUT : "tiered by fanout"}] on $ntag top-risk driver net(s)" : "No changes applied"}]
        set _rj_top [_rj_obj [list schema_version n 1 skill s "fanout_analysis" category s "early_resolution" \
            detected b [expr {$ntag > 0}] generated_at s $_rj_ts elapsed_seconds n $dt \
            trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts,\n  \"thresholds\": $_rj_thresholds\n\}\n"
        set _rj_fh [open [file join $outdir "result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[BCNA\] WARNING: could not write $outdir/result.json: $_rj_err"
    }
}

## ---------------------------------------------------------------------------
## Auto-run on `source`: analyze the CURRENTLY OPEN design and write the fanout
## constraints to <OUTDIR>/fix.tcl + <OUTDIR>/fix.xdc (default OUTDIR=".").
## Then:  source fix.tcl   to apply now, or add fix.xdc to the constraint set for
## future runs. Set env BCNA_NO_AUTORUN=1 to only load the procs (then call
## run_bcna_analysis <outdir> yourself).
## ---------------------------------------------------------------------------
if {![info exists ::env(BCNA_NO_AUTORUN)] || $::env(BCNA_NO_AUTORUN) eq "0"} {
    if {[catch {current_design} _bcna_cd] || $_bcna_cd eq ""} {
        puts "\[BCNA\] no open design - open a checkpoint first, then: source detect.tcl"
    } else {
        run_bcna_analysis [_bcna_env OUTDIR "."]
    }
}
