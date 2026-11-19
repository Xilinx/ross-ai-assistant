# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#############################################################################
## adaptive_physopt.tcl
##
## Adaptive post-place congestion-gated + met/not-met AggressiveExplore phys_opt.
## Implements the "vivado-adaptive-physopt" skill (see SKILL.md).
##
## Flow: report timing + congestion on the post-place design, then
##   - congestion > 5  -> single plain phys_opt_design -directive AggressiveExplore
##   - congestion <= 5 -> up to 5 iterations of:
##        timing MET     -> over-constrain (set_clock_uncertainty) + AggressiveExplore
##        timing NOT met -> AggressiveExplore with NO over-constraining
##     re-timing under the REAL constraints after each pass to decide the next.
## Any temporary uncertainty is stripped before the final re-time, and the
## checkpoint after the final iteration is written (final.dcp). No scoring/best.dcp.
##
## Usage (run AFTER place_design, on the IN-MEMORY design):
##   source adaptive_physopt.tcl
## It uses the currently-open design (no reload). If NO design is open it will
## open_checkpoint env PHYSOPT_DCP instead.
##
## Env tunables:
##   PHYSOPT_MODE      conservative|balanced|aggressive   (default balanced)
##   PHYSOPT_CLOCKS    explicit clock set to over-constrain; default = all clocks
##   PHYSOPT_MAX_ITER  max AggressiveExplore passes        (default 5)
##   PHYSOPT_OUT_DIR   output directory                    (default <run_dir>/physopt_run,
##                                                          else <cwd>/physopt_run; always absolute)
##   PHYSOPT_DCP       post-place checkpoint to open ONLY if no design is open
#############################################################################

# -------------------------- config (env-overridable) ---------------------
proc _po_env {name def} {
    if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
    return $def
}
set DCP      [_po_env PHYSOPT_DCP     ""]
set MODE     [_po_env PHYSOPT_MODE    balanced]
set CLOCKS   [_po_env PHYSOPT_CLOCKS  ""]
set MAX_ITER [_po_env PHYSOPT_MAX_ITER 5]
# Always resolve to an absolute path anchored at the run directory (set by the
# generated run.tcl), never a bare "./physopt_run": a relative path is resolved
# against whatever the process cwd happens to be, and Vivado's checkpoint writer
# then fails with "[Runs 95-5] Couldn't create .../physopt_run/.final_dcpx_tmp".
if {[info exists ::run_dir] && $::run_dir ne ""} {
    set _po_base $::run_dir
} else {
    set _po_base [pwd]
}
set OUT_DIR  [file normalize [_po_env PHYSOPT_OUT_DIR [file join $_po_base physopt_run]]]
file mkdir $OUT_DIR

# -------------------------- config ---------------------------------------
array unset UNC_PCT
array set UNC_PCT {conservative 0.02 balanced 0.05 aggressive 0.09}
if {![info exists UNC_PCT($MODE)]} {
    puts "WARN: unknown mode '$MODE', defaulting to balanced"
    set MODE balanced
}
set OC_PCT        $UNC_PCT($MODE)
set OC_CAP_PCT    0.10     ;# never tighten more than 10% of period
set CONG_HIGH     5        ;# congestion > this (level 5) is HIGH -> single plain pass
set t0 [clock seconds]

# -------------------------- logging --------------------------------------
set LOG {}
proc log {msg} {
    global LOG
    set line "\[[clock format [clock seconds] -format %H:%M:%S]\] $msg"
    puts $line
    lappend LOG $line
}

# -------------------------- helpers --------------------------------------
# Parse WNS / TNS / failing-endpoints (setup) from a report_timing_summary file.
proc parse_timing_summary {file} {
    set wns 0.0 ; set tns 0.0 ; set fep 0
    if {![file exists $file]} { return [list $wns $tns $fep] }
    set fh [open $file r]
    set data [read $fh]
    close $fh
    if {[regexp {Design Timing Summary(.*)} $data -> tail]} {
        foreach line [split $tail "\n"] {
            set t [string trim $line]
            if {$t eq ""} { continue }
            # first numeric row: WNS  TNS  TNS-Failing-Endpoints  TNS-Total-Endpoints ...
            if {[regexp {^(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\d+)\s+(\d+)} $t -> w tn f tot]} {
                set wns $w ; set tns $tn ; set fep $f
                break
            }
        }
    }
    return [list $wns $tns $fep]
}

# Maximum congestion level (0-7) parsed from a report_design_analysis -congestion file.
proc max_congestion {rptfile} {
    set maxlvl -1
    if {![file exists $rptfile]} { return $maxlvl }
    set fh [open $rptfile r]
    while {[gets $fh line] >= 0} {
        if {[regexp -nocase {north|south|east|west|horizontal|vertical} $line]} {
            foreach tok [regexp -all -inline {\d+} $line] {
                if {$tok <= 7 && $tok > $maxlvl} { set maxlvl $tok }
            }
        }
    }
    close $fh
    return $maxlvl
}

proc cell_count {} { return [llength [get_cells -hier -quiet]] }

proc pctimp {new base} {
    if {$base == 0} { return 0.0 }
    return [expr {double($new - $base) / abs(double($base)) * 100.0}]
}

# =========================================================================
# PHASE 1 : baseline characterization
# =========================================================================
log "PHASE 1: baseline characterization"
if {![catch {current_design} _cd] && $_cd ne ""} {
    log "  using already-open IN-MEMORY design [get_property NAME [current_design]]"
} elseif {$DCP ne ""} {
    log "  no open design - opening checkpoint $DCP"
    open_checkpoint $DCP
} else {
    error "adaptive_physopt: no design is open and PHYSOPT_DCP is not set. Run this AFTER place_design (in-memory), or set env PHYSOPT_DCP=<post_place.dcp>."
}
report_timing_summary -file $OUT_DIR/baseline_timing.rpt -quiet
lassign [parse_timing_summary $OUT_DIR/baseline_timing.rpt] BWNS BTNS BFEP
catch {report_design_analysis -congestion -file $OUT_DIR/baseline_cong.rpt}
set BCONG  [max_congestion $OUT_DIR/baseline_cong.rpt]
set BCELLS [cell_count]
log [format "BASELINE  WNS=%.3fns  TNS=%.3fns  failing_eps=%d  congestion=%d  cells=%d" \
        $BWNS $BTNS $BFEP $BCONG $BCELLS]

# ---- resolve the clock set for over-constraining (used when timing is MET) ----
# Over-constraining BUILDS MARGIN, so it targets a DEFINED set of clocks:
# explicit PHYSOPT_CLOCKS if given, otherwise ALL clocks in the design.
set oc_clocks {}
if {$CLOCKS ne ""} {
    foreach c $CLOCKS {
        if {[llength [get_clocks -quiet $c]]} { lappend oc_clocks $c } else { log "WARN: clock '$c' not found, skipping" }
    }
    log "over-constrain clock set (user-specified): $oc_clocks"
} else {
    foreach clk [get_clocks -quiet] { lappend oc_clocks [get_property NAME $clk] }
    log "over-constrain clock set (all clocks): [llength $oc_clocks] clock(s)"
}

# Apply setup uncertainty (= OC_PCT of each clock's own period, capped at OC_CAP_PCT).
proc _oc_apply {clocks pct cap} {
    set done {}
    foreach cn $clocks {
        set clk [get_clocks -quiet $cn]
        if {[llength $clk] == 0} { continue }
        set per [get_property PERIOD $clk]
        if {$per eq "" || $per <= 0} { continue }
        set unc  [expr {$pct * $per}]
        set capv [expr {$cap * $per}]
        if {$unc > $capv} { set unc $capv }
        set_clock_uncertainty -setup $unc $clk
        lappend done $cn
    }
    return $done
}
proc _oc_remove {clocks} {
    foreach cn $clocks {
        set clk [get_clocks -quiet $cn]
        if {[llength $clk]} { set_clock_uncertainty -setup 0.0 $clk }
    }
}

# =========================================================================
# CONGESTION GATE  +  met/not-met iterations
# =========================================================================
set rows {}            ;# per-pass records: {n overconstrained wns tns fep cong cells rt}
if {$BCONG > $CONG_HIGH} {
    # ---- HIGH congestion: single plain AggressiveExplore pass, NO over-constrain
    log "CONGESTION $BCONG > $CONG_HIGH (HIGH) -> single plain phys_opt_design pass (no over-constraining)"
    set ts [clock seconds]
    if {[catch {phys_opt_design -directive AggressiveExplore} pe]} { log "  phys_opt_design error: $pe" }
    set rt [expr {[clock seconds]-$ts}]
    report_timing_summary -file $OUT_DIR/iteration_1_timing.rpt -quiet
    lassign [parse_timing_summary $OUT_DIR/iteration_1_timing.rpt] wns tns fep
    catch {report_design_analysis -congestion -file $OUT_DIR/iteration_1_cong.rpt}
    set cong [max_congestion $OUT_DIR/iteration_1_cong.rpt]
    lappend rows [list 1 0 $wns $tns $fep $cong [cell_count] $rt]
    log [format "  pass 1 (plain): WNS=%.3f  TNS=%.3f  fail_eps=%d  cong=%d  rt=%ds" $wns $tns $fep $cong $rt]
} else {
    # ---- LOW congestion: met/not-met state machine, up to MAX_ITER iterations
    log "CONGESTION $BCONG <= $CONG_HIGH -> adaptive met/not-met loop (up to $MAX_ITER iterations)"
    set met [expr {$BWNS >= 0}]
    set pw  $BWNS ; set pt $BTNS
    for {set n 1} {$n <= $MAX_ITER} {incr n} {
        set oc {}
        if {$met} {
            set oc [_oc_apply $oc_clocks $OC_PCT $OC_CAP_PCT]
            log [format "  iter %d: timing MET -> over-constrain %.1f%% on %d clock(s) + AggressiveExplore" $n [expr {$OC_PCT*100}] [llength $oc]]
        } else {
            log "  iter $n: timing NOT met -> AggressiveExplore (no over-constraining)"
        }
        set ts [clock seconds]
        if {[catch {phys_opt_design -directive AggressiveExplore} pe]} {
            log "  phys_opt_design error: $pe -- stopping loop"
            _oc_remove $oc
            break
        }
        set rt [expr {[clock seconds]-$ts}]
        # re-time under the REAL constraints: remove any temporary uncertainty first
        _oc_remove $oc
        report_timing_summary -file $OUT_DIR/iteration_${n}_timing.rpt -quiet
        lassign [parse_timing_summary $OUT_DIR/iteration_${n}_timing.rpt] wns tns fep
        catch {report_design_analysis -congestion -file $OUT_DIR/iteration_${n}_cong.rpt}
        set cong [max_congestion $OUT_DIR/iteration_${n}_cong.rpt]
        set dwns_ps [expr {($wns-$pw)*1000.0}]
        lappend rows [list $n [expr {[llength $oc] ? 1 : 0}] $wns $tns $fep $cong [cell_count] $rt]
        log [format "  iter %d result: WNS=%.3f (%+.1fps)  TNS=%.3f  fail_eps=%d  cong=%d  rt=%ds" $n $wns $dwns_ps $tns $fep $cong $rt]
        # decide the next iteration from the REAL met/not-met state
        set met [expr {$wns >= 0}]
        # early stop on negligible movement between passes
        if {$n > 1 && abs($dwns_ps) < 1.0 && abs([pctimp $tns $pt]) < 1.0} {
            log "  negligible change vs previous pass -> stop early"
            set pw $wns ; set pt $tns
            break
        }
        set pw $wns ; set pt $tns
    }
}

# =========================================================================
# finalize : strip any temporary uncertainty, re-time, write final checkpoint
# =========================================================================
_oc_remove $oc_clocks
report_timing_summary -file $OUT_DIR/final_timing.rpt -quiet
lassign [parse_timing_summary $OUT_DIR/final_timing.rpt] FWNS FTNS FFEP
write_checkpoint -force $OUT_DIR/final.dcp
log [format "FINAL  WNS=%.3fns  TNS=%.3fns  failing_eps=%d  ->  %s" $FWNS $FTNS $FFEP $OUT_DIR/final.dcp]

# =========================================================================
# outputs : comparison table + decisions summary (no scoring, no best.dcp)
# =========================================================================
set C [open $OUT_DIR/physopt_comparison.rpt w]
puts $C [format "%-7s %-8s %-10s %-11s %-9s %-6s %-11s %-7s" "ITER" "OVERCON" "WNS(ns)" "TNS(ns)" "FAIL_EP" "CONG" "CELLS" "RT(s)"]
puts $C [format "%-7s %-8s %-10.3f %-11.3f %-9d %-6d %-11d %-7s" "base" "-" $BWNS $BTNS $BFEP $BCONG $BCELLS "-"]
foreach rec $rows {
    lassign $rec n oc wns tns fep cong cells rt
    puts $C [format "%-7d %-8s %-10.3f %-11.3f %-9d %-6d %-11d %-7d" $n [expr {$oc ? "yes" : "no"}] $wns $tns $fep $cong $cells $rt]
}
puts $C [format "%-7s %-8s %-10.3f %-11.3f %-9d %-6s %-11s %-7s" "final" "-" $FWNS $FTNS $FFEP "-" "-" "-"]
close $C

set S [open $OUT_DIR/physopt_summary.txt w]
puts $S "============== ADAPTIVE POST-PLACE PHYSOPT SUMMARY =============="
puts $S "design              : [expr {[catch {get_property NAME [current_design]} _n] ? {?} : $_n}]"
puts $S "checkpoint (opened) : [expr {$DCP ne {} ? $DCP : {in-memory design}}]"
puts $S "mode                : $MODE  (setup uncertainty [format %.1f [expr {$OC_PCT*100}]]% of period, cap [format %.0f [expr {$OC_CAP_PCT*100}]]%)"
puts $S "baseline            : WNS=[format %.3f $BWNS]ns  TNS=[format %.3f $BTNS]ns  fail_eps=$BFEP  cong=$BCONG"
if {$BCONG > $CONG_HIGH} {
    puts $S "path                : HIGH congestion (> $CONG_HIGH) -> single plain AggressiveExplore pass"
} else {
    puts $S "path                : met/not-met loop (<= $MAX_ITER iters); over-constrain only when timing met"
}
puts $S "final               : WNS=[format %.3f $FWNS]ns  TNS=[format %.3f $FTNS]ns  fail_eps=$FFEP  ->  $OUT_DIR/final.dcp"
puts $S "----------------------------------------------------------------"
puts $S "decisions log:"
foreach l $LOG { puts $S "  $l" }
puts $S "----------------------------------------------------------------"
puts $S "elapsed             : [expr {[clock seconds]-$t0}] s"
close $S

log "WROTE $OUT_DIR/physopt_comparison.rpt , $OUT_DIR/physopt_summary.txt , $OUT_DIR/final.dcp"
log "DONE_ADAPTIVE_PHYSOPT in [expr {[clock seconds]-$t0}] s"
