# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
###########################################################################
## detect_broadcast_control.tcl   (READ-ONLY - no design mutation)
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
##   source detect_broadcast_control.tcl
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

proc run_bcna_analysis {outdir} {
    file mkdir $outdir
    set t0 [clock seconds]

    # ---- thresholds / tunables ----------------------------------------
    set MIN_FANOUT   [_bcna_env MIN_FANOUT   150]  ;# smallest fanout worth scanning (Mixed rule floor)
    set MODULE_DEPTH [_bcna_env MODULE_DEPTH 4]    ;# hierarchy depth used for span / impacted-logic modules
    set TOPN         [_bcna_env TOPN         50]   ;# how many top-risk nets to table + constrain
    set TARGET_FANOUT [_bcna_env TARGET_FANOUT 200] ;# FORCE_MAX_FANOUT target in the constraint file

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

        lappend rows [list $dcell $dref $dclass $fanout $span $macro $fabric [array size IMPMOD] $impfrac $risk [join $rules ";"]]
        incr nproc
    }
    puts "\[BCNA\] flagged broadcast nets = $nproc  (unique drivers = [array size SEEN])"

    set ranked [lsort -real -decreasing -index 9 $rows]

    # ---- 4. write CSV -------------------------------------------------
    set csv [open $outdir/broadcast_nets.csv w]
    puts $csv "driver_cell,driver_ref,driver_class,fanout,hier_span,macro_loads,fabric_loads,impacted_modules,impacted_logic_frac,risk_score,rules"
    foreach r $ranked {
        lassign $r dcell dref dclass fanout span macro fabric imod impfrac risk rules
        puts $csv [format "%s,%s,%s,%d,%d,%d,%d,%d,%.4f,%.3f,%s" $dcell $dref $dclass $fanout $span $macro $fabric $imod $impfrac $risk $rules]
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
    set apf [open $outdir/apply_fanout_constraints.tcl w]
    puts $apf "# BCNA fanout constraints - reduce broadcast-control timing risk."
    puts $apf "#   FORCE_MAX_FANOUT target = $TARGET_FANOUT"
    puts $apf "#   MAX_FANOUT_MODE : GLOBAL_BCAST -> SLR ; MIXED_MACRO_FAB -> MACRO ; else CLOCK_REGION"
    puts $apf "#   LUT drivers: REGISTER the decode BEFORE replication (comment marked)."
    puts $apf "# Review, then source before place_design and re-run placement."
    puts $apf ""
    set n 0
    foreach r $ranked {
        lassign $r dcell dref dclass fanout span macro fabric imod impfrac risk rules
        if {$dref eq "PORT"} { incr n ; if {$n >= $TOPN} break ; continue }
        set mode CLOCK_REGION
        if {[string first GLOBAL_BCAST $rules] >= 0}    { set mode SLR }
        if {[string first MIXED_MACRO_FAB $rules] >= 0} { set mode MACRO }
        set esc [string map [list {[} {\[} {]} {\]}] $dcell]
        puts $apf "# risk=[format %.2f $risk] class=$dclass fanout=$fanout span=$span rules=$rules"
        if {$dclass eq "LUT"} { puts $apf "#   NOTE: register this LUT decode before replicating." }
        puts $apf "set_property FORCE_MAX_FANOUT $TARGET_FANOUT \[get_cells {$esc}\]"
        puts $apf "set_property MAX_FANOUT_MODE $mode \[get_cells {$esc}\]"
        puts $apf ""
        incr n ; if {$n >= $TOPN} break
    }
    close $apf

    set dt [expr {[clock seconds] - $t0}]
    puts "\[BCNA\] done in ${dt}s -> $outdir/{bcna_summary.rpt, broadcast_nets.csv, apply_fanout_constraints.tcl}"
}
