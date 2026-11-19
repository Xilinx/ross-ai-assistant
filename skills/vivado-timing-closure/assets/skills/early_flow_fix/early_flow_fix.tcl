# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# early_flow_fix.tcl — Universal Early Fixes for All Stage 1 Runs
# =============================================================================
# Sourced after open_checkpoint in all S1 runs. Contains structural fixes that
# are always safe to apply:
#   1. MBUFG_GROUP — Group BUFG_GTs sharing same driver
#   2. HARDSYNC CE — Fix BUFGCE CE_TYPE for same-MMCM-source paths
#   3. GT-MRMAC LOC — Constrain un-LOC'd MRMACs to GT's SLR
#   4. PCIE Clock Root — Optimal USER_CLOCK_ROOT for PCIE/QDMA IPs
#
# Results written to: $run_dir/early_fixes.json
# =============================================================================

puts "INFO: ========================================================"
puts "INFO: Running Early Flow Fixes..."
puts "INFO: ========================================================"

# Helper: check if a fix is enabled via env var (default=enabled)
proc _ef_enabled {name} {
    set key "EF_[string toupper $name]"
    if {[info exists ::env($key)]} { return [expr {$::env($key) ne "0"}] }
    return 1
}

# Initialize results
set _ef_results [dict create]

# =============================================================================
# 1. MBUFG_GROUP: Group BUFG_GTs sharing same driver pin
# =============================================================================
set _mbufg_groups 0
set _mbufg_nets 0
if {![_ef_enabled MBUFG_GROUP]} {
    puts "INFO: EARLY FIX: MBUFG_GROUP — disabled by config"
} else {
set _mbufg_groups 0
set _mbufg_nets 0
set _bufgts [get_cells -quiet -hier -filter {REF_NAME == BUFG_GT}]

if {[llength $_bufgts] > 0} {
    array unset _drv_groups
    array set _drv_groups {}

    foreach _bg $_bufgts {
        set _ipin [get_pins -quiet -of_objects $_bg -filter {REF_PIN_NAME == I}]
        if {$_ipin eq ""} { continue }
        set _inet [get_nets -quiet -of_objects $_ipin]
        if {$_inet eq ""} { continue }
        set _dpin [get_pins -quiet -leaf -of_objects $_inet -filter {DIRECTION == OUT}]
        if {$_dpin eq ""} { continue }
        set _dname [get_property NAME $_dpin]
        if {![info exists _drv_groups($_dname)]} {
            set _drv_groups($_dname) [list]
        }
        lappend _drv_groups($_dname) $_bg
    }

    foreach _drv [array names _drv_groups] {
        set _cells $_drv_groups($_drv)
        if {[llength $_cells] < 2} { continue }
        incr _mbufg_groups
        set _grp_name "MBUFG_GRP_${_mbufg_groups}"
        set _onets [list]
        foreach _bg $_cells {
            set _opin [get_pins -quiet -of_objects $_bg -filter {REF_PIN_NAME == O}]
            if {$_opin eq ""} { continue }
            set _onet [get_nets -quiet -of_objects $_opin]
            if {$_onet ne ""} { lappend _onets [get_property NAME $_onet] }
        }
        if {[llength $_onets] > 0} {
            set_property MBUFG_GROUP $_grp_name [get_nets $_onets]
            set _mbufg_nets [expr {$_mbufg_nets + [llength $_onets]}]
        }
    }
}

if {$_mbufg_groups > 0} {
    puts "INFO: ============ EARLY FIX APPLIED: MBUFG_GROUP ($_mbufg_groups groups, $_mbufg_nets nets) ============"
} else {
    puts "INFO: EARLY FIX: No MBUFG_GROUP needed"
}
}
dict set _ef_results mbufg_group applied [expr {$_mbufg_groups > 0}]
dict set _ef_results mbufg_group groups $_mbufg_groups
dict set _ef_results mbufg_group nets $_mbufg_nets

# =============================================================================
# 2. HARDSYNC CE: Fix BUFGCE CE_TYPE for same-MMCM-source paths
# =============================================================================
set _hardsync_count 0
if {![_ef_enabled HARDSYNC_CE]} {
    puts "INFO: EARLY FIX: HARDSYNC CE — disabled by config"
} else {
set _all_bufgce [get_cells -quiet -hier -filter {REF_NAME == BUFGCE}]

foreach _buf2 $_all_bufgce {
    set _ce_type [get_property -quiet CE_TYPE $_buf2]
    if {$_ce_type ne "SYNC"} { continue }

    set _ce_pin [get_pins -quiet -of_objects $_buf2 -filter {REF_PIN_NAME == CE}]
    if {$_ce_pin eq ""} { continue }
    set _ce_net [get_nets -quiet -of_objects $_ce_pin]
    if {$_ce_net eq ""} { continue }
    set _ce_driver [get_pins -quiet -leaf -of_objects $_ce_net -filter {DIRECTION == OUT}]
    if {$_ce_driver eq ""} { continue }

    set _ce_drv_cell [get_cells -quiet -of_objects $_ce_driver]
    if {$_ce_drv_cell eq ""} { continue }
    set _ce_drv_ref [get_property REF_NAME $_ce_drv_cell]
    if {![regexp {^FD} $_ce_drv_ref]} { continue }

    # Get Buffer1 (drives flop's clock)
    set _flop_c_pin [get_pins -quiet -of_objects $_ce_drv_cell -filter {REF_PIN_NAME == C}]
    if {$_flop_c_pin eq ""} { continue }
    set _flop_clk_net [get_nets -quiet -of_objects $_flop_c_pin]
    if {$_flop_clk_net eq ""} { continue }
    set _flop_clk_driver [get_pins -quiet -leaf -of_objects $_flop_clk_net -filter {DIRECTION == OUT}]
    if {$_flop_clk_driver eq ""} { continue }

    set _buf1 [get_cells -quiet -of_objects $_flop_clk_driver]
    if {$_buf1 eq ""} { continue }
    set _buf1_ref [get_property REF_NAME $_buf1]
    if {$_buf1_ref ne "BUFGCE"} { continue }

    # Compare MMCM sources
    set _buf1_ipin [get_pins -quiet -of_objects $_buf1 -filter {REF_PIN_NAME == I}]
    if {$_buf1_ipin eq ""} { continue }
    set _buf1_inet [get_nets -quiet -of_objects $_buf1_ipin]
    if {$_buf1_inet eq ""} { continue }
    set _mmcm_pin_a [get_pins -quiet -leaf -of_objects $_buf1_inet -filter {DIRECTION == OUT}]
    if {$_mmcm_pin_a eq ""} { continue }

    set _buf2_ipin [get_pins -quiet -of_objects $_buf2 -filter {REF_PIN_NAME == I}]
    if {$_buf2_ipin eq ""} { continue }
    set _buf2_inet [get_nets -quiet -of_objects $_buf2_ipin]
    if {$_buf2_inet eq ""} { continue }
    set _mmcm_pin_b [get_pins -quiet -leaf -of_objects $_buf2_inet -filter {DIRECTION == OUT}]
    if {$_mmcm_pin_b eq ""} { continue }

    if {[get_property NAME $_mmcm_pin_a] eq [get_property NAME $_mmcm_pin_b]} {
        set_property CE_TYPE HARDSYNC [get_cells $_buf2]
        incr _hardsync_count
    }
}

if {$_hardsync_count > 0} {
    puts "INFO: ============ EARLY FIX APPLIED: HARDSYNC CE ($_hardsync_count BUFGCEs) ============"
} else {
    puts "INFO: EARLY FIX: No HARDSYNC CE needed"
}
}
dict set _ef_results hardsync_ce applied [expr {$_hardsync_count > 0}]
dict set _ef_results hardsync_ce count $_hardsync_count

# =============================================================================
# 3. GT-MRMAC LOC: Constrain un-LOC'd MRMACs to GT's SLR
# =============================================================================
set _gtmr_moves 0
if {![_ef_enabled GT_MRMAC_LOC]} {
    puts "INFO: EARLY FIX: GT-MRMAC LOC — disabled by config"
} else {
# Wrapped in catch, matching every other early-fix block in this same
# file (PCIE clock root two blocks below, each Chaithanya skill further
# down) -- an uncaught error here used to abort not just this one fix but
# everything after it in this file: PCIE clock root, the Chaithanya
# skills loop, and the final _ef_results/early_fixes.json write would
# never run.
if {[catch {source [file join [file dirname [info script]] "gt_mrmac_early.tcl"]} _gtmr_err]} {
    puts "WARNING: GT-MRMAC LOC detection failed: $_gtmr_err"
}

if {$_gtmr_moves > 0} {
    puts "INFO: ============ EARLY FIX APPLIED: GT-MRMAC LOC ($_gtmr_moves MRMACs) ============"
} else {
    puts "INFO: EARLY FIX: No GT-MRMAC LOC needed"
}
}
dict set _ef_results gt_mrmac_loc applied [expr {$_gtmr_moves > 0}]
dict set _ef_results gt_mrmac_loc relocations $_gtmr_moves

# =============================================================================
# 5. PCIE Clock Root: Optimal USER_CLOCK_ROOT for PCIE/QDMA IPs
# =============================================================================
set _pcie_cr_moves 0
if {![_ef_enabled PCIE_CLOCK_ROOT]} {
    puts "INFO: EARLY FIX: PCIE Clock Root — disabled by config"
} else {
if {[catch {source [file join [file dirname [info script]] "pcie_clock_root.tcl"]} _pcr_err]} {
    puts "WARNING: PCIE clock root detection failed: $_pcr_err"
}

if {$_pcie_cr_moves > 0} {
    puts "INFO: ============ EARLY FIX APPLIED: PCIE CLOCK ROOT ($_pcie_cr_moves nets) ============"
} else {
    puts "INFO: EARLY FIX: No PCIE Clock Root needed"
}
}
dict set _ef_results pcie_clock_root applied [expr {$_pcie_cr_moves > 0}]
dict set _ef_results pcie_clock_root nets $_pcie_cr_moves

# =============================================================================
# 5. Macro Clustering Analysis
# =============================================================================
# Remove standalone Macro Clustering — now part of Structural Analysis loop below
# =============================================================================
dict set _ef_results macro_clustering applied 0

# =============================================================================
# 6. Structural Analysis — Early Resolution Analyses
# =============================================================================
set _er_total 0
set _er_applied 0
set _er_dir [file join [file dirname [file dirname [info script]]] "early_resolution"]
if {![_ef_enabled STRUCTURAL_ANALYSIS]} {
    puts "INFO: EARLY FIX: Structural Analysis — disabled by config"
} else {
set _er_skills {
    {macro_clustering_analysis "Macro Clustering Analysis"   source}
    {control_set_analysis      "Control Set Analysis"        source}
    {fanout_analysis           "Fanout Analysis (BCNA)"      source}
    {retiming_analysis         "Retiming Analysis"           source}
    {srl_timing_opt_analysis   "SRL Timing Opt Analysis"     source}
    {bram_uram_unpack_analysis "BRAM/URAM Unpack Analysis"   read_iphys_opt_tcl}
}

foreach _skill $_er_skills {
    set _folder [lindex $_skill 0]
    set _label [lindex $_skill 1]
    set _apply_method [lindex $_skill 2]
    set _script [file join $_er_dir $_folder "detect.tcl"]
    if {[file exists $_script]} {
        incr _er_total
        set _outdir [file join $run_dir $_folder]
        set env(OUTDIR) $_outdir
        set env(SRLB_OUTDIR) $_outdir
        file mkdir $_outdir
        if {[catch {source $_script} _er_err]} {
            puts "INFO: $_label: skipped ($_er_err)"
        } else {
            incr _er_applied
            set _fix [file join $run_dir $_folder "fix.tcl"]
            if {[file exists $_fix] && [file size $_fix] > 0} {
                if {$_apply_method eq "read_iphys_opt_tcl"} {
                    catch {read_iphys_opt_tcl $_fix}
                } else {
                    # Source line-by-line so one error doesn't skip the rest
                    set _fh [open $_fix r]
                    set _fix_ok 0
                    set _fix_err 0
                    while {[gets $_fh _line] >= 0} {
                        set _tl [string trim $_line]
                        if {$_tl eq "" || [string index $_tl 0] eq "#"} continue
                        if {[catch {uplevel #0 $_line} _lerr]} {
                            incr _fix_err
                        } else {
                            incr _fix_ok
                        }
                    }
                    close $_fh
                    if {$_fix_err > 0} {
                        puts "INFO: $_label: fix.tcl applied $_fix_ok cmds ($_fix_err skipped — cells not found)"
                    }
                }
            }
        }
    }
}
if {$_er_applied > 0} {
    puts "INFO: ============ EARLY FIX: Structural Analysis applied ($_er_applied/$_er_total) ============"
} else {
    puts "INFO: EARLY FIX: Structural Analysis — none applied"
}
}
dict set _ef_results structural_analysis applied [expr {$_er_applied > 0}]
dict set _ef_results structural_analysis count $_er_applied

# =============================================================================
# Hard-IP locality (upstream b0cfa7a7): opt-in, each on only when EF_<NAME> is 1.
# They are standalone pre-place advisors upstream; here they run like the
# structural analyses: detect.tcl writes <run_dir>/<skill>/fix.tcl, applied one
# caught command per line.
# =============================================================================
proc _ef_optin {name} {
    set key "EF_[string toupper $name]"
    return [expr {[info exists ::env($key)] && $::env($key) eq "1"}]
}
foreach _hip {
    {hardip_proximity_pblock     "Hard-IP Proximity Pblock"   HDPL}
    {prevent_hardip_slr_crossing "Hard-IP SLR Locality"       HIPL}
} {
    lassign $_hip _folder _label _pfx
    dict set _ef_results $_folder applied 0
    dict set _ef_results $_folder count 0
    if {![_ef_optin $_folder]} {
        continue
    }
    set _script [file join $_er_dir $_folder "detect.tcl"]
    if {![file exists $_script]} {
        puts "INFO: EARLY FIX: $_label -- detect.tcl not found"
        continue
    }
    set _outdir [file join $run_dir $_folder]
    file mkdir $_outdir
    set env(OUTDIR) $_outdir
    set env(${_pfx}_OUTDIR) $_outdir
    if {[catch {source $_script} _hip_err]} {
        puts "INFO: EARLY FIX: $_label -- skipped ($_hip_err)"
        continue
    }
    set _fix [file join $_outdir "fix.tcl"]
    set _fix_ok 0
    set _fix_err 0
    if {[file exists $_fix] && [file size $_fix] > 0} {
        set _fh [open $_fix r]
        while {[gets $_fh _line] >= 0} {
            set _tl [string trim $_line]
            if {$_tl eq "" || [string index $_tl 0] eq "#"} continue
            if {[catch {uplevel #0 $_line}]} { incr _fix_err } else { incr _fix_ok }
        }
        close $_fh
    }
    puts "INFO: EARLY FIX: $_label -- fix.tcl applied $_fix_ok cmds ($_fix_err skipped)"
    dict set _ef_results $_folder applied [expr {$_fix_ok > 0}]
    dict set _ef_results $_folder count $_fix_ok
}

# =============================================================================
# Write results to JSON
# =============================================================================
set _json_path [file join $run_dir "early_fixes.json"]
set _fh [open $_json_path w]
puts $_fh "\{"
set _first 1
foreach _key [dict keys $_ef_results] {
    if {!$_first} { puts $_fh "," }
    set _first 0
    set _val [dict get $_ef_results $_key]
    puts -nonewline $_fh "  \"$_key\": \{"
    set _first_inner 1
    foreach {_k _v} $_val {
        if {!$_first_inner} { puts -nonewline $_fh ", " }
        set _first_inner 0
        # Key off the FIELD NAME, not the value, to decide bool vs number --
        # every skill entry's "applied" field is always a genuine boolean
        # (built via [expr {... > 0}], so always literally 0 or 1), but
        # every other field (groups/nets/count/relocations) is a genuine
        # count that can just as legitimately BE 0 or 1 -- inferring from
        # the value alone used to serialize e.g. "nets": true for exactly
        # 1 net grouped, or "relocations": false for 0 relocations,
        # corrupting the schema for any consumer expecting a number there.
        if {$_k eq "applied"} {
            set _vout [expr {$_v ? "true" : "false"}]
            puts -nonewline $_fh "\"$_k\": $_vout"
        } else {
            puts -nonewline $_fh "\"$_k\": $_v"
        }
    }
    puts -nonewline $_fh "\}"
}
puts $_fh "\n\}"
close $_fh

puts "INFO: ========================================================"
puts "INFO: Early Flow Fixes complete. Results: $_json_path"
puts "INFO: ========================================================"
