# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Clock-only, board-derived fixture. All design constants come from the request
# and board preset. This runner never opens reference designs or previous trials.
foreach required {clock_repro_root clock_repro_request clock_repro_output clock_repro_log} {
    if {![info exists ::$required]} {error "Missing runner input $required"}
}
if {[current_project -quiet] ne ""} {error "Fresh process with no project required"}
if {[file exists $::clock_repro_output]} {error "Output directory must be fresh"}
set started [clock milliseconds]
set skill_dir [file normalize [file join [file dirname [info script]] ../..]]
source [file join $skill_dir .. vivado-ip-configurator lib ipcfg ipcfg.tcl]
source [file join $skill_dir lib ipiasm.tcl]
set ::ipcfg::native_log_path $::clock_repro_log
puts [ipcfg::python_status]

# The shared serializer returns data for dict/list parsing, never Tcl to source.
set request [ipcfg::python_exec -c {
import json, sys
sys.path.insert(0, sys.argv[1])
from plan_clocks import tcl_data
from validate_feasibility import load_json
from pathlib import Path
print(tcl_data(load_json(Path(sys.argv[2]))))
} [file join $skill_dir scripts] $::clock_repro_request]
set board_repo [file join $::clock_repro_root inputs rave2_board board_files]
set_param board.repoPaths [list $board_repo]
create_project clock_trial [file join $::clock_repro_output project] -part [dict get $request part]
set_property BOARD_PART [dict get $request board_part] [current_project]
create_bd_design clock_trial
puts [ipiasm::begin_design]

# The board preset is the authority for the PS reference. Probe it once, then
# remove the PS from this clock-only fixture to avoid unrelated system DRCs.
set created [ipcfg::capture [file join $::clock_repro_output ps_create.tcldict] \
    {ipcfg::create_cell xilinx.com:ip:ps_wizard processing_system} {SUCCESS:}]
if {[dict get $created status] ne "OK"} {error $created}
set bound [ipcfg::capture [file join $::clock_repro_output ps_board.tcldict] \
    {ipcfg::board_bind processing_system {CONFIG.PS_BOARD_INTERFACE ps_pmc_fixed_io} {} {Board-owned reference clocks}} {BOARD:bound}]
if {[dict get $bound status] ne "OK"} {error $bound}
set references {}
foreach pin [get_bd_pins -quiet processing_system/pl*_ref_clk] {
    lappend references [list [get_property CONFIG.FREQ_HZ $pin] [file tail $pin]]
}
if {![llength $references]} {error "No board reference clock discovered"}
lassign [lindex [lsort -integer -index 0 $references] 0] source_hz source_name
if {![regexp {^pl([0-9]+)_ref_clk$} $source_name -> source_index]} {error "Unsupported PS reference schema"}
set source_parameter PMC_CRP_PL${source_index}_REF_CTRL_FREQMHZ
set matching_configurations {}
foreach property [list_property [get_bd_cells processing_system]] {
    if {![string match CONFIG.*CONFIG $property]} {continue}
    set configuration [get_property $property [get_bd_cells processing_system]]
    if {![catch {dict exists $configuration $source_parameter} found] && $found} {
        lappend matching_configurations $configuration
    }
}
if {[llength $matching_configurations] != 1} {error "Missing or ambiguous board nominal reference parameter $source_parameter"}
set ps_configuration [lindex $matching_configurations 0]
set source_requested_hz [expr {round(1000000 * [dict get $ps_configuration $source_parameter])}]
ipcfg::write_evidence [file join $::clock_repro_output board_source.tcldict] \
    [dict create board [get_property BOARD_PART [current_project]] pin $source_name actual_hz $source_hz requested_hz $source_requested_hz parameter $source_parameter]
delete_bd_objs [get_bd_cells processing_system]
create_bd_port -dir I -type clk -freq_hz $source_hz reference

# Discover the vector-schema wizard from the current part's catalog, not from a
# reference topology. Selection is deterministic when one supported kind exists.
set available [get_ipdefs -all xilinx.com:ip:clkx5_wiz:*]
if {![llength $available]} {error "No supported vector clock generator in this catalog"}
set wizard [lindex [lsort -decreasing -dictionary $available] 0]
set discovered [ipcfg::create_cell $wizard capability_probe]
if {![string match SUCCESS:* $discovered]} {error $discovered}
set capacity [llength [split [get_property CONFIG.CLKOUT_PORT [get_bd_cells capability_probe]] ,]]
set ip_xml [get_property XML_FILE_NAME [get_ipdefs -all $wizard]]
delete_bd_objs [get_bd_cells capability_probe]
set planner_file [file join $::clock_repro_output planner_request.json]
ipcfg::python_exec -c {
import hashlib, json, pathlib, sys
request = json.loads(pathlib.Path(sys.argv[1]).read_text())
clocks = []
for clock in request['clocks']:
    clocks.append(dict(clock, source='reference', sinks=[clock['id']], resets=[], requirement='prompt#' + clock['id']))
sources = {'reference': {'hz':int(sys.argv[3]), 'requested_hz':int(sys.argv[4]), 'max_outputs':int(sys.argv[5]), 'vlnv':sys.argv[6], 'evidence':'board_source.tcldict'}}
fingerprint = {'tool':sys.argv[7], 'part':request['part'], 'board':request['board_part'], 'ip':hashlib.sha256(pathlib.Path(sys.argv[8]).read_bytes()).hexdigest(), 'sources':hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()}
pathlib.Path(sys.argv[2]).write_text(json.dumps(dict(schema_version=1, fingerprint=fingerprint, sources=sources, clocks=clocks), indent=2))
} $::clock_repro_request $planner_file $source_hz $source_requested_hz $capacity $wizard [version] $ip_xml
set plan [ipcfg::python_script [file join $skill_dir scripts plan_clocks.py] $planner_file --limit 8 --tcl]
ipcfg::write_evidence [file join $::clock_repro_output candidates.tcldict] $plan
set result [ipiasm::realize_clock_candidates $plan {reference /reference} $::clock_repro_output]
if {[string match CLOCK_REALIZE_FAIL:* $result]} {error $result}

# Output names follow the prompt; frequency is never set to the expected value.
foreach output [dict get $result observations] {
    set boundary [create_bd_port -dir O -type clk [dict get $output clock]]
    connect_bd_net [get_bd_pins [dict get $output pin]] $boundary
}
validate_bd_design
set observed {}
foreach output [dict get $result observations] {
    set actual [get_property CONFIG.FREQ_HZ [get_bd_pins [dict get $output pin]]]
    if {$actual != [dict get $output hz]} {error "Output changed after externalization"}
    dict set observed [dict get $output clock] $actual
}
save_bd_design
set elapsed [expr {[clock milliseconds] - $started}]
ipcfg::write_evidence [file join $::clock_repro_output trial_result.tcldict] \
    [dict create status PASS observations $observed candidate [dict get $result candidate] generators [dict get $result generators] elapsed_ms $elapsed request_sha256 [dict get $plan request_sha256]]
puts "CLOCK_REPRO:PASS elapsed_ms=$elapsed candidate=[dict get $result candidate] observations={$observed} evidence=$::clock_repro_output"