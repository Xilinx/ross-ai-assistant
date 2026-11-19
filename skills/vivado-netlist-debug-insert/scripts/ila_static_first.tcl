# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# ila_static_first.tcl: route M1, "static ILA before the partition flag", for UltraScale+ DFX designs.
# Source ila_insert.tcl first, then this file. It adds procs to the same `ila` namespace and changes nothing in ila_insert.tcl.
#
#   ila::static_first_check_input ?-rp <cell>?   accept a static-only checkpoint from before partitioning, refuse a flagged one
#   ila::plan / ila::apply                       as in SKILL.md, on static nets only
#   ila::static_first_finish <outdir>            print the facts (what to save, what is not done). Writes no file, runs no step
#
# What the route does and does not do (the library says this in "ILA:" lines too):
#   - Static nets only. Nets inside the reconfigurable partition (RP) are not reachable by this route.
#   - No per-RP probes file is produced. Vivado names the RP in a separate partial file with no probes in it.
#   - Vivado adds the debug hub to the static part when the first core is inserted (an empty black box, 0 primitives) and builds it at
#     opt_design (measured: the hub cell is a black box before opt_design and built after it). This library does not create it.
# Why no opt_design here: with the RP still a black box, opt_design fails DRC INBB-3 (E4 run m1_bscan_rm_a_bscan).

namespace eval ila {
    # Names of a collection of cells ({} for an empty one). get_property NAME on the whole collection, never per element.
    proc static_first_names {objs} {
        if {[llength $objs] == 0} { return {} }
        return [get_property NAME $objs]
    }

    # Black box cells that could be the RP of a static-only checkpoint: not primitives, not a debug hub.
    proc static_first_candidates {} {
        set out {}
        foreach c [get_cells -quiet -hier -filter {IS_BLACKBOX == 1 && !IS_PRIMITIVE}] {
            if {[regexp {dbg_hub|xsdbm} [get_property REF_NAME $c]]} continue
            lappend out [get_property NAME $c]
        }
        return $out
    }

    proc static_first_check_input {args} {
        variable facts
        array set o {-rp {} -allow_state_reset {}}
        foreach {k v} $args {
            if {![info exists o($k)]} { return -code error "ILA_BAD_OPTION: static_first_check_input knows -rp and -allow_state_reset, not $k" }
            set o($k) $v
        }
        set flagged [static_first_names [get_cells -quiet -hier -filter {HD.RECONFIGURABLE == 1}]]
        set flagged_ok 0   ;# 2026.2 and newer, a flagged design with no debug yet: the flagged cells are the partitions, linked or not
        if {[llength $flagged] > 0 && [dfx_insert_proceeds]} {
            set flagged_ok 1
        } elseif {[llength $flagged] > 0} {
            if {[dfx_insert_supported]} {
                lassign [dfx_debug_infra] cores ports
                say "REFUSED: [join $flagged {, }] carries HD.RECONFIGURABLE=1 and the design already carries debug infrastructure (cores: [expr {[llength $cores] ? [join $cores {, }] : {none}}]; S_BSCAN ports on: [expr {[llength $ports] ? [join $ports {, }] : {none}}]). This is the skill's refusal, not Vivado's: in a measured end-to-end run on Vivado 2026.2 such designs failed after the insertion (grey child update_design -buffer_ports Common 17-39, or opt_design Opt 31-65)."
                say "REQUIRED CHANGE: give me the static checkpoint from before partitioning and before any debug port or core was added, or instantiate the ILA in the module source."
                return -code error "ILA_REFUSED_FLAGGED: [join $flagged {, }] is flagged HD.RECONFIGURABLE=1 and already carries debug ports or cores. This is the skill's refusal (measured: on Vivado 2026.2 the insertion then failed downstream). Give the static checkpoint from before partitioning and before any debug port or core."
            }
            say "REFUSED: [join $flagged {, }] carries HD.RECONFIGURABLE=1, so this design is already flagged as a DFX design. Vivado 2026.1 refuses create_debug_core in a flagged design for any net (Common 17-69), and this route cannot get around that."
            say "REQUIRED CHANGE: give me the static checkpoint from before partitioning: the static design synthesized with the partition as a black box and no HD.RECONFIGURABLE (not the checkpoint after the RM was linked). Vivado 2026.2 removes the refusal in its source (not run by this skill), so a flagged design may work there."
            return -code error "ILA_REFUSED_FLAGGED: [join $flagged {, }] already has HD.RECONFIGURABLE=1. Vivado 2026.1 refuses debug insertion in a flagged design (Common 17-69). Give the static checkpoint from before partitioning."
        }
        set arch [check_input {*}[expr {$o(-allow_state_reset) ne "" ? [list -allow_state_reset $o(-allow_state_reset)] : {}}]]
        if {$arch eq "versal"} {
            say "REFUSED: the static-first route is for UltraScale and UltraScale+ DFX designs. This design is Versal."
            return -code error "ILA_REFUSED_STATIC_FIRST_VERSAL: the static-first route is not for Versal"
        }
        if {$flagged_ok} { say [dfx_insert_note] }   ;# after check_input and the Versal refusal: the note is for a design this route accepts
        if {$o(-rp) ne ""} {
            set cands [static_first_names [get_cells -quiet $o(-rp)]]
            if {[llength $cands] == 0 || (![get_property IS_BLACKBOX [get_cells $o(-rp)]] && !($flagged_ok && [lsearch -exact $flagged [lindex $cands 0]] >= 0))} {
                say "REFUSED: -rp $o(-rp) is not a black box cell in this design."
                return -code error "ILA_REFUSED_NO_BLACKBOX: -rp $o(-rp) is not a black box cell"
            }
        } elseif {$flagged_ok} {
            set cands $flagged
        } else {
            set cands [static_first_candidates]
        }
        if {[llength $cands] == 0} {
            say "REFUSED: no black box cell marks where an RM will go. This is not a static-only checkpoint from before partitioning. For an ordinary design use the ordinary input check (AI only: ila::check_input)."
            return -code error "ILA_REFUSED_NO_BLACKBOX: no black box cell found"
        }
        foreach c $cands {
            set miss [dfx_bscan_missing $c]
            if {$flagged_ok && [lsearch -exact $flagged $c] >= 0} {
                # a flagged partition (2026.2 and newer): do not call it unflagged; say whether an RM is linked (a real run, e29 B8, had one: ref rp_cnt16)
                say "STATIC-FIRST: partition $c: flagged HD.RECONFIGURABLE=1, [expr {[get_property IS_BLACKBOX [get_cells $c]] ? {no RM linked (black box)} : {RM linked}}], ref=[get_property REF_NAME [get_cells $c]], S_BSCAN ports: [expr {12 - [llength $miss]}] of 12 (a static-only core needs no ports)"
            } else {
                say "STATIC-FIRST: black box cell $c: ref=[get_property REF_NAME [get_cells $c]], HD.RECONFIGURABLE not set, S_BSCAN ports: [expr {12 - [llength $miss]}] of 12 (a static-only core needs no ports)"
            }
        }
        dict set facts static_first 1
        # 1 only when every partition is flagged and has its RM linked: then the finish text has no link or flag step
        dict set facts static_first_flagged [expr {$flagged_ok && [llength [lmap c $cands {expr {[lsearch -exact $flagged $c] >= 0 && ![get_property IS_BLACKBOX [get_cells $c]] ? $c : [continue]}}]] == [llength $cands]}]
        dict set facts static_first_rps [join $cands " "]
        say "STATIC-FIRST LIMITS: static nets only. The nets inside [join $cands {, }] are not reachable by this route. No per-RP probes file is produced. Vivado adds the debug hub to the static part as an empty black box when the first core is inserted, and builds it at opt_design."
        return $arch
    }

    # Print the facts after ila::apply on a static-only checkpoint: what is in the open design, what to save, what is not done (link, flag,
    # opt_design, the probes file). Lists no build step. Writes no file and runs none of the steps (opt_design is not run, because DRC INBB-3 stops it while the partition is a black box). <outdir> is
    # only the folder named in the printed paths.
    proc static_first_finish {outdir} {
        variable facts
        if {![dict exists $facts static_first]} { return -code error "ILA_STATIC_FIRST_NOT_CHECKED: check the static-only input first (AI only: ila::static_first_check_input)" }
        set created [expr {[dict exists $facts created_cores] ? [dict get $facts created_cores] : ""}]
        if {[llength $created] == 0} { return -code error "ILA_NO_APPLY: plan and apply first (AI only: ila::plan, then ila::apply). Nothing to write." }
        foreach c $created {
            if {[llength [get_debug_cores -quiet $c]] == 0} { return -code error "ILA_STATIC_FIRST_CORE_MISSING: planned core $c is not in the design" }
        }
        # a flagged partition (2026.2 and newer) is not a black box that must be linked: its RM is in, or the flag is already set
        set flagged_run [expr {[dict exists $facts static_first_flagged] && [dict get $facts static_first_flagged]}]
        if {$flagged_run} {
            say "FINISHED: ILA cores [join $created {, }] are defined in the open design. Nothing was written: to keep them, save the open design as $outdir/static_first.dcp before the session closes (AI only: write_checkpoint -force $outdir/static_first.dcp). opt_design was NOT run."
            say "NOT DONE (yours, not run by this skill): the partition is already flagged and the RM is linked, so there is nothing to link or flag. The ILA and the hub stay in the static part. The probes file ($outdir/static_first.ltx) can be written only after opt_design; it lists the static nets only, and there is no per-RP probes file."
            say "STATIC-FIRST LIMITS: static nets only, the RM nets are not reachable by this route. No per-RP probes file. Vivado adds the debug hub to the static part as an empty black box when the first core is inserted, and builds it at opt_design."
            return $outdir/static_first.dcp
        }
        say "FINISHED: ILA cores [join $created {, }] are defined in the open design. Nothing was written: to keep them, save the open design as $outdir/static_first.dcp before the session closes (AI only: write_checkpoint -force $outdir/static_first.dcp). opt_design was NOT run: with the partition a black box it fails DRC INBB-3."
        say "NOT DONE (yours, not run by this skill): the RMs are not linked and the partition is not flagged, and opt_design needs both (AI only: read_checkpoint -cell <rp> <rm>.dcp -strict for each RM, then set HD.RECONFIGURABLE on the partition). The ILA and the hub stay in the static part. The probes file ($outdir/static_first.ltx) can be written only after opt_design; it lists the static nets only, and there is no per-RP probes file."
        say "STATIC-FIRST LIMITS: static nets only, the RM nets are not reachable by this route. No per-RP probes file. Vivado adds the debug hub to the static part as an empty black box when the first core is inserted, and builds it at opt_design."
        return $outdir/static_first.dcp
    }
}
