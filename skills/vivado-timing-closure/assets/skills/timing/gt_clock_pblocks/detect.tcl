# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# Output folder: RUN_DIR (set once by the framework to the detection folder; no skill changes it),
# else OUTDIR, else the current directory. RUN_DIR is used first because other skills in the same
# Vivado session (e.g. apps_skills) overwrite OUTDIR and leave it pointing at their own folder.
set outdir [expr {[info exists ::env(RUN_DIR)] && $::env(RUN_DIR) ne "" ? $::env(RUN_DIR) : ([info exists ::env(OUTDIR)] && $::env(OUTDIR) ne "" ? $::env(OUTDIR) : [pwd])}]
file mkdir $outdir
set ::gtcl_base $outdir

#===============================================================================
# gt_clock_load_pblock_v7.tcl
#-------------------------------------------------------------------------------
# V2 -- fixes two confirmed root causes of the Spirent_Ethernet_TSR-978309
# regression (WNS -0.308->-2.263ns, TNS -2,393->-180,363ns, 35K->277K failing
# endpoints), found by direct report_timing + gt_clock_analysis.rpt study, and
# reconfirmed systemically across a text-only audit of all 25 designs under
# GT_CLOCKS_MULTI_DESIGN_TEST:
#
#   FIX 1 (leaf_clock_loads cap bug -- the confirmed root cause):
#     V1's early-return on hitting -max_loads discarded EVERYTHING accumulated
#     so far by returning BEFORE appending any cells from the oversized net.
#     If a SINGLE net's own fanout already exceeded max_loads (500000) -- e.g.
#     a GTM_QUAD's *USRCLK-fed distribution net, which can carry 500K+ loads --
#     the clkpin came back with loads={}, silently vanished from EMIT/SKIP/NOOP
#     reporting entirely, and NEVER got a pblock. Its sibling channel (under
#     the cap) still got compacted alone into a whole-SLR pblock -- an
#     asymmetric, half-applied constraint. V2 always keeps accumulating
#     (truncating at exactly max_loads) so a clkpin's true scale is never
#     silently invisible.
#
#   FIX 2 (new -slr_dominance_frac guard):
#     Even when correctly detected, a whole-SLR pblock for a clock whose own
#     load population is already a large fraction of that SLR's total flop
#     capacity (e.g. Spirent's CH2_TXOUTCLK = 309,506 loads, 18.9% of SLR2's
#     1,640,448-FF capacity, forced into 9 clock regions all pinned at the 70%
#     headroom cap) is far more likely to cause congestion than help --
#     compacting the design's own majority/system clock is not the same
#     problem this skill targets (a small, genuinely localizable GT-adjacent
#     load). Default 0.50: if a single clock's nff exceeds 50% of its SLR's
#     total FF capacity, SKIP the whole-SLR pblock for it instead of emitting.
#
#   FIX 3 (visibility): every capped/skipped-for-dominance clkpin is now
#     explicitly logged and appears in gt_clock_analysis.rpt (never silently
#     dropped), closing the exact blind spot that made the regression
#     invisible until a manual report_timing study found it.
#
# V3 -- closes the four residual risks identified after the Spirent_Ethernet
# re-regression and the Teledyne/Samsung/VIAVI pre-existing failure study:
#
#   V3 FIX 1 (resource-aware sizing -- BRAM/URAM/DSP, not just FF):
#     A compact (<=max_hcr CR) candidate is now ALSO checked for RAMB/URAM/DSP
#     site capacity (same 70% headroom as FF) before being accepted. If the
#     chosen CRs don't have enough free BRAM/URAM/DSP sites for the cells the
#     trace actually needs to carry, the candidate is treated as NOT fitting
#     (exactly like an FF overflow) and escalates to the normal whole-SLR
#     fallback path. The whole-SLR candidate is then subject to the SAME
#     -slr_dominance_frac flat-percentage check as FF, but computed against
#     each resource type's OWN total SLR capacity -- if ANY of FF/BRAM/URAM/DSP
#     would exceed the flat dominance limit, the clock is SKIPPED ENTIRELY
#     (never a partial pblock), per the same "attempt in full, validate
#     cumulatively, commit whole or skip whole" pattern already used for FF.
#     (Fixes: Place 30-2958 BRAM-capacity placer failures.)
#
#   V3 FIX 2 (never touch a fixed LOC -- exclude, don't just preserve):
#     Any cell with IS_LOC_FIXED==1 is dropped from a clock's candidate load
#     list BEFORE sizing/pblock-building even begins (back to the pre-pass
#     filtering model, applied correctly this time): it is never added to any
#     pblock at all -- not with -clear_locs, not without it. This is not
#     limited to recognized dedicated-site primitives like BUFG_GT_SYNC; ANY
#     cell that already has a fixed LOC for any reason is left completely
#     alone and excluded from the pblock's cell list entirely.
#     (Fixes: Place 30-6836 / Place 30-1960 BUFG_GT_SYNC dedicated-site
#     failures.)
#
#   V3 FIX 3 (DFX/HD.RECONFIGURABLE exclusion, whole-clock skip):
#     Before a clock's loads are considered for ANY pblock (compact, whole-
#     SLR, or entangled), every traced cell -- the GT cell itself, every
#     buffer/MMCM/PLL cell passed through during the leaf-load walk, and
#     every final load cell -- is checked for HD.RECONFIGURABLE. If ANY of
#     them is a DFX (partial-reconfiguration) cell, the ENTIRE clock is
#     skipped outright (no pblock suggestion at all, not even a trimmed one),
#     because a DFX reconfigurable-module pblock must range every resource
#     type the RM uses and this skill has no way to guarantee that.
#     (Fixes: DRC HDPR-18 pblock-range violations, VIAVI_100gx8.)
#
#   V3 FIX 4 (dominance stays flat-percentage, NOT live-occupancy):
#     -slr_dominance_frac is intentionally still compared against each
#     resource's TOTAL SLR capacity (flat %), not the baseline checkpoint's
#     real current occupancy -- by explicit instruction. Simpler, same
#     semantics as V2, now just applied across FF/BRAM/URAM/DSP uniformly.
#
#   V3 FIX 5 (trigger-condition gate -- require the clock to be violating):
#     After tracing a GT clock's loads (trace still runs -- needed to know the
#     real downstream clocks, see below), check live on the open design
#     whether EITHER the GT pin's own clock OR any clock found on a sample of
#     its final load cells is a confirmed timing violator, checked in BOTH
#     directions -- as the CAPTURING clock of a failing path (-to) AND as the
#     LAUNCHING clock of a failing cross-domain path whose capture lands on a
#     different clock entirely (-from). If NEITHER direction, on EITHER
#     clock, shows a violation, skip entirely (NOOP) -- never size, never
#     emit. Two real designs each exposed one direction being insufficient
#     alone: CienaMaia_TSR_976837's GT pin clock (client_qsfp28_0_rxoutclk) is
#     healthy, but the real failing clock driving its traced loads
#     (qsfpdd_dcmac_flexif_clk393) sits several buffer/MMCM hops further down
#     the same tree -- so load-sampled clocks must be checked too, not just
#     the pin's. Ciena_cr_1141550's GT clocks (dp0_client_gtm_ch0/2/4/6_
#     rxoutclk etc.) are perfectly healthy on their OWN intra-clock row
#     (+0.630ns) -- a -to-only check would call them clean -- but each
#     LAUNCHES a failing cross-domain path into core_390_778mhz (down to
#     -0.486ns) that only a -from check catches. Spirent_Ethernet and
#     design_T, by contrast, show their GT clocks genuinely clean in
#     BOTH directions -- the design's real bottleneck (TxClk312,
#     mmcm_clk_400/600 off IPAD_clk_100mhz) has no GT-clock involvement at
#     all, confirming the gate correctly suppresses them while correctly
#     NOT suppressing Ciena_cr_1141550's. advantest_extraclk_1116_vp1802 (GT
#     clocks genuinely negative) and CienaMaia_TSR_976837 (downstream clock
#     genuinely negative) both show real wins and both pass this gate.
#     Default ON (-no_require_violating to disable for A/B comparison).
#
# V4 -- closes the three confirmed root causes of the 3 genuine flow failures
# (2x "Place 30-2958" BRAM-undersize on Teledyne_LeCroy_SummitZ6_NFM, 1x
# "Place 30-5425" SLL-overcommit on Spirent_Ethernet) and 2 Vivado segfaults
# (design_P, design_T baselines;
# Spirent_RoCE_2x800g experiment) found in the 35-design V3 study. All 3 of
# these designs' whole-SLR fallback pblocks were 189,485-491,791 cells; every
# OTHER (cleanly-completing) design's whole-SLR fallback pblock was 8,000-
# 17,000 cells -- a 10-50x gap with zero overlap.
#
#   V4 FIX A (absolute whole-SLR pblock cell-count cap, independent of %):
#     -slr_dominance_frac (V2/V3) only ever measured PERCENTAGE of SLR
#     capacity -- it never caught the failures above because every one of
#     them came in at 10-48% of SLR FF capacity, under the 50% default limit.
#     A flat, absolute cap (-whole_slr_cell_cap, default 100,000 cells) is
#     now checked in ADDITION to (not instead of) the existing %-based check,
#     on every whole-SLR fallback candidate (both the "entangled" and "clean
#     GT horizontal overflow" paths). This is a hard, near-certain backstop:
#     it does not try to predict routing/placement outcomes, it pattern-
#     matches against the empirical fact that pblocks this size have a 100%
#     failure rate across the full 35-design study to date.
#
#   V4 FIX B (BRAM capacity/requirement unit-conflation bug, not just a gap):
#     V3's res_ref_pattern literally called res_site_pattern -- the exact
#     same "RAMB*" wildcard was reused on BOTH the capacity side (SITE_TYPE,
#     where Vivado enumerates a RAMB36 site AND its 2 child RAMB18 half-sites
#     as separate matched objects) and the requirement side (REF_NAME, where
#     a RAMB36E2 cell and a RAMB18E2 cell were counted as equivalent 1-unit
#     entries despite the former being 2x the physical weight of the latter).
#     This is the exact fingerprint behind the real Vivado placer error seen
#     on Teledyne_LeCroy_SummitZ6_NFM -- "capacity is 48, but cells require at
#     least 96" -- a clean 2x ratio. V4 replaces the single flat pattern with
#     gtpb::res_weighted_patterns: BRAM is now counted in RAMB18-equivalent
#     units on BOTH sides (RAMB36*-matched sites/cells weighted x2, RAMB18*-
#     matched weighted x1); URAM/DSP keep a flat x1 weight (no known split-
#     primitive ambiguity for those on this device family).
#     (Fixes: Place 30-2958 BRAM-capacity placer failures, V3's claim to this
#     was real in intent but had a real bug in the counting itself.)
#
#   V4 FIX C (cross-SLR "disturbance" heuristic -- NOT a true SLL capacity
#     check, and cannot be one):
#     SLL (Super Long Line) demand is a property of a NET, consumed only when
#     its two endpoints land in different SLRs. This script can force the
#     SLR of the load cell going into the pblock, but NOT the SLR of whatever
#     the net's other endpoint is -- that is an emergent placement decision
#     made entirely by Vivado's placer AFTER this pblock is committed, and is
#     not knowable in advance. Unlike FF/BRAM/URAM/DSP (where every candidate
#     cell is GUARANTEED to land inside the pblock by construction, so
#     required-vs-available is a real, static comparison), there is no
#     equivalent guarantee available for SLL. V4 therefore does NOT claim to
#     check SLL capacity. Instead, gtpb::whole_slr_disturbance_frac measures,
#     in the CURRENT already-placed (unconstrained) baseline layout, what
#     fraction of a candidate's load cells are NOT already sitting in the
#     proposed target SLR. A high fraction means this pblock would forcibly
#     relocate a large share of cells away from wherever Vivado's own
#     (successful) placer chose to put them -- which correlates with new
#     cross-SLR (and hence SLL) demand that did not exist before, without
#     claiming to quantify it exactly. DISABLED BY DEFAULT (max_disturb_frac
#     defaults to 1.0, which no real fraction can exceed, so this check is a
#     structural no-op unless explicitly turned on): all 6 known failures in
#     the 35-design V3 study are already covered by FIX A + FIX B, so this
#     heuristic -- whose 0.35 threshold is invented, not derived from any
#     observed failure the way the 100K cap was -- has no proven benefit yet
#     and a real cost (it would sometimes SKIP a candidate that was fine).
#     Opt in with -max_disturb_frac <frac, e.g. 0.35> once/if the V4
#     experiment batch surfaces real evidence it's needed.
#
# V5 -- EXPERIMENTAL, user-requested, not yet validated:
#
#   V5 FIX E (secondary-target clocks via confirmed cross-clock interaction):
#     Previously, only a clock that ITSELF passed require_violating ever got
#     a pblock suggestion. New in V5: for every clock that DOES pass
#     require_violating (clk1), check CONFIRMED real timing interaction
#     (gtpb::shares_timing, the same threshold-free get_timing_paths check
#     already used for entanglement -- not a geometric guess) against EVERY
#     OTHER GT's clock pins (clk2), not just the pre-filtered "far" subset
#     the existing entanglement check used. Rationale: compacting only
#     clk1's own loads may not fully resolve a cross-domain path if the
#     OTHER side of that path (clk2's own loads) stays scattered -- clk2 may
#     never independently violate on its own terms, yet still be the
#     reason clk1's cross-domain path is slow.
#     Sizing (as explicitly requested): if clk2 is in the SAME SLR as clk1,
#     it goes through the IDENTICAL compact-growth-else-whole-SLR path as any
#     normal clock (grow to <=max_hcr clock regions, escalate automatically
#     if it doesn't fit) -- no new code path, same guards. If clk2 is in a
#     DIFFERENT SLR than clk1, it is marked entangled with clk1 and routed
#     through the EXISTING entangled whole-SLR path -- same -slr_dominance_frac
#     check, same V4 FIX A (100K cap) and FIX C guards, nothing bypassed.
#     clk2 is NEVER emitted without going through every existing safety gate
#     this skill already has (DFX check, min_loads, dominance, FIX A/B/C) --
#     only its OWN require_violating check is bypassed, justified by its
#     confirmed interaction with an already-violating clk1.
#     A secondary clock is tagged in the report/HTML with WHY it was
#     included (which violating clock it's entangled with), so it is always
#     distinguishable from a clock that triggered on its own.
#     Performance note: this checks shares_timing for every (violating clk1,
#     every other GT) pair, broader than the existing geometric pre-filter --
#     cached per clock-pair (tpair), but still more get_timing_paths calls
#     than V4 on designs with many GTs.
#
#   V5 FIX F (DATA_IF fallback now actually checks timing):
#     The dead-out-clock DATA_IF fallback previously had NO require_violating
#     check at all -- it fired purely on fanout (>= min_loads), regardless of
#     whether the design had any timing problem anywhere. Confirmed on
#     SM_Optics_rigel (postroute) and Viavi_eth800g (postplace): both have
#     ZERO violated path-groups anywhere in the whole design, yet both still
#     emitted DATA_IF pblocks that regressed WNS -- pure downside, no
#     theoretical upside, because nothing was ever checked. Fixed by reusing
#     the exact same gtpb::any_clock_violating/sample_load_clocks/
#     clock_is_violating machinery already used for the clk-sourced path,
#     applied to the data-interface load cells themselves (passing "" as the
#     pin-clock arg, which clock_is_violating already treats as "skip, check
#     only the sampled load clocks"). Gated behind the same -require_violating
#     flag as everything else.
#
#   V6 FIX (sibling-emit override, Pass 1.5c):
#     Root cause confirmed on advantest_vp1802_0805: this design's PIE8 GT
#     lanes are 8 structurally-identical instances (same quad hierarchy,
#     differing only by a lane index, e.g. DN_PHY_PIE8[0] vs DN_PHY_PIE8[2]).
#     5 of the 8 genuinely violate on their own CH0_TXOUTCLK (require_violating
#     passes, full ~41K-cell pblock emitted); the other 3 are themselves
#     structurally healthy (NOOP, correctly -- no violation on that lane
#     alone) and fell back to a near-empty ~512-cell DATA_IF pblock instead.
#     The pre-existing (pre-V1) hand-built recipe for this design pblocked
#     all 8 lanes uniformly and measurably beat this skill's asymmetric
#     result (-0.082ns historical vs -0.328 to -0.664ns V4/V5-alone, and far
#     worse once RQS reuse is stacked on the asymmetric layout -- the 3
#     "free" lanes' ~41K cells each were left to drift and disrupt the
#     floorplan regularity the other 5 pinned lanes depend on). Fixed by
#     saving the raw ingredients (gt, clkpin, already-traced loads) of every
#     clock that NOOP'd purely on require_violating (not DFX/capped/low-
#     fanout) into $sibling_noop_candidates, then after Pass 1 completes,
#     checking each candidate's "sibling key" (its GT hierarchy path with
#     bracketed lane-index digits normalized away, joined with its clkpin
#     name) against every clock that DID qualify into $clocks. A match means
#     a structural sibling genuinely violates -- so this healthy lane is
#     promoted into $clocks too, using its OWN already-correctly-traced load
#     cells (gtpb::leaf_clock_loads already proved byte-identical cell
#     counts to the historical hard-coded recipe for this exact case -- no
#     re-tracing needed, unlike V5 FIX E which traces clk2 from scratch).
#     Every existing Pass 2 guard (dominance %, FIX A/C/G absolute+cumulative
#     cap, disturbance heuristic) still applies in full to the promoted
#     entry; only the require_violating gate on THIS clock individually is
#     bypassed, justified by the confirmed sibling's own violation. Tagged
#     "[V6-sibling-promoted]" in the displayed clock name for report
#     visibility, same convention as V5 FIX E's "[V5-secondary]" tag.
#
#   V7 FIX (SLR-crossing-violation trigger condition):
#     require_violating (V3 FIX 5) only ever asked "is SOME clock on this
#     GT's pin or sampled loads confirmed violating anywhere" -- it never
#     asked whether that violation's own critical path has anything to do
#     with SLR distance. A GT clock can be genuinely violating with every
#     one of its violating paths entirely local to one SLR -- a skew/
#     congestion/logic-depth problem this skill's whole-SLR or compact-
#     growth containment cannot fix, since compacting a clock's loads only
#     shortens CROSS-SLR routing, not same-SLR delay. Fixed by
#     gtpb::clock_violating_with_slr_crossing, which uses get_timing_paths'
#     real, documented "-inter_slr data_crossing" filter (confirmed from
#     Vivado's own help page) to restrict the search to paths whose
#     DATAPATH actually crosses an SLR -- Vivado's own routing-aware
#     determination, not a manual "are the two endpoint cells in different
#     SLRs" comparison (a path can route through a different SLR and back,
#     which a naive endpoint-only check would miss entirely). The new gate,
#     gtpb::any_clock_violating_crossing_slr, requires a confirmed violation
#     on an SLR-data-crossing path specifically, before proceeding past
#     require_violating -- on both the clk-sourced path and the DATA_IF
#     fallback. Every other fix (FIX A-G, V6 sibling-emit override) is
#     completely untouched; a clock entangled in via FIX E or promoted via
#     V6's sibling-override is still justified transitively through its
#     already-SLR-crossing-violating partner/sibling, so this new gate is
#     applied ONLY at the two original require_violating call sites,
#     nowhere else.
#
# Everything else (pass 1/2 structure, entanglement detection, horizontal
# compaction, per-CR 70% headroom, per-SLR 50% combined-load guard, HTML
# report) is UNCHANGED from gt_clock_load_pblock.tcl / V2 / V3 / V4 / V5 / V6.
#
# Outputs (same convention as V1):
#   fix.tcl                 (only when >=1 pblock is suggested)
#   gt_clock_analysis.rpt   (always)
#   gt_clock_pblocks.html   (always)
#
# Run on a PLACED design:  source gt_clock_load_pblock_v7.tcl
#===============================================================================

namespace eval gtpb {
    variable FF_PER_SLICE 16
    variable slrOf
    variable capFF
    variable slrCapFF
    variable capRes           ;# V3: non-FF site capacity cache, key "$crn,$restype"
    variable slrCapRes        ;# V3: non-FF SLR capacity cache, key "$slr,$restype"
    variable coneMember
    variable coneBuilt
    variable gtClk            ;# cache: gt -> list of its TX/RX out-clock names
    variable violCache        ;# V3 FIX 5: cache clock_is_violating results by
                               ;# clock name -- without this, a shared system
                               ;# clock reachable from many different GT
                               ;# clocks' load samples gets re-queried via
                               ;# get_timing_paths over and over (the same
                               ;# "unconditional expensive per-clock query"
                               ;# slowness this session already hit once with
                               ;# gskew_trigger_check.tcl).
    variable RESKINDS {BRAM URAM DSP}   ;# V3 FIX 1: resource types sized alongside FF
    variable WHOLE_SLR_CELL_CAP 100000  ;# V4 FIX A: absolute hard cap, independent
                                         ;# of the %-based dominance check
    variable WHOLE_SLR_MAX_DISTURB_FRAC 1.0  ;# V4 FIX C: disabled by default (1.0
                                              ;# = no real fraction exceeds it);
                                              ;# opt in via -max_disturb_frac
    variable DEDICATED_BUFFER_PAT {BUFG_GT|BUFGCE|BUFGCE_DIV|BUFG|MMCM|PLL|DPLL}
    variable BUFG_GT_FAMILY_PAT {BUFG_GT}        ;# New_GT_clock_load: transparent GT-distribution
                                        ;# buffer family (covers BUFG_GT, MBUFG_GT, and any
                                        ;# other *_GT mesh/distribution buffer) -- any number
                                        ;# of hops through these is legitimate clock
                                        ;# distribution, never a load, never disqualifying.
                                        ;# NOTE: every match site also explicitly EXCLUDES
                                        ;# BUFG_GT_SYNC (a GT enable/clear SYNC companion
                                        ;# cell, not a clock buffer -- its only outputs are
                                        ;# CESYNC/CLRSYNC control signals; walking through it
                                        ;# loops back on its own sync logic and never reaches
                                        ;# a real downstream clock stage). Confirmed via live
                                        ;# trace on advantest_vp1802_0805's DN_PHY_PIE8[0]
                                        ;# lane this session.
    variable OTHER_CLOCK_DRIVER_PAT {BUFGCE|BUFGCE_DIV|BUFGCTRL|BUFGMUX|BUFGDIV|MMCM|PLL|DPLL}
                                        ;# New_GT_clock_load: genuinely different clock-driver
                                        ;# primitives (gate/mux/regenerate the clock) -- hitting
                                        ;# any of these DISQUALIFIES the whole clock (no pblock
                                        ;# at all for its loads), it does not just stop counting.
    variable BUFG_GT_FAMILY_FILTER {REF_NAME =~ *BUFG_GT* && REF_NAME !~ *BUFG_GT_SYNC*}   ;# native Vivado -filter equivalent; BUFG_GT_SYNC excluded -- it is a GT enable/clear SYNC companion cell (outputs are CESYNC/CLRSYNC control signals, not a clock), walking through it loops on its own sync pins and never reaches real downstream clock stages.
    variable OTHER_CLOCK_DRIVER_FILTER {REF_NAME =~ *BUFGCE* || REF_NAME =~ *BUFGCE_DIV* || REF_NAME =~ *BUFGCTRL* || REF_NAME =~ *BUFGMUX* || REF_NAME =~ *BUFGDIV* || REF_NAME =~ *MMCM* || REF_NAME =~ *PLL* || REF_NAME =~ *DPLL*}
                                        ;# V3 FIX 2: dedicated-site clock buffer/PLL
                                        ;# REF_NAME pattern -- excluded from any
                                        ;# pblock's cell list UNCONDITIONALLY,
                                        ;# regardless of IS_LOC_FIXED (proven
                                        ;# unreliable: these cells can read
                                        ;# IS_LOC_FIXED=0 at analysis time even
                                        ;# though their site is architecturally
                                        ;# mandated -- confirmed via the
                                        ;# Samsung_Medsion / Teledyne_LeCroy
                                        ;# SummitZ6 Place 30-6836 failures,
                                        ;# where BUFG_GT_SYNC cells reached
                                        ;# fix.tcl's add_cells_to_pblock list
                                        ;# via the gt_data_loads() DEAD-OUT-CLOCK
                                        ;# fallback path, which had NO buffer
                                        ;# exclusion at all)
}

proc gtpb::cr_name {cr} { return [get_property -quiet NAME $cr] }
proc gtpb::cr_xy {crn} { if {[regexp {X(\d+)Y(\d+)} $crn -> x y]} { return [list $x $y] }; return [list 0 0] }
proc gtpb::minl {l} { set m [lindex $l 0]; foreach v $l { if {$v < $m} { set m $v } }; return $m }
proc gtpb::maxl {l} { set m [lindex $l 0]; foreach v $l { if {$v > $m} { set m $v } }; return $m }
proc gtpb::htmlesc {s} { return [string map {& &amp; < &lt; > &gt; \" &quot;} $s] }
proc gtpb::shortgt {name} {
    if {[regexp {([A-Za-z0-9_]+\[[0-9]+\])} $name -> m]} { return "$m/[lindex [split $name /] end]" }
    return [join [lrange [split $name /] end-1 end] /]
}

proc gtpb::cr_ff_cap {crn} {
    variable FF_PER_SLICE
    variable capFF
    if {[info exists capFF($crn)]} { return $capFF($crn) }
    set slices [get_sites -quiet -of_objects [get_clock_regions -quiet $crn] -filter {SITE_TYPE =~ SLICE*}]
    set c [expr {[llength $slices] * $FF_PER_SLICE}]
    set capFF($crn) $c
    return $c
}

proc gtpb::cr_slr {crn} {
    variable slrOf
    if {[info exists slrOf($crn)]} { return $slrOf($crn) }
    set s [get_slrs -quiet -of_objects [get_clock_regions -quiet $crn]]
    set nm [expr {[llength $s] ? [get_property -quiet NAME $s] : ""}]
    set slrOf($crn) $nm
    return $nm
}

# FIX I (congestion gate): run report_design_analysis -congestion ONCE per
# invocation and record which SLR(s) have Global or Short router congestion
# at level >= 5 (the report's own built-in default threshold -- rows below
# level 5 are never listed at all, so any matching row here is already a
# real, elevated congestion hotspot). Deliberately ignores Long congestion
# per explicit instruction -- Long-type congestion alone is not treated as
# disqualifying. Both the post-placement ("Placer Final Level Congestion
# Reporting") and post-route ("Router Initial Congestion") tables are parsed
# if present; either one's matching rows mark that row's SLR as congested.
proc gtpb::init_congestion_gate {outdir} {
    variable _congestedSLRs
    array unset _congestedSLRs
    set _rpt [file join $outdir "congestion_gate_check.rpt"]
    if {[catch {report_design_analysis -congestion -file $_rpt} _err]} {
        puts "WARNING: FIX I congestion gate -- report_design_analysis -congestion failed ($_err); congestion gate disabled for this run (no SLR will be treated as congested)."
        return
    }
    if {[catch {open $_rpt r} fh]} {
        puts "WARNING: FIX I congestion gate -- could not read $_rpt; congestion gate disabled for this run."
        return
    }
    set _inTable 0
    while {[gets $fh line] >= 0} {
        if {[string match "*Congestion Reporting*" $line] || [string match "*Router Initial Congestion*" $line]} {
            set _inTable 1
            continue
        }
        if {!$_inTable} { continue }
        if {![regexp {^\|\s*(North|South|East|West)\s*\|\s*(Global|Long|Short)\s*\|\s*(\d+)\s*\|\s*\(([^,]+),} $line -> _dir _type _level _tile1]} { continue }
        if {$_type ne "Global" && $_type ne "Short"} { continue }
        if {$_level < 5} { continue }
        if {[catch {get_clock_regions -quiet -of_objects [get_tiles -quiet $_tile1]} _cr] || ![llength $_cr]} { continue }
        set _crn [gtpb::cr_name $_cr]
        set _slr [gtpb::cr_slr $_crn]
        if {$_slr ne ""} { set _congestedSLRs($_slr) 1 }
    }
    close $fh
    if {[array size _congestedSLRs]} {
        puts "FIX I (congestion gate): SLR(s) with Global/Short router congestion >= level 5 -- suggestions will be suppressed there: [lsort [array names _congestedSLRs]]"
    } else {
        puts "FIX I (congestion gate): no SLR has Global/Short router congestion >= level 5 -- congestion gate will not suppress any suggestion this run."
    }
}

proc gtpb::slr_congested {slr} {
    variable _congestedSLRs
    if {$slr eq ""} { return 0 }
    return [info exists _congestedSLRs($slr)]
}

# total FF-site capacity of a whole SLR (sum of its clock regions), cached
proc gtpb::slr_ff_cap {slr} {
    variable slrCapFF
    if {[info exists slrCapFF($slr)]} { return $slrCapFF($slr) }
    set total 0
    foreach cr [get_clock_regions -quiet *] {
        set crn [gtpb::cr_name $cr]
        if {[gtpb::cr_slr $crn] eq $slr} { incr total [gtpb::cr_ff_cap $crn] }
    }
    set slrCapFF($slr) $total
    return $total
}

# V3 FIX 1 / V4 FIX B: non-FF site-type capacity, now WEIGHTED per sub-type
# instead of one flat pattern reused for both capacity (SITE_TYPE) and
# requirement (REF_NAME) -- see V4 FIX B header note for why the old flat
# "RAMB*"-for-both approach was a real unit-conflation bug, not just
# imprecise. Each entry is {pattern weight}; weight is the number of
# RAMB18-equivalent units one matched site/cell counts for.
proc gtpb::res_weighted_patterns {restype} {
    switch -- $restype {
        BRAM { return {{RAMB36* 2} {RAMB18* 1}} }
        URAM { return {{URAM* 1}} }
        DSP  { return {{DSP* 1}} }
        default { return {} }
    }
}

proc gtpb::cr_res_cap {crn restype} {
    variable capRes
    set key "$crn,$restype"
    if {[info exists capRes($key)]} { return $capRes($key) }
    set c 0
    foreach pw [gtpb::res_weighted_patterns $restype] {
        lassign $pw pat wt
        set sites [get_sites -quiet -of_objects [get_clock_regions -quiet $crn] -filter "SITE_TYPE =~ $pat"]
        incr c [expr {[llength $sites]*$wt}]
    }
    set capRes($key) $c
    return $c
}

proc gtpb::slr_res_cap {slr restype} {
    variable slrCapRes
    set key "$slr,$restype"
    if {[info exists slrCapRes($key)]} { return $slrCapRes($key) }
    set total 0
    foreach cr [get_clock_regions -quiet *] {
        set crn [gtpb::cr_name $cr]
        if {[gtpb::cr_slr $crn] eq $slr} { incr total [gtpb::cr_res_cap $crn $restype] }
    }
    set slrCapRes($key) $total
    return $total
}

# how many (weighted) units of this resource type these cells actually need
# (by REF_NAME) -- see V4 FIX B: weighted so a RAMB36E2 cell counts 2x a
# RAMB18E2 cell, matching the weighting used on the capacity side.
proc gtpb::count_res_cells {cells restype} {
    if {![llength $cells]} { return 0 }
    set total 0
    foreach pw [gtpb::res_weighted_patterns $restype] {
        lassign $pw pat wt
        set n [llength [filter $cells "REF_NAME =~ $pat"]]
        incr total [expr {$n*$wt}]
    }
    return $total
}

# free (headroom-adjusted) resource capacity across a SET of chosen CRs, given
# a per-CR committed ledger for that resource type (mirrors gtpb::cr_free_ff).
proc gtpb::chosen_free_res {chosenCRs restype headroom committedResVar} {
    upvar 1 $committedResVar committedRes
    set total 0
    foreach crn $chosenCRs {
        set cap [gtpb::cr_res_cap $crn $restype]
        if {$cap <= 0} { continue }
        set key "$crn,$restype"
        set comm [expr {[info exists committedRes($key)] ? $committedRes($key) : 0}]
        set avail [expr {int($cap*$headroom) - $comm}]
        if {$avail > 0} { incr total $avail }
    }
    return $total
}

# V3 FIX 3: true if ANY cell in this list is a DFX (partial-reconfiguration)
# cell -- checked on the GT cell itself, every buffer/MMCM/PLL cell the clock
# tree passes through, and every final load cell ("upstream or downstream of
# the GT load cells").
proc gtpb::any_hd_reconfigurable {cells} {
    foreach c $cells {
        if {$c eq ""} { continue }
        if {[catch {get_property -quiet HD.RECONFIGURABLE $c} v]} { continue }
        if {$v ne "" && $v != 0} { return 1 }
    }
    return 0
}

# FIX J (DFX floorplan-overlap gate): any_hd_reconfigurable above only checks
# whether THIS clock's own cells (GT/buffers/loads) are individually tagged
# HD.RECONFIGURABLE -- it says nothing about whether the CANDIDATE PBLOCK
# AREA we are about to draw happens to geometrically overlap an UNRELATED
# DFX Reconfigurable Module's pblock elsewhere in the floorplan. Vivado's DFX
# rule is purely area-based: any pblock whose area overlaps an RM's area
# must range every resource type that RM uses (e.g. DCMAC), regardless of
# which cells are actually inside our pblock. Confirmed root cause of DRC
# HDPR-18 failures on VIAVI_*_DFX_* / Samsung_5G_8TRX_DFX_* designs in V1 --
# none of those GTs' own load cells were HD.RECONFIGURABLE, but their
# whole-SLR pblock's area overlapped an existing RM pblock anyway.
#
# gtpb::init_dfx_rm_regions builds, ONCE per invocation, the set of clock
# regions occupied by any existing DFX RM pblock (found via every
# HD.RECONFIGURABLE cell's own pblock membership, not by guessing pblock
# names). gtpb::regions_hit_dfx_rm then checks a candidate region list
# against that set.
proc gtpb::init_dfx_rm_regions {} {
    variable _dfxRmRegions
    array unset _dfxRmRegions
    # Query in the pblock->cells direction (get_cells -of_objects $pb), not
    # cell->pblock (get_pblocks -of_objects $cell) -- confirmed via live
    # testing that the latter returns EMPTY for some DFX designs even though
    # the RM pblock demonstrably exists (e.g. Vivado's own log referring to
    # "Reconfigurable Pblock p0_pblock" by name for a design where the
    # cell->pblock query found nothing). The pblock->cells direction is the
    # standard/reliable containment query and works regardless of how/when
    # Vivado internally associates cells with the RM pblock.
    if {[catch {get_pblocks -quiet *} _allPbs]} { set _allPbs {} }
    set _rmPblocks {}
    foreach _pb $_allPbs {
        if {[catch {get_cells -quiet -of_objects $_pb -filter {HD.RECONFIGURABLE == 1}} _rmc]} { continue }
        if {[llength $_rmc]} { lappend _rmPblocks $_pb }
    }
    # Belt-and-suspenders: also keep the original cell->pblock direction in
    # case some RM pblocks are only discoverable that way for other designs.
    if {[catch {get_cells -quiet -hier -filter {HD.RECONFIGURABLE == 1}} _rmCells]} { set _rmCells {} }
    foreach _c $_rmCells {
        if {[catch {get_pblocks -quiet -of_objects $_c} _pbs]} { continue }
        foreach _pb $_pbs { if {[lsearch -exact $_rmPblocks $_pb] < 0} { lappend _rmPblocks $_pb } }
    }
    foreach _pb $_rmPblocks {
        if {[catch {get_clock_regions -quiet -of_objects $_pb} _crs]} { continue }
        foreach _cr $_crs {
            set _crn [gtpb::cr_name $_cr]
            if {$_crn ne ""} { set _dfxRmRegions($_crn) 1 }
        }
    }
    if {[array size _dfxRmRegions]} {
        puts "FIX J (DFX floorplan-overlap gate): clock region(s) occupied by an existing DFX RM pblock -- GT pblocks will not be drawn there: [lsort [array names _dfxRmRegions]]"
    }
}

proc gtpb::regions_hit_dfx_rm {regions} {
    variable _dfxRmRegions
    foreach _r $regions {
        set _rn [string map {CLOCKREGION_ {}} $_r]
        if {[info exists _dfxRmRegions($_rn)]} { return 1 }
    }
    return 0
}

# V3 FIX 2: any cell with IS_LOC_FIXED==1 must already have been dropped from
# $cells upstream (Pass 1 -exclude_fixed filtering) -- this is a defensive
# re-check only, so a fixed cell is NEVER added to a pblock even if it slipped
# through (e.g. -keep_fixed was explicitly passed). It is silently skipped,
# not added without -clear_locs -- excluded entirely, never touched.
proc gtpb::emit_pblock_cells {pb cells clear_locs} {
    set safe {}
    foreach c $cells {
        if {[catch {get_property -quiet IS_LOC_FIXED $c} v]} { set v 0 }
        if {$v ne "" && $v != 0} { continue }
        lappend safe $c
    }
    set lines {}
    if {[llength $safe]} {
        lappend lines "add_cells_to_pblock $pb \[get_cells -quiet {"
        foreach cc $safe {
            set _e [string map [list "\[" "\\\[" "\]" "\\\]"] $cc]
            lappend lines "    $_e"
        }
        lappend lines "}\][expr {$clear_locs ? { -clear_locs} : {}}]"
    }
    return $lines
}

# V3 FIX 1 + FIX 4: evaluate whether adding `nff`/`loads` to SLR `slr` (on top
# of whatever FF/BRAM/URAM/DSP is already queued there this run) stays within
# the flat -slr_dominance_frac percentage of EACH resource's TOTAL SLR
# capacity (never live/baseline occupancy -- V3 FIX 4, by explicit instruction).
# FF is always checked; BRAM/URAM/DSP are only checked when this clock's loads
# actually need >0 of that resource (so FF-only clocks are unaffected).
proc gtpb::eval_slr_dominance {slr nff loads frac priorNff priorResDict} {
    variable RESKINDS
    set slrcap [gtpb::slr_ff_cap $slr]
    set candNff [expr {$priorNff + $nff}]
    set domfracFF [expr {$slrcap > 0 ? double($candNff)/$slrcap : 0}]
    set over [expr {$domfracFF > $frac}]
    set resInfo {}
    foreach rt $RESKINDS {
        set req [gtpb::count_res_cells $loads $rt]
        if {$req == 0} { continue }
        set rcap [gtpb::slr_res_cap $slr $rt]
        set prior [expr {[dict exists $priorResDict $rt] ? [dict get $priorResDict $rt] : 0}]
        set cand [expr {$prior + $req}]
        set domfracR [expr {$rcap > 0 ? double($cand)/$rcap : 0}]
        set rover [expr {$domfracR > $frac}]
        if {$rover} { set over 1 }
        lappend resInfo [dict create restype $rt req $req prior $prior cand $cand cap $rcap domfrac $domfracR over $rover]
    }
    return [dict create ok [expr {!$over}] slrcap $slrcap candNff $candNff domfracFF $domfracFF resInfo $resInfo]
}

# short human-readable resource-check fragment appended to SKIP/EMIT reasons
proc gtpb::dominance_resline {ev} {
    set parts {}
    foreach ri [dict get $ev resInfo] {
        set flag [expr {[dict get $ri over] ? " EXCEEDS LIMIT" : ""}]
        lappend parts [format "%s: %d+%d=%d/%d sites (%.1f%%%s)" [dict get $ri restype] [dict get $ri prior] [dict get $ri req] [dict get $ri cand] [dict get $ri cap] [expr {[dict get $ri domfrac]*100}] $flag]
    }
    if {![llength $parts]} { return "" }
    return "  \[resource check: [join $parts {, }]\]"
}

# V4 FIX C: fraction of these load cells that are NOT currently placed (in
# the open, unconstrained baseline layout) inside $targetSlr. See V4 FIX C
# header note -- this is a heuristic proxy for cross-SLR disturbance, not a
# true SLL capacity check (which is not computable at this point in the flow).
proc gtpb::whole_slr_disturbance_frac {cells targetSlr} {
    if {![llength $cells]} { return 0.0 }
    set n [llength $cells]
    set moved 0
    foreach c $cells {
        if {[catch {get_clock_regions -quiet -of_objects $c} cr] || ![llength $cr]} { incr moved; continue }
        set crn [gtpb::cr_name $cr]
        set s [gtpb::cr_slr $crn]
        if {$s eq "" || $s ne $targetSlr} { incr moved }
    }
    return [expr {double($moved)/$n}]
}

# V4: combined extra guard applied to EVERY whole-SLR fallback candidate, on
# TOP of (never instead of) eval_slr_dominance's existing %-based FF/BRAM/
# URAM/DSP check. Returns {ok <bool> reason <string-or-empty>}.
# V5 FIX G: the absolute cap is now CUMULATIVE per SLR, mirroring how
# slrGrpNff already accumulates for the %-based check -- priorNff (cells
# already committed to whole-SLR pblocks in this SLR this run, from earlier
# clocks including FIX-E secondary ones) is added to this candidate's own
# cell count before comparing against WHOLE_SLR_CELL_CAP. Without this, N
# independent candidates each under the 100K cap (e.g. 5x ~80K FIX-E
# secondary clocks sharing one SLR) could individually pass while
# collectively committing ~400K cells to a single SLR -- exactly the
# TeledyneLecroy_062124_johnbieker postplace regression (V4->V5, -1.452ns)
# root-caused this way: 5 separate 79,704-cell EMITs stacked into SLR1,
# 24.3% -> 29.2% -> 34.0% -> 38.9% -> 39.1% dominance, each check passing
# individually. A single legitimate ~80K whole-SLR emit (priorNff=0) is
# untouched by this change.
proc gtpb::guard_whole_slr_emit {slr loads priorNff} {
    variable WHOLE_SLR_CELL_CAP
    variable WHOLE_SLR_MAX_DISTURB_FRAC
    set n [llength $loads]
    set cum [expr {$priorNff + $n}]
    if {$cum > $WHOLE_SLR_CELL_CAP} {
        return [dict create ok 0 reason [format "V5 FIX G: candidate has %d cells; combined with %d cells already committed to whole-SLR pblocks in %s this run = %d cells, exceeding the absolute hard cap of %d (independent of the %%-based dominance check above, and now CUMULATIVE per SLR rather than per-candidate). Every flow failure/segfault seen across the 35-design V3 study occurred on whole-SLR pblocks of 189,485-491,791 cells; every clean completion was 8,000-17,000 cells. SKIPPING regardless of what the %%-based math says, specifically to stop a repeat of those failures -- including the case where several individually-small candidates stack past the cap together." $n $priorNff $slr $cum $WHOLE_SLR_CELL_CAP]]
    }
    set dfrac [gtpb::whole_slr_disturbance_frac $loads $slr]
    if {$dfrac > $WHOLE_SLR_MAX_DISTURB_FRAC} {
        return [dict create ok 0 reason [format "V4 FIX C (heuristic, NOT a hard capacity check -- SLL demand cannot be computed exactly here, see header): %.1f%% of this candidate's %d load cells are not currently placed in target SLR %s in the baseline's own unconstrained layout (limit %.0f%%). Forcibly relocating that many cells into one SLR is likely to introduce new cross-SLR (SLL) demand beyond what the natural placement required. SKIPPING conservatively -- may occasionally skip a candidate that would have been fine; that tradeoff is intentional given flow failures are unacceptable and a missed WNS gain is not." [expr {$dfrac*100}] $n $slr [expr {$WHOLE_SLR_MAX_DISTURB_FRAC*100}]]]
    }
    return [dict create ok 1 reason ""]
}

# all CLOCKREGION_XmYn grid tokens that make up an SLR (for a whole-SLR pblock)
proc gtpb::slr_regions {slr} {
    set regs {}
    foreach cr [get_clock_regions -quiet *] {
        set crn [gtpb::cr_name $cr]
        if {[gtpb::cr_slr $crn] ne $slr} { continue }
        lappend regs [expr {[string match CLOCKREGION_* $crn] ? $crn : "CLOCKREGION_$crn"}]
    }
    return $regs
}

proc gtpb::cr_free_ff {crn headroom committedVar} {
    upvar 1 $committedVar committed
    set total [gtpb::cr_ff_cap $crn]
    set comm  [expr {[info exists committed($crn)] ? $committed($crn) : 0}]
    set avail [expr {int($total*$headroom) - $comm}]
    return [expr {$avail > 0 ? $avail : 0}]
}

# V2 FIX 1: never discard already-found (or in-progress) loads on hitting the
# cap. Keep appending cells from the CURRENT net too, truncating at exactly
# max_loads, so a clkpin whose real fanout is huge still comes back with a
# large representative (non-empty) loads list and capped=1 -- instead of
# silently returning {} (which made it vanish from >= min_loads entirely).
# Cheap pre-check: discover this clock's generated clocks (at each
# BUFG_GT/MBUFG_GT stage) and whether the walk would be disqualified --
# WITHOUT enumerating the (often tens-of-thousands-cell) load fanout on each
# net. Uses native Vivado -filter queries (get_cells -of_objects $net
# -filter {REF_NAME=~...}) to find buffer/disqualifying cells directly,
# instead of a per-pin Tcl loop over every fanout pin on a huge net. Only
# once a violation is confirmed (via clock_is_violating on the own clock or
# one of these generated clocks) does the real gtpb::leaf_clock_loads walk
# (which DOES need the full per-pin loop, for accurate sizing) get called.
# Native, non-walk-based discovery: Vivado already knows a generated clock's
# source/master clock (this is literally what the indentation in the Intra
# Clock Table reflects, e.g. dn0_pclk250 nested under dn0_txoutclk) -- query
# it directly via the MASTER_CLOCK property instead of relying on a netlist
# walk, which can fail to find a path at all when an intermediate cell (e.g.
# BUFG_GT_SYNC) has no modeled clock-forwarding pin. This is the PRIMARY
# mechanism; the netlist-walk-based discovery in quick_gen_clocks below is
# kept as a supplementary source (unioned together) in case some design
# defines a generated clock without MASTER_CLOCK set but still has a real,
# walkable netlist connection.
proc gtpb::native_generated_clocks {clkname} {
    if {$clkname eq ""} { return {} }
    set out {}
    if {[catch {get_clocks -quiet -filter "MASTER_CLOCK == \"$clkname\""} _gc] || $_gc eq ""} { return {} }
    foreach gc $_gc {
        if {[catch {get_property -quiet NAME $gc} gcnm]} { continue }
        if {$gcnm ne "" && $gcnm ne $clkname} { lappend out $gcnm }
    }
    return [lsort -unique $out]
}

proc gtpb::quick_gen_clocks {startpin} {
    variable BUFG_GT_FAMILY_FILTER
    variable OTHER_CLOCK_DRIVER_FILTER
    set toVisit [list $startpin]
    set seenNet {}
    set gen_clocks {}
    set disqualified 0
    set disqual_reason ""
    set _hop 0
    while {[llength $toVisit]} {
        if {[llength $seenNet] > 200} {
            puts "      quick_gen_clocks: SAFETY CAP hit (200 distinct nets visited from $startpin) -- stopping walk early, relying on native_generated_clocks for gen_clocks instead"
            break
        }
        set p [lindex $toVisit 0]; set toVisit [lrange $toVisit 1 end]
        incr _hop
        set net [get_nets -quiet -of_objects $p]
        if {$net eq "" || [lsearch -exact $seenNet $net] >= 0} { continue }
        set _fanout [llength [get_pins -quiet -leaf -of_objects $net]]
        puts "      quick_gen_clocks hop $_hop: pin=$p  fanout=$_fanout  (queue=[llength $toVisit] seen=[llength $seenNet])"
        lappend seenNet $net
        set bufcells [get_cells -quiet -of_objects $net -filter $BUFG_GT_FAMILY_FILTER]
        foreach bc $bufcells {
            foreach op [get_pins -quiet -of_objects $bc -filter {DIRECTION==OUT}] {
                lappend toVisit $op
                if {![catch {get_clocks -quiet -of_objects $op} _cks] && $_cks ne ""} {
                    foreach _ck $_cks {
                        if {[catch {get_property -quiet NAME $_ck} _cknm]} { continue }
                        if {$_cknm ne ""} { lappend gen_clocks $_cknm }
                    }
                }
            }
        }
        set badcells [get_cells -quiet -of_objects $net -filter $OTHER_CLOCK_DRIVER_FILTER]
        if {[llength $badcells]} {
            set disqualified 1
            set _bref [get_property -quiet REF_NAME [lindex $badcells 0]]
            set disqual_reason "hit $_bref ([lindex $badcells 0]) downstream of BUFG_GT/MBUFG_GT -- clock re-driven/gated/regenerated; not GT-proximity-eligible"
            break
        }
    }
    return [list [lsort -unique $gen_clocks] $disqualified $disqual_reason]
}

proc gtpb::leaf_clock_loads {startpin max_loads} {
    variable BUFG_GT_FAMILY_PAT
    variable OTHER_CLOCK_DRIVER_PAT
    # New_GT_clock_load walk: transparent through any number of BUFG_GT-family
    # (BUFG_GT, MBUFG_GT, etc) hops; DISQUALIFIES the whole clock (returns
    # disqualified=1, no loads trusted) if it hits a genuinely different
    # clock-driver primitive (BUFGCE/BUFGCE_DIV/BUFGCTRL/BUFGMUX/BUFGDIV/MMCM/
    # PLL/DPLL) anywhere downstream -- validated this session across the
    # 150-design GT_FLOORPLAN_TCL_SURVEY sweep (0 false disqualifications on
    # advantest_vp1802_0805 / Cerebras_jb_drop_031623 smoke tests).
    set toVisit [list $startpin]; set seenNet {}; set loads {}; set capped 0
    set passthrough {}
    set disqualified 0
    set disqual_reason ""
    set gen_clocks {}
    while {[llength $toVisit]} {
        set p [lindex $toVisit 0]; set toVisit [lrange $toVisit 1 end]
        set net [get_nets -quiet -of_objects $p]
        if {$net eq "" || [lsearch -exact $seenNet $net] >= 0} { continue }
        lappend seenNet $net
        set inpins [get_pins -quiet -leaf -of_objects $net -filter {DIRECTION==IN}]
        foreach lp $inpins {
            set cell [get_cells -quiet -of_objects $lp]
            if {$cell eq ""} { continue }
            set ref [get_property -quiet REF_NAME $cell]
            if {![regexp -nocase {BUFG_GT_SYNC} $ref] && [regexp -nocase $BUFG_GT_FAMILY_PAT $ref]} {
                lappend passthrough $cell
                foreach op [get_pins -quiet -of_objects $cell -filter {DIRECTION==OUT}] {
                    lappend toVisit $op
                    # Track the clock object generated at THIS BUFG_GT/MBUFG_GT
                    # distribution stage (this GT's own distribution tree) -- NOT
                    # a sampled load clock from an unrelated domain. Checking
                    # violations on these too (in addition to the raw GT pin
                    # clock) catches the common case where the GT's own pin
                    # clock has no real intra-domain paths (blank WNS) but its
                    # own generated/distributed clock (e.g. dn0_pclk250) does.
                    if {![catch {get_clocks -quiet -of_objects $op} _opcks] && $_opcks ne ""} {
                        foreach _opck $_opcks {
                            if {[catch {get_property -quiet NAME $_opck} _opcknm]} { continue }
                            if {$_opcknm ne ""} { lappend gen_clocks $_opcknm }
                        }
                    }
                }
            } elseif {[regexp -nocase $OTHER_CLOCK_DRIVER_PAT $ref]} {
                set disqualified 1
                set disqual_reason "hit $ref ($cell) downstream of BUFG_GT/MBUFG_GT -- clock re-driven/gated/regenerated; not GT-proximity-eligible"
                break
            } else {
                lappend loads $cell
                if {[llength $loads] >= $max_loads} { set capped 1; break }
            }
        }
        if {$disqualified || $capped} { break }
    }
    return [list [lsort -unique $loads] $capped [lsort -unique $passthrough] $disqualified $disqual_reason [lsort -unique $gen_clocks]]
}

# Fallback for GTs whose out-clocks are dead: the fabric cells directly wired to
# the GT's RX/TX data/ctrl pins (the GT's user-interface logic) - to be compacted
# near the GT even though the GT does not source its own clock.
# V3 FIX 2: true if this cell is a dedicated-site clock buffer/MMCM/PLL --
# excluded unconditionally, independent of IS_LOC_FIXED (see DEDICATED_BUFFER_PAT).
proc gtpb::is_dedicated_cell {cell} {
    variable DEDICATED_BUFFER_PAT
    if {[catch {get_property -quiet REF_NAME $cell} ref]} { return 0 }
    return [regexp $DEDICATED_BUFFER_PAT $ref]
}

proc gtpb::gt_data_loads {gt max_loads} {
    set gtcell [get_cells -quiet $gt]
    set loads {}
    foreach dp [get_pins -quiet -of_objects $gtcell -filter {NAME =~ *RXDATA* || NAME =~ *RXCTRL* || NAME =~ *RXVALID* || NAME =~ *RXHEADER* || NAME =~ *TXDATA* || NAME =~ *TXCTRL* || NAME =~ *TXHEADER*}] {
        set net [get_nets -quiet -of_objects $dp]
        if {$net eq ""} { continue }
        foreach lp [get_pins -quiet -leaf -of_objects $net] {
            set c [get_cells -quiet -of_objects $lp]
            # V3 FIX 2: this path (unlike leaf_clock_loads) never walked past
            # buffer/PLL/MMCM cells -- it previously added them straight into
            # loads. This is the confirmed mechanism by which BUFG_GT_SYNC
            # cells reached add_cells_to_pblock on Samsung_Medsion and the
            # Teledyne_LeCroy SummitZ6 designs (their Aurora-wrapped GT has no
            # pin matching *TXOUTCLK*/*RXOUTCLK*, so it fell to this DATA_IF
            # fallback, which pulled bufg_gt/BUFG_GT_SYNC straight in).
            if {$c ne "" && $c ne $gtcell && ![gtpb::is_dedicated_cell $c]} { lappend loads $c }
        }
        if {[llength $loads] > $max_loads} { break }
    }
    return [lsort -unique $loads]
}

# Grow the pblock starting AT the GT's own clock region, spreading HORIZONTALLY
# within the GT's SLR until nff FFs fit. The anchor (GT) CR is ALWAYS included so
# the pblock is GT-adjacent; interacting-GT clocks that share a GT region overlap
# there. committed[] keeps fabric CRs from being oversubscribed as it grows out.
proc gtpb::choose_regions {anchorCR nff headroom max_cr committedVar} {
    upvar 1 $committedVar committed
    set anchorSLR [gtpb::cr_slr $anchorCR]
    lassign [gtpb::cr_xy $anchorCR] ax ay
    set cand {}
    foreach cr [get_clock_regions -quiet *] {
        set crn [gtpb::cr_name $cr]
        if {$crn eq $anchorCR || [gtpb::cr_slr $crn] ne $anchorSLR} { continue }
        lassign [gtpb::cr_xy $crn] x y
        set d [expr {abs($y-$ay)*3 + abs($x-$ax)}]   ;# horizontal-first spread
        lappend cand [list $d $crn]
    }
    set cand [lsort -integer -index 0 $cand]
    set order [concat [list $anchorCR] [lmap p $cand {lindex $p 1}]]
    set chosen {}; set cap 0; set remaining $nff
    foreach crn $order {
        if {$crn ne $anchorCR} {
            if {$remaining <= 0} { break }
            if {$max_cr > 0 && [llength $chosen] >= $max_cr} { break }
            set avail [gtpb::cr_free_ff $crn $headroom committed]
            if {$avail <= 0} { continue }
        } else {
            set avail [gtpb::cr_free_ff $crn $headroom committed]
        }
        lappend chosen $crn
        incr cap $avail
        set take [expr {$remaining < $avail ? $remaining : $avail}]
        if {$take > 0} { if {[info exists committed($crn)]} { incr committed($crn) $take } else { set committed($crn) $take } }
        set remaining [expr {$remaining - $take}]
    }
    set fits [expr {$remaining <= 0}]
    return [list $chosen $cap $fits $anchorSLR]
}

# Spread an SLR-level pblock's FF loads across the SLR's clock regions, weighted
# by each CR's remaining free capacity (<= headroom). Updates the shared per-CR
# ledger so the 70%/CR combined cap holds across horizontal AND SLR-level pblocks.
# Returns [list placed unplaced].
proc gtpb::distribute_slr {slr nff headroom committedVar} {
    upvar 1 $committedVar committed
    set crs {}
    foreach cr [get_clock_regions -quiet *] {
        set crn [gtpb::cr_name $cr]
        if {[gtpb::cr_slr $crn] ne $slr} { continue }
        set free [gtpb::cr_free_ff $crn $headroom committed]
        if {$free > 0} { lappend crs [list $free $crn] }
    }
    set crs [lsort -integer -index 0 -decreasing $crs]
    set remaining $nff
    foreach fc $crs {
        if {$remaining <= 0} { break }
        lassign $fc free crn
        set take [expr {$remaining < $free ? $remaining : $free}]
        if {[info exists committed($crn)]} { incr committed($crn) $take } else { set committed($crn) $take }
        set remaining [expr {$remaining - $take}]
    }
    return [list [expr {$nff - $remaining}] $remaining]
}

# cached datapath leaf cone of a GT (BFS over LOW-fanout nets only)
proc gtpb::build_cone {gt max_depth max_fan} {
    variable coneMember
    variable coneBuilt
    if {[info exists coneBuilt($gt)]} { return }
    set coneBuilt($gt) 1
    array unset seen; set frontier {}
    foreach pin [get_pins -quiet -of_objects [get_cells -quiet $gt]] {
        set net [get_nets -quiet -of_objects $pin]
        if {$net eq ""} { continue }
        set lp [get_pins -quiet -leaf -of_objects $net]
        if {[llength $lp] > $max_fan} { continue }
        foreach np $lp {
            set nc [get_cells -quiet -of_objects $np]
            if {$nc eq "" || [info exists seen($nc)]} { continue }
            set seen($nc) 1; set coneMember($gt,$nc) 1; lappend frontier $nc
        }
    }
    for {set d 1} {$d < $max_depth} {incr d} {
        set next {}
        foreach cell $frontier {
            foreach net [get_nets -quiet -of_objects [get_pins -quiet -of_objects $cell]] {
                set lp [get_pins -quiet -leaf -of_objects $net]
                if {[llength $lp] > $max_fan} { continue }
                foreach np $lp {
                    set nc [get_cells -quiet -of_objects $np]
                    if {$nc eq "" || [info exists seen($nc)]} { continue }
                    set seen($nc) 1; set coneMember($gt,$nc) 1; lappend next $nc
                }
            }
        }
        set frontier $next
        if {[llength $frontier] == 0} { break }
    }
}

proc gtpb::loads_hit_cone {loads gt} {
    variable coneMember
    foreach c $loads { if {[info exists coneMember($gt,$c)]} { return 1 } }
    return 0
}

# clock names on a pin
proc gtpb::pin_clocks {pin} {
    set cl {}
    foreach c [get_clocks -quiet -of_objects $pin] { lappend cl [get_property -quiet NAME $c] }
    return $cl
}

# V3 FIX 5 (trigger-condition gate -- "is this clock actually violating?"):
# a GT's own recovered clock can have a huge traced load count via leaf_clock_
# loads (which walks straight through any passthrough MMCM/PLL/BUFG_GT and can
# pick up an entirely unrelated, much larger system clock that happens to
# share that buffering stage) while the GT's OWN clock is itself perfectly
# healthy. Confirmed directly from real baseline Intra Clock Tables across 4
# designs: Spirent_Ethernet, design_T, and Ciena_cr_1141550 all show every
# GT-derived clock with POSITIVE WNS (the real failing clock in each is
# something totally unrelated -- TxClk312, mmcm_clk_400/600 off IPAD_clk_100mhz,
# etc.) yet the skill still emitted (sometimes design-wrecking) pblocks for
# them; advantest_extraclk_1116_vp1802, where the skill produced a large real
# win, shows the GT's OWN clocks (dnN_pl_pclk, nested directly under each GT's
# own TXOUTCLK) genuinely negative. This proc returns 1 only when the clock's
# own worst slack (queried live on the open design, not a report-file guess)
# is actually negative -- i.e. this GT clock is itself a confirmed timing
# violator, not just structurally large.
# V3 FIX 5 (corrected): a GT clock can be a confirmed timing violator in
# EITHER direction -- as the CAPTURING clock of a failing intra-clock path
# (-to), or as the LAUNCHING clock of a failing INTER-clock (cross-domain)
# path whose capture lands on some other clock entirely (-from). Checking
# only -to misses the second case completely. Confirmed real and material:
# Ciena_cr_1141550's dp0_client_gtm_ch0/2/4/6_rxoutclk (and the host/dp2
# siblings) are perfectly healthy on their OWN intra-clock row (e.g.
# dp0_client_gtm_ch0_rxoutclk = +0.630ns, 0 TNS) -- but each LAUNCHES a
# failing cross-domain path into core_390_778mhz (WNS as bad as -0.486ns,
# -0.070 to -26ns TNS per pair) that a -to-only check would never see, since
# core_390_778mhz -- not the GT clock -- is the capturing clock on those
# paths. Both directions must be checked for a GT clock to be fairly judged.
proc gtpb::clock_is_violating {clkname} {
    variable violCache
    if {$clkname eq ""} { return 0 }
    if {[info exists violCache($clkname)]} { return $violCache($clkname) }
    set result 0
    if {![catch {get_clocks -quiet $clkname} cobj] && $cobj ne ""} {
        # Check INTER-clock-group paths (-to alone: capture=this clock, launch=
        # any other clock; -from alone: launch=this clock, capture=any other
        # clock -- Vivado's get_timing_paths does not restrict the OTHER side
        # when only one of -to/-from is given, so this naturally spans every
        # path group touching this clock, matching the Inter Clock Table), AND
        # explicitly ALSO check INTRA-clock-group (-to AND -from together:
        # both launch and capture = this clock, matching the Intra Clock
        # Table) as a third, explicit, defensive query -- redundant with the
        # two above under normal Vivado path-group defaults, but kept
        # separate and explicit so intra-group violations are never
        # accidentally missed regardless of any path-group filtering.
        foreach qargs {{-to} {-from} {-to -from}} {
            set _cmd [concat [list get_timing_paths -quiet -delay_type max] $qargs [list $cobj -max_paths 1 -nworst 1]]
            if {![catch {eval $_cmd} wp] && [llength $wp]} {
                if {![catch {get_property -quiet SLACK [lindex $wp 0]} slack] && $slack ne "" && $slack < 0} {
                    set result 1
                    break
                }
            }
        }
    }
    set violCache($clkname) $result
    return $result
}

# V7 FIX (new trigger condition): native Vivado inter-SLR path query, NOT a
# manual endpoint-SLR comparison. get_timing_paths' real, documented
# "-inter_slr data_crossing" filter restricts the path search to paths whose
# DATAPATH actually crosses an SLR (Vivado's own routing-aware
# determination, not just "are the two endpoint cells in different SLRs" --
# a path can route through a different SLR and back, which a naive
# endpoint-only comparison would miss). Combined with -to/-from $cobj, this
# directly answers "is there a violating (slack<0) path on this clock that
# ALSO crosses an SLR" in one query per direction -- a GT clock can be
# genuinely violating yet have every one of its violating paths entirely
# local to one SLR, which this skill's whole-SLR/compact-growth containment
# cannot help (it only shortens cross-SLR routing). Independent cache from
# clock_is_violating's plain violCache, since this is a different query
# (different -filter), not a post-hoc check on the same worst path.
proc gtpb::clock_violating_with_slr_crossing {clkname} {
    variable violSlrCrossCache
    if {$clkname eq ""} { return 0 }
    if {[info exists violSlrCrossCache($clkname)]} { return $violSlrCrossCache($clkname) }
    set result 0
    if {![catch {get_clocks -quiet $clkname} cobj] && $cobj ne ""} {
        # Same inter+intra coverage as clock_is_violating (see its comment),
        # combined with the -inter_slr data_crossing filter.
        foreach qargs {{-to} {-from} {-to -from}} {
            set _cmd [concat [list get_timing_paths -quiet -delay_type max] $qargs [list $cobj -inter_slr data_crossing -max_paths 1 -nworst 1]]
            if {![catch {eval $_cmd} wp] && [llength $wp]} {
                if {![catch {get_property -quiet SLACK [lindex $wp 0]} slack] && $slack ne "" && $slack < 0} {
                    set result 1
                    break
                }
            }
        }
    }
    set violSlrCrossCache($clkname) $result
    return $result
}

# V7 FIX: same iteration pattern as any_clock_violating (GT pin clock, then a
# sample of final load clocks), but using the inter-SLR-data-crossing-aware
# query above instead of the plain one.
proc gtpb::any_clock_violating_crossing_slr {pinclk loads samplesize} {
    if {[gtpb::clock_violating_with_slr_crossing $pinclk]} { return 1 }
    foreach ck [gtpb::sample_load_clocks $loads $samplesize] {
        if {$ck eq $pinclk} { continue }
        if {[gtpb::clock_violating_with_slr_crossing $ck]} { return 1 }
    }
    return 0
}

# V3 FIX 5: the GT's own pin clock can be healthy while the REAL relevant
# clock is several buffer/MMCM hops downstream (confirmed: CienaMaia_TSR_976837
# -- client_qsfp28_0_rxoutclk at the GT pin is healthy, but the actual failing
# clock driving its traced loads, qsfpdd_dcmac_flexif_clk393, sits several
# hops further down the same clock tree). So the gate must ALSO check a sample
# of the FINAL loads' own clock domains, not just the GT pin's immediate one.
proc gtpb::sample_load_clocks {cells samplesize} {
    set n [llength $cells]
    set sample $cells
    # stride-sample evenly across the WHOLE list rather than taking a prefix
    # slice -- $cells is alphabetically sorted (lsort -unique in
    # leaf_clock_loads), so a plain prefix would systematically only ever see
    # whichever hierarchy branch happens to sort first, potentially missing
    # the genuinely-violating downstream clock entirely on a huge load list.
    if {$samplesize > 0 && $n > $samplesize} {
        set stride [expr {double($n)/$samplesize}]
        set sample {}
        for {set i 0} {$i < $samplesize} {incr i} {
            lappend sample [lindex $cells [expr {int($i*$stride)}]]
        }
    }
    array unset seen; set out {}
    foreach c $sample {
        if {[catch {get_clocks -quiet -of_objects $c} cl]} { continue }
        foreach cc $cl {
            if {[catch {get_property -quiet NAME $cc} nm]} { continue }
            if {$nm eq "" || [info exists seen($nm)]} { continue }
            set seen($nm) 1
            lappend out $nm
        }
    }
    return $out
}

# true if EITHER the GT pin's own clock OR any clock found on a sample of its
# final load cells is a confirmed timing violator.
proc gtpb::any_clock_violating {pinclk loads samplesize} {
    if {[gtpb::clock_is_violating $pinclk]} { return 1 }
    foreach ck [gtpb::sample_load_clocks $loads $samplesize] {
        if {$ck eq $pinclk} { continue }
        if {[gtpb::clock_is_violating $ck]} { return 1 }
    }
    return 0
}

# a GT's TX/RX out-clock names (cached)
proc gtpb::gt_clocks {gt} {
    variable gtClk
    if {[info exists gtClk($gt)]} { return $gtClk($gt) }
    set cl {}
    foreach pin [get_pins -quiet -of_objects [get_cells -quiet $gt] -filter {DIRECTION==OUT && (NAME =~ *TXOUTCLK* || NAME =~ *RXOUTCLK*)}] {
        foreach c [get_clocks -quiet -of_objects $pin] { lappend cl [get_property -quiet NAME $c] }
    }
    set gtClk($gt) [lsort -unique $cl]
    return $gtClk($gt)
}

# THRESHOLD-FREE verification: are there real timing paths between the owning
# clock(s) and a far GT's clock(s)? (either direction). Pair results are cached.
proc gtpb::shares_timing {ownClks gt pairVar} {
    upvar 1 $pairVar seenpair
    foreach oc $ownClks {
        foreach fc [gtpb::gt_clocks $gt] {
            if {$oc eq "" || $fc eq "" || $oc eq $fc} { continue }
            set key [lsort [list $oc $fc]]
            if {[info exists seenpair($key)]} { if {$seenpair($key)} { return 1 }; continue }
            set hit 0
            if {[llength [get_timing_paths -quiet -from [get_clocks -quiet $oc] -to [get_clocks -quiet $fc] -max_paths 1 -nworst 1]] ||
                [llength [get_timing_paths -quiet -from [get_clocks -quiet $fc] -to [get_clocks -quiet $oc] -max_paths 1 -nworst 1]]} { set hit 1 }
            set seenpair($key) $hit
            if {$hit} { return 1 }
        }
    }
    return 0
}

# ---- text analysis report (always) -------------------------------------------
proc gtpb::write_report {rptfile records} {
    set fh [open $rptfile w]
    puts $fh "GT clock-load pblock analysis (V7)"
    puts $fh "generated: [clock format [clock seconds]]"
    set nE 0; set nS 0; set nN 0
    foreach r $records { switch [dict get $r decision] { EMIT {incr nE} SKIP {incr nS} NOOP {incr nN} } }
    puts $fh "records=[llength $records]  EMIT=$nE  SKIP=$nS  NOOP=$nN"
    puts $fh "============================================================================"
    puts $fh [format "%-44s %-6s %-7s %-8s %s" "GT/clock" "dec" "ffLoads" "CR/SLR" "detail"]
    foreach r $records {
        puts $fh [format "%-44s %-6s %-7s %-8s %s" "[dict get $r gt]/[dict get $r clk]" \
            [dict get $r decision] [dict get $r nff] "[dict get $r spread]/[dict get $r nslr]" [dict get $r reason]]
    }
    close $fh
}

# ---- interactive HTML device view (current spread + resolved pblocks) --------
proc gtpb::write_html {htmlfile gmaxx gmaxy crlist gtlist records} {
    set CELL 42; set LEFT 92; set TOP 28; set BOT 34
    set W [expr {$LEFT + ($gmaxx+1)*$CELL + 20}]
    set H [expr {$TOP + ($gmaxy+1)*$CELL + $BOT}]
    array set slrMinY {}; array set slrMaxY {}; array set crExist {}; set slrset {}
    foreach ce $crlist { lassign $ce x y slr
        if {[lsearch -exact $slrset $slr] < 0} { lappend slrset $slr }
        if {![info exists slrMinY($slr)] || $y < $slrMinY($slr)} { set slrMinY($slr) $y }
        if {![info exists slrMaxY($slr)] || $y > $slrMaxY($slr)} { set slrMaxY($slr) $y }
        set crExist($x,$y) $slr
    }
    set slrset [lsort $slrset]
    set palette {#eaf1fe #fdeaea #eafbea #fff6e2 #f1eafb #eafbfb}
    set barpal  {#2563eb #dc2626 #16a34a #d97706 #7c3aed #0891b2}
    array set slrcol {}; array set slrbar {}; set i 0
    foreach s $slrset { set slrcol($s) [lindex $palette [expr {$i%[llength $palette]}]]; set slrbar($s) [lindex $barpal [expr {$i%[llength $barpal]}]]; incr i }
    array set dcol {EMIT #16a34a SKIP #dc2626 NOOP #6b7280 NOLOAD #94a3b8}

    set nE 0; set nS 0; set nN 0; set nX 0
    foreach r $records { switch [dict get $r decision] { EMIT {incr nE} SKIP {incr nS} NOOP {incr nN} NOLOAD {incr nX} } }

    set fh [open $htmlfile w]
    if {[catch {get_property -quiet NAME [current_design]} _dn]} { set _dn "design" }
    puts $fh "<!DOCTYPE html><html><head><meta charset='utf-8'><title>GT clock-load pblock report (V5)</title>"
    puts $fh {<style>
      :root{--emit:#16a34a;--skip:#dc2626;--noop:#6b7280;--none:#94a3b8;}
      *{box-sizing:border-box;}
      body{font-family:'Segoe UI',Roboto,Arial,sans-serif;margin:0;color:#1f2937;background:#f8fafc;}
      header{background:#111827;color:#fff;padding:14px 20px;}
      header h1{margin:0;font-size:18px;font-weight:600;}
      header .sub{opacity:.75;font-size:12px;margin-top:2px;}
      .pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;color:#fff;margin-right:6px;}
      .pill.e{background:var(--emit);} .pill.s{background:var(--skip);} .pill.n{background:var(--noop);} .pill.x{background:var(--none);} .pill.t{background:#374151;}
      .wrap{padding:16px 20px;}
      h2{font-size:14px;margin:18px 0 8px;color:#374151;}
      table.sum{border-collapse:collapse;width:100%;font-size:12.5px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.06);border-radius:8px;overflow:hidden;}
      table.sum th{background:#f1f5f9;text-align:left;padding:8px 10px;font-weight:600;color:#475569;border-bottom:1px solid #e2e8f0;}
      table.sum td{padding:7px 10px;border-bottom:1px solid #eef2f7;vertical-align:top;}
      table.sum tr{cursor:pointer;} table.sum tr:hover{background:#f8fafc;} table.sum tr.on{background:#fff7ed;}
      .badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700;color:#fff;}
      .badge.EMIT{background:var(--emit);} .badge.SKIP{background:var(--skip);} .badge.NOOP{background:var(--noop);} .badge.NOLOAD{background:var(--none);}
      .num{display:inline-flex;width:20px;height:20px;border-radius:50%;background:#374151;color:#fff;font-size:11px;align-items:center;justify-content:center;font-weight:700;}
      code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:11.5px;}
      .two{display:flex;gap:22px;flex-wrap:wrap;align-items:flex-start;margin-top:8px;}
      .mapbox{background:#fff;border-radius:8px;padding:10px;box-shadow:0 1px 2px rgba(0,0,0,.06);}
      .maptitle{font-size:13px;font-weight:700;color:#334155;margin:2px 0 6px;}
      .legend{font-size:12px;margin:4px 0 8px;color:#475569;}
      .legend b{display:inline-block;margin-right:14px;}
      .ctl button{margin:2px;padding:5px 10px;border:1px solid #cbd5e1;background:#fff;border-radius:6px;cursor:pointer;font-size:12px;}
      .ctl button:hover{background:#f1f5f9;}
      .gtm{cursor:pointer;} .glow circle{stroke:#111;stroke-width:3;}
      text{font-family:'Segoe UI',Arial,sans-serif;}
    </style>}
    puts $fh "</head><body>"
    puts $fh "<header><h1>GT clock-load pblock report (V5)</h1><div class='sub'>[gtpb::htmlesc $_dn] &nbsp;&middot;&nbsp; click a row or GT marker to isolate it on both maps</div>"
    puts $fh "<div style='margin-top:8px'><span class='pill t'>[llength $records] GTs</span><span class='pill e'>$nE emit</span><span class='pill s'>$nS skip</span><span class='pill n'>$nN no-op</span><span class='pill x'>$nX no-load</span></div></header>"
    puts $fh "<div class='wrap'>"

    puts $fh "<h2>Per-GT summary</h2>"
    puts $fh "<table class='sum'><tr><th>#</th><th>GT (short)</th><th>clock</th><th>decision</th><th>FF loads</th><th>spread<br>CR / SLR</th><th>action / conflict</th></tr>"
    set idx 0
    foreach r $records {
        set dec [dict get $r decision]
        set act [expr {$dec eq "EMIT" ? "pblock &rarr; <code>[join [dict get $r chosen] { }]</code>" : [gtpb::htmlesc [dict get $r reason]]}]
        puts $fh "<tr id='item$idx' class='$dec' onclick='sel($idx)'><td><span class='num' style='background:$dcol($dec)'>$idx</span></td><td style='font-family:Consolas,monospace;font-size:11px;word-break:break-all;max-width:460px'>[gtpb::htmlesc [dict get $r gt]]</td><td>[dict get $r clk]</td><td><span class='badge $dec'>$dec</span></td><td>[dict get $r nff]</td><td>[dict get $r spread] / [dict get $r nslr]</td><td>$act</td></tr>"
        incr idx
    }
    puts $fh "</table>"

    puts $fh "<h2>Device maps</h2>"
    puts $fh "<div class='ctl'><button onclick=\"showAll(1)\">Show all</button><button onclick=\"showAll(0)\">Clear</button><button onclick=\"showClass('EMIT')\">Only EMIT</button><button onclick=\"showClass('SKIP')\">Only SKIP</button></div>"
    puts $fh "<div class='legend'><b>&#9679; GT (numbered, colored by decision)</b><b style='color:#2563eb'>&#9633; current load spread</b><b style='color:#16a34a'>&#9632; proposed pblock</b><b style='color:#dc2626'>&mdash; conflict link</b></div>"
    puts $fh "<div class='two'>"

    foreach spec {{L {Current — load spread} spread} {R {Expected — resolved pblocks} pblock}} {
        lassign $spec sfx title kind
        puts $fh "<div class='mapbox'><div class='maptitle'>$title</div><svg id='dev$sfx' width='$W' height='$H'>"
        for {set y 0} {$y <= $gmaxy} {incr y} {
            for {set x 0} {$x <= $gmaxx} {incr x} {
                set px [expr {$LEFT + $x*$CELL}]; set py [expr {$TOP + ($gmaxy-$y)*$CELL}]
                if {[info exists crExist($x,$y)]} {
                    puts $fh "<rect x='$px' y='$py' width='[expr {$CELL-3}]' height='[expr {$CELL-3}]' rx='3' fill='$slrcol($crExist($x,$y))' stroke='#cbd5e1'><title>CLOCKREGION_X${x}Y${y} ($crExist($x,$y))</title></rect>"
                } else {
                    puts $fh "<rect x='$px' y='$py' width='[expr {$CELL-3}]' height='[expr {$CELL-3}]' rx='3' fill='#f8fafc' stroke='#eef2f7'/>"
                }
            }
        }
        foreach s $slrset {
            set y0 [expr {$TOP + ($gmaxy-$slrMaxY($s))*$CELL}]
            set y1 [expr {$TOP + ($gmaxy-$slrMinY($s))*$CELL + $CELL-3}]
            puts $fh "<rect x='6' y='$y0' width='15' height='[expr {$y1-$y0}]' rx='3' fill='$slrbar($s)' opacity='0.85'/>"
            puts $fh "<text x='28' y='[expr {($y0+$y1)/2+4}]' font-size='12' font-weight='700' fill='#334155'>$s</text>"
        }
        for {set x 0} {$x <= $gmaxx} {incr x} { puts $fh "<text x='[expr {$LEFT+$x*$CELL+($CELL-3)/2}]' y='[expr {$TOP+($gmaxy+1)*$CELL+12}]' font-size='9' fill='#94a3b8' text-anchor='middle'>X$x</text>" }
        for {set y 0} {$y <= $gmaxy} {incr y} { puts $fh "<text x='[expr {$LEFT-5}]' y='[expr {$TOP+($gmaxy-$y)*$CELL+($CELL-3)/2+3}]' font-size='9' fill='#94a3b8' text-anchor='end'>Y$y</text>" }
        set idx 0
        foreach r $records {
            set dec [dict get $r decision]
            puts $fh "<g id='rec${sfx}$idx' style='display:inline'>"
            if {$kind eq "spread"} {
                set lminx [dict get $r lminx]; set lmaxx [dict get $r lmaxx]; set lminy [dict get $r lminy]; set lmaxy [dict get $r lmaxy]
                set bx [expr {$LEFT + $lminx*$CELL - 2}]; set by [expr {$TOP + ($gmaxy-$lmaxy)*$CELL - 2}]
                set bw [expr {($lmaxx-$lminx+1)*$CELL}]; set bh [expr {($lmaxy-$lminy+1)*$CELL}]
                set bcol [expr {$dec eq "SKIP" ? "#dc2626" : ($dec eq "EMIT" ? "#2563eb" : "#94a3b8")}]
                if {$dec ne "NOLOAD"} { puts $fh "<rect x='$bx' y='$by' width='$bw' height='$bh' fill='none' stroke='$bcol' stroke-dasharray='5' stroke-width='2' rx='4'/>" }
                if {$dec eq "SKIP"} {
                    set ox [expr {$LEFT + [dict get $r gx]*$CELL + $CELL/2}]; set oy [expr {$TOP + ($gmaxy-[dict get $r gy])*$CELL + $CELL/2}]
                    foreach fg [dict get $r fargtcoords] { lassign $fg fx fy
                        set tx [expr {$LEFT + $fx*$CELL + $CELL/2}]; set ty [expr {$TOP + ($gmaxy-$fy)*$CELL + $CELL/2}]
                        puts $fh "<line x1='$ox' y1='$oy' x2='$tx' y2='$ty' stroke='#dc2626' stroke-width='2' opacity='0.6'/>"
                    }
                }
            } else {
                foreach crn [dict get $r chosen] {
                    if {[regexp {X(\d+)Y(\d+)} $crn -> cx cy]} {
                        set px [expr {$LEFT + $cx*$CELL}]; set py [expr {$TOP + ($gmaxy-$cy)*$CELL}]
                        puts $fh "<rect x='$px' y='$py' width='[expr {$CELL-3}]' height='[expr {$CELL-3}]' rx='3' fill='rgba(22,163,74,0.45)' stroke='#16a34a' stroke-width='2'/>"
                    }
                }
            }
            puts $fh "</g>"
            incr idx
        }
        array unset seenkey
        set idx 0
        foreach r $records {
            set dec [dict get $r decision]
            set gx [dict get $r gx]; set gy [dict get $r gy]
            set key "$gx,$gy"
            set k [expr {[info exists seenkey($key)] ? $seenkey($key) : 0}]; set seenkey($key) [expr {$k+1}]
            set ox [expr {($k % 3)*13 - 13}]; set oy [expr {($k / 3)*13 - 6}]
            set cx [expr {$LEFT + $gx*$CELL + ($CELL-3)/2 + $ox}]; set cy [expr {$TOP + ($gmaxy-$gy)*$CELL + ($CELL-3)/2 + $oy}]
            puts $fh "<g class='gtm' id='gtm${sfx}$idx' onclick='sel($idx)'><circle cx='$cx' cy='$cy' r='10' fill='$dcol($dec)' stroke='#fff' stroke-width='2'><title>#$idx [gtpb::htmlesc [dict get $r gt]] / [dict get $r clk] ($dec)</title></circle><text x='$cx' y='[expr {$cy+4}]' font-size='10' font-weight='700' fill='#fff' text-anchor='middle'>$idx</text></g>"
            incr idx
        }
        puts $fh "</svg></div>"
    }
    puts $fh "</div></div>"
    puts $fh {<script>
      function setRow(i,on){var it=document.getElementById('item'+i); if(it)it.classList.toggle('on',on);
        ['L','R'].forEach(function(s){var g=document.getElementById('rec'+s+i); if(g)g.style.display=on?'inline':'none';
          var m=document.getElementById('gtm'+s+i); if(m)m.classList.toggle('glow',on);});}
      function sel(i){var g=document.getElementById('recL'+i); setRow(i, g && g.style.display=='none');}
      function showAll(v){for(var i=0;document.getElementById('recL'+i);i++)setRow(i,!!v);}
      function showClass(c){for(var i=0;document.getElementById('item'+i);i++)setRow(i,document.getElementById('item'+i).classList.contains(c));}
      window.onload=function(){showAll(1);};
    </script>}
    puts $fh "</body></html>"
    close $fh
}

proc gt_clock_load_pblock {args} {
    set max_cr    0
    set max_hcr   3          ;# horizontal spread cap: max clock regions per pblock
    set slr_load_frac 0.5    ;# skip an SLR if its combined GT FF-load exceeds this
    set slr_dominance_frac 0.50  ;# V2: skip a whole-SLR pblock if this ONE clock's
                                  ;# own nff exceeds this fraction of the SLR's total
                                  ;# FF capacity (likely the design's own majority
                                  ;# clock, not a compactable GT-local load)
    set headroom  0.70       ;# per clock region occupancy cap (70%)
    set min_loads 100
    set max_loads 2000000   ;# V2.1: outer safety cap only -- the real per-clock cap
                             ;# is now computed per-GT from its own SLR's dominance
                             ;# ceiling (see pass 1), so this rarely binds anymore
    set anchor    "gt"        ;# gt = start at GT's own CR/SLR (default) | dominant = load majority
    set outfile   "fix.tcl"
    set htmlfile  ""
    set outdir    ""
    set do_html   1
    set force     0
    set clear_locs 1          ;# add_cells_to_pblock -clear_locs (unfix any existing
                               ;# LOC) -- only ever applies to cells that already
                               ;# passed the -exclude_fixed filter below, so this
                               ;# never touches a cell that had a fixed LOC
    set exclude_fixed 1       ;# V3 FIX 2: drop IS_LOC_FIXED cells from a clock's
                               ;# candidate load list BEFORE sizing/pblock-building
                               ;# even begins -- never assigned to any pblock at
                               ;# all (not just never LOC-cleared), regardless of
                               ;# whether it's a recognized dedicated-site buffer
                               ;# (e.g. BUFG_GT_SYNC) or any other fixed cell.
                               ;# Override with -keep_fixed only if you specifically
                               ;# want fixed cells considered (not recommended).
    set require_violating 1  ;# V3 FIX 5: only trace/compact a GT clock whose
                               ;# OWN worst slack is actually negative (a
                               ;# confirmed timing violator). Confirmed via
                               ;# real baseline Intra Clock Table data across
                               ;# Spirent_Ethernet/design_T/Ciena_cr_1141550
                               ;# (every GT clock healthy, skill still acted,
                               ;# regressed/crashed) vs advantest_extraclk_1116
                               ;# (GT clocks genuinely negative, skill helped a
                               ;# lot) that this is the single most reliable
                               ;# predictor of whether this skill should act at
                               ;# all. Disable with -no_require_violating only
                               ;# for A/B comparison against the always-on
                               ;# (V1/V2-style) behavior.
    set whole_slr_cell_cap   100000   ;# V4 FIX A
    set max_disturb_frac    1.0       ;# V4 FIX C: disabled by default, opt in
    set max_gt_span   3
    set max_load_span 6
    set gt_margin     1
    set trace_depth   3
    set trace_fanout  32
    set hist_sample   5000
    set verify        "timing"   ;# timing (get_timing_paths, threshold-free) | cone
    for {set i 0} {$i < [llength $args]} {incr i} {
        switch -- [lindex $args $i] {
            -max_cr        { set max_cr    [lindex $args [incr i]] }
            -max_hcr       { set max_hcr   [lindex $args [incr i]] }
            -slr_load_frac { set slr_load_frac [lindex $args [incr i]] }
            -slr_dominance_frac { set slr_dominance_frac [lindex $args [incr i]] }
            -headroom      { set headroom  [lindex $args [incr i]] }
            -min_loads     { set min_loads [lindex $args [incr i]] }
            -max_loads     { set max_loads [lindex $args [incr i]] }
            -anchor        { set anchor    [lindex $args [incr i]] }
            -out           { set outfile   [lindex $args [incr i]] }
            -html          { set htmlfile  [lindex $args [incr i]] }
            -outdir        { set outdir    [lindex $args [incr i]] }
            -no_html       { set do_html 0 }
            -no_clear_locs { set clear_locs 0 }
            -keep_fixed    { set exclude_fixed 0 }
            -no_require_violating { set require_violating 0 }
            -whole_slr_cell_cap { set whole_slr_cell_cap [lindex $args [incr i]] }
            -max_disturb_frac   { set max_disturb_frac   [lindex $args [incr i]] }
            -max_gt_span   { set max_gt_span   [lindex $args [incr i]] }
            -max_load_span { set max_load_span [lindex $args [incr i]] }
            -gt_margin     { set gt_margin     [lindex $args [incr i]] }
            -trace_depth   { set trace_depth   [lindex $args [incr i]] }
            -trace_fanout  { set trace_fanout  [lindex $args [incr i]] }
            -hist_sample   { set hist_sample   [lindex $args [incr i]] }
            -verify        { set verify        [lindex $args [incr i]] }
            -force         { set force 1 }
            default        { }
        }
    }
    if {[catch {current_design} cd] || $cd eq ""} { error "gt_clock_load_pblock: no open design. Open a PLACED checkpoint first (open_checkpoint <placed>.dcp)." }

    # V3 bugfix: defense-in-depth against [Common 17-736] "tcl.collection
    # ResultDisplayLimit was exceeded" -- this is a hard ERROR (not a warning)
    # that Vivado throws whenever a >500-element Collection needs implicit
    # string-list conversion. Root cause found and fixed at the actual trigger
    # (a Tcl lmap applied directly to filter's Collection-typed return value,
    # replaced with foreach+lappend elsewhere in this file) -- this set_param
    # is the vendor-documented belt-and-suspenders on top of that real fix.
    catch {set_param tcl.collectionResultDisplayLimit 0}

    if {$outdir eq ""} { set outdir [file join $::gtcl_base "gt_clock_pblocks"] }
    set base $outdir
    file mkdir $outdir
    set xdcfile    [file join $outdir [file tail $outfile]]
    if {$htmlfile eq ""} { set htmlfile [file join $outdir "gt_clock_pblocks.html"] }
    set reportfile [file join $outdir "gt_clock_analysis.rpt"]

    array unset gtpb::slrOf; array unset gtpb::capFF; array unset gtpb::slrCapFF
    array unset gtpb::coneMember; array unset gtpb::coneBuilt
    array unset gtpb::gtClk
    array unset gtpb::violCache
    set gtpb::WHOLE_SLR_CELL_CAP $whole_slr_cell_cap
    set gtpb::WHOLE_SLR_MAX_DISTURB_FRAC $max_disturb_frac
    gtpb::init_congestion_gate $outdir
    gtpb::init_dfx_rm_regions

    set gts [get_cells -quiet -hier -filter {PRIMITIVE_TYPE =~ *.GT.* || PRIMITIVE_GROUP == GT}]
    if {[llength $gts] == 0} {
        set gts [get_cells -quiet -hier -filter {REF_NAME =~ *GT*CHANNEL* || REF_NAME =~ *GT*_DUAL || REF_NAME =~ *GT*_QUAD}]
    }
    if {[llength $gts] == 0} { puts "No GT primitives found (tried PRIMITIVE_TYPE =~ *.GT.*)."; return }
    puts "gt_clock_load_pblock (V5): design '$cd' | GT primitives=[llength $gts] | outdir=$outdir | verify=$verify | slr_dominance_frac=$slr_dominance_frac | require_violating=$require_violating | whole_slr_cell_cap=$whole_slr_cell_cap | max_disturb_frac=$max_disturb_frac"

    # device grid + GT positions
    set crlist {}; set gmaxx 0; set gmaxy 0
    foreach cr [get_clock_regions -quiet *] {
        set crn [gtpb::cr_name $cr]; lassign [gtpb::cr_xy $crn] x y
        if {$x > $gmaxx} { set gmaxx $x }; if {$y > $gmaxy} { set gmaxy $y }
        lappend crlist [list $x $y [gtpb::cr_slr $crn]]
    }
    set gtpos {}; array set gtCoord {}
    foreach g $gts {
        set cr [get_clock_regions -quiet -of_objects $g]
        if {[llength $cr]} {
            set crn [gtpb::cr_name $cr]; lassign [gtpb::cr_xy $crn] gx gy
            lappend gtpos [list $g $gx $gy [gtpb::cr_slr $crn]]
            set gtCoord($g) [list $gx $gy]
        }
    }
    set midX [expr {$gmaxx/2.0}]   ;# left half (X<midX) vs right half - the two GT 'sides'

    # ---- Pass 1: analyse every GT clock; build mutual entanglement graph ------
    set clocks {}
    array set entangled {}     ;# gtCell -> 1
    array set entWithSet {}    ;# gtCell -> list of partner GT cells
    array set tpair {}         ;# cache: clock-pair -> shares-timing (0/1)
    set lowfanout_records {}   ;# V2.1: every clkpin that never makes it into $clocks
                                ;# (too small, or hit its dominance ceiling) still
                                ;# gets an explicit record here -- nothing vanishes
                                ;# silently anymore.
    set sibling_noop_candidates {}  ;# V6 FIX: every clk-sourced or DATA_IF candidate
                                ;# that NOOP'd purely due to require_violating (its
                                ;# OWN clock is structurally healthy) is saved here,
                                ;# raw ingredients intact, so Pass 1.5c below can
                                ;# re-examine it: if a STRUCTURAL SIBLING (same GT
                                ;# hierarchy pattern, differing only by a lane/channel
                                ;# index, e.g. DN_PHY_PIE8[0] vs DN_PHY_PIE8[2], same
                                ;# clkpin name) DID qualify and get emitted, this
                                ;# healthy sibling is promoted into $clocks too, using
                                ;# its own already-correctly-traced load cells. See
                                ;# Pass 1.5c header comment for the full rationale
                                ;# (confirmed root cause: advantest_vp1802_0805's
                                ;# PIE8 lanes 0/1/6 are structurally healthy and were
                                ;# left at a 512-cell DATA_IF fallback while sibling
                                ;# lanes 2/3/4/5/7 -- identical hardware, genuinely
                                ;# violating -- got their full ~41K-cell pblock,
                                ;# producing an asymmetric floorplan that measurably
                                ;# underperforms the historical hand-built recipe
                                ;# which pblocked all 8 lanes uniformly).
    set gi 0
    foreach gt $gts {
        incr gi
        puts "  pass1 $gi/[llength $gts]: $gt"
        set gtcr [get_clock_regions -quiet -of_objects $gt]
        set gtX 0; set gtY 0
        if {[llength $gtcr]} { lassign [gtpb::cr_xy [gtpb::cr_name $gtcr]] gtX gtY }
        set ownGTslr [expr {[llength $gtcr] ? [gtpb::cr_slr [gtpb::cr_name $gtcr]] : ""}]
        set ownSide  [expr {$gtX < $midX ? "L" : "R"}]

        # V2.1 FIX (truncation ambiguity): cap the leaf-load trace at THIS GT's own
        # SLR dominance ceiling, not the flat global max_loads. Either the trace
        # completes UNDER the ceiling -> capped=0 and the load list is COMPLETE and
        # accurate (safe to pblock in full), or it HITS the ceiling -> capped=1 and
        # we already know, without finishing the walk, that this clock alone would
        # violate -slr_dominance_frac -- so it gets SKIPPED outright, never emitted
        # with a partial/arbitrary subset of its true loads.
        set _gtSlrCap [expr {$ownGTslr ne "" ? [gtpb::slr_ff_cap $ownGTslr] : 0}]
        set _domCeiling [expr {$_gtSlrCap > 0 ? int(ceil($_gtSlrCap*$slr_dominance_frac))+1 : $max_loads}]
        set _perGtMax [expr {min($max_loads, $_domCeiling)}]

        set clkpins [get_pins -quiet -of_objects $gt -filter {DIRECTION==OUT && (NAME =~ *TXOUTCLK* || NAME =~ *RXOUTCLK*)}]
        # gather processing sources: each GT out-clock with enough clock loads; if
        # none (dead out-clocks), fall back to the GT's DATA-interface fabric cells.
        set sources {}
        foreach pin $clkpins {
            puts "    TRACE: entering pin=$pin"
            set clkpin [get_property -quiet REF_PIN_NAME $pin]
            if {$clkpin eq ""} { set clkpin [lindex [split $pin /] end] }
            set _pinclk [lindex [gtpb::pin_clocks $pin] 0]

            # --- CHEAP pre-check (native-filtered, no per-pin fanout loop) ---
            # Discover gen_clocks + disqualification and check violation
            # BEFORE paying for the expensive full leaf_clock_loads walk
            # (which enumerates every fanout pin on nets that can carry tens
            # of thousands of loads). Most clocks NOOP (don't violate), so
            # this ordering skips the costly walk entirely for the common
            # case -- only clocks that actually need sizing pay for it.
            lassign [gtpb::quick_gen_clocks $pin] gen_clocks q_disqualified q_disqual_reason
            # Union with natively-discovered generated clocks (via MASTER_CLOCK
            # property) -- the primary, reliable mechanism; the netlist-walk
            # result above is kept as a supplementary source.
            set gen_clocks [lsort -unique [concat $gen_clocks [gtpb::native_generated_clocks $_pinclk]]]
            if {$q_disqualified} {
                set _why "SKIP: $q_disqual_reason"
                puts "    NOTE: $gt / $clkpin : $_why"
                lappend lowfanout_records [dict create gt $gt clk $clkpin gx $gtX gy $gtY gslr $ownGTslr \
                    decision SKIP reason $_why nff 0 spread 0 nslr 0 \
                    lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
                set seenGT($gt) 1
                continue
            }
            # V3 FIX 5 (trigger-condition gate, default ON) / New_GT_clock_load V2:
            # trigger is the GT's own TXOUTCLK/RXOUTCLK clock OR any clock
            # GENERATED at a BUFG_GT/MBUFG_GT distribution stage within this
            # GT's own clock tree (e.g. dn0_pclk250) -- deliberately NOT
            # sampling arbitrary load-cell clocks from potentially unrelated
            # domains (that broader OR-check was confirmed this session to
            # drive ~59%% of observed WNS degradations across V4-V7). The
            # GT's own raw pin clock very often has no real intra-domain
            # paths at all (blank WNS); checking its own generated/
            # distributed clocks too catches the common real-violation case
            # without reintroducing the unrelated-clock degradation risk.
            set _ownclk_viol [gtpb::clock_is_violating $_pinclk]
            set _genclk_viol 0
            set _genclk_hit ""
            if {!$_ownclk_viol} {
                foreach _gck $gen_clocks {
                    if {$_gck eq $_pinclk} { continue }
                    if {[gtpb::clock_is_violating $_gck]} { set _genclk_viol 1; set _genclk_hit $_gck; break }
                }
            }
            if {$require_violating && !$_ownclk_viol && !$_genclk_viol} {
                set _why "NOOP: neither clock '$_pinclk' (this GT's own pin clock) nor any of its [llength $gen_clocks] own generated/distributed clock(s) ($gen_clocks) is a confirmed timing violator (worst slack >= 0 ns everywhere checked) -- structurally healthy by this version's own-clock-tree-only trigger. SKIPPED (-require_violating, default on; disable with -no_require_violating). (fast pre-check -- full load trace skipped)"
                puts "    NOTE: $gt / $clkpin : $_why"
                lappend lowfanout_records [dict create gt $gt clk $clkpin gx $gtX gy $gtY gslr $ownGTslr \
                    decision NOOP reason $_why nff 0 spread 0 nslr 0 \
                    lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
                set seenGT($gt) 1
                # V6 FIX: save raw ingredients -- Pass 1.5c may still promote this
                # if a structural sibling GT (same hierarchy pattern, different
                # lane index) genuinely violates and gets emitted.
                lappend sibling_noop_candidates [dict create gt $gt clkpin $clkpin gtX $gtX gtY $gtY \
                    gtcr $gtcr ownGTslr $ownGTslr ownSide $ownSide stype clk \
                    loads {} pin $pin capped 0 passthrough {}]
                continue
            }
            # FIX I (congestion gate): this clock IS a confirmed violator, but
            # if its own SLR already has Global or Short router congestion at
            # level >= 5, providing a pblock here risks making that congestion
            # worse rather than helping -- the same failure mode seen on
            # Teradyne_tsr_976770/advantest_vp1802_0805 (routing congestion
            # regressions after applying a real fix). Skip entirely rather
            # than add more placement pressure to an already-congested SLR.
            if {[gtpb::slr_congested $ownGTslr]} {
                set _why "NOOP: clock '$_pinclk' (or its generated/distributed clocks) IS a confirmed timing violator, but this GT's own SLR ($ownGTslr) already has Global or Short router congestion >= level 5 -- a pblock suggestion here risks worsening that congestion rather than helping. SKIPPED (FIX I, congestion gate)."
                puts "    NOTE: $gt / $clkpin : $_why"
                lappend lowfanout_records [dict create gt $gt clk $clkpin gx $gtX gy $gtY gslr $ownGTslr \
                    decision NOOP reason $_why nff 0 spread 0 nslr 0 \
                    lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
                set seenGT($gt) 1
                continue
            }

            # --- Violating (or require_violating disabled): now pay for the
            # full, accurate load-enumeration walk needed for sizing. ---
            lassign [gtpb::leaf_clock_loads $pin $_perGtMax] loads capped passthrough disqualified disqual_reason gen_clocks
            if {$disqualified} {
                # Defensive only -- quick_gen_clocks above already checked this;
                # should not normally trigger, kept as a safety net.
                set _why "SKIP: $disqual_reason"
                puts "    NOTE: $gt / $clkpin : $_why"
                lappend lowfanout_records [dict create gt $gt clk $clkpin gx $gtX gy $gtY gslr $ownGTslr \
                    decision SKIP reason $_why nff [llength $loads] spread 0 nslr 0 \
                    lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
                set seenGT($gt) 1
                continue
            }
            # V3 FIX 2: drop IS_LOC_FIXED cells from the candidate load list
            # BEFORE sizing/pblock-building -- they are never assigned to any
            # pblock at all (not just never LOC-cleared).
            if {$exclude_fixed && [llength $loads]} { set loads [filter $loads {!IS_LOC_FIXED}] }
            # V3 FIX 2 (independent, unconditional): ALSO drop any dedicated-
            # site clock buffer/MMCM/PLL cell regardless of IS_LOC_FIXED --
            # that property is proven unreliable for these cells at analysis
            # time (confirmed: BUFG_GT_SYNC reached fix.tcl on Samsung_Medsion/
            # Teledyne_LeCroy SummitZ6 with IS_LOC_FIXED=0). leaf_clock_loads
            # already treats these as passthrough when walked via a clock net,
            # but this is a second, independent guarantee for any path.
            if {[llength $loads]} {
                set _filtered {}
                foreach c $loads { if {![gtpb::is_dedicated_cell $c]} { lappend _filtered $c } }
                set loads $_filtered
            }
            # V3 FIX 3: DFX exclusion -- check the GT cell itself, every buffer/
            # MMCM/PLL cell passed through, and every final load cell. If ANY
            # is HD.RECONFIGURABLE, skip this clock ENTIRELY -- no pblock, not
            # even a trimmed one (a DFX RM's pblock must range every resource
            # type it uses; this skill cannot guarantee that).
            if {[gtpb::any_hd_reconfigurable [concat [list $gt] $passthrough $loads]]} {
                set _why "SKIP: HD.RECONFIGURABLE (DFX) cell(s) detected upstream or downstream of this clock's load tree (GT cell, a clock buffer/MMCM/PLL it passes through, or one of its final loads) -- skipped entirely, no pblock suggestion for this clock. A DFX reconfigurable-module pblock must range every resource type the RM uses; this skill has no way to guarantee that, so it never touches a DFX-adjacent clock."
                puts "    NOTE: $gt / $clkpin : $_why"
                lappend lowfanout_records [dict create gt $gt clk $clkpin gx $gtX gy $gtY gslr $ownGTslr \
                    decision SKIP reason $_why nff [llength $loads] spread 0 nslr 0 \
                    lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
                set seenGT($gt) 1
                continue
            }
            if {$capped} {
                set _why "SKIP: leaf-load trace reached its per-clock dominance ceiling ($_perGtMax loads, from SLR $ownGTslr capacity=$_gtSlrCap x -slr_dominance_frac=$slr_dominance_frac) before finishing -- this clock's real fanout alone meets/exceeds the safe compaction budget for its own SLR; SKIPPED rather than emitting a partial/truncated pblock (its true total is larger than $_perGtMax, not shown)"
                puts "    NOTE: $gt / $clkpin : $_why"
                lappend lowfanout_records [dict create gt $gt clk $clkpin gx $gtX gy $gtY gslr $ownGTslr \
                    decision SKIP reason $_why nff [llength $loads] spread 0 nslr 0 \
                    lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
                set seenGT($gt) 1
                continue
            }
            if {[llength $loads] >= $min_loads} {
                lappend sources [list clk $clkpin $loads $pin $capped $passthrough]
            } else {
                set _why "NOOP: only [llength $loads] loads (< -min_loads=$min_loads) -- genuinely low fanout, not a compaction candidate (this is the expected, benign case for a one-directional GT lane, e.g. an RX-only or TX-only sub-instance)"
                puts "    NOTE: $gt / $clkpin : $_why"
                lappend lowfanout_records [dict create gt $gt clk $clkpin gx $gtX gy $gtY gslr $ownGTslr \
                    decision NOOP reason $_why nff [llength $loads] spread 0 nslr 0 \
                    lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
            }
        }
        if {[llength $sources] == 0} {
            set dloads [gtpb::gt_data_loads $gt $max_loads]
            if {$exclude_fixed && [llength $dloads]} { set dloads [filter $dloads {!IS_LOC_FIXED}] }
            # V3 FIX 2 (independent, unconditional): gt_data_loads() already
            # excludes dedicated-site buffer/MMCM/PLL cells internally now, but
            # re-check here too (defense in depth, same reasoning as above).
            if {[llength $dloads]} {
                set _dfiltered {}
                foreach c $dloads { if {![gtpb::is_dedicated_cell $c]} { lappend _dfiltered $c } }
                set dloads $_dfiltered
            }
            if {[gtpb::any_hd_reconfigurable [concat [list $gt] $dloads]]} {
                puts "    -> SKIP $gt : HD.RECONFIGURABLE (DFX) cell(s) detected in data-interface cells -- skipped entirely"
                lappend lowfanout_records [dict create gt $gt clk "DATA_IF" gx $gtX gy $gtY gslr $ownGTslr \
                    decision SKIP reason "SKIP: HD.RECONFIGURABLE (DFX) cell(s) detected upstream or downstream of the GT's data-interface cells -- skipped entirely, no pblock suggestion." \
                    nff [llength $dloads] spread 0 nslr 0 \
                    lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
                set seenGT($gt) 1
            } elseif {[llength $dloads] >= $min_loads} {
                # V5 FIX F: the DATA_IF fallback previously had NO timing check
                # at all -- it fired purely on fanout (>= min_loads), with zero
                # awareness of whether the design needs any help. Confirmed on
                # SM_Optics_rigel (postroute) and Viavi_eth800g (postplace):
                # both have ZERO violated path-groups anywhere in the whole
                # design (BASELINE CLEAN), yet both still emitted DATA_IF
                # pblocks that regressed WNS -- pure downside, no theoretical
                # upside, because nothing was ever checked. Reuses the EXACT
                # same machinery already proven for the clk-sourced path
                # (gtpb::any_clock_violating / sample_load_clocks /
                # clock_is_violating) -- just applied to the data-interface
                # load cells themselves instead of a GT pin's out-clock, since
                # there is no live out-clock to check for a dead-out-clock GT.
                # clock_is_violating("") returns 0 immediately, so passing ""
                # as the pin-clock argument correctly skips straight to
                # sampling the clocks found on $dloads itself.
                if {$require_violating && ![gtpb::any_clock_violating "" $dloads 500]} {
                    # DATA_IF fallback (dead out-clocks, no TXOUTCLK/RXOUTCLK pin to
                    # check directly) -- V2 still samples the loads' own clocks here,
                    # since there is no GT-pin clock object to restrict to.
                    set _why "NOOP: DATA_IF candidate ([llength $dloads] direct data-interface loads) -- no violating clock found among its load cells (worst slack >= 0 ns everywhere checked), structurally healthy. SKIPPED (-require_violating, default on; disable with -no_require_violating)."
                    puts "    NOTE: $gt / DATA_IF : $_why"
                    lappend lowfanout_records [dict create gt $gt clk "DATA_IF" gx $gtX gy $gtY gslr $ownGTslr \
                        decision NOOP reason $_why nff [llength $dloads] spread 0 nslr 0 \
                        lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
                    set seenGT($gt) 1
                    # V6 FIX: save raw ingredients for possible Pass 1.5c promotion.
                    lappend sibling_noop_candidates [dict create gt $gt clkpin "DATA_IF" gtX $gtX gtY $gtY \
                        gtcr $gtcr ownGTslr $ownGTslr ownSide $ownSide stype data \
                        loads $dloads pin "" capped 0 passthrough {}]
                } elseif {[gtpb::slr_congested $ownGTslr]} {
                    # FIX I extension: the congestion gate previously only existed in the
                    # clk-sourced branch above -- DATA_IF candidates never consulted
                    # gtpb::slr_congested at all, regardless of congestion level. Same
                    # reasoning as the clk-sourced FIX I: a pblock here risks making
                    # already-bad SLR congestion worse.
                    set _why "NOOP: DATA_IF candidate ([llength $dloads] direct data-interface loads) is a confirmed violator, but this GT's own SLR ($ownGTslr) already has Global or Short router congestion >= level 5 -- a pblock suggestion here risks worsening that congestion rather than helping. SKIPPED (FIX I, congestion gate, DATA_IF extension)."
                    puts "    NOTE: $gt / DATA_IF : $_why"
                    lappend lowfanout_records [dict create gt $gt clk "DATA_IF" gx $gtX gy $gtY gslr $ownGTslr \
                        decision NOOP reason $_why nff [llength $dloads] spread 0 nslr 0 \
                        lminx $gtX lmaxx $gtX lminy $gtY lmaxy $gtY chosen {} fargtcoords {}]
                    set seenGT($gt) 1
                } else {
                    puts "    -> $gt : dead out-clocks; using [llength $dloads] direct DATA-interface cells"
                    lappend sources [list data DATA_IF $dloads "" 0 {}]
                }
            } else {
                puts "    -> SKIP $gt : no out-clock loads; data-interface cells=[llength $dloads] (< min_loads=$min_loads)"
            }
        }
        foreach src $sources {
            lassign $src stype clkpin loads pin capped passthrough
            set nloads [llength $loads]

            # FF count over ALL loads (fast C-level filter); pblock still uses all loads
            set ffcells [expr {[llength $loads] ? [filter $loads {REF_NAME =~ FD*}] : {}}]
            set nff [llength $ffcells]
            if {$nff == 0} { set nff $nloads; set ffcells $loads }
            # spread histogram from a SAMPLE (per-cell get_clock_regions is the costly part)
            set sample $ffcells
            if {$hist_sample > 0 && [llength $sample] > $hist_sample} { set sample [lrange $sample 0 [expr {$hist_sample-1}]] }
            array unset histff; array unset slrhist
            foreach c $sample {
                set cr [get_clock_regions -quiet -of_objects $c]
                if {![llength $cr]} { continue }
                set crn [gtpb::cr_name $cr]
                if {[info exists histff($crn)]} { incr histff($crn) } else { set histff($crn) 1 }
                set slr [gtpb::cr_slr $crn]
                if {$slr ne ""} { if {[info exists slrhist($slr)]} { incr slrhist($slr) } else { set slrhist($slr) 1 } }
            }
            set occupied [array names histff]
            set spread [llength $occupied]
            if {$spread == 0} { puts "    -> SKIP $gt / $clkpin : $nloads loads but none are placed in a clock region (unplaced?)"; continue }
            set nslr [llength [array names slrhist]]

            set domCR ""; set domN -1
            foreach crn $occupied { if {$histff($crn) > $domN} { set domN $histff($crn); set domCR $crn } }
            set domSLR ""; set domSN -1
            foreach s [array names slrhist] { if {$slrhist($s) > $domSN} { set domSN $slrhist($s); set domSLR $s } }
            lassign [gtpb::cr_xy $domCR] domX domY

            # V4 FIX D: when this clock's path passes through a dedicated
            # distribution buffer (BUFG_GT/BUFGCE/BUFGCE_DIV/BUFG/MMCM/PLL/
            # DPLL -- already identified as $passthrough during the trace),
            # that buffer's OWN physical site is the real point the clock
            # fans out from, not the GT's own pin nor the load centroid.
            # Neither gtCRname nor domCR ever queried this -- the buffer was
            # only ever used for the DFX check and then discarded. If the
            # buffer sits somewhere other than near the GT (common when it
            # re-times/divides the clock before a wide fanout), anchoring on
            # gtCRname/domCR leaves the buffer-to-pblock clock network
            # spanning the same long distance this skill exists to avoid.
            set bufCR ""
            if {[llength $passthrough]} {
                array unset bufhist
                foreach bc $passthrough {
                    if {[catch {get_clock_regions -quiet -of_objects $bc} bcr] || ![llength $bcr]} { continue }
                    set bcrn [gtpb::cr_name $bcr]
                    if {[info exists bufhist($bcrn)]} { incr bufhist($bcrn) } else { set bufhist($bcrn) 1 }
                }
                set bufN -1
                foreach bcrn [array names bufhist] { if {$bufhist($bcrn) > $bufN} { set bufN $bufhist($bcrn); set bufCR $bcrn } }
            }

            set xs {}; set ys {}
            foreach crn $occupied { lassign [gtpb::cr_xy $crn] x y; lappend xs $x; lappend ys $y }
            set minX [gtpb::minl $xs]; set maxX [gtpb::maxl $xs]
            set minY [gtpb::minl $ys]; set maxY [gtpb::maxl $ys]
            set loadSpan [expr {($maxX-$minX)+($maxY-$minY)}]
            set refSLR [expr {$ownGTslr ne "" ? $ownGTslr : $domSLR}]
            set farGTs {}
            foreach gp $gtpos {
                lassign $gp gn gx gy gs
                if {$gn eq $gt} { continue }
                set candSide [expr {$gx < $midX ? "L" : "R"}]
                if {$gs ne $refSLR || $candSide ne $ownSide} { lappend farGTs $gn }
            }
            set conflict [expr {[llength $farGTs] > 0}]
            if {$stype eq "data"} { set conflict 0 }   ;# direct GT interface -> always compact near GT

            if {!$force && $conflict} {
                set ownClks [gtpb::pin_clocks $pin]
                foreach fg $farGTs {
                    set hit 0
                    if {$verify eq "timing" && [llength $ownClks]} {
                        set hit [gtpb::shares_timing $ownClks $fg tpair]
                    } else {
                        gtpb::build_cone $fg $trace_depth $trace_fanout
                        set hit [gtpb::loads_hit_cone $loads $fg]
                    }
                    if {$hit} {
                        set entangled($gt) 1; set entangled($fg) 1
                        lappend entWithSet($gt) $fg; lappend entWithSet($fg) $gt
                    }
                }
            }
            set fgcoords {}
            foreach fg $farGTs { if {[info exists gtCoord($fg)]} { lappend fgcoords $gtCoord($fg) } }

            lappend clocks [dict create gt $gt clkpin $clkpin gtX $gtX gtY $gtY \
                gtCRname [expr {[llength $gtcr] ? [gtpb::cr_name $gtcr] : {}}] gtSLR $ownGTslr \
                loads $loads nloads $nloads nff $nff spread $spread nslr $nslr capped $capped \
                minX $minX maxX $maxX minY $minY maxY $maxY loadSpan $loadSpan \
                domCR $domCR domSLR $domSLR bufCR $bufCR farGTs $farGTs fgcoords $fgcoords conflict $conflict]
        }
    }

    # ---- Pass 1.5c (V6 FIX): sibling-emit override -----------------------------
    # Root cause (confirmed on advantest_vp1802_0805): several GT lanes share the
    # EXACT same hierarchy pattern, differing only by a lane/channel index (e.g.
    # phypcs_top/DN_PHY_PIE8[0].../CH0_TXOUTCLK vs .../DN_PHY_PIE8[2].../CH0_TXOUTCLK
    # -- same quad, same clkpin name, structurally identical hardware). On that
    # design, 5 of 8 such sibling lanes genuinely violate (require_violating
    # passes, full ~41K-cell pblock emitted) while 3 are themselves structurally
    # healthy (NOOP, correctly -- no violation on that lane alone) and fall back
    # to a near-empty ~512-cell DATA_IF pblock instead. The historical hand-built
    # recipe (gtpblock_shared, pre-dates this skill) pblocked all 8 lanes
    # uniformly and measurably outperformed this skill's asymmetric result
    # (-0.082ns historical vs -0.328 to -0.664ns V4/V5-alone, and far worse once
    # RQS is stacked on the asymmetric layout -- confirmed root-caused to the 3
    # "free" lanes' ~41K cells each disrupting the floorplan regularity the other
    # 5 pinned lanes rely on). This pass promotes those healthy-but-structurally-
    # sibling candidates into $clocks using their OWN already-correctly-traced
    # load cells (gtpb::leaf_clock_loads already proved byte-identical to the
    # historical script's hard-coded cell counts for this exact case) -- no
    # re-tracing needed, unlike V5 FIX E which has to trace clk2 from scratch.
    # Every existing Pass 2 guard (dominance %, FIX A/C/G absolute+cumulative
    # cap, disturbance heuristic) still applies in full to the promoted entry;
    # only the require_violating gate on THIS clock individually is bypassed,
    # justified by the confirmed sibling's own violation.
    proc gtpb::sibling_key {gt clkpin} {
        return "[regsub -all {\[\d+\]} $gt {[*]}]|$clkpin"
    }
    array set _v6_emitted_keys {}
    foreach c $clocks {
        set _v6_emitted_keys([gtpb::sibling_key [dict get $c gt] [dict get $c clkpin]]) 1
    }
    set _v6_new_entries {}
    foreach cand $sibling_noop_candidates {
        set gt [dict get $cand gt]; set clkpin [dict get $cand clkpin]
        set _skey [gtpb::sibling_key $gt $clkpin]
        if {![info exists _v6_emitted_keys($_skey)]} { continue }  ;# no violating sibling -> leave as NOOP
        set gtX [dict get $cand gtX]; set gtY [dict get $cand gtY]
        set gtcr [dict get $cand gtcr]; set ownGTslr [dict get $cand ownGTslr]
        set ownSide [dict get $cand ownSide]; set stype [dict get $cand stype]
        set loads [dict get $cand loads]; set pin [dict get $cand pin]
        set capped [dict get $cand capped]; set passthrough [dict get $cand passthrough]
        set nloads [llength $loads]

        set ffcells [expr {[llength $loads] ? [filter $loads {REF_NAME =~ FD*}] : {}}]
        set nff [llength $ffcells]
        if {$nff == 0} { set nff $nloads; set ffcells $loads }
        set sample $ffcells
        if {$hist_sample > 0 && [llength $sample] > $hist_sample} { set sample [lrange $sample 0 [expr {$hist_sample-1}]] }
        array unset histff6; array unset slrhist6
        foreach c6 $sample {
            set cr6 [get_clock_regions -quiet -of_objects $c6]
            if {![llength $cr6]} { continue }
            set crn6 [gtpb::cr_name $cr6]
            if {[info exists histff6($crn6)]} { incr histff6($crn6) } else { set histff6($crn6) 1 }
            set slr6 [gtpb::cr_slr $crn6]
            if {$slr6 ne ""} { if {[info exists slrhist6($slr6)]} { incr slrhist6($slr6) } else { set slrhist6($slr6) 1 } }
        }
        set occupied6 [array names histff6]
        set spread6 [llength $occupied6]
        if {$spread6 == 0} {
            puts "  V6 sibling-promote SKIP: $gt / $clkpin -- loads have no placed clock region"
            continue
        }
        set nslr6 [llength [array names slrhist6]]
        set domCR6 ""; set domN6 -1
        foreach crn6 $occupied6 { if {$histff6($crn6) > $domN6} { set domN6 $histff6($crn6); set domCR6 $crn6 } }
        set domSLR6 ""; set domSN6 -1
        foreach s6 [array names slrhist6] { if {$slrhist6($s6) > $domSN6} { set domSN6 $slrhist6($s6); set domSLR6 $s6 } }
        set bufCR6 ""
        if {[llength $passthrough]} {
            array unset bufhist6
            foreach bc6 $passthrough {
                if {[catch {get_clock_regions -quiet -of_objects $bc6} bcr6] || ![llength $bcr6]} { continue }
                set bcrn6 [gtpb::cr_name $bcr6]
                if {[info exists bufhist6($bcrn6)]} { incr bufhist6($bcrn6) } else { set bufhist6($bcrn6) 1 }
            }
            set bufN6 -1
            foreach bcrn6 [array names bufhist6] { if {$bufhist6($bcrn6) > $bufN6} { set bufN6 $bufhist6($bcrn6); set bufCR6 $bcrn6 } }
        }
        set xs6 {}; set ys6 {}
        foreach crn6 $occupied6 { lassign [gtpb::cr_xy $crn6] x6 y6; lappend xs6 $x6; lappend ys6 $y6 }
        set minX6 [gtpb::minl $xs6]; set maxX6 [gtpb::maxl $xs6]
        set minY6 [gtpb::minl $ys6]; set maxY6 [gtpb::maxl $ys6]
        set loadSpan6 [expr {($maxX6-$minX6)+($maxY6-$minY6)}]
        set gtCRname6 [expr {[llength $gtcr] ? [gtpb::cr_name $gtcr] : {}}]

        puts "  V6 sibling-promote: $gt / $clkpin included -- structural sibling of an already-violating/emitted clock (key: $_skey), [llength $loads] loads, promoted using its own already-traced cells"

        lappend _v6_new_entries [dict create gt $gt clkpin $clkpin gtX $gtX gtY $gtY \
            gtCRname $gtCRname6 gtSLR $ownGTslr \
            loads $loads nloads $nloads nff $nff spread $spread6 nslr $nslr6 capped $capped \
            minX $minX6 maxX $maxX6 minY $minY6 maxY $maxY6 loadSpan $loadSpan6 \
            domCR $domCR6 domSLR $domSLR6 bufCR $bufCR6 farGTs {} fgcoords {} conflict 0 \
            v6sibling 1]
    }
    foreach e $_v6_new_entries { lappend clocks $e }

    # ---- Pass 1.5 (V5 FIX E): secondary-target clocks -------------------------
    # For every clock that passed require_violating (clk1), check CONFIRMED
    # real timing interaction against EVERY other GT's out-clock pins (clk2),
    # not just the geometrically-prefiltered "far" subset entanglement used.
    # A clk2 found here is included even though it never independently
    # violates -- see V5 FIX E header note for the full rationale and safety
    # guarantees (every existing gate still applies; only require_violating
    # itself is bypassed for clk2, justified by the confirmed partnership).
    array set _v5seen {}
    foreach c $clocks { set _v5seen("[dict get $c gt]|[dict get $c clkpin]") 1 }
    set _v5_new_entries {}
    foreach c $clocks {
        set gt1 [dict get $c gt]; set clkpin1 [dict get $c clkpin]
        if {$clkpin1 eq "DATA_IF"} { continue }  ;# no live clock domain to check interaction from
        set pin1 [get_pins -quiet -of_objects [get_cells -quiet $gt1] -filter "REF_PIN_NAME == $clkpin1"]
        if {$pin1 eq ""} { continue }
        set ownClks1 [gtpb::pin_clocks $pin1]
        if {![llength $ownClks1]} { continue }
        set slr1 [dict get $c gtSLR]; if {$slr1 eq ""} { set slr1 [dict get $c domSLR] }
        foreach gp $gtpos {
            lassign $gp gt2 gx2 gy2 gslr2
            if {$gt2 eq $gt1} { continue }
            if {![gtpb::shares_timing $ownClks1 $gt2 tpair]} { continue }
            # FIX H (inter-SLR entanglement gate): GT1 is violating and GT2
            # shares a timing path with it, but if GT2 sits in a DIFFERENT SLR
            # than GT1, compacting each one's loads tightly near its own
            # location does not address the fact that their shared path still
            # has to cross from GT1's SLR to GT2's -- it just makes both ends
            # more rigid/localized, which can make that specific SLR-crossing
            # path HARDER to route/legalize (more congestion/delay there, not
            # less). Skip pulling in GT2 entirely for this cross-SLR entangled
            # relationship; GT1 itself may still qualify independently on its
            # own merits elsewhere in this pass.
            if {$slr1 ne "" && $gslr2 ne "" && $slr1 ne $gslr2} {
                puts "  V5 secondary-target SKIP (FIX H, inter-SLR entanglement gate): $gt2 (entangled with violating $gt1/$clkpin1) sits in $gslr2, different from $gt1's own $slr1 -- compacting both ends near their own SLR would not resolve the shared cross-SLR path and risks making it worse. Not providing a suggestion for this entangled pairing."
                continue
            }
            foreach pin2 [get_pins -quiet -of_objects [get_cells -quiet $gt2] -filter {DIRECTION==OUT && (NAME =~ *TXOUTCLK* || NAME =~ *RXOUTCLK*)}] {
                set clkpin2 [get_property -quiet REF_PIN_NAME $pin2]
                if {$clkpin2 eq ""} { set clkpin2 [lindex [split $pin2 /] end] }
                set _key "$gt2|$clkpin2"
                if {[info exists _v5seen($_key)]} { continue }
                set _v5seen($_key) 1

                set _perGtMax2 [expr {$gslr2 ne "" ? min($max_loads, int(ceil([gtpb::slr_ff_cap $gslr2]*$slr_dominance_frac))+1) : $max_loads}]
                lassign [gtpb::leaf_clock_loads $pin2 $_perGtMax2] loads2 capped2 passthrough2 disqualified2 disqual_reason2
                if {$disqualified2} {
                    puts "  V5 secondary-target SKIP: $gt2 / $clkpin2 (entangled with violating $gt1/$clkpin1) -- $disqual_reason2"
                    continue
                }
                if {$exclude_fixed && [llength $loads2]} { set loads2 [filter $loads2 {!IS_LOC_FIXED}] }
                if {[llength $loads2]} {
                    set _f2 {}
                    foreach cc $loads2 { if {![gtpb::is_dedicated_cell $cc]} { lappend _f2 $cc } }
                    set loads2 $_f2
                }
                if {[gtpb::any_hd_reconfigurable [concat [list $gt2] $passthrough2 $loads2]]} {
                    puts "  V5 secondary-target SKIP: $gt2 / $clkpin2 (entangled with violating $gt1/$clkpin1) -- DFX cell detected"
                    continue
                }
                if {[llength $loads2] < $min_loads} {
                    puts "  V5 secondary-target SKIP: $gt2 / $clkpin2 (entangled with violating $gt1/$clkpin1) -- only [llength $loads2] loads (< -min_loads), not worth a suggestion"
                    continue
                }
                if {$capped2} {
                    puts "  V5 secondary-target SKIP: $gt2 / $clkpin2 (entangled with violating $gt1/$clkpin1) -- trace hit its dominance ceiling, true fanout too large to safely size"
                    continue
                }

                set ffcells2 [filter $loads2 {REF_NAME =~ FD*}]
                set nff2 [llength $ffcells2]
                if {$nff2 == 0} { set nff2 [llength $loads2]; set ffcells2 $loads2 }
                set sample2 $ffcells2
                if {$hist_sample > 0 && [llength $sample2] > $hist_sample} { set sample2 [lrange $sample2 0 [expr {$hist_sample-1}]] }
                array unset histff2; array unset slrhist2
                foreach cc $sample2 {
                    set cr2 [get_clock_regions -quiet -of_objects $cc]
                    if {![llength $cr2]} { continue }
                    set crn2 [gtpb::cr_name $cr2]
                    if {[info exists histff2($crn2)]} { incr histff2($crn2) } else { set histff2($crn2) 1 }
                    set slrx [gtpb::cr_slr $crn2]
                    if {$slrx ne ""} { if {[info exists slrhist2($slrx)]} { incr slrhist2($slrx) } else { set slrhist2($slrx) 1 } }
                }
                set occupied2 [array names histff2]
                set spread2 [llength $occupied2]
                if {$spread2 == 0} {
                    puts "  V5 secondary-target SKIP: $gt2 / $clkpin2 -- loads have no placed clock region"
                    continue
                }
                set nslr2 [llength [array names slrhist2]]
                set domCR2 ""; set domN2 -1
                foreach crn2 $occupied2 { if {$histff2($crn2) > $domN2} { set domN2 $histff2($crn2); set domCR2 $crn2 } }
                set domSLR2 ""; set domSN2 -1
                foreach s2 [array names slrhist2] { if {$slrhist2($s2) > $domSN2} { set domSN2 $slrhist2($s2); set domSLR2 $s2 } }
                set xs2 {}; set ys2 {}
                foreach crn2 $occupied2 { lassign [gtpb::cr_xy $crn2] xx yy; lappend xs2 $xx; lappend ys2 $yy }
                set minX2 [gtpb::minl $xs2]; set maxX2 [gtpb::maxl $xs2]
                set minY2 [gtpb::minl $ys2]; set maxY2 [gtpb::maxl $ys2]
                set loadSpan2 [expr {($maxX2-$minX2)+($maxY2-$minY2)}]
                set gtcr2 [get_clock_regions -quiet -of_objects $gt2]
                set gtCRname2 [expr {[llength $gtcr2] ? [gtpb::cr_name $gtcr2] : {}}]
                set tslr2 [expr {$gslr2 ne "" ? $gslr2 : $domSLR2}]
                set sameSLR [expr {$slr1 ne "" && $tslr2 ne "" && $slr1 eq $tslr2}]

                puts "  V5 secondary-target: $gt2 / $clkpin2 included -- confirmed real timing shared with violating $gt1/$clkpin1 ([llength $loads2] loads, [expr {$sameSLR ? {same SLR -> compact-growth path} : {different SLR ($tslr2 vs $slr1) -> whole-SLR path}}])"

                if {!$sameSLR && $tslr2 ne ""} {
                    set entangled($gt2) 1
                    lappend entWithSet($gt2) $gt1
                }

                lappend _v5_new_entries [dict create gt $gt2 clkpin $clkpin2 gtX $gx2 gtY $gy2 \
                    gtCRname $gtCRname2 gtSLR $gslr2 \
                    loads $loads2 nloads [llength $loads2] nff $nff2 spread $spread2 nslr $nslr2 capped $capped2 \
                    minX $minX2 maxX $maxX2 minY $minY2 maxY $maxY2 loadSpan $loadSpan2 \
                    domCR $domCR2 domSLR $domSLR2 bufCR "" farGTs {} fgcoords {} conflict 0 \
                    v5secondary 1 v5partner "$gt1/$clkpin1"]
            }
        }
    }
    foreach e $_v5_new_entries { lappend clocks $e }
    if {[llength $_v5_new_entries]} {
        puts "V5 FIX E: added [llength $_v5_new_entries] secondary-target clock(s) via confirmed cross-clock interaction with an already-violating clock"
    }

    # ---- Pass 2: emit pblocks (horizontal <=max_hcr CR, else whole-SLR);
    #      entangled clusters get a whole-SLR pblock instead of being skipped ----
    array set committed {}
    array set seenGT {}
    set records {}; set xdcbody {}; set nemit 0
    # resource-capacity double-claim fix: compact pblocks are queued here
    # instead of emitted immediately, so the decision of whether a compact
    # candidate's region ends up inside an SLR that LATER (or EARLIER, in
    # processing order) also gets its own whole-SLR pblock can be resolved
    # once, after every GT has been processed -- order-independent. Resolved
    # in the "resolve deferred compact-pblock decisions" block below.
    set compactQueue {}

    set hcr [expr {$max_cr > 0 ? $max_cr : $max_hcr}]

    array set slrLoad {}
    foreach c $clocks {
        set tslr [dict get $c gtSLR]; if {$tslr eq ""} { set tslr [dict get $c domSLR] }
        if {$tslr eq ""} { continue }
        if {[info exists slrLoad($tslr)]} { incr slrLoad($tslr) [dict get $c nff] } else { set slrLoad($tslr) [dict get $c nff] }
    }
    array set slrOver {}
    foreach s [array names slrLoad] {
        set cap [gtpb::slr_ff_cap $s]
        set lim [expr {int($cap*$slr_load_frac)}]
        set slrOver($s) [expr {$cap > 0 && $slrLoad($s) > $lim}]
        if {$slrOver($s)} {
            puts "SLR $s : combined GT loads=$slrLoad($s) FF > [expr {int($slr_load_frac*100)}]% of SLR flop cap ($cap, limit=$lim) -> NO pblock suggestions for this SLR"
        }
    }

    array set slrGrpCells {}
    array set slrGrpGts {}
    array set slrGrpNff {}
    array set slrGrpRes {}      ;# V3 FIX 1: key "$slr,$restype" -> cumulative
                                 ;# BRAM/URAM/DSP committed to whole-SLR pblocks
    array set committedRes {}   ;# V3 FIX 1: key "$crn,$restype" -> cumulative
                                 ;# BRAM/URAM/DSP committed to compact pblocks

    foreach c $clocks {
        set gt [dict get $c gt]; set clkpin [dict get $c clkpin]
        set nff [dict get $c nff]; set spread [dict get $c spread]; set nslr [dict get $c nslr]
        set loads [dict get $c loads]
        set tslr [dict get $c gtSLR]; if {$tslr eq ""} { set tslr [dict get $c domSLR] }
        # V5 FIX E: tag secondary-target clocks (included via confirmed
        # interaction with an already-violating clock, not independently
        # violating) directly in the displayed clock name, so they are
        # always distinguishable in gt_clock_analysis.rpt / the HTML report.
        set _v5tag [expr {[dict exists $c v5secondary] && [dict get $c v5secondary] ? " \[V5-secondary, entangled with [dict get $c v5partner]\]" : ""}]
        # V6 FIX: tag sibling-promoted clocks (healthy on their own, promoted
        # because a structural sibling GT genuinely violates) the same way.
        set _v6tag [expr {[dict exists $c v6sibling] && [dict get $c v6sibling] ? " \[V6-sibling-promoted\]" : ""}]
        set base [dict create gt $gt clk "$clkpin$_v5tag$_v6tag" gx [dict get $c gtX] gy [dict get $c gtY] \
            gslr [dict get $c domSLR] nff $nff spread $spread nslr $nslr \
            lminx [dict get $c minX] lmaxx [dict get $c maxX] lminy [dict get $c minY] lmaxy [dict get $c maxY]]

        # --- guard: SLR combined GT-load over -slr_load_frac -> skip that SLR ---
        if {[info exists slrOver($tslr)] && $slrOver($tslr)} {
            set why "SKIP: SLR $tslr combined GT-load ($slrLoad($tslr) FF) > [expr {int($slr_load_frac*100)}]% of SLR flop cap ([gtpb::slr_ff_cap $tslr]) - design GT loads too high, no pblock provided"
            puts "GT $gt / $clkpin : $why"
            set seenGT($gt) 1
            lappend records [dict merge $base [dict create decision SKIP reason $why chosen {} fargtcoords {}]]
            continue
        }

        # --- entangled (multi-SLR shared connectivity) -> whole-SLR pblock -----
        if {!$force && [info exists entangled($gt)]} {
            set slr [expr {$tslr ne "" ? $tslr : [dict get $c domSLR]}]
            set partners [expr {[info exists entWithSet($gt)] ? [lsort -unique $entWithSet($gt)] : {}}]
            set pcoords {}
            foreach p $partners { if {[info exists gtCoord($p)]} { lappend pcoords $gtCoord($p) } }
            if {$slr eq ""} {
                set why "SKIP: entangled but GT SLR unknown (unplaced?)"
                puts "GT $gt / $clkpin : $why"
                set seenGT($gt) 1
                lappend records [dict merge $base [dict create decision SKIP reason $why chosen {} fargtcoords $pcoords]]
                continue
            }
            # Entangled -> the fallback action is ALWAYS a whole-SLR pblock (this
            # is what shares datapath connectivity requires). We build the full
            # candidate first, then VALIDATE it against the cumulative dominance
            # budget for this SLR (this clock's own load PLUS whatever other GT
            # clocks are already queued for whole-SLR treatment in the same SLR
            # this run) before committing. Two outcomes only:
            #   - fits the budget  -> EMIT the complete whole-SLR pblock in full.
            #   - exceeds it -> SKIP this clock's pblock ENTIRELY (no partial /
            #     cropped pblock), logged clearly in the report and HTML.
            set _priorNff [expr {[info exists slrGrpNff($slr)] ? $slrGrpNff($slr) : 0}]
            set _priorResDict [dict create]
            foreach rt $gtpb::RESKINDS {
                set _k "$slr,$rt"
                dict set _priorResDict $rt [expr {[info exists slrGrpRes($_k)] ? $slrGrpRes($_k) : 0}]
            }
            set ev [gtpb::eval_slr_dominance $slr $nff $loads $slr_dominance_frac $_priorNff $_priorResDict]
            set slrcap [dict get $ev slrcap]; set domfrac [dict get $ev domfracFF]
            set resline [gtpb::dominance_resline $ev]
            if {![dict get $ev ok]} {
                set why [format "Attempted whole-SLR pblock for %s in %s (entangled with %d far GT(s): %s): this clock (nff=%d) combined with %d FF already queued for whole-SLR pblocks in %s this run -> %.1f%% of its total flop capacity (%d). This EXCEEDS the -slr_dominance_frac limit (%.0f%%)%s -- SKIPPING this clock's pblock ENTIRELY (no partial/cropped pblock is applied; none of its %d cells are added to any pblock). Likely this design's own majority/system clock, or combined with other already-queued GT clocks it now dominates this SLR." $clkpin $slr [llength $partners] [join $partners {, }] $nff $_priorNff $slr [expr {$domfrac*100}] $slrcap [expr {$slr_dominance_frac*100}] $resline $nff]
                puts "GT $gt / $clkpin : SKIP (exceeds SLR dominance limit): $why"
                set seenGT($gt) 1
                lappend records [dict merge $base [dict create decision SKIP reason $why chosen {} fargtcoords $pcoords]]
                continue
            }
            # V4: passed the %-based dominance check -- now also run the V4
            # FIX A (absolute cap) / FIX C (disturbance heuristic) guard
            # before committing to EMIT.
            set _guard [gtpb::guard_whole_slr_emit $slr $loads $_priorNff]
            if {![dict get $_guard ok]} {
                set why "Attempted whole-SLR pblock for $clkpin in $slr (entangled with [llength $partners] far GT(s): [join $partners {, }]): passed the %-based dominance check, but failed a V4 guard -- [dict get $_guard reason]"
                puts "GT $gt / $clkpin : SKIP (V4 guard): $why"
                set seenGT($gt) 1
                lappend records [dict merge $base [dict create decision SKIP reason $why chosen {} fargtcoords $pcoords]]
                continue
            }
            foreach cc $loads { lappend slrGrpCells($slr) $cc }
            lappend slrGrpGts($slr) "$gt/$clkpin"
            set slrGrpNff($slr) [dict get $ev candNff]
            foreach ri [dict get $ev resInfo] { set _rk "$slr,[dict get $ri restype]"; set slrGrpRes($_rk) [dict get $ri cand] }
            set slrRegs [gtpb::slr_regions $slr]
            set why [format "Attempted whole-SLR pblock for %s in %s (entangled with %d far GT(s): %s): combined with %d FF already queued = %.1f%% of SLR capacity (%d), within the %.0f%% dominance limit%s -> EMITTED in full (all %d cells included)." $clkpin $slr [llength $partners] [join $partners {, }] $_priorNff [expr {$domfrac*100}] $slrcap [expr {$slr_dominance_frac*100}] $resline $nff]
            puts "GT $gt / $clkpin : EMIT (whole-SLR, entangled): $why"
            set seenGT($gt) 1
            lappend records [dict merge $base [dict create decision EMIT reason $why gslr $slr chosen $slrRegs fargtcoords $pcoords]]
            continue
        }

        # --- clean GT: grow horizontally up to $hcr CR; overflow -> whole SLR --
        set domCR [dict get $c domCR]; set domSLR [dict get $c domSLR]
        set gtCRname [dict get $c gtCRname]
        set bufCR [expr {[dict exists $c bufCR] ? [dict get $c bufCR] : ""}]
        if {$bufCR ne ""} {
            # V4 FIX D: a dedicated distribution buffer (BUFG_GT/BUFGCE/MMCM/
            # PLL/...) sits in this clock's path -- its own site is the real
            # physical fanout point, preferred over both the GT pin and the
            # load centroid (see V4 FIX D note above where $bufCR is derived).
            set anchorCR $bufCR
        } elseif {$anchor eq "dominant" || $gtCRname eq ""} {
            set anchorCR $domCR
        } else {
            set anchorCR $gtCRname
        }
        lassign [gtpb::choose_regions $anchorCR $nff $headroom $hcr committed] chosen cap fits aslr

        # V3 FIX 1: the compact (<=$hcr CR) candidate is ALSO sized for BRAM/
        # URAM/DSP, not just FF. If the chosen CRs don't have enough free
        # (headroom-adjusted) sites for whatever this clock's loads actually
        # need, treat it exactly like an FF overflow -- $fits becomes 0 and
        # the existing whole-SLR fallback path below takes over.
        set _resReq {}
        if {$fits} {
            foreach rt $gtpb::RESKINDS {
                set req [gtpb::count_res_cells $loads $rt]
                if {$req == 0} { continue }
                set avail [gtpb::chosen_free_res $chosen $rt $headroom committedRes]
                lappend _resReq [list $rt $req $avail]
                if {$avail < $req} { set fits 0 }
            }
        }

        if {$fits} {
            set pb "pblock_gt_[string map {/ _ [ _ ] _ . _} $gt]_$clkpin"
            set resnote ""
            if {[llength $_resReq]} {
                set rp {}
                foreach e $_resReq { lassign $e rt req avail; lappend rp "$rt: $req req / $avail avail (OK)" }
                set resnote "  \[resource check: [join $rp {, }]\]"
            }
            set why "compact fits into [llength $chosen] CR (<=$hcr) in $aslr {[join $chosen { }]}$resnote"
            puts "GT $gt / $clkpin : loads=[dict get $c nloads] ff=$nff spread=$spread CR / $nslr SLR -> $why (queued -- final emission decided after all GTs processed)"
            set chosenCR {}
            foreach _cr $chosen { if {[string match CLOCKREGION_* $_cr]} { lappend chosenCR $_cr } else { lappend chosenCR CLOCKREGION_$_cr } }
            set seenGT($gt) 1
            # commit the resource usage against the chosen CRs (even split --
            # this skill doesn't model exact per-cell CR assignment for non-FF
            # resources, so distribute the requirement evenly like FF does by
            # weight during choose_regions). This bookkeeping stays immediate --
            # it only needs to be correct relative to OTHER compact candidates,
            # which is unaffected by whole-SLR timing.
            foreach e $_resReq {
                lassign $e rt req avail
                set _share [expr {int(ceil(double($req)/[llength $chosen]))}]
                foreach _cr $chosen {
                    set _k "$_cr,$rt"
                    set committedRes($_k) [expr {([info exists committedRes($_k)] ? $committedRes($_k) : 0) + $_share}]
                }
            }
            # resource-capacity double-claim fix: queue this compact decision
            # instead of emitting it now. Resolved after all GTs are processed,
            # once we know the FINAL set of SLRs that got their own whole-SLR
            # pblock -- if this candidate's SLR is among them, its cells get
            # merged into that existing pblock instead of a new overlapping one.
            lappend compactQueue [dict create pb $pb gt $gt clkpin $clkpin \
                chosenCR $chosenCR loads $loads clear_locs $clear_locs \
                base $base aslr $aslr chosen $chosen why $why]
        } else {
            # Horizontal clock-region budget exceeded -> the fallback action is
            # ALWAYS a whole-SLR pblock (never an immediate skip). We build the
            # full candidate first, exactly as V1 always did, then VALIDATE it
            # against the cumulative dominance budget for this SLR before
            # committing. Two outcomes only, never a middle ground:
            #   - fits the budget  -> EMIT the complete whole-SLR pblock, all
            #     of this clock's cells included, nothing cropped.
            #   - exceeds the budget -> SKIP this clock's pblock ENTIRELY (zero
            #     cells added anywhere for it) and log exactly why in both
            #     gt_clock_analysis.rpt and the HTML report. No partial/cropped
            #     pblock is ever emitted for a clock that fails this check.
            # V3 FIX 1: the compact candidate can fail for FF overflow, a
            # BRAM/URAM/DSP shortfall in the chosen CRs, or both -- describe
            # whichever actually happened instead of assuming FF overflow.
            set _compactFailReasons {}
            if {[llength $_resReq]} {
                foreach e $_resReq {
                    lassign $e rt req avail
                    if {$avail < $req} { lappend _compactFailReasons "$rt short ($req req / $avail avail in the $hcr-CR candidate)" }
                }
            }
            if {$nff > $cap} { lappend _compactFailReasons "FF overflow (ff~$nff > $cap available in the $hcr-CR candidate)" }
            set _compactFailWhy [expr {[llength $_compactFailReasons] ? [join $_compactFailReasons {; }] : "exceeds the $hcr-CR horizontal budget"}]
            set slr [expr {$tslr ne "" ? $tslr : $aslr}]
            set _priorNff [expr {[info exists slrGrpNff($slr)] ? $slrGrpNff($slr) : 0}]
            set _priorResDict [dict create]
            foreach rt $gtpb::RESKINDS {
                set _k "$slr,$rt"
                dict set _priorResDict $rt [expr {[info exists slrGrpRes($_k)] ? $slrGrpRes($_k) : 0}]
            }
            set ev [gtpb::eval_slr_dominance $slr $nff $loads $slr_dominance_frac $_priorNff $_priorResDict]
            set slrcap [dict get $ev slrcap]; set domfrac [dict get $ev domfracFF]
            set resline [gtpb::dominance_resline $ev]
            if {![dict get $ev ok]} {
                set why [format "Attempted whole-SLR pblock for %s in %s: compact (<=%d CR) candidate %s, so this clock would be combined with %d FF already queued for other whole-SLR pblocks in %s this run -> %.1f%% of its total flop capacity (%d). This EXCEEDS the -slr_dominance_frac limit (%.0f%%)%s -- SKIPPING this clock's pblock ENTIRELY (no partial/cropped pblock is applied; none of its %d cells are added to any pblock). Likely this design's own majority/system clock, or combined with other already-queued GT clocks it now dominates this SLR." $clkpin $slr $hcr $_compactFailWhy $_priorNff $slr [expr {$domfrac*100}] $slrcap [expr {$slr_dominance_frac*100}] $resline $nff]
                puts "GT $gt / $clkpin : SKIP (exceeds SLR dominance limit): $why"
                set seenGT($gt) 1
                lappend records [dict merge $base [dict create decision SKIP reason $why chosen {} fargtcoords {}]]
                continue
            }
            # V4: passed the %-based dominance check -- now also run the V4
            # FIX A (absolute cap) / FIX C (disturbance heuristic) guard
            # before committing to EMIT.
            set _guard [gtpb::guard_whole_slr_emit $slr $loads $_priorNff]
            if {![dict get $_guard ok]} {
                set why "Attempted whole-SLR pblock for $clkpin in $slr: compact (<=$hcr CR) candidate $_compactFailWhy, falls back to whole-SLR; passed the %-based dominance check, but failed a V4 guard -- [dict get $_guard reason]"
                puts "GT $gt / $clkpin : SKIP (V4 guard): $why"
                set seenGT($gt) 1
                lappend records [dict merge $base [dict create decision SKIP reason $why chosen {} fargtcoords {}]]
                continue
            }
            foreach cc $loads { lappend slrGrpCells($slr) $cc }
            lappend slrGrpGts($slr) "$gt/$clkpin"
            set slrGrpNff($slr) [dict get $ev candNff]
            foreach ri [dict get $ev resInfo] { set _rk "$slr,[dict get $ri restype]"; set slrGrpRes($_rk) [dict get $ri cand] }
            set slrRegs [gtpb::slr_regions $slr]
            set why [format "Attempted whole-SLR pblock for %s in %s: compact (<=%d CR) candidate %s -> falls back to whole-SLR pblock. Combined with %d FF already queued = %.1f%% of SLR capacity (%d), within the %.0f%% dominance limit%s -> EMITTED in full (all %d cells included)." $clkpin $slr $hcr $_compactFailWhy $_priorNff [expr {$domfrac*100}] $slrcap [expr {$slr_dominance_frac*100}] $resline $nff]
            puts "GT $gt / $clkpin : EMIT (whole-SLR fallback): $why"
            set seenGT($gt) 1
            lappend records [dict merge $base [dict create decision EMIT reason $why gslr $slr chosen $slrRegs fargtcoords {}]]
        }
    }

    # ---- emit ONE combined pblock per SLR that accrued SLR-level GT loads -----
    array set _slrDfxSkipped {}
    foreach slr [lsort [array names slrGrpCells]] {
        set cells [lsort -unique $slrGrpCells($slr)]
        set slrRegs [gtpb::slr_regions $slr]
        if {[gtpb::regions_hit_dfx_rm $slrRegs]} {
            puts "SLR $slr : SKIP (FIX J, DFX floorplan-overlap gate) -- this SLR's clock regions overlap an existing DFX RM pblock; combined pblock for [llength $slrGrpGts($slr)] GT clock(s) ([join $slrGrpGts($slr) {, }]) NOT emitted."
            set _slrDfxSkipped($slr) 1
            continue
        }
        set gnff [expr {[info exists slrGrpNff($slr)] ? $slrGrpNff($slr) : [llength $cells]}]
        lassign [gtpb::distribute_slr $slr $gnff $headroom committed] _pl _unpl
        if {$_unpl > 0} { puts "WARNING: SLR $slr : $_unpl FF of combined GT load do not fit under [expr {int($headroom*100)}]%/CR across the SLR" }
        set pb "pblock_gt_slr_[string map {/ _ [ _ ] _ . _ - _} $slr]"
        lappend xdcbody "# SLR-level pblock ($slr) for entangled/overflow GT clusters: [join $slrGrpGts($slr) {, }]"
        lappend xdcbody "if {\[llength \[get_pblocks -quiet $pb\]\]} { delete_pblock $pb }"
        lappend xdcbody "create_pblock $pb"
        lappend xdcbody "resize_pblock $pb -add {$slrRegs}"
        foreach _l [gtpb::emit_pblock_cells $pb $cells $clear_locs] { lappend xdcbody $_l }
        lappend xdcbody ""
        incr nemit
        puts "SLR $slr : combined SLR-level pblock $pb with [llength $cells] cells from [llength $slrGrpGts($slr)] GT clock(s)"
    }

    # ---- resolve deferred compact-pblock decisions (resource-capacity
    # double-claim fix) -- every whole-SLR pblock has now been finalized
    # above, so the set of SLRs with their own whole-SLR pblock is complete
    # and no longer depends on GT processing order. Any compact candidate
    # whose own SLR is in that set gets merged into the EXISTING whole-SLR
    # pblock (same name, reused) instead of emitting a separate, potentially
    # overlapping pblock; everything else emits its own standalone compact
    # pblock exactly as before. ----
    array set _slrHasWholePblock {}
    foreach _slr [array names slrGrpCells] { set _slrHasWholePblock($_slr) 1 }
    foreach _qe $compactQueue {
        set _qaslr [dict get $_qe aslr]
        set _qgt [dict get $_qe gt]; set _qclkpin [dict get $_qe clkpin]
        if {[gtpb::regions_hit_dfx_rm [dict get $_qe chosenCR]] || [info exists _slrDfxSkipped($_qaslr)]} {
            set _dfxWhy "compact candidate region(s) ([join [dict get $_qe chosen] { }]) overlap an existing DFX RM pblock (directly, or via its SLR's whole-SLR pblock also being DFX-skipped) -- no pblock suggestion for this clock."
            puts "GT $_qgt / $_qclkpin : SKIP (FIX J, DFX floorplan-overlap gate): $_dfxWhy"
            lappend records [dict merge [dict get $_qe base] [dict create decision SKIP reason $_dfxWhy chosen {} fargtcoords {}]]
            continue
        }
        if {[info exists _slrHasWholePblock($_qaslr)]} {
            set _existingPb "pblock_gt_slr_[string map {/ _ [ _ ] _ . _ - _} $_qaslr]"
            set _mergeWhy "compact candidate region(s) ([join [dict get $_qe chosen] { }]) fall inside $_qaslr, which already has its own whole-SLR pblock this run -- merged into $_existingPb instead of creating a separate (overlapping) pblock. [dict get $_qe why]"
            puts "GT $_qgt / $_qclkpin : EMIT (merged into existing whole-SLR pblock): $_mergeWhy"
            lappend xdcbody "# $_qgt/$_qclkpin : compact candidate merged into existing whole-SLR pblock $_existingPb (own region already claimed by $_qaslr's whole-SLR pblock)"
            foreach _l [gtpb::emit_pblock_cells $_existingPb [dict get $_qe loads] [dict get $_qe clear_locs]] { lappend xdcbody $_l }
            lappend xdcbody ""
            lappend records [dict merge [dict get $_qe base] [dict create decision EMIT reason $_mergeWhy gslr $_qaslr chosen [dict get $_qe chosen] fargtcoords {}]]
        } else {
            set _qpb [dict get $_qe pb]
            lappend xdcbody "if {\[llength \[get_pblocks -quiet $_qpb\]\]} { delete_pblock $_qpb }"
            lappend xdcbody "create_pblock $_qpb"
            lappend xdcbody "resize_pblock $_qpb -add {[dict get $_qe chosenCR]}"
            foreach _l [gtpb::emit_pblock_cells $_qpb [dict get $_qe loads] [dict get $_qe clear_locs]] { lappend xdcbody $_l }
            lappend xdcbody ""
            incr nemit
            lappend records [dict merge [dict get $_qe base] [dict create decision EMIT reason [dict get $_qe why] gslr $_qaslr chosen [dict get $_qe chosen] fargtcoords {}]]
        }
    }

    # ---- utilization guard: verify per-CR <= headroom(70%) and per-SLR <= 50% -
    set utilLines {}
    array set slrUse {}
    set crViol {}
    foreach crn [lsort [array names committed]] {
        set cap [gtpb::cr_ff_cap $crn]
        if {$cap <= 0} { continue }
        set used $committed($crn)
        set pct  [expr {100.0*$used/$cap}]
        set slr  [gtpb::cr_slr $crn]
        if {[info exists slrUse($slr)]} { incr slrUse($slr) $used } else { set slrUse($slr) $used }
        if {$pct > [expr {$headroom*100.0 + 0.5}]} {
            lappend crViol [format "%s=%.1f%%" $crn $pct]
            lappend utilLines [format "  CR  %-16s %7d / %-7d = %5.1f%%  OVER %d%%" $crn $used $cap $pct [expr {int($headroom*100)}]]
        } else {
            lappend utilLines [format "  CR  %-16s %7d / %-7d = %5.1f%%" $crn $used $cap $pct]
        }
    }
    set slrViol {}
    foreach s [lsort [array names slrUse]] {
        set cap [gtpb::slr_ff_cap $s]
        if {$cap <= 0} { continue }
        set pct [expr {100.0*$slrUse($s)/$cap}]
        set flag [expr {$pct > [expr {$slr_load_frac*100.0 + 0.5}] ? "  OVER [expr {int($slr_load_frac*100)}]%" : ""}]
        if {$flag ne ""} { lappend slrViol [format "%s=%.1f%%" $s $pct] }
        puts [format "SLR-util %s : GT loads=%d / cap=%d = %.1f%% (cap %d%%)%s" $s $slrUse($s) $cap $pct [expr {int($slr_load_frac*100)}] $flag]
    }
    if {[llength $crViol]} {
        puts "WARNING: clock region(s) over [expr {int($headroom*100)}]% combined GT load: [join $crViol {, }]"
    } else {
        puts "OK: every clock region stays within [expr {int($headroom*100)}]% combined GT load."
    }
    if {[llength $slrViol]} {
        puts "WARNING: SLR(s) over [expr {int($slr_load_frac*100)}]% combined GT load: [join $slrViol {, }]"
    }

    # V2.1 FIX (visibility): merge in every capped/low-fanout clkpin recorded during
    # pass 1 -- these used to vanish with no trace at all (the exact blind spot that
    # hid the Spirent_Ethernet regression). Now every clkpin examined gets a row.
    foreach r $lowfanout_records { lappend records $r }

    foreach gn $gts {
        if {[info exists seenGT($gn)]} { continue }
        set gx 0; set gy 0; set gs "-"
        if {[info exists gtCoord($gn)]} { lassign $gtCoord($gn) gx gy }
        lappend records [dict create gt $gn clk "-" gx $gx gy $gy gslr $gs decision NOLOAD \
            reason "no qualifying TXOUTCLK/RXOUTCLK loads (< -min_loads=$min_loads, or unplaced)" \
            nff 0 spread 0 nslr 0 lminx $gx lmaxx $gx lminy $gy lmaxy $gy chosen {} fargtcoords {}]
    }

    gtpb::write_report $reportfile $records
    if {[info exists utilLines]} {
        set _uh [open $reportfile a]
        puts $_uh ""
        puts $_uh "Utilization guard (per-CR cap [expr {int($headroom*100)}]% , per-SLR cap [expr {int($slr_load_frac*100)}]% , per-clock SLR-dominance cap [expr {int($slr_dominance_frac*100)}]%)"
        puts $_uh "----------------------------------------------------------------------------"
        foreach l $utilLines { puts $_uh $l }
        foreach s [lsort [array names slrUse]] {
            set cap [gtpb::slr_ff_cap $s]
            if {$cap <= 0} { continue }
            puts $_uh [format "  SLR %-14s %7d / %-7d = %5.1f%%" $s $slrUse($s) $cap [expr {100.0*$slrUse($s)/$cap}]]
        }
        close $_uh
    }
    puts "== wrote $reportfile =="
    if {$do_html} { gtpb::write_html $htmlfile $gmaxx $gmaxy $crlist $gtpos $records; puts "== wrote $htmlfile ([llength $records] records) ==" }
    if {$nemit > 0} {
        set xfd [open $xdcfile w]
        puts $xfd "# GT TXOUTCLK/RXOUTCLK load-compaction pblocks (overlap-aware, single-SLR, entanglement-consistent). V5."
        puts $xfd "# Apply after opt_design, before place_design."
        puts $xfd ""
        foreach l $xdcbody { puts $xfd $l }
        close $xfd
        puts "== wrote $xdcfile  ($nemit pblock group(s)) =="
    } else {
        puts "== no pblock suggestions from analysis - $xdcfile NOT generated =="
    }
}

# ---- auto-run on source (only inside Vivado with an open design) --------------
if {![info exists ::env(SKILL_NOAUTORUN)] && ![catch {current_design} _cd] && $_cd ne ""} {
    # Framework hand-off (PFR): never reuse a stale fix/flag from an earlier run in this folder.
    set _gtcl_fix  [file join $::gtcl_base "gt_clock_pblocks" "fix.tcl"]
    set _gtcl_flag [file join $::gtcl_base "gt_clock_pblocks_detected.flag"]
    file delete -force $_gtcl_fix $_gtcl_flag
    if {[catch {gt_clock_load_pblock} _gtcl_err]} {
        puts "\[DETECT\] WARNING: GT clock load analysis failed: $_gtcl_err"
    } elseif {[file exists $_gtcl_fix] && [file size $_gtcl_fix] > 0} {
        # <skill>_detected.flag tells the framework the skill fired; the fix itself is fix.tcl
        set _gtcl_fh [open $_gtcl_flag w]
        puts $_gtcl_fh "GT_CLOCK_LOAD_DETECTED"
        puts $_gtcl_fh "fix_tcl=$_gtcl_fix"
        puts $_gtcl_fh "constrains=pblocks:gt_clock_loads"
        puts $_gtcl_fh "compatible_with=all"
        close $_gtcl_fh
        puts "\[DETECT\] GT clock load pblock fix generated: $_gtcl_fix (flag: $_gtcl_flag)"
    } else {
        puts "\[DETECT\] No GT clock load pblock suggestions."
    }
} else {
    puts "gt_clock_load_pblock_v5.tcl sourced: proc defined, but NO open design was detected."
    puts "  -> Open a PLACED design in Vivado, then run:  gt_clock_load_pblock"
}
