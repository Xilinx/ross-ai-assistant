# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# detect.tcl — Timing/Structural Analysis Detection
# Runs structural analysis skills and generates combined fix for the Stage 2 run
# Skills: macro_clustering, fanout, retiming, srl_timing_opt, bram_uram_unpack
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set _cs_run_dir $env(RUN_DIR)
} else {
    set _cs_run_dir [pwd]
}

set _er_base [file join [file dirname [file dirname [file dirname [info script]]]] "early_resolution"]

set _cs_skills {
    {macro_clustering_analysis "Macro Clustering"          source}
    {fanout_analysis           "Fanout Analysis (BCNA)"    source}
    {retiming_analysis         "Retiming Analysis"         source}
    {srl_timing_opt_analysis   "SRL Timing Opt"            source}
    {bram_uram_unpack_analysis "BRAM/URAM Unpack"          read_iphys_opt_tcl}
}

set _cs_total 0
set _cs_applied 0
set _cs_fix_files {}

foreach _skill $_cs_skills {
    set _folder [lindex $_skill 0]
    set _label [lindex $_skill 1]
    set _apply_method [lindex $_skill 2]
    set _script [file join $_er_base $_folder "detect.tcl"]
    if {![file exists $_script]} { continue }

    incr _cs_total
    set _outdir [file join $_cs_run_dir $_folder]
    set env(OUTDIR) $_outdir
    set env(SRLB_OUTDIR) $_outdir
    file mkdir $_outdir

    if {[catch {source $_script} _err]} {
        puts "INFO: Structural Analysis/$_label: detect skipped ($_err)"
        continue
    }

    set _fix [file join $_outdir "fix.tcl"]
    if {[file exists $_fix] && [file size $_fix] > 0} {
        incr _cs_applied
        lappend _cs_fix_files $_fix
        puts "INFO: Structural Analysis/$_label: fix.tcl generated ([file size $_fix] bytes)"
    } else {
        puts "INFO: Structural Analysis/$_label: no fix generated"
    }
}

# Write combined fix.xdc that sources all individual fix.tcl files
if {$_cs_applied > 0} {
    set _combined [file join $_cs_run_dir "structural_analysis_fix.xdc"]
    set _fh [open $_combined w]
    puts $_fh "# Combined Structural Analysis Fix — auto-generated"
    puts $_fh "# Sources individual fix.tcl files from each skill"
    foreach _f $_cs_fix_files {
        set _folder [file tail [file dirname $_f]]
        puts $_fh "# --- $_folder ---"
        # Read and inline each fix.tcl (line-by-line for error tolerance)
        set _ifh [open $_f r]
        while {[gets $_ifh _line] >= 0} {
            set _tl [string trim $_line]
            if {$_tl eq "" || [string index $_tl 0] eq "#"} {
                puts $_fh $_line
                continue
            }
            puts $_fh "catch {$_line}"
        }
        close $_ifh
    }
    close $_fh

    # Write detection flag
    set _flag [file join $_cs_run_dir "structural_analysis_detected.flag"]
    set _fh [open $_flag w]
    puts $_fh "CHAITHANYA_SKILLS_DETECTED"
    puts $_fh "skills_applied=$_cs_applied"
    puts $_fh "skills_total=$_cs_total"
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
        set _rj_fired {}
        set _rj_fired_plain {}
        foreach _rj_f $_cs_fix_files {
            set _rj_fname [file tail [file dirname $_rj_f]]
            lappend _rj_fired [_rj_str $_rj_fname]
            lappend _rj_fired_plain $_rj_fname
        }
        set _rj_counts [_rj_obj [list skills_applied n $_cs_applied skills_total n $_cs_total]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "$_cs_applied of $_cs_total delegated sub-skills produced a non-empty fix.tcl"
        set _rj_changes "Wrote structural_analysis_fix.xdc combining fix.tcl from: [join $_rj_fired_plain ", "]"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "structural_analysis" category s "timing" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts,\n  \"fired_skills\": \[[join $_rj_fired ", "]\]\n\}\n"
        set _rj_fh2 [open [file join $_cs_run_dir "structural_analysis_result.json"] w]
        puts -nonewline $_rj_fh2 $_rj_json
        close $_rj_fh2
    } _rj_err]} {
        puts "INFO: Structural Analysis: could not write structural_analysis_result.json ($_rj_err)"
    }

    puts "INFO: ============ STRUCTURAL ANALYSIS: $_cs_applied/$_cs_total detected, fix written ============"
} else {
    puts "INFO: STRUCTURAL ANALYSIS: $_cs_total ran, no fixes generated"
}
