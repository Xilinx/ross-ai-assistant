# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# What an earlier stage leaves in memory -- open gates, board ownership, requirement pins --
# must be re-derived or restored when a later stage runs in a new Vivado process. Run each
# phase in its OWN fresh Vivado process, in order, against the same evidence directory:
#   set resume_live_phase one|two|three
#   set resume_live_evidence <lab>/tests/evidence/workflow_benchmark/<dir>
#   source tests/integration/test_resume_state_live.tcl

namespace eval resume_live {
    variable root [file normalize [file join [file dirname [info script]] ../../../../..]]
    variable assertions 0
}
proc resume_live::assert {expression message} {
    if {![uplevel 1 [list expr $expression]]} {error "ASSERT:$message"}
    incr ::resume_live::assertions
}
proc resume_live::plan_json {} {
    return {{
  "schema_version": 1,
  "stages": [
    {"name": "board", "operations": [
      {"id": "board_preset", "op": "call", "proc": "ipiasm::require_board_preset", "args": [], "expect": ["READY:"]},
      {"id": "gpio_create", "op": "call", "proc": "ipiasm::create_cell", "args": ["xilinx.com:ip:axi_gpio", "/gpio_boot", {}], "expect": ["SUCCESS:"]},
      {"id": "gpio_bind", "op": "set_property", "object": {"kind": "cell", "path": "/gpio_boot"},
       "properties": {"CONFIG.GPIO_BOARD_INTERFACE": "GPIO_BOOTROM_RI"}, "reason": "board preset", "expect": ["PROPERTY:OK"]}
    ]},
    {"name": "cells", "operations": [
      {"id": "gpio_second", "op": "call", "proc": "ipiasm::create_cell", "args": ["xilinx.com:ip:axi_gpio", "/gpio_second", {}], "expect": ["SUCCESS:"]},
      {"id": "gpio_pin", "op": "call", "proc": "ipcfg::require", "args": ["/gpio_boot", {"CONFIG.C_INTERRUPT_PRESENT": 1}, "spec: interrupt output"], "expect": ["REQ:pinned+applied"]},
      {"id": "gpio_check", "op": "call", "proc": "ipiasm::check_cell_config", "args": ["/gpio_boot", {"CONFIG.C_INTERRUPT_PRESENT": 1}], "expect": ["CELL_CONFIG:OK"]}
    ]}
  ]
}}
}
# Not named "load": Vivado's IPI init scripts run in the caller's namespace and call Tcl's load.
# Sourced at global level so the libraries create ::ipcfg and ::ipiasm, not nested namespaces.
proc resume_live::load_skills {} {
    foreach library {{vivado-ip-configurator lib ipcfg ipcfg.tcl} {vivado-ipi-assembler lib ipiasm.tcl}} {
        uplevel #0 [list source -notrace [file join $::resume_live::root .claude skills {*}$library]]
    }
}
proc resume_live::reopen {} {
    set_param board.repoPaths [list [file join $::resume_live::root inputs rave2_board board_files]]
    open_project [file join $::resume_live::evidence scratch resume.xpr]
    open_bd_design [get_files design_1.bd]
    resume_live::load_skills
    ipiasm::policy unattended
}

proc resume_live::one {} {
    set_param board.repoPaths [list [file join $::resume_live::root inputs rave2_board board_files]]
    create_project resume [file join $::resume_live::evidence scratch] -part xc2ve3558-sfva1440-2MP-e-S
    set_property board_part amd.com:rave2:part0:1.3 [current_project]
    create_bd_design design_1
    resume_live::load_skills
    resume_live::assert {[string match SUCCESS:* [ipiasm::begin_design]]} "begin_design"
    ipiasm::policy unattended
    set channel [open $::resume_live::plan {WRONLY CREAT EXCL}]
    puts $channel [resume_live::plan_json]
    close $channel
    set board [ipiasm::assemble_from_plan $::resume_live::plan board]
    resume_live::assert {[string match ASSEMBLY_PLAN:PARTIAL* $board]} "board: $board"
    resume_live::assert {[dict exists $::ipcfg::board_owned /gpio_boot]} "board_bind recorded no ownership"
    set recorded [ipcfg::ledger_entry]
    resume_live::assert {[dict exists $recorded board_owned /gpio_boot]} "ownership not persisted"
    save_bd_design
    set result [dict create board $board owned [ipcfg::board_owned]]
    close_project
    return $result
}

proc resume_live::two {} {
    resume_live::reopen
    resume_live::assert {![ipiasm::_gate_get board]} "board gate open in a new process before resume"
    set cells [ipiasm::assemble_from_plan $::resume_live::plan cells]
    set status [ipiasm::stage_execution_status]
    # cells is this plan's last stage, so the whole plan now passes.
    resume_live::assert {[string match "ASSEMBLY_PLAN:PASS stages=2 *" $cells]} "cells: $cells"
    resume_live::assert {[dict exists $status operations @regate:board_preset]} "board gate not re-derived"
    resume_live::assert {[ipiasm::_gate_get board]} "board gate closed after resume"
    resume_live::assert {[dict exists [ipcfg::ledger_entry] req /gpio_boot]} "pin not persisted"
    save_bd_design
    close_project
    return [dict create cells $cells operations [dict keys [dict get $status operations]]]
}

proc resume_live::three {} {
    resume_live::reopen
    set board [ipiasm::assemble_from_plan $::resume_live::plan board]
    set status [ipiasm::stage_execution_status]
    set bind [dict get $status operations gpio_bind raw]
    resume_live::assert {[string match ASSEMBLY_PLAN:PARTIAL* $board]} "board replay: $board / $bind"
    resume_live::assert {![string match *OWNERSHIP_UNKNOWN* $bind]} "ownership lost: $bind"
    set audit [ipcfg::audit_requirements]
    resume_live::assert {[string match REQ:OK* $audit]} "pins lost: $audit"
    close_project
    return [dict create board $board bind $bind audit $audit]
}

if {![info exists resume_live_phase] || $resume_live_phase ni {one two three}} {
    error "set resume_live_phase to one, two or three"
}
if {![info exists resume_live_evidence]} {error "set resume_live_evidence"}
if {[version -short] ne "2026.1"} {error "Requires native Vivado 2026.1"}
if {[current_project -quiet] ne "" || [llength [info commands ::ipiasm::init]]} {
    error "Each phase needs a fresh Vivado process with no project or loaded helpers"
}
set ::resume_live::evidence [file normalize $resume_live_evidence]
set ::resume_live::plan [file join $::resume_live::evidence plan.json]
file mkdir $::resume_live::evidence
set phase_code [catch {resume_live::$resume_live_phase} phase_result phase_options]
if {[current_project -quiet] ne ""} {catch {close_project}}
set channel [open [file join $::resume_live::evidence phase_$resume_live_phase.tcldict] {WRONLY CREAT EXCL}]
puts $channel [dict create schema_version 1 phase $resume_live_phase pid [pid] code $phase_code \
    result $phase_result assertions $::resume_live::assertions]
close $channel
puts "RESUME_LIVE:phase=$resume_live_phase pid=[pid] code=$phase_code assertions=$::resume_live::assertions"
if {$phase_code} {return -options $phase_options $phase_result}
