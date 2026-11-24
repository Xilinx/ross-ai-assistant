# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# ila_insert.tcl: netlist ILA insertion on a synthesized Vivado design.
# Source it in a Vivado session that has a synthesized design open, then call
# the procs in the order given in SKILL.md:
#
#   ila::check_input                      refuse a placed or routed design, note existing cores
#   ila::plan  <spec> ?options?           resolve, qualify, group by clock, cost, print the plan
#   ila::apply                            create the cores and connect the probes, print the next steps (needs an accepted plan)
#   ila::write_probes <file>              after the user's opt_design: write the probes file
#
# Every number the procs report comes from a Vivado query. Nothing is estimated
# except the memory cost, which is labelled "formula".

# Without this, converting a collection of more than 500 objects to a Tcl list is an error.
set_param tcl.collectionResultDisplayLimit 0

# [split] The library is in the files below, in load order. Each holds one topic and was moved from this file without edits.
apply {dir {
    source [file join $dir ila_util.tcl]
    source [file join $dir ila_input.tcl]
    source [file join $dir ila_dfx_gate.tcl]
    source [file join $dir ila_resolve.tcl]
    source [file join $dir ila_net_state.tcl]
    source [file join $dir ila_clocks.tcl]
    source [file join $dir ila_qualify.tcl]
    source [file join $dir ila_plan.tcl]
    source [file join $dir ila_budget.tcl]
    source [file join $dir ila_apply.tcl]
    source [file join $dir ila_change.tcl]
    source [file join $dir ila_next_steps.tcl]
    source [file join $dir ila_probes.tcl]
}} [file dirname [file normalize [info script]]]

# ila::help (the human guide, references/help.md). Optional: the library works without it.
catch {source [file join [file dirname [info script]] ila_help.tcl]}
