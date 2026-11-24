# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_next_steps.tcl: guards and printed steps that stay shipped. Sourced by ila_insert.tcl.

namespace eval ila {
    # the preconditions of next_steps: a plan, and an apply that made cores.
    proc require_applied {what} {
        variable plan
        if {![dict exists $plan groups] || [llength [dict get $plan groups]] == 0} {
            return -code error "ILA_NO_PLAN: this step needs a plan and an apply made in this session, and there is no plan with cores. Plan and apply first (AI only: $what needs ila::plan, then ila::apply). Nothing was run and nothing was written."
        }
        if {[llength [created_list]] == 0} {
            return -code error "ILA_NOT_APPLIED: this step needs the cores the apply step makes, and this session has created none (apply has not run, or beginning again removed them). Apply first (AI only: $what needs ila::apply). Nothing was run and nothing was written."
        }
        require_last_apply_ok $what
    }


    # A failed apply leaves the cores it had made (the plan still lists all of them), and a finish on that design would write files for some of
    # the cores and a plan and facts for all of them.
    proc require_last_apply_ok {what} {
        variable apply_failed
        if {$apply_failed} {
            return -code error "ILA_APPLY_FAILED: the last apply failed, so the design holds some of the cores of the plan and not all of them. This step was not run and nothing was written. Remove the cores this session added and begin again, fix the cause (the error the apply printed), then plan and apply again (AI only: $what was not run; ila::reset, then ila::plan and ila::apply)."
        }
    }

    # ---- 6b. finish for a flat design in an open Vivado project (plan B item 5, e21, e43) ------------------------
    # The user's project run does its own opt_design, so this finish only saves the debug constraints (save_constraints), like the Versal DFX
    # finish. e21 (2026.1, flat KCU116 project): after save_constraints impl_1 is out of date and synth_1 is not. With ONE constraint file
    # (the target of the constraint set) save_constraints appends the debug lines to the user's own XDC, so the old text is kept beside the outputs.
    # A project is told from a checkpoint session by get_runs, as in dfx_finish_usp (current_project is not empty in a checkpoint session, e29).
    proc project_open_p {} {
        if {[catch {current_project -quiet} proj] || $proj eq ""} { return 0 }
        return [expr {[llength [get_runs -quiet]] > 0}]
    }

    # The partitions among rps that have no pblock. place_design stops on them with DRC HDPR-30. A pblock query that fails says nothing (the
    # caller then makes no claim), as partition_bram does.
    proc dfx_partitions_without_pblock {rps} {
        set out {}
        foreach rp $rps {
            if {[catch {set pbs [get_pblocks -quiet -of_objects [get_cells -quiet $rp]]}]} continue
            if {[llength $pbs] == 0} { lappend out $rp }
        }
        return $out
    }

    # Print what is true after ila::apply: what the skill added, what it did not do, and what the user must do to keep the cores (save them).
    # Facts only: no build step is listed, offered or recommended; implementation is the user's business (Mike, 2026-11-04). The AI-only note
    # holds the facts the AI needs when the user names a step. One text for a checkpoint session and one for an open project (project_open_p).
    proc next_steps {} {
        variable plan
        require_applied ila::next_steps
        if {[catch {require_architecture} arch]} { set arch "" }
        # A static-first session (ila_static_first.tcl was used): the partition may still be a black box, so its own facts are the ones to print.
        variable facts
        if {[dict exists $facts static_first] && [llength [info commands ::ila::static_first_finish]] > 0} {
            static_first_finish <folder>
            return
        }
        set cores [created_list]
        set dfx [expr {[dict exists $plan dfx] && [dict get $plan dfx]}]
        say "DONE: [expr {[llength $cores] == 1 ? "the ILA core [lindex $cores 0] is" : "the ILA cores [join $cores {, }] are"}] in the open design only. Nothing was saved, nothing was written and nothing was built: this skill runs no opt_design, place, route or image step."
        if {[project_open_p]} {
            set files [lsort -unique [get_files -quiet -of_objects [current_fileset -constrset]]]
            set impl [lindex [lsearch -all -inline -glob [get_runs -quiet] impl_*] 0]
            if {$impl eq ""} { set impl impl_1 }
            set role ""
            if {$dfx} {
                # A DFX project has one implementation run per configuration: the open configuration's run, not impl_1 (v-dfx, Versal DFX: child_b_impl_1).
                set oc [expr {[catch {dfx_open_config} r] ? "" : $r}]
                set impl [expr {$oc eq "" ? "<the implementation run of this configuration>" : [lindex $oc 2]}]
                set role [expr {$oc eq "" ? "" : [lindex $oc 1]}]
            }
            set last [expr {$arch eq "versal" ? "write_device_image" : "write_bitstream"}]
            if {$dfx} {
                say "WARNING: save_constraints writes the debug cores into their own constraint files (one for the static part, one for each module that has a core) and may rewrite your own constraint files in the same project (the same constraints, different text). Keep a copy of your files first and check them after the save."
            } elseif {[llength $files] == 1} {
                say "WARNING: this project has one constraint file, [lindex $files 0]. save_constraints rewrites it: the debug lines are written into it, next to your own lines. Keep a copy of the file first (name the copy with a .bak ending) and check the file after the save."
            }
            say "TO KEEP THEM: save the project's constraints before the design is closed; closing it without a save loses the cores (AI only: the save is save_constraints, run only when the user asks for it)."
            set notes [list "The run $impl builds the cores only after reset_run $impl; launch_runs $impl -to_step $last runs opt_design, builds the cores and writes the probes file beside the image"]
            if {$dfx && $role eq "parent"} { lappend notes "Resetting the parent run resets every child run too, and the children reuse the parent's static part" }
            if {$dfx && $role eq "child"} { lappend notes "A module core needs only this configuration's run again; a static core is added only while the parent is open" }
            if {$dfx} { lappend notes "Force Up-to-date on an implementation run keeps its old results without the new cores. Without a pblock for each partition the parent run stops at place_design (DRC HDPR-30)" }
        } else {
            say "TO KEEP THEM: save the design before this session closes; closing it without a save loses the cores (AI only: the save is write_checkpoint -force <path>, run only to a path the user named)."
            set notes [list "opt_design builds the cores and the debug hub, and the probes file can be written only after it"]
            if {$arch eq "versal"} { lappend notes "On Versal the hub unique_id in a probes file is final only after route_design: a probes file written earlier holds a provisional id, and Hardware Manager finds no ILA with it" }
            if {$dfx && $arch ne "versal"} {
                say "NOTE: opt_design builds the debug hub from the clock periods. With the clock constraints read after opt_design, Vivado prints Common 17-55 and the crossing from your clock to the hub clock is left unconstrained."
            }
            if {$dfx} {
                lappend notes "This is a DFX design: the floorplan, the module checkpoints, the greybox child and pr_verify are the user's own DFX flow"
                set rps [expr {[catch {lmap r [dfx_cells] {dict get $r cell}} v] ? {} : $v}]
                foreach rp [dfx_partitions_without_pblock $rps] {
                    say "WARNING: partition $rp has no pblock, so place_design stops on it with DRC HDPR-30 (Missing PBLOCK On Reconfigurable Cell). The floorplan is yours: this skill adds none."
                }
            }
        }
        say "  (AI only: facts for a step the user names; never list, offer or recommend one. [join $notes {. }].)"
    }
}
