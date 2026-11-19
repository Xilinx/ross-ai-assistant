# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# detect.tcl — Congestion/Control Set Remap Detection
# Runs control_set_analysis and generates CONTROL_SET_REMAP fix for the Stage 2 run
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set _csr_run_dir $env(RUN_DIR)
} else {
    set _csr_run_dir [pwd]
}

set _csr_script [file join [file dirname [file dirname [file dirname [info script]]]] "early_resolution" "control_set_analysis" "detect.tcl"]

if {![file exists $_csr_script]} {
    puts "INFO: Control Set Remap: detect.tcl not found, skipping"
    return
}

set _csr_outdir [file join $_csr_run_dir "control_set_analysis"]
set env(OUTDIR) $_csr_outdir
file mkdir $_csr_outdir

if {[catch {source $_csr_script} _csr_err]} {
    puts "INFO: Control Set Remap: detect skipped ($_csr_err)"
    return
}

set _csr_fix [file join $_csr_outdir "fix.tcl"]
if {[file exists $_csr_fix] && [file size $_csr_fix] > 0} {
    # Copy fix as the XDC for the Stage 2 run, one caught command per line.
    # Upstream (2026-10-08) dropped this copy and sources fix.tcl directly, so one
    # cell missing from the fix run's netlist fails the whole run; kept here --
    # the 2026.1 report's design A winner (PFR_Congestion_control_set_remap) came
    # from this caught form.
    set _combined [file join $_csr_run_dir "control_set_remap_fix.xdc"]
    set _fh [open $_combined w]
    puts $_fh "# Control Set Remap Fix — auto-generated"
    set _ifh [open $_csr_fix r]
    while {[gets $_ifh _line] >= 0} {
        set _tl [string trim $_line]
        if {$_tl eq "" || [string index $_tl 0] eq "#"} {
            puts $_fh $_line
            continue
        }
        puts $_fh "catch {$_line}"
    }
    close $_ifh
    close $_fh

    # Write detection flag
    set _flag [file join $_csr_run_dir "control_set_remap_detected.flag"]
    set _fh [open $_flag w]
    puts $_fh "CONTROL_SET_REMAP_DETECTED"
    close $_fh

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
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "Delegated control_set_analysis produced a non-empty fix.tcl ([file size $_csr_fix] bytes) -- see $_csr_outdir/result.json for the underlying fragmentation thresholds"
        set _rj_changes "Wrote control_set_remap_fix.xdc (control_set_analysis/fix.tcl, one caught command per line)"
        set _rj_json [_rj_obj [list \
            schema_version n 1 \
            skill          s "control_set_remap" \
            category       s "congestion" \
            detected       b 1 \
            generated_at   s $_rj_ts \
            source_outdir  s $_csr_outdir \
            trigger_condition s $_rj_cond \
            design_changes s $_rj_changes \
        ]]
        set _rj_fh [open [file join $_csr_run_dir "control_set_remap_result.json"] w]
        puts $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "INFO: Control Set Remap: could not write control_set_remap_result.json ($_rj_err)"
    }

    puts "INFO: ============ CONTROL SET REMAP: fix generated ============"
} else {
    foreach stale [list \
        [file join $_csr_run_dir "control_set_remap_fix.xdc"] \
        [file join $_csr_run_dir "control_set_remap_detected.flag"] \
        [file join $_csr_run_dir "control_set_remap_result.json"]] {
        if {[file exists $stale]} { file delete -force $stale }
    }
    puts "INFO: Control Set Remap: no fix generated"
}
