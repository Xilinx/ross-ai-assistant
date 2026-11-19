# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
import base64
from collections import Counter
import os
import re
import runpy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


TESTS = Path(__file__).resolve().parents[1]
SKILLS = TESTS.parents[1]
REPORT = SKILLS.parents[1] / "tests/evidence/issue-closure/assembler_procedure_profile.json"


class WorkflowLifecycle(unittest.TestCase):
    def test_source_files_hash_inclusion_and_top(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("top source.sv", "logic.vhd"):
                (root / name).write_bytes(b"abc")
            setup = f'''
set source_path {{{root / 'top source.sv'}}}
set other_path {{{root / 'logic.vhd'}}}
set sha ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
set files [dict create $source_path $sha $other_path [string toupper $sha]]
set expected_top fixture_top
set actual_top fixture_top
set included [list $source_path $other_path]
proc get_files {{quiet patterns}} {{
    if {{$quiet ne "-quiet" || [llength $patterns] != 1}} {{error "wrong get_files arguments"}}
    set path [lindex $patterns 0]
    if {{$path in $::included}} {{return [list $path]}}
    return {{}}
}}
proc current_fileset {{}} {{return sources_1}}
proc get_property {{key object}} {{
    if {{$key ne "top" || $object ne "sources_1"}} {{error "wrong top query"}}
    return $::actual_top
}}
'''
            cases = (
                ("matching", "", r"SOURCE_FILES:OK 2 file(s) top=fixture_top"),
                ("empty", "set files {}", "SOURCE_FILES_FAIL:nonempty*"),
                ("odd_dict", "set files {orphan}", "SOURCE_FILES_FAIL:nonempty*"),
                ("malformed_list", 'set files "\\{"', "SOURCE_FILES_FAIL:nonempty*"),
                ("relative", "set files [dict create relative.sv $sha]", "SOURCE_FILES_FAIL:invalid*"),
                ("bad_sha", "dict set files $source_path invalid", "SOURCE_FILES_FAIL:invalid*"),
                ("nonhex_sha", "dict set files $source_path [string repeat z 64]", "SOURCE_FILES_FAIL:invalid*"),
                ("absent", "dict set files ${source_path}.missing $sha", "SOURCE_FILES_FAIL:absent*"),
                ("directory", "dict set files [file dirname $source_path] $sha", "SOURCE_FILES_FAIL:absent*"),
                ("wrong_sha", "dict set files $other_path [string repeat 0 64]", "SOURCE_FILES_FAIL:SHA256 mismatch:*"),
                ("not_in_project", "set included [list $source_path]", "SOURCE_FILES_FAIL:source file not included*"),
                ("different_file", "proc get_files {args} {return [list ${::source_path}.other]}", "SOURCE_FILES_FAIL:source file not included*"),
                ("missing_get_files", "rename get_files {}", "SOURCE_FILES_FAIL:*get_files*"),
                ("query_error", 'proc get_files {args} {error query_failed}', "SOURCE_FILES_FAIL:query_failed"),
                ("hash_error", 'proc ipcfg::python_exec {args} {error hash_failed}', "SOURCE_FILES_FAIL:hash_failed"),
                ("wrong_top", "set actual_top other_top", "SOURCE_FILES_FAIL:synthesis top mismatch:*"),
                ("empty_top", "set expected_top { }", "SOURCE_FILES_FAIL:expected synthesis top required"),
                ("top_error", 'proc current_fileset {} {error no_fileset}', "SOURCE_FILES_FAIL:no_fileset"),
            )
            for name, change, expected in cases:
                with self.subTest(name=name):
                    self.run_tcl(setup + change + f'''
set result [ipiasm::check_source_files $files $expected_top]
if {{![string match {{{expected}}} $result]}} {{error $result}}
''')
            (root / "logic.vhd").write_bytes(b"changed")
            self.run_tcl(setup + '''
set result [ipiasm::check_source_files $files $expected_top]
if {![string match {SOURCE_FILES_FAIL:SHA256 mismatch:*} $result]} {error $result}
''')

    def test_cell_config_checks_live_properties(self):
        self.run_tcl('''
proc get_bd_cells {args} {return /camera}
proc list_property {args} {return [dict keys $::properties]}
proc get_property {key cell} {
    if {![dict exists $::properties $key]} {return {}}
    return [dict get $::properties $key]
}
set properties {CONFIG.RATE 1500 CONFIG.ENABLED true}
set result [ipiasm::check_cell_config /camera {CONFIG.RATE 1500}]
if {![string match CELL_CONFIG:OK* $result]} {error $result}
set result [ipiasm::check_cell_config /camera {CONFIG.RATE 1500.0 CONFIG.ENABLED 1}]
if {![string match CELL_CONFIG:OK* $result]} {error $result}
dict set properties CONFIG.RATE 800
set result [ipiasm::check_cell_config /camera {CONFIG.RATE 1500}]
if {![string match CELL_CONFIG_FAIL:* $result]} {error $result}
set result [ipiasm::check_cell_config /camera {CONFIG.MISSING {}}]
if {![string match {CELL_CONFIG_FAIL:*has no property CONFIG.MISSING} $result]} {error $result}
set result [ipiasm::check_cell_config /camera {}]
if {$result ne "CELL_CONFIG_FAIL:nonempty property dictionary required"} {error $result}
foreach property {CONFIG.MMI_CONFIG CONFIG.PS11_CONFIG} {
    dict set properties $property {UNRELATED retained RATE 1500 ENABLED true}
    set result [ipiasm::check_cell_config /camera [dict create $property {ENABLED 1 RATE 1500.0}]]
    if {$result ne "CELL_CONFIG:OK /camera 1 property(s)"} {error $result}
    set result [ipiasm::check_cell_config /camera [dict create $property {RATE 800}]]
    if {![string match "CELL_CONFIG_FAIL:*${property}/RATE(reverted:*=1500,want=800)*" $result]} {error $result}
    set result [ipiasm::check_cell_config /camera [dict create $property {MISSING {}}]]
    if {![string match "CELL_CONFIG_FAIL:*${property}/MISSING(missing)*" $result]} {error $result}
    set result [ipiasm::check_cell_config /camera [dict create $property {}]]
    if {![string match CELL_CONFIG_FAIL:* $result]} {error $result}
    dict set properties $property {}
    set result [ipiasm::check_cell_config /camera [dict create $property {RATE 1500}]]
    if {![string match "CELL_CONFIG_FAIL:*${property}/RATE(missing)*" $result]} {error $result}
}
''')

    def test_inventory_requires_connected_bram(self):
        self.run_tcl('''
proc get_bd_cells {args} {
    if {[lsearch -exact $args -of_objects] >= 0} {return /memory}
    return /controller
}
proc get_bd_intf_pins {args} {return /controller/BRAM_PORTA}
proc find_bd_objs {args} {return $::peers}
proc get_property {args} {return xilinx.com:ip:blk_mem_gen:8.4}
set peers {}
ipiasm::_gate_set inventory 1 fixture
set result [ipiasm::gate_inventory {axi_bram_ctrl 1}]
if {![string match INVENTORY_FAIL:BRAM_FAIL:* $result]} {error $result}
if {[dict get $::ipiasm::gates inventory]} {error "Stale inventory gate"}
set peers /memory/BRAM_PORTA
set result [ipiasm::gate_inventory {axi_bram_ctrl 1}]
if {![string match INVENTORY:OK* $result]} {error $result}
if {![dict get $::ipiasm::gates inventory]} {error "Inventory did not open"}
set result [ipiasm::gate_inventory {axi_bram_ctrl invalid}]
if {[dict get $::ipiasm::gates inventory]} {error "Malformed retry left gate open"}
''')

    def run_tcl(self, body):
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n"
        script += "if {[catch {\n" + body + "\n} detail]} {puts stderr $detail; exit 1}\n"
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_configurator_reload_preserves_state(self):
        self.run_tcl(f'''
set ::ipcfg::req {{cell {{{{CONFIG.VALUE 7 0 requirement}}}}}}
set ::ipcfg::board_owned {{cell evidence}}
set ::ipcfg::mode assemble
set before [list $::ipcfg::req $::ipcfg::board_owned $::ipcfg::mode]
source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
if {{$before ne [list $::ipcfg::req $::ipcfg::board_owned $::ipcfg::mode]}} {{
    error "Configurator reload discarded live state"
}}
''')

    def test_assembler_reload_preserves_state(self):
        self.run_tcl(f'''
set ::ipiasm::gates {{board 1 inventory 1 qos 0}}
set ::ipiasm::executed_plan {{path fixture sha256 fingerprint}}
set before [list $::ipiasm::gates $::ipiasm::executed_plan]
source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
if {{$before ne [list $::ipiasm::gates $::ipiasm::executed_plan]}} {{
    error "Assembler reload discarded live state"
}}
''')

    def test_changed_library_rejected_without_state_loss(self):
        for skill in ("vivado-ip-configurator", "vivado-ipi-assembler"):
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as directory:
                library = SKILLS / skill / "lib" / ("ipcfg/ipcfg.tcl" if skill == "vivado-ip-configurator" else "ipiasm.tcl")
                copy = Path(directory) / library.name
                copy.write_bytes(library.read_bytes())
                self.run_tcl(f'''
set ::ipcfg::req {{cell retained}}
set ::ipiasm::stage_completion {{fixture retained}}
if {{![catch {{source {{{copy}}}}} message] || ![string match LIBRARY_FAIL:* $message]}} {{
    error "Different library path accepted"
}}
if {{$::ipcfg::req ne {{cell retained}} || $::ipiasm::stage_completion ne {{fixture retained}}}} {{
    error "Rejected load changed state"
}}
''')


class InterfaceRoutes(unittest.TestCase):
    run_tcl = WorkflowLifecycle.run_tcl

    def test_exact_peers_and_port_first_resolution(self):
        self.run_tcl('''
set queries {}
proc get_bd_intf_ports {quiet path} {
    lappend ::queries [list port $path]
    if {$quiet ne "-quiet"} {error "wrong lookup options"}
    if {$path eq "/external"} {return /external}
    return {}
}
proc get_bd_intf_pins {quiet path} {
    lappend ::queries [list pin $path]
    if {$path eq "/external"} {error "port must win"}
    return [list $path]
}
proc get_bd_intf_nets {args} {error "net names are not provenance"}
proc find_bd_objs {args} {
    if {[lrange $args 0 2] ne {-relation connected_to -thru_hier}} {error "wrong peer options"}
    switch -- [lindex $args 3] {
        /external {return {fabric/S_AXI/}}
        /producer/M_INI {return {/other/S_INI /nested/consumer/S_INI}}
        default {error "unexpected source"}
    }
}
set result [ipiasm::check_interface_routes {{external /fabric/S_AXI} {/producer/M_INI nested/consumer/S_INI}}]
if {$result ne "INTERFACE_ROUTES:OK n=2"} {error $result}
if {[lrange $queries 0 2] ne {{port /external} {port /fabric/S_AXI} {pin /fabric/S_AXI}}} {
    error "lookup order changed: $queries"
}
''')

    def test_rejects_malformed_missing_and_disconnected_edges(self):
        setup = '''
set edges {{/source/M_AXI /target/S_AXI}}
proc get_bd_intf_ports {args} {return {}}
proc get_bd_intf_pins {quiet path} {return [list $path]}
proc get_bd_intf_nets {args} {return /same_net}
proc find_bd_objs {args} {return /target/S_AXI}
'''
        cases = (
            ("empty", "set edges {}"),
            ("malformed_list", 'set edges "\\{"'),
            ("malformed_edge", 'set edges [list "\\{"]'),
            ("short", "set edges {{/source/M_AXI}}"),
            ("long", "set edges {{/source/M_AXI /target/S_AXI extra}}"),
            ("empty_path", "set edges {{{} /target/S_AXI}}"),
            ("blank_path", "set edges {{{ } /target/S_AXI}}"),
            ("root_path", "set edges {{/ /target/S_AXI}}"),
            ("missing", "proc get_bd_intf_pins {args} {return {}}"),
            ("wrong_full_path", "proc get_bd_intf_pins {args} {return /other/S_AXI}"),
            ("disconnected", "proc find_bd_objs {args} {return {}}"),
            ("wrong_peer", "proc find_bd_objs {args} {return /other/S_AXI}"),
            ("prefix_peer", "proc find_bd_objs {args} {return /target/S_AXI_extra}"),
            ("transitive_only", "proc find_bd_objs {args} {return /bridge/S_AXI}"),
            ("query_error", "proc find_bd_objs {args} {error query_failed}"),
            ("lookup_error", "proc get_bd_intf_ports {args} {error lookup_failed}"),
            ("missing_api", "rename find_bd_objs {}"),
            ("second_edge_fails", "lappend edges {/source/M_AXI /other/S_AXI}"),
        )
        for name, change in cases:
            with self.subTest(name=name):
                self.run_tcl(setup + change + '''
set result [ipiasm::check_interface_routes $edges]
if {![string match INTERFACE_ROUTES_FAIL:* $result]} {error $result}
''')
        for path in ("/source/*", "/source/?", "/source/[ab]", "/source/a]", r"/source/\\name"):
            for edge in ((path, "/target/S_AXI"), ("/source/M_AXI", path)):
                with self.subTest(edge=edge):
                    self.run_tcl(setup + f'''
set result [ipiasm::check_interface_routes [list [list {{{edge[0]}}} {{{edge[1]}}}]]]
if {{![string match {{INTERFACE_ROUTES_FAIL:*invalid exact interface path:*}} $result]}} {{error $result}}
''')

    def test_reports_every_bad_edge_in_one_pass(self):
        # One defect per attempt makes a wrong topology cost one source per edge.
        self.run_tcl('''
proc get_bd_intf_ports {args} {return {}}
proc get_bd_intf_pins {quiet path} {return [list $path]}
proc find_bd_objs {args} {return {}}
set edges {
    {/source/M_AXI /target/S_AXI}
    {/source/N_AXI /target/T_AXI}
    {/source/* /target/U_AXI}
}
set result [ipiasm::check_interface_routes $edges]
if {![string match {INTERFACE_ROUTES_FAIL:3 defect(s):*} $result]} {error "not batched: $result"}
foreach fragment {
    {disconnected interface edge: /source/M_AXI -> /target/S_AXI}
    {disconnected interface edge: /source/N_AXI -> /target/T_AXI}
    {invalid exact interface path: /source/*}
} {
    if {[string first $fragment $result] < 0} {error "missing '$fragment' in: $result"}
}
''')


class WorkflowOutput(WorkflowLifecycle):
    def test_native_capture_records_and_classifies_messages(self):
        with tempfile.TemporaryDirectory(prefix="native capture ") as directory:
            self.run_tcl(f'''
set directory {{{directory}}}
set console {{CRITICAL WARNING: old message}}
proc get_msg_config {{args}} {{
    return [regexp -all -line "^[lindex $args 1]:" $::console]
}}
proc write_messages {{args}} {{
    set channel [open [lindex $args end] w]
    puts $channel "\n########\n# Generated by Vivado fixture\n# Command Used: write_messages\n########\n"
    puts -nonewline $channel $::console
    close $channel
}}
set receipt [ipcfg::capture [file join $directory ok.tcldict] {{set value {{SUCCESS:kept}}}}]
if {{[dict get $receipt status] ne "OK" || [dict get $receipt warning_count] != 0}} {{error "Old warning leaked"}}
set receipt [ipcfg::capture [file join $directory warning.tcldict] {{append ::console "\nWARNING: ordinary notice"; set value SUCCESS:kept}}]
if {{[dict get $receipt status] ne "OK" || [dict get $receipt warning_count] != 1}} {{error "Ordinary warning misclassified"}}
set receipt [ipcfg::capture [file join $directory bad.tcldict] {{append ::console "\nCRITICAL WARNING: ignored setting"; set value SUCCESS:kept}}]
if {{[dict get $receipt status] ne "FAIL" || [dict get $receipt code] != 0}} {{error "Critical warning accepted"}}
set channel [open [dict get $receipt evidence_path] r]
set record [dict get [read $channel] data]
close $channel
if {{[dict get $record raw] ne "SUCCESS:kept" || [dict get $record critical_count] != 1}} {{error "Lost native failure"}}
set receipt [ipcfg::capture [file join $directory exception.tcldict] {{error {{full diagnostic}} {{original stack}} {{FIXTURE CODE}}}}]
if {{[dict get $receipt status] ne "FAIL" || [dict get $receipt code] != 1}} {{error "Exception accepted"}}
set channel [open [dict get $receipt evidence_path] r]
set record [dict get [read $channel] data]
close $channel
if {{[dict get $record options -errorcode] ne {{FIXTURE CODE}}}} {{error "Lost exception options"}}
set calls 0
if {{![catch {{ipcfg::capture [file join $directory ok.tcldict] {{incr calls}}}}] || $calls != 0}} {{error "Overwrite ran script"}}
foreach {{label message}} {{ignored {{WARNING: disabled parameter ignored}} error {{ERROR: native error}}}} {{
    set receipt [ipcfg::capture [file join $directory $label.tcldict] {{append ::console "\n$message"; set value SUCCESS:kept}} {{SUCCESS:}}]
    if {{[dict get $receipt status] ne "FAIL"}} {{error "Unsafe message accepted"}}
}}
set nested "WARNING: \\[IP_Flow 19-3374\\] An attempt to modify the value of disabled parameter 'P' from '1' to '0' has been ignored for IP 'd_cam_0/bd_1/bd_1_phy_0'"
set receipt [ipcfg::capture [file join $directory subcore.tcldict] {{append ::console "\n$nested"; set value SUCCESS:kept}} {{SUCCESS:}}]
if {{[dict get $receipt status] ne "OK"}} {{error "Subsystem-internal ignore failed the caller"}}
set channel [open [dict get $receipt evidence_path] r]
set record [dict get [read $channel] data]
close $channel
if {{[llength [dict get $record subcore_ignored]] != 1}} {{error "Subsystem-internal ignore not recorded"}}
proc get_bd_cells {{args}} {{return [lindex $args end]}}
foreach {{label kept propagated verdict}} {{
    width 0x0000000000000000 0x000000000000 OK  radix 0x10 16 OK  bool true 1 OK
    latency 6 12 FAIL  flag true 0 FAIL
}} {{
    set line "WARNING: \\[BD 41-702\\] Propagation TCL tries to overwrite USER strength parameter P($kept) on '/tg_0' with propagated value($propagated). Command ignored"
    set receipt [ipcfg::capture [file join $directory p702_$label.tcldict] {{append ::console "\n$line"; set value SUCCESS:kept}} {{SUCCESS:}}]
    if {{[dict get $receipt status] ne $verdict}} {{error "41-702 $kept -> $propagated read as [dict get $receipt status]"}}
}}
rename get_bd_cells {{}}
set top "WARNING: \\[IP_Flow 19-3374\\] An attempt to modify the value of disabled parameter 'P' from '1' to '0' has been ignored for IP 'd_cam_0'"
set receipt [ipcfg::capture [file join $directory topcore.tcldict] {{append ::console "\n$top"; set value SUCCESS:kept}} {{SUCCESS:}}]
if {{[dict get $receipt status] ne "FAIL"}} {{error "Ignored write on the caller's IP accepted"}}
set receipt [ipcfg::capture [file join $directory predicate.tcldict] {{set value {{REQ_FAIL:rejected}}}} {{REQ:}}]
if {{[dict get $receipt status] ne "FAIL"}} {{error "Failure predicate accepted"}}
if {{![string match "result does not match expected {{REQ:}}: got 'REQ_FAIL:rejected'*" [dict get $receipt excerpt]]}} {{error "Expectation miss not explained: [dict get $receipt excerpt]"}}
set receipt [ipcfg::capture [file join $directory changed.tcldict] {{set ::console replaced; set value SUCCESS:kept}}]
if {{[dict get $receipt status] ne "FAIL" || ![dict exists $receipt capture_error]}} {{error "Rewritten history accepted"}}
set emitted 0
proc get_msg_config {{args}} {{expr {{[lindex $args 1] eq "CRITICAL WARNING" ? $::emitted : 0}}}}
set receipt [ipcfg::capture [file join $directory missing.tcldict] {{incr ::emitted; set value SUCCESS:kept}}]
if {{[dict get $receipt status] ne "FAIL" || [dict get $receipt critical_count] != 1 || ![dict exists $receipt capture_error]}} {{error "Disappearing native warning accepted"}}
set ::ipcfg::native_log_path [file join $directory native.log]
set channel [open $::ipcfg::native_log_path w]; close $channel
set receipt [ipcfg::capture [file join $directory logged.tcldict] {{
    incr ::emitted
    set channel [open $::ipcfg::native_log_path a]
    puts $channel {{CRITICAL WARNING: read-only write}}
    close $channel
    set value SUCCESS:kept
}}]
if {{[dict get $receipt status] ne "FAIL" || [dict get $receipt critical_count] != 1 || ![file isfile [dict get $receipt native_log]]}} {{error "Native log warning lost"}}
proc get_msg_config {{args}} {{expr {{[lindex $args 1] eq "WARNING" ? $::emitted : 0}}}}
set receipt [ipcfg::capture [file join $directory internal.tcldict] {{incr ::emitted; set value SUCCESS:kept}}]
if {{[dict get $receipt status] ne "OK" || [dict get $receipt unprinted_warning_count] != 1}} {{error "Internal counter discrepancy not recorded"}}
set receipt [ipcfg::capture [file join $directory reordered.tcldict] {{set ::console "INFO: late insert\n$::console"; set value SUCCESS:kept}} {{SUCCESS:}}]
if {{[dict get $receipt status] ne "OK" || [dict get $receipt history_reordered] != 1}} {{error "Reordered export with a native log failed the caller"}}
set receipt [ipcfg::capture [file join $directory truncated.tcldict] {{set channel [open $::ipcfg::native_log_path w]; close $channel; set value SUCCESS:kept}}]
if {{[dict get $receipt status] ne "FAIL" || ![dict exists $receipt capture_error]}} {{error "Truncated native log accepted"}}
''')

    def test_evidence_retains_full_payload_without_console_dump(self):
        with tempfile.TemporaryDirectory(prefix="evidence path ") as directory:
            destination = Path(directory) / "result.tcldict"
            self.run_tcl(f'''
set payload [dict create raw [string repeat {{CONFIG.VALUE {{text with spaces}} }} 5000] error_info {{original diagnostic}}]
set summary [ipcfg::write_evidence {{{destination}}} $payload]
if {{[dict get $summary status] ne "RECORDED" || [string length $summary] > 512}} {{error "Not a compact recording receipt"}}
set channel [open {{{destination}}} r]
fconfigure $channel -encoding utf-8 -translation lf
set record [read $channel]
close $channel
if {{[dict get $record data] ne $payload}} {{error "Lost payload or diagnostics"}}
if {{[dict get $summary bytes] != [file size {{{destination}}}]}} {{error "Incorrect byte count"}}
if {{![catch {{ipcfg::write_evidence {{{destination}}} replacement}}]}} {{error "Overwrote evidence"}}
if {{![catch {{ipcfg::write_evidence relative.tcldict ignored}}]}} {{error "Relative path accepted"}}
''')


class WorkflowContext(WorkflowLifecycle):
    def test_same_directory_different_project_is_not_resume(self):
        self.run_tcl('''
set project scratch
proc current_project {} {return $::project}
proc current_bd_design {} {return design}
proc get_property {args} {return /tmp/shared-project-directory}
ipiasm::init
set ::ipcfg::req {cell evidence}
set project delivery
if {![string match ASM_FAIL:* [ipiasm::init]]} {error "Different project accepted as resume"}
if {$::ipcfg::req ne {cell evidence}} {error "Refusal changed requirements"}
if {![string match SUCCESS:* [ipiasm::begin_design]] || $::ipcfg::req ne {}} {
    error "Explicit project transition did not clear scratch requirements"
}
''')

    def test_full_state_survives_resume_and_clears_on_transition(self):
        self.run_tcl('''
set design scratch
proc current_project {} {return project}
proc current_bd_design {} {return $::design}
proc get_property {args} {return /tmp/lifecycle-fixture}
ipiasm::init
set names {ipcfg::req ipcfg::board_owned ipcfg::board_override ipcfg::bd_file
    ipiasm::clk_wizard ipiasm::last_validate_console ipiasm::last_reset_sync_findings
    ipiasm::stage_t ipiasm::stage_log ipiasm::forced_fp ipiasm::delivery_fp
    ipiasm::executed_plan ipiasm::interface_contracts ipiasm::stage_completion
    ipiasm::stage_result ipiasm::plan_request}
foreach name $names {set ::$name {fixture evidence}}
set ::ipiasm::gates {board 1 inventory 1 qos 1}
set ::ipiasm::qos_sub {qos 1 noc 1}
set ::ipiasm::unused_qos_routes {context scratch routes {noc/S00_AXI/MC_0 unused}}
foreach command {ipiasm::init ipiasm::begin_design} {
    if {![string match SUCCESS:* [$command]]} {error "Resume failed"}
    foreach name $names {if {[set ::$name] ne {fixture evidence}} {error "Resume lost $name"}}
}
set design delivery
if {![string match SUCCESS:* [ipiasm::begin_design]]} {error "Transition failed"}
if {[info exists ::ipiasm::unused_qos_routes]} {error "Unused QoS declarations leaked"}
foreach name $names {
    if {$name eq "ipiasm::stage_t"} {continue}
    if {[set ::$name] ne {}} {error "Transition leaked $name"}
}
foreach gate {board inventory qos} {
    if {[dict get $::ipiasm::gates $gate]} {error "Open gate leaked"}
}
foreach gate {qos noc} {
    if {[dict get $::ipiasm::qos_sub $gate]} {error "QoS subgate leaked"}
}
''')

    def test_resume_and_explicit_transition(self):
        self.run_tcl('''
set directory /tmp/scratch
proc current_project {} {return project}
proc current_bd_design {} {return design}
proc get_property {args} {return $::directory}
ipiasm::init
set ::ipcfg::req {cell evidence}
set ::ipcfg::board_owned {cell ownership}
set ::ipiasm::gates {board 1 inventory 1 qos 0}
set before [list $::ipcfg::req $::ipcfg::board_owned $::ipiasm::gates]
ipiasm::init
if {$before ne [list $::ipcfg::req $::ipcfg::board_owned $::ipiasm::gates]} {
    error "Same-design init discarded state"
}
set directory /tmp/delivery
if {![string match ASM_FAIL:* [ipiasm::init]]} {error "Implicit transition accepted"}
if {$before ne [list $::ipcfg::req $::ipcfg::board_owned $::ipiasm::gates]} {
    error "Refused transition changed state"
}
if {![string match SUCCESS:* [ipiasm::begin_design]]} {error "Cannot begin delivery"}
if {$::ipcfg::req ne {} || $::ipcfg::board_owned ne {} ||
    [dict get $::ipiasm::gates board] != 0} {error "Scratch state leaked"}
''')

    def test_requirements_are_structured(self):
        self.run_tcl('''
set ::ipcfg::req [dict create cell [list [list CONFIG.TEXT {value with spaces} {} {reason {with braces}}]]]
set record [lindex [ipcfg::requirements_data cell] 0]
if {[dict get $record expected] ne {value with spaces} ||
    [dict get $record reason] ne {reason {with braces}}} {error "Lossy requirements"}
if {[ipcfg::requirements_data missing] ne {}} {error "Unknown cell requirements"}
set inventory [ipiasm::api_inventory {ipcfg::requirements*}]
if {![dict exists $inventory ipcfg::requirements_data]} {error "Missing API"}
''')


class WorkflowApi(unittest.TestCase):
    run_tcl = WorkflowLifecycle.run_tcl

    def test_documented_signatures_and_plan_registry_match_runtime(self):
        reference = (SKILLS / "vivado-ipi-assembler/REFERENCE.md").read_text()
        signatures = re.findall(r"`((?:ipcfg|ipiasm)::\w+) (\{[^`]*\})`", reference)
        self.assertGreater(len(signatures), 30)
        documented = " ".join(f"[list {name} {signature}]" for name, signature in signatures)
        registry = runpy.run_path(str(SKILLS / "vivado-ipi-assembler/scripts/compile_assembly_plan.py"))["ALLOWED_PROCS"]
        allowed = " ".join(sorted(registry))
        self.run_tcl(f'''
set inventory [ipiasm::api_inventory]
foreach name {{{allowed}}} {{
    if {{![dict exists $inventory $name]}} {{error "Whitelist helper missing: $name"}}
}}
foreach entry [list {documented}] {{
    lassign $entry name formals
    if {{![dict exists $inventory $name]}} {{error "Documented helper missing: $name"}}
    set names {{}}
    foreach formal $formals {{
        set argument [lindex $formal 0]
        lappend names $argument
        set optional [info default $name $argument value]
        if {{$optional != ([llength $formal] == 2)}} {{error "Optional argument drift: $name $argument"}}
        if {{$optional && $value ne [lindex $formal 1]}} {{error "Default drift: $name $argument"}}
    }}
    if {{$names ne [info args $name]}} {{error "Argument drift: $name"}}
}}
''')

    def test_preflight_and_structured_requirements_do_not_execute_calls(self):
        self.run_tcl('''
set calls 0
proc ipiasm::fixture_api {required {optional {with spaces}} args} {incr ::calls; error "MUST_NOT_RUN"}
foreach arguments {{required} {required optional} {required optional more values}} {
    set result [ipiasm::api_preflight [list [list ipiasm::fixture_api $arguments]]]
    if {[dict get $result status] ne "PASS"} {error "Valid arity refused"}
}
foreach entry {{ipiasm::fixture_api {}} {ipiasm::missing_helper {}} {ipiasm::gate_status {extra}}} {
    set result [ipiasm::api_preflight [list $entry]]
    if {[dict get $result status] ne "FAIL"} {error "Invalid call accepted"}
}
if {$calls} {error "Preflight executed mutation"}
if {[dict exists [ipiasm::api_inventory] ipcfg::_python_probe]} {error "Private helper exposed"}
set expected {spaces ; $literal [brackets] {nested}}
set reason "first line\nsecond line"
set ::ipcfg::req [dict create /cell [list [list CONFIG.TEXT $expected baseline $reason]]]
set before $::ipcfg::req
set record [lindex [ipcfg::requirements_data /cell] 0]
if {[dict get $record expected] ne $expected || [dict get $record reason] ne $reason ||
    [dict get $record base] ne "baseline" || $before ne $::ipcfg::req} {error "Lossy or mutating accessor"}
''')


class WorkflowPythonLauncher(unittest.TestCase):
    def test_hostile_environment_preserves_arguments_and_exit_code(self):
        with tempfile.TemporaryDirectory(prefix="offline python ") as directory:
            script = Path(directory) / "argument check.py"
            script.write_text("import os, sys\nassert all(name not in os.environ for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'PYTHONINSPECT'))\nprint(sys.argv[1])\nraise SystemExit(7)\n")
            environment = os.environ.copy()
            environment.update(IPCFG_PYTHON=sys.executable, PYTHONHOME="/nonexistent/home",
                               PYTHONPATH="/nonexistent/path", PYTHONSTARTUP="/nonexistent/startup",
                               PYTHONINSPECT="1", LD_LIBRARY_PATH="/nonexistent/libraries")
            result = subprocess.run(
                ["/bin/sh", str(SKILLS / "vivado-ip-configurator/lib/ipcfg/vivado_python.sh"),
                 str(script), "spaces ; $literal [brackets]"],
                env=environment, input="raise SystemExit(9)\n", capture_output=True,
                text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "spaces ; $literal [brackets]")

    def test_required_bundle_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment.pop("IPCFG_PYTHON", None)
            environment.update(IPCFG_VIVADO_ROOT=directory, IPCFG_REQUIRE_BUNDLED="1")
            result = subprocess.run(
                ["/bin/sh", str(SKILLS / "vivado-ip-configurator/lib/ipcfg/vivado_python.sh"),
                 "-c", "print('unexpected host fallback')"],
                env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("PYTHON_RUNTIME_FAIL", result.stderr)

    def test_discovers_vivado_on_path_without_host_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "tps/lnx64/python-fixture/bin"
            bundle.mkdir(parents=True)
            (bundle / "python3").symlink_to(sys.executable)
            executables = root / "bin"
            executables.mkdir()
            vivado = executables / "vivado"
            vivado.write_text("#!/bin/sh\nexit 99\n")
            vivado.chmod(0o755)
            environment = os.environ.copy()
            for key in ("IPCFG_PYTHON", "IPCFG_VIVADO_ROOT", "XILINX_VIVADO", "VIVADO_PATH", "IPCFG_REQUIRE_BUNDLED"):
                environment.pop(key, None)
            environment.update(PATH=str(executables))
            result = subprocess.run(
                ["/bin/sh", str(SKILLS / "vivado-ip-configurator/lib/ipcfg/vivado_python.sh"),
                 "-c", "import json, unittest; print('bundle selected')"],
                env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("bundle selected", result.stdout)
            script = f'''
source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
if {{[catch {{
    set runtime [ipcfg::python_runtime]
    if {{[dict get $runtime source] ne "vivado"}} {{error "Bundle not selected"}}
    puts [ipcfg::python_exec -c {{import json, unittest; print("bundle selected")}}]
}} detail]}} {{puts stderr $detail; exit 1}}
'''
            result = subprocess.run(
                [shutil.which("tclsh")], input=script, env=environment,
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("bundle selected", result.stdout)


class WorkflowPython(WorkflowLifecycle):
    def test_failure_contract_and_environment_absence(self):
        self.run_tcl(f'''
set ::env(IPCFG_PYTHON) {{{sys.executable}}}
foreach name {{PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT LD_LIBRARY_PATH}} {{
    unset -nocomplain ::env($name)
}}
set probes 0
trace add execution ipcfg::_python_probe enter {{apply {{{{args}} {{incr ::probes}}}}}}
if {{![string match {{PYTHON:OK *}} [ipcfg::python_status]]}} {{error "Preflight failed"}}
foreach {{program expected}} {{
    {{import ipcfg_intentionally_missing_dependency}} 1
    {{raise SystemExit(7)}} 7
}} {{
    if {{![catch {{ipcfg::python_exec -c $program}} message options]}} {{error "Failure hidden"}}
    set code [dict get $options -errorcode]
    if {{[lindex $code 0] ne "CHILDSTATUS" || [lindex $code 2] != $expected}} {{error "Lost exit code: $code"}}
    if {{$expected == 1 && ![string match *ModuleNotFoundError* $message]}} {{error "Lost import diagnostic"}}
    foreach name {{PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT LD_LIBRARY_PATH}} {{
        if {{[info exists ::env($name)]}} {{error "Absent environment variable leaked: $name"}}
    }}
}}
ipcfg::python_exec -c {{print("recovered")}}
if {{$probes != 1}} {{error "Repeated probes after child failure: $probes"}}
set ::env(IPCFG_REQUIRE_BUNDLED) 1
ipcfg::python_runtime
if {{$probes != 2}} {{error "Changed runtime key did not trigger preflight"}}
set ::env(IPCFG_PYTHON) /nonexistent/python
if {{![catch {{ipcfg::python_runtime}} message] || ![string match PYTHON_RUNTIME_FAIL:* $message]}} {{
    error "Invalid override fell back"
}}
unset ::env(IPCFG_PYTHON)
proc ipcfg::_python_bundle_candidates {{}} {{return {{}}}}
if {{![catch {{ipcfg::python_runtime}} message] || ![string match PYTHON_RUNTIME_FAIL:* $message]}} {{
    error "Unavailable bundle fell back despite requested bundle-only policy"
}}
''')

    def test_checked_script_entrypoint(self):
        with tempfile.TemporaryDirectory(prefix="python entry ") as directory:
            script = Path(directory) / "entry.py"
            script.write_text("import sys\nprint(sys.argv[1])\n")
            self.run_tcl(f'''
set ::env(IPCFG_PYTHON) {{{sys.executable}}}
set result [ipcfg::python_script {{{script}}} {{one argument}}]
if {{$result ne {{one argument}}}} {{error "Argument not preserved"}}
foreach path {{relative.py /nonexistent/script.py}} {{
    if {{![catch {{ipcfg::python_script $path}} message] ||
        ![string match PYTHON_SCRIPT_FAIL:* $message]}} {{error "Invalid script path accepted"}}
}}
''')

    def test_environment_restored_and_runtime_cached(self):
        with tempfile.TemporaryDirectory(prefix="python path ") as directory:
            script = Path(directory) / "argument check.py"
            script.write_text("import sys\nprint(sys.argv[1])\n")
            self.run_tcl(f'''
set ::env(IPCFG_PYTHON) {{{sys.executable}}}
foreach name {{PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT LD_LIBRARY_PATH}} {{
    set ::env($name) /nonexistent/inherited
}}
set saved [array get ::env]
set probes 0
trace add execution ipcfg::_python_probe enter {{apply {{{{args}} {{incr ::probes}}}}}}
set value [ipcfg::python_exec {{{script}}} {{argument with spaces}}]
if {{$value ne {{argument with spaces}}}} {{error "Lost argument boundary"}}
ipcfg::python_exec -c {{import argparse, csv, hashlib, json; print("ready")}}
if {{$probes != 1}} {{error "Repeated runtime discovery: $probes"}}
if {{![catch {{ipcfg::python_exec -c {{raise RuntimeError("fixture failure")}}}}]}} {{error "Failure hidden"}}
foreach {{name value}} $saved {{
    if {{![info exists ::env($name)] || $::env($name) ne $value}} {{error "Environment changed: $name"}}
}}
set ::env(IPCFG_PYTHON) /nonexistent/python
if {{![string match PYTHON_RUNTIME_FAIL:* [ipcfg::python_status]]}} {{error "Invalid override accepted"}}
''')


class BootTrafficContract(unittest.TestCase):
    def test_initial_boot_set_preserve_and_reject(self):
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n" + r'''
set stored {MC_0 {read_bw 1600 write_bw 1600}}
proc get_bd_intf_pins {args} {return /noc/S09_AXI}
proc get_property {args} {
    if {[lindex $args end-1] eq "CONFIG.CONNECTIONS"} {return $::stored}
    return BEST_EFFORT
}
proc set_property {args} {set ::stored [dict get [lindex $args 1] CONFIG.CONNECTIONS]}
proc ipiasm::qos_owner {pin} {return own}
if {[catch {
    set result [ipiasm::set_qos /noc/S09_AXI M03_AXI 5 5 BEST_EFFORT 4 true]
    if {![string match SUCCESS:* $result]} {error $result}
    if {![dict get $stored M03_AXI initial_boot] ||
        [dict get $stored M03_AXI read_avg_burst] != 4 ||
        [dict get $stored MC_0 read_bw] != 1600} {error "Incorrect boot route"}
    ipiasm::set_qos /noc/S09_AXI M03_AXI 6 6
    if {![dict get $stored M03_AXI initial_boot]} {error "Lost boot flag"}
    set before $stored
    set result [ipiasm::set_qos /noc/S09_AXI M02_AXI 5 5 BEST_EFFORT 4 invalid]
    if {![string match QOS_FAIL:* $result] || $stored ne $before} {
        error "Invalid flag changed routes"
    }
    ipiasm::set_qos /noc/S09_AXI M03_AXI 5 5 BEST_EFFORT 4 false
    if {[dict get $stored M03_AXI initial_boot]} {error "Cannot clear boot flag"}
} detail]} {puts stderr $detail; exit 1}
puts BOOT_TRAFFIC_OK
'''
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("BOOT_TRAFFIC_OK", result.stdout)


class UnusedTrafficContract(unittest.TestCase):
    def run_qos_case(self, command, expected):
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n" + r'''
set design design
proc current_project {} {return project}
proc current_bd_design {} {return $::design}
proc get_property {args} {return /tmp/qos-fixture}
proc ipiasm::_all_noc_cells {} {return noc}
proc ipiasm::noc_traffic_spec {} {return $::rows}
proc ipiasm::check_memory_subsystem {args} {return MEM:OK}
proc ipiasm::check_noc_solution {args} {return NOC:OK}
set rows {{noc/S12_AXI MC_0 1 1 BEST_EFFORT BEST_EFFORT isp/NMU own}}
''' + "\nif {[catch {\n" + command + r'''
} detail]} {puts stderr $detail; exit 1}
puts "RESULT:$result"
'''
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout.split("RESULT:")[-1].strip(), expected)

    def test_unused_reservation_passes_preaddress_and_counts_bandwidth(self):
        self.run_qos_case('ipiasm::set_unused_qos_routes {/noc/S12_AXI/MC_0 {No video source}}; set result [ipiasm::check_noc_pre_address 4]', r'NOC_PRE:OK[\s\S]*declared=1 total_rd=1MB/s total_wr=1MB/s isochronous=0[\s\S]*unused=1')

    def test_undeclared_zero_fails_even_with_allow(self):
        self.run_qos_case('lset rows 0 2 0; lset rows 0 3 0; set result [ipiasm::check_qos {*}]', r'QOS_FAIL:.*BOTH directions')

    def test_wrong_unused_bandwidth_fails(self):
        for read_bw, write_bw in ((0, 0), (0, 1), (1, 0), (14224, 14224), (1, 500)):
            with self.subTest(read_bw=read_bw, write_bw=write_bw):
                self.run_qos_case(f'ipiasm::set_unused_qos_routes {{noc/S12_AXI/MC_0 unused}}; lset rows 0 2 {read_bw}; lset rows 0 3 {write_bw}; set result [ipiasm::check_qos]', r'QOS_FAIL:declared-unused.*1/1 MB/s BEST_EFFORT')

    def test_wrong_unused_class_fails(self):
        for column in (4, 5):
            with self.subTest(column=column):
                self.run_qos_case(f'ipiasm::set_unused_qos_routes {{noc/S12_AXI/MC_0 unused}}; lset rows 0 {column} ISOCHRONOUS; set result [ipiasm::check_qos]', r'QOS_FAIL:declared-unused.*BEST_EFFORT')

    def test_missing_route_fails(self):
        self.run_qos_case('ipiasm::set_unused_qos_routes {noc/S13_AXI/MC_0 unused}; set result [ipiasm::check_qos]', r'QOS_FAIL:declared-unused.*missing')

    def test_nonowning_unused_fails(self):
        for owner in ('ignored', 'unresolved'):
            with self.subTest(owner=owner):
                self.run_qos_case(f'ipiasm::set_unused_qos_routes {{noc/S12_AXI/MC_0 unused}}; lset rows 0 7 {owner}; set result [ipiasm::check_qos]', r'QOS_FAIL:declared-unused.*owner=')

    def test_scope_and_clear(self):
        for change in ('set design other', 'ipiasm::set_unused_qos_routes {}'):
            with self.subTest(change=change):
                self.run_qos_case('ipiasm::set_unused_qos_routes {noc/S12_AXI/MC_0 unused}; ' + change + '; set result [ipiasm::check_qos]; if {[string match *unused=* $result]} {error "Stale unused intent"}', r'QOS:OK declared=1')

    def test_declaration_validation(self):
        for routes in ('{noc/*/MC_0 unused}', '{noc/S12_AXI/MC_0 { }}', '{noc/S12_AXI/MC_0 unused /noc/S12_AXI/MC_0 duplicate}'):
            with self.subTest(routes=routes):
                self.run_qos_case('set result [ipiasm::set_unused_qos_routes ' + routes + ']', r'QOS_FAIL:unused routes require')

    def test_active_one_way_and_default_checks_preserved(self):
        self.run_qos_case('lset rows 0 2 0; lset rows 0 3 750; set result [ipiasm::check_qos]', r'QOS:OK declared=1 total_rd=0MB/s total_wr=750MB/s')

    def test_the_500_500_seed_is_reported_but_does_not_block(self):
        # The reference design leaves 61 of its 92 destination entries at exactly
        # 500/500, sizing explicitly only where the contract names a rate. Failing
        # the gate on the seed refused to address the gold design and blocked entry
        # into step 8, so it must surface as a note on a passing gate.
        self.run_qos_case('lset rows 0 2 500; lset rows 0 3 500; set result [ipiasm::check_qos]', r'QOS:OK')
        self.run_qos_case('lset rows 0 2 500; lset rows 0 3 500; set result [ipiasm::check_qos]', r'NOTE: 1 connection\(s\) sit at Vivado')
        self.run_qos_case('lset rows 0 2 500; lset rows 0 3 500; set result [ipiasm::check_qos]', r'(?s)^(?!.*QOS_FAIL).*$')

    def test_unused_does_not_exempt_active_routes(self):
        self.run_qos_case('ipiasm::set_unused_qos_routes {noc/S12_AXI/MC_0 unused}; lappend rows {noc/S11_AXI MC_0 0 0 ISOCHRONOUS ISOCHRONOUS camera/NMU own}; set result [ipiasm::check_qos]', r'QOS_FAIL:1 owning connection')

    def test_declaration_closes_gate(self):
        self.run_qos_case('ipiasm::_gate_set qos 1; ipiasm::set_unused_qos_routes {noc/S12_AXI/MC_0 unused}; set result [ipiasm::_gate_get qos]', r'^0$')


class ProcedureContracts(unittest.TestCase):
    observations = []
    inventory = set()
    outcomes = {}

    @classmethod
    def setUpClass(cls):
        cls.observations = []
        cls.inventory = set()
        cls.outcomes = {}

    @classmethod
    def tearDownClass(cls):
        live_observations = []
        live_source = os.environ.get("IPCFG_LIVE_TIMING_REPORT")
        if live_source:
            live_inventory = set()
            live_entries = Counter()
            for line in Path(live_source).read_text().splitlines():
                fields = line.split()
                if line.startswith("COV_DECL "):
                    live_inventory.add(fields[1])
                elif line.startswith("COV_HIT "):
                    live_entries[fields[1]] += 1
                elif line.startswith("COV_TIME "):
                    _, procedure, inclusive, exclusive, return_code, label = fields
                    live_observations.append({
                        "procedure": procedure, "inclusive_us": int(inclusive),
                        "exclusive_us": int(exclusive), "return_code": int(return_code),
                        "test": label,
                    })
            if live_inventory != cls.inventory:
                raise AssertionError("Live timing inventory differs from tested libraries")
            if live_entries != Counter(entry["procedure"] for entry in live_observations):
                raise AssertionError("Live timing has unmatched procedure entries")
            if any(not 0 <= entry["exclusive_us"] <= entry["inclusive_us"] for entry in live_observations):
                raise AssertionError("Invalid live timing duration")
        procedures = {}
        for name in sorted(cls.inventory):
            calls = [entry for entry in cls.observations if entry["procedure"] == name]
            durations = [entry["inclusive_us"] for entry in calls]
            procedures[name] = {
                "calls": len(calls),
                "inclusive_total_us": sum(durations),
                "inclusive_min_us": min(durations) if durations else None,
                "inclusive_max_us": max(durations) if durations else None,
                "inclusive_mean_us": sum(durations) / len(durations) if durations else None,
                "exclusive_total_us": sum(entry["exclusive_us"] for entry in calls),
                "tests": sorted({entry["test"] for entry in calls}),
            }
            live_calls = [entry for entry in live_observations if entry["procedure"] == name]
            live_durations = [entry["inclusive_us"] for entry in live_calls]
            procedures[name]["live"] = {
                "calls": len(live_calls),
                "inclusive_total_us": sum(live_durations),
                "inclusive_min_us": min(live_durations) if live_durations else None,
                "inclusive_max_us": max(live_durations) if live_durations else None,
                "inclusive_mean_us": sum(live_durations) / len(live_durations) if live_durations else None,
                "exclusive_total_us": sum(entry["exclusive_us"] for entry in live_calls),
                "tests": sorted({entry["test"] for entry in live_calls}),
            }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({
            "scope": "Original Tcl library procedures; isolated mocked empty-design contract tests",
            "timing": "Instrumented tclsh elapsed microseconds; includes trace overhead; not live Vivado performance",
            "total": len(procedures),
            "executed": sum(bool(value["calls"]) for value in procedures.values()),
            "procedures": procedures,
            "observations": cls.observations,
            "case_outcomes": cls.outcomes,
            "live_source": live_source,
            "live_observations": live_observations,
            "live_executed": len({entry["procedure"] for entry in live_observations}),
        }, indent=2) + "\n")
        lines = [
            "# Assembler Procedure Coverage And Timing", "",
            f"Executed and timed: {sum(bool(value['calls']) for value in procedures.values())}/{len(procedures)} procedures.",
            "Scope: all assembler and configurator procedures, including private helpers.",
            "Fixture: isolated tclsh processes, mocked Vivado primitives; original library bodies are not replaced.",
            "Hardware-dependent procedures often exercise missing-resource, empty-design or gate-refusal paths.",
            "This is procedure-entry coverage, not complete branch coverage or successful hardware integration.",
            "Times are elapsed microseconds with tracing overhead, not live Vivado performance estimates.",
            "Inclusive time includes traced child calls; exclusive time subtracts them. Totals across procedures overlap.",
            "Autofix regression: closed inventory or applicable NoC QoS gates return GATE_FAIL before any repair helper runs.",
            f"Contract cases: {sum(value == 'PASS' for value in cls.outcomes.values())} passed; {sum(value != 'PASS' for value in cls.outcomes.values())} failed.",
            "", "| Procedure | Calls | Total us | Mean us | Min us | Max us | Exclusive us | Tests |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for name, data in procedures.items():
            mean = f"{data['inclusive_mean_us']:.2f}" if data['calls'] else "-"
            lines.append(
                f"| `{name}` | {data['calls']} | {data['inclusive_total_us']} | {mean} | "
                f"{data['inclusive_min_us']} | {data['inclusive_max_us']} | "
                f"{data['exclusive_total_us']} | {', '.join(data['tests'])} |"
            )
        if live_source:
            lines.extend([
                "", "## Native Vivado Timing", "",
                f"{len({entry['procedure'] for entry in live_observations})}/{len(procedures)} procedures timed in the four live IP groups.",
                "Separate workload; includes instrumentation overhead. Not a speedup comparison or whole-system benchmark.",
                "Unlisted procedures have no live timing, not zero execution cost. The JSON retains every call and return code.",
                "", "| Procedure | Calls | Total us | Mean us | Min us | Max us | Exclusive us | Groups |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ])
            for name, record in procedures.items():
                data = record["live"]
                if data["calls"]:
                    lines.append(
                        f"| `{name}` | {data['calls']} | {data['inclusive_total_us']} | "
                        f"{data['inclusive_mean_us']:.2f} | {data['inclusive_min_us']} | "
                        f"{data['inclusive_max_us']} | {data['exclusive_total_us']} | {', '.join(data['tests'])} |"
                    )
        REPORT.with_suffix(".md").write_text("\n".join(lines) + "\n")

    def test_zz_every_procedure_has_completed_timing(self):
        if not self.observations:
            self.skipTest("Completeness gate requires the full contract suite")
        timed = {entry["procedure"] for entry in self.observations}
        self.assertEqual(timed, self.inventory, f"Untimed: {sorted(self.inventory - timed)}")
        for entry in self.observations:
            self.assertGreaterEqual(entry["inclusive_us"], entry["exclusive_us"])
            self.assertGreaterEqual(entry["exclusive_us"], 0)

    def run_case(self, name, command, expected, setup="", code=0):
        self.outcomes[name] = "FAIL"
        with tempfile.TemporaryDirectory() as root:
            script = f'''
source {{{SKILLS / "vivado-ip-configurator/lib/ipcfg/ipcfg.tcl"}}}
source {{{SKILLS / "vivado-ipi-assembler/lib/ipiasm.tcl"}}}
source {{{TESTS / "support/procedure_trace.tcl"}}}
set root {{{root}}}
cd $root
set ::ipcfg::cache_file [file join $root cache.json]
set ::procedure_trace::label {name}
proc current_project {{args}} {{return project}}
proc current_bd_design {{args}} {{return design}}
proc current_bd_instance {{args}} {{return /}}
proc get_property {{args}} {{
    set key [lindex $args end-1]
    switch -- $key {{
        DIRECTORY {{return $::root}}
        PART {{return fixture_part}}
        FAMILY {{return fixture_family}}
        default {{return {{}}}}
    }}
}}
foreach getter {{get_bd_cells get_bd_pins get_bd_intf_pins get_bd_ports
    get_bd_intf_ports get_bd_nets get_bd_intf_nets get_bd_addr_spaces
    get_bd_addr_segs get_ipdefs get_ips get_parts get_files get_board_parts
    get_board_part_interfaces get_board_components get_board_interface_ports
    get_board_part_pins get_board_part_presets list_property get_msg_config}} {{
    proc $getter {{args}} {{return {{}}}}
}}
proc set_property {{args}} {{error FIXTURE_MUTATION_FORBIDDEN}}
proc save_bd_design {{args}} {{return {{}}}}
proc validate_bd_design {{args}} {{return {{}}}}
proc report_property {{args}} {{return {{}}}}
proc get_param {{args}} {{return {{}}}}
proc version {{args}} {{return 2026.1}}
{setup}
set code [catch {{{command}}} result options]
puts "CASE_RESULT $code [binary encode base64 -maxlen 0 $result]"
puts "TRACE_FRAMES [llength $::procedure_trace::frames]"
'''
            result = subprocess.run(
                ["tclsh"], input=script, text=True, capture_output=True, timeout=20,
            )
        observed = None
        for line in result.stdout.splitlines():
            if line.startswith("COV_DECL "):
                self.inventory.add(line.split()[1])
            elif line.startswith("COV_TIME "):
                _, procedure, inclusive, exclusive, return_code, label = line.split()
                self.observations.append({
                    "procedure": procedure, "inclusive_us": int(inclusive),
                    "exclusive_us": int(exclusive), "return_code": int(return_code),
                    "test": label,
                })
            elif line.startswith("CASE_RESULT "):
                _, status, encoded = line.split(" ", 2)
                observed = (int(status), base64.b64decode(encoded).decode())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(observed, result.stdout + result.stderr)
        self.assertEqual(observed[0], code, f"{command}: {observed[1]}")
        self.assertRegex(observed[1], "(?s)" + expected, command)
        self.assertIn("TRACE_FRAMES 0", result.stdout.splitlines())
        entries = Counter(line.split()[1] for line in result.stdout.splitlines() if line.startswith("COV_HIT "))
        completed = Counter(line.split()[1] for line in result.stdout.splitlines() if line.startswith("COV_TIME "))
        self.assertEqual(entries, completed)
        self.outcomes[name] = "PASS"


CASES = {
    "clock_candidate_bad_plan": ('ipiasm::realize_clock_candidates {status invalid} {} /tmp', r'CLOCK_REALIZE_FAIL:'),
    "clock_mhz_precision": ('ipiasm::_clock_mhz 22579200', r'^22\.579200$'),
    "clock_candidate_config_error": ('ipiasm::_configure_clock_candidate absent {} input source', r'.*', '', 1),
    "clock_contract_registration": ('ipiasm::set_clock_contracts {} {} {} {}', r'CLOCK_CONTRACT_FAIL:'),
    "propagation_current_follows_is_validated": (
        "set ::ipiasm::design_validated 0; lappend out [ipiasm::_propagation_current]; "
        "set ::ipiasm::design_validated 1; lappend out [ipiasm::_propagation_current]; "
        "set ::props {NAME}; lappend out [ipiasm::_propagation_current]; set out",
        r"^0 1 0$",
        "proc current_bd_design {args} {return d}; set ::props {IS_VALIDATED NAME}; "
        "proc list_property {args} {return $::props}; proc get_property {args} {return 1}"),
    "same_param_value": (
        "list [ipcfg::_same_param_value 0x0000007F 0x7f] [ipcfg::_same_param_value 010 10] "
        "[ipcfg::_same_param_value FALSE 0] [ipcfg::_same_param_value 6 12] [ipcfg::_same_param_value None NA]",
        r"^1 1 1 0 0$"),
    "bundle_irqs_without_a_processing_system": (
        "ipiasm::validate_bundle 0 {} 5 0", r"IRQ_CAPACITY:NA no processing system in this design; 5 interrupt",
        "ipiasm::_gate_set inventory 1 fixture"),
    "freq_hz_reads_double_form": (
        "ipiasm::feasibility::freq_hz /lpddrmc/pll_clkout0", r"^266525000$",
        "proc list_property {args} {return CONFIG.FREQ_HZ}; proc get_property {args} {return 2.66525e+08}"),
    "freq_hz_refuses_text": (
        "ipiasm::feasibility::freq_hz /lpddrmc/pll_clkout0", r"^FEASIBILITY_FAIL:expected a frequency in Hz",
        "proc list_property {args} {return CONFIG.FREQ_HZ}; proc get_property {args} {return unknown}", 1),
    "clock_fingerprint_relative_paths": ('ipiasm::clock_fingerprint out spec.json', r'^CLOCK_FINGERPRINT_FAIL:absolute'),
    "clock_input_driver_absent": ('ipiasm::_input_driver /absent/clk', r'.*', '', 1),
    "clock_contracts_from_absent_fingerprint": (
        'ipiasm::register_clock_contracts {{id c0 cell /clk expected 100000000}} /tmp/absent_fingerprint.tcldict',
        r'^CLOCK_CONTRACT_FAIL:'),
    "clock_preflight_empty_plan": ('ipiasm::preflight_clock_plan {} {}', r'^CLOCK_PREFLIGHT:OK proven=0 pending=0$'),
    "clock_preflight_batches_defects": (
        'ipiasm::preflight_clock_plan {{id c0} {id c1}} {{cell /rst}}',
        r'^CLOCK_PREFLIGHT_FAIL:3 defect\(s\):.*clock c0:.*clock c1:.*reset /rst:'),
    "clock_contracts_save_unregistered": (
        'ipiasm::save_clock_contracts /tmp/unwritten.tcldict',
        r'^CLOCK_CONTRACTS_SAVE_FAIL:nothing registered$'),
    "clock_contracts_load_missing": (
        'ipiasm::load_clock_contracts /tmp/absent.tcldict',
        r'^CLOCK_CONTRACTS_LOAD_FAIL:cannot read '),
    "clock_search_rejects_invalid": ('ipcfg::search_clock_config v src {{id a hz 0}} /tmp/clksearch', r'^CLOCK_SEARCH:ERROR:.*positive integer'),
    "clock_search_scalar_wizard_config": (
        'ipcfg::clocksearch::config {{drive BUFG taps {1 a} requested_hz 200000000} '
        '{drive BUFG taps {1 b} requested_hz 33333333}} {outputs 3 taps 0 input 1 schema scalar} explicit 100000000',
        r'^CONFIG\.CLKOUT1_USED true CONFIG\.CLKOUT1_REQUESTED_OUT_FREQ 200 CONFIG\.CLKOUT2_USED true '
        r'CONFIG\.CLKOUT2_REQUESTED_OUT_FREQ 33\.333333 CONFIG\.CLKOUT3_USED false CONFIG\.PRIM_IN_FREQ 100$'),
    "clock_search_vector_wizard_config": (
        'ipcfg::clocksearch::config {{drive MBUFGCE taps {1 a 2 b} requested_hz 300000000}} '
        '{outputs 2 taps 1 input 1 schema vector} propagate 100000000',
        r'^CONFIG\.CLKOUT_USED true,false CONFIG\.CLKOUT_REQUESTED_OUT_FREQUENCY 300,100 '
        r'CONFIG\.CLKOUT_DRIVES MBUFGCE,BUFG$'),
    "help_classifies_failure_tokens": (
        'ipcfg::help search_clock_config',
        r'ok: CLOCK_SEARCH:PASS +fail: CLOCK_SEARCH:ERROR CLOCK_SEARCH:FAIL'),
    "native_guard_untrusted_cell_refused": (
        'list [ipiasm::_native_guard create_bd_cell 0] [ipiasm::_native_guard create_bd_cell 1] [ipiasm::_trusted_caller] '
        '[ipiasm::_trusted_file [file join $::ipiasm::library_dir ipiasm.tcl]] [ipiasm::_trusted_file /tmp/agent.tcl] '
        '[catch {ipiasm::_probe_create v c}]',
        r"^\{GATE_FAIL:create_bd_cell requires the 'plan' gate .*only in a scratch project under <out>/.ipiasm/.*\} "
        r"\{GATE_FAIL:create_bd_cell requires the 'plan' gate .*\} 0 1 0 [01]$"),
    "clock_requirements_weakened_by_tolerance": (
        "ipiasm::_clock_requirements_weakened "
        "{checks {{id c kind clock_frequency subject /a/clk expected 300 source s stage clocks tolerance_hz 0}} resets {} context x} "
        "{checks {{id c kind clock_frequency subject /a/clk expected 300 source s stage clocks tolerance_hz 5}} resets {} context x}",
        r"^clock c: tolerance_hz 0 -> 5$"),
    "mem_sysclk_from_ports_unwired": ("ipiasm::_mem_sysclk_from_ports /noc {/noc/sys_clk0}", r"^MEM_SYSCLK_FAIL:no PS hsm\*_ref_clk output, and \{/noc/sys_clk0\} not driven by a top-level clock port$"),
    "deliver_refuses_without_a_passing_gate": (
        "proc ipiasm::final_contract_gate_from_plan {args} {return \"FINAL_CONTRACT_FAIL:1 check(s)\\n  PLAN_EXECUTION_FAIL:none\"}; "
        "ipiasm::deliver [file join $root out]",
        r"^DELIVER_FAIL:final gate: FINAL_CONTRACT_FAIL:1 check\(s\) \|   PLAN_EXECUTION_FAIL:none$"),
    "deliver_names_the_failing_check_past_passing_ones": (
        "proc ipiasm::final_contract_gate_from_plan {args} {return [join [concat {{FINAL_CONTRACT_FAIL:1 check(s)}} "
        "[lrepeat 12 {  w WINDOW_SCOPE:OK required=1}] {{  VALIDATE:PASS} {ADVISORY excluded segments (2)} "
        "{  REFERENCE_COVERAGE:FAIL 1 functional IP(s)} {  UNEXPLAINED axi_gpio: add it}}] \\n]}; "
        "ipiasm::deliver [file join $root out]",
        r"^DELIVER_FAIL:final gate: FINAL_CONTRACT_FAIL:1 check\(s\) \|   REFERENCE_COVERAGE:FAIL 1 functional IP\(s\) \|   UNEXPLAINED axi_gpio: add it$"),
    "gate_failures_lead_with_failure_markers": (
        "join [ipiasm::_gate_failures [list {  NOC:OK ddr_windows=2} {  the .ncr on disk is OLDER than the block design} "
        "{  AIE:OK 1 AI Engine config path(s):} {  aie/S00_AXI <- memory_noc/M00_AXI} "
        "{  COVERAGE_FAIL:1/9 required contract(s) have no passing readback deferred={}} "
        "{  functional_contracts.visibility_policy[0]: needs ipiasm::check_visibility -> VISIBILITY:OK}]] |",
        r"^  COVERAGE_FAIL:1/9 [^|]*\|  functional_contracts\.visibility_policy\[0\]: needs [^|]*\|"
        r"  the \.ncr on disk is OLDER than the block design\|  aie/S00_AXI <- memory_noc/M00_AXI$"),
    "gate_failures_skip_note_paragraphs": (
        "join [ipiasm::_gate_failures [list {  CLOCK_CONTRACTS:OK clocks=5} "
        "{  NOTE: no connection asks for ISOCHRONOUS. Every path here is} {        best-effort, so a live path}  "
        "{  NOTE: 2 connection(s) sit at Vivado's 500/500 MB/s seed:} "
        "{  memory_noc/S00_AXI/MC_0 (driven by processing_system/FPD_AXI_NOC0)} "
        "{  memory_noc/S01_AXI/MC_0 (driven by isp/TILE0_ISP0_NMU)} {        That is a valid choice} "
        "{        (UG994: the NoC compiler solves only against what you declare). Silence} "
        "{  window_isp WINDOW_SCOPE:OK required=1} {  window_aie VISIBILITY_FAIL:missing=2 reached=11} "
        "{ADVISORY excluded segments (2) (intentional, not a gap):} {  /mc/S0_AXI/ADDRESS_BLOCK} {  /mc/S1_AXI/ADDRESS_BLOCK} "
        "{  COVERAGE_FAIL:1/18 required contract(s) have no passing readback deferred={}} "
        "{  functional_contracts.memory_reachability[0]: needs required_dram_masters -> DRAM_REACH:OK} "
        "{  REFERENCE_COVERAGE:FAIL 1 functional IP(s)} {  UNEXPLAINED axi_gpio: add it}]] |",
        r"^  window_aie VISIBILITY_FAIL:missing=2 reached=11\|  COVERAGE_FAIL:1/18 .*deferred=\{\}\|"
        r"  functional_contracts\.memory_reachability\[0\]: needs required_dram_masters -> DRAM_REACH:OK\|"
        r"  REFERENCE_COVERAGE:FAIL 1 functional IP\(s\)\|  UNEXPLAINED axi_gpio: add it$"),
    "deliver_gates_the_plan_in_out_before_it_is_recorded": (
        "file mkdir $root/out; close [open $root/out/assembly_plan.json w]; set ::ipiasm::executed_plan {}; "
        "proc ipiasm::final_contract_gate_from_plan {plan spec} {return \"FINAL_CONTRACT_FAIL:1 check(s)\\n  gated [file tail [file dirname $plan]]/[file tail $plan] [file tail $spec]\"}; "
        "ipiasm::deliver [file join $root out]",
        r"^DELIVER_FAIL:final gate: FINAL_CONTRACT_FAIL:1 check\(s\) \|   gated out/assembly_plan\.json design_spec\.json$"),
    "part_examples_one_per_family": (
        "proc get_ipdefs {args} {return v:ip:x:1.0}; proc get_parts {args} {return [lindex $args end]}; "
        "proc get_property {args} {return {xck26-a xck26-b xcvc1902-c xcsu35p-d}}; ipcfg::part_examples v:ip:x:1.0 2",
        r"^supported_count 4 example_parts \{xck26-a xcsu35p-d\}$"),
    "part_cache_identity": (
        "set ::env(IPCFG_PART_CACHE) /x/part.tcldict; list [ipcfg::_part_cache_file] [string match */* [ipcfg::_build_key]]",
        r"^/x/part.tcldict 1$"),
    "board_interface_choices_try_preferred_interfaces": (
        "proc current_project {args} {return p}; proc get_board_part_interfaces {args} {return {mem clk led}}; "
        "proc get_property {args} {lassign [lrange $args end-1 end] name object; switch -- $name {"
        " BOARD_PART {return b} NAME {return $object}"
        " PREFERENCES {return [dict get {mem x:ip:mc:* clk {y:ip:z:* x:ip:mc:*} led x:ip:gpio:*} $object]}"
        " default {return Custom}}}; "
        "proc create_ip {args} {}; proc get_ips {args} {return ip}; proc get_files {args} {}; proc remove_files {args} {}; "
        "proc set_msg_config {args} {}; proc reset_msg_config {args} {}; "
        "proc list_property {args} {return {CONFIG.MEM_BOARD_INTERFACE CONFIG.SYSCLK_BOARD_INTERFACE}}; "
        "proc set_property {name value object} {if {$value ni {Custom} && "
        "[list $name $value] ni {{CONFIG.MEM_BOARD_INTERFACE mem} {CONFIG.SYSCLK_BOARD_INTERFACE clk}}} {error rejected}}; "
        "ipiasm::_board_interface_choices x:ip:mc:1.0",
        r"^config \{CONFIG\.MEM_BOARD_INTERFACE mem CONFIG\.SYSCLK_BOARD_INTERFACE clk\} ambiguous \{\}$"),
    "native_failure_note_leads_with_critical_ids_and_repair": (
        "ipiasm::_native_failure_note [list "
        "{WARNING: [BD 41-702] Propagation TCL tries to overwrite USER strength parameter W(6) on '/r' with propagated value(12). Command ignored} "
        "{CRITICAL WARNING: [BD 41-1348] Reset pin /t0/rst_n (associated clock /t0/clk) is connected to asynchronous reset source /rst_in.} "
        "{CRITICAL WARNING: [BD 41-1348] Reset pin /t1/rst_n (associated clock /t1/clk) is connected to asynchronous reset source /rst_in.}]",
        r"^native messages fail it \(3, 2 error/critical\): BD 41-1348 x2 \(a reset pin is asynchronous[^;]*; "
        r"ipiasm::ensure_reset_domain <reset-cell> <clock-pin> /rst_in \{/t0/rst_n /t1/rst_n\} in the clocks stage\): "
        r"CRITICAL WARNING: \[BD 41-1348\] Reset pin /t0/rst_n .*; CRITICAL WARNING: .*/t1/rst_n.*; WARNING: \[BD 41-702\]"),
    "example_reference_needs_a_project": ("proc current_project {args} {return {}}; ipiasm::example_reference xilinx.com:ip:xdma /tmp/x",
        r"^EXAMPLE_REF_FAIL:open the scratch project first$"),
    "scratch_bind_without_intent": ("ipiasm::scratch_bind /nonexistent/out", r"^SCRATCH_BIND_FAIL:no binding_intent.json in /nonexistent/out$"),
    "reference_precision_rounded": ('ipcfg::reference_precision 11.111', r'^REF_PRECISION:ROUNDED value=11\.111 full=11\.111111111 ratio=100/9 '),
    "preset_drift_without_board": ('ipcfg::preset_drift missing', r'^PRESET_CHECK_ERROR:'),
    "rule_options_unloaded": ('ipcfg::automation_rule_options missing xilinx.com:bd_rule:none_such', r'^RULE_OPTIONS_ERROR:rule .* is not loaded'),
    "pinned_reset_none": ("ipcfg::pinned_reset nothing", r"^$"),
    "clock_placement_without_search": ("ipcfg::clock_placement_status", r"^CLOCK_PLACEMENT:ERROR:no clock search has run$"),
    "script_check_names_unknown_commands": (
        "list [ipcfg::script_check [file join $root s.tcl]] [ipcfg::script_check [file join $root ok.tcl]] [ipcfg::script_check [file join $root none.tcl]]",
        r"^\{SCRIPT_CHECK_FAIL:s.tcl calls command\(s\) that do not exist here: \{get_made_up_rules ipcfg::made_up\}.*\} \{SCRIPT_CHECK:OK ok.tcl\} \{SCRIPT_CHECK_FAIL:cannot read",
        'set c [open [file join $root s.tcl] w]; puts $c "# ipcfg::in_comment\\nset ::ipcfg::a_variable 1\\nipcfg::made_up x\\nset r \\[get_made_up_rules -of y\\]\\nproc local_helper {} {}\\nlocal_helper\\nregexp {\\[A-Z\\]} x"; close $c; '
        'set c [open [file join $root ok.tcl] w]; puts $c "proc my_step {} {return 1}\\nset v \\[my_step\\]\\nipcfg::help board"; close $c'),
    "help_lists_call_shape_and_results": ("ipcfg::help board_bind",
        r"^ipcfg::board_bind cell binding \?extra=\{\}\? \?reason=board preset\?\n    .*\n    ok: BOARD:bound   fail: BOARD_FAIL"),
    "help_unknown": ("ipcfg::help zz_no_such", r"^HELP:none match"),
    "camera_format_checked_before_wiring": (
        "list [ipiasm::check_camera_isp_format] [ipiasm::_tile_format_props /isp 0]",
        r"^\{FORMAT_FAIL:1/1 camera/ISP format mismatch \(planned ISP-bridge link, not yet wired\).*"
        r"RAW12 into isp tile 0, which is set to '16' \(CONFIG.C_TILE0_ISP0_BASIC_DATA_FORMAT\).*\} "
        r"\{CONFIG.C_TILE0_ISP0_BASIC_DATA_FORMAT CONFIG.C_TILE0_ISP1_BASIC_DATA_FORMAT\}$",
        'proc get_bd_cells {args} {if {[string match *mipi* $args]} {return /cam} '
        'elseif {[string match *visp* $args]} {return /isp} else {return {}}}\n'
        'proc list_property {args} {return {CONFIG.C_TILE0_ISP0_BASIC_DATA_FORMAT CONFIG.C_TILE0_ISP1_BASIC_DATA_FORMAT '
        'CONFIG.C_TILE1_ISP0_BASIC_DATA_FORMAT}}\n'
        'proc get_property {args} {switch -- [lindex $args end-1] {CONFIG.C_SPRT_ISP_BRIDGE {return true} '
        'CONFIG.CMN_PXL_FORMAT {return RAW12} NAME {return [string trimleft [lindex $args end] /]} '
        'default {return 16}}}'),
    "dependent_param_named_from_native_log": (
        "set ::ipcfg::native_log_path [file join $root native.log]; "
        "set c [open $::ipcfg::native_log_path w]; puts $c start; close $c; set n [ipcfg::_native_log_size]; "
        "set c [open $::ipcfg::native_log_path a]; puts $c {ERROR: [IP_Flow 19-3478] Validation failed for parameter "
        "'Enable Overdrive(C_ENABLE_OVERDRIVE)' with current value '0' for BD Cell 'isp'. Please Enable Overdrive voltage}; close $c; "
        "list [ipcfg::_dependent_params [ipcfg::_native_log_since $n] {CONFIG.C_CLK 720}] "
        "[ipcfg::_dependent_params [ipcfg::_native_log_since $n] {CONFIG.C_ENABLE_OVERDRIVE 1}] [ipcfg::_native_log_since -1]",
        r"^\{CONFIG\.C_ENABLE_OVERDRIVE \(now '0': Please Enable Overdrive voltage\)\} \{\} \{\}$"),
    "create_cell_swapped_arguments": (
        "ipiasm::gate_override board fixture; list [ipiasm::create_cell mailbox xilinx.com:ip:axi_bram_ctrl]",
        r"CONFIGURE_FAIL:ARGUMENT_ORDER:'mailbox' is not an IP type"),
    "require_board_value_needs_spec_statement": (
        "list [ipcfg::require /cam {CONFIG.LANES 4} lanes] [dict exists $::ipcfg::board_override /cam] "
        "[ipcfg::require /cam {CONFIG.LANES 4} lanes {} {spec camera: 4 lanes}] [dict get $::ipcfg::board_override /cam]",
        r"^\{REQ_FAIL:/cam pinned but NOT written.*BOARD_OWNED.*5th argument board_override.*\} 0 "
        r"\{REQ:pinned\+applied 1 .*OVERRODE the board preset on LANES because 'spec camera: 4 lanes'.*\} "
        r"\{\{CONFIG.LANES 4 \{spec camera: 4 lanes\}\}\}$",
        'proc ipcfg::apply_dict {cell d {allow 0}} {expr {$allow ? "SUCCESS:set" : "CONFIGURE_FAIL:BOARD_OWNED:LANES"}}'),
    "irq_paths_empty": ("list [ipiasm::check_irq_paths {}] [ipiasm::check_irq_paths {{a/b CONST:0}}] [ipiasm::_irq_source /x/pin] [ipiasm::_irq_width /x/pin] [ipiasm::set_irq_contracts {}] [ipiasm::check_irq_contracts]",
        r"^\{IRQ_PATHS_FAIL:no interrupt slots given\} \{IRQ_PATHS_FAIL:1/1 slot\(s\)\n  a/b: no such pin\} UNCONNECTED 1 \{IRQ_CONTRACT_FAIL:empty interrupt table is not acceptance\} \{IRQ_CONTRACTS:NA not registered\}$"),
    "record_evidence_rejects": ("list [ipiasm::record_evidence l {bogus} OK] [ipiasm::record_evidence l {interrupts} X_FAIL] [ipiasm::record_evidence l {} OK]",
        r"^\{EVIDENCE_FAIL:unknown category bogus.*\} \{EVIDENCE_FAIL:only a passing result is evidence: X_FAIL\} \{EVIDENCE_FAIL:name the DesignSpec categories"),
    "board_repo_rejects": ("list [ipiasm::register_board_repo relative/dir] [ipiasm::register_board_repo [file join $root none]] [ipiasm::register_board_repo $root]",
        r"^\{BOARD_REPO_FAIL:give an absolute.*\} \{BOARD_REPO_FAIL:.* is not a directory\} \{BOARD_REPO_FAIL:no board.xml under"),
    "create_cell_wrong_part": ("ipcfg::create_cell v:ip:mem mem",
        # Without a release database there is no catalog walk (it read SUPPORTED_PARTS of look-alike IP
        # and took minutes); the refusal points to ipcfg::ip_availability instead.
        r"^CONFIGURE_FAIL:WRONG_PART:v:ip:mem:1.0 is not supported on fixture_part .*ipcfg::ip_availability",
        'proc get_ipdefs {args} {set p [lindex $args end]; if {$p eq "*"} {return {v:ip:mem:1.0 v:ip:alt:1.0 v:ip:far:1.0}}; if {$p eq "v:ip:mem"} {return v:ip:mem:1.0}; return {}}; '
        'proc get_property {args} {set k [lindex $args end-1]; set o [lindex $args end]; '
        'switch -- $k {PART {return fixture_part} SUPPORTED_PARTS {return [expr {$o eq "v:ip:alt:1.0" ? "" : "other_part"}]} '
        'DISPLAY_NAME {return [dict get {v:ip:mem:1.0 {Block Memory Generator} v:ip:alt:1.0 {Embedded Memory Generator} v:ip:far:1.0 {Memory Generator Far}} $o]} default {return {}}}}'),
    "verify_stuck_scalar_config": ("list [ipcfg::verify_stuck isp {CONFIG.C_TILE0_CONFIG 1}] [ipcfg::verify_stuck isp {CONFIG.C_TILE0_CONFIG 2}] [ipcfg::verify_stuck isp {CONFIG.M_CONFIG {A 1 B 2}}]",
        r"^\{\} CONFIG.C_TILE0_CONFIG\(=1,want=2\) CONFIG.M_CONFIG/B\(reverted:=3,want=2\)$",
        'proc get_bd_cells {args} {return /isp}; '
        'proc get_property {args} {return [dict get {CONFIG.C_TILE0_CONFIG 1 CONFIG.M_CONFIG {A 1 B 3}} [lindex $args end-1]]}'),
    "board_pin_missing_hook": ("ipiasm::connect_board_intf fixed_io ps/FIXED_IO",
        r"^BOARD_CONN_FAIL:no such interface pin ps/FIXED_IO; ps interface pins: \{M_AXI\}; board hooks: \{PS_BOARD_INTERFACE=Custom\}; .*ipcfg::board_bind ps \{CONFIG.<hook> fixed_io\}",
        'proc get_bd_cells {args} {return [expr {[lindex $args end] in {ps /ps} ? "/ps" : ""}]}; '
        'proc get_bd_intf_pins {args} {return [expr {[lindex $args end] eq "/ps" ? "/ps/M_AXI" : ""}]}; '
        'proc get_board_part_interfaces {args} {return fixed_io}; '
        'proc list_property {args} {return CONFIG.PS_BOARD_INTERFACE}; '
        'proc get_property {args} {return [dict get {CONFIG.PS_BOARD_INTERFACE Custom VLNV {}} [lindex $args end-1]]}'),
    "region_selectors_rejects_sizes_only": ("ipiasm::region_selectors mem {low 0x80000000}", r"^REGION_SELECT_FAIL:regions must be"),
    "region_ranges_missing_cell": ("ipiasm::memory_region_ranges absent", r"^no such cell absent$", "", 1),
    "region_pick_and_format": (
        "set r [ipiasm::_region_ranges_from_dump \"Parameter Name:   MC_CHAN_REGION0\nRange List:       R_LOW R LOW (0x0000 to 0x00FF; 256B)\nType: string\"]; "
        "ipiasm::_format_selectors [ipiasm::_pick_region_selectors $r {{low 0 16}}] $r",
        r"^MC_CHAN_REGION0=R_LOW \(0x0-0xFF\)$"),
    "pinned_reset_reports_all": (
        "ipcfg::pinned_reset cam",
        r"^PINNED_RESET:2 FMT=RAW8\(want RAW12\) RATE=800\(want 1500\) restore=\{CONFIG\.FMT RAW12 CONFIG\.RATE 1500\}$",
        'set ::ipcfg::req [dict create cam {{CONFIG.FMT RAW12 RAW8 spec} {CONFIG.RATE 1500 800 spec} {CONFIG.LANES 4 1 spec}}]; '
        'proc get_bd_cells {args} {return /cam}; '
        'proc get_property {args} {return [dict get {CONFIG.FMT RAW8 CONFIG.RATE 800 CONFIG.LANES 4} [lindex $args end-1]]}'),
    "pinned_drift_none": ("ipcfg::pinned_drift nothing", r"^items \{\} restore \{\}$"),
    # A board binding re-applies the interface's preset, which can reset values pinned on the
    # cell; they were pinned on purpose, so the binding restores them and fails only if they
    # will not stick.
    "board_conn_restores_pinned": (
        "ipiasm::connect_board_intf mipi cam/phy",
        r"^SUCCESS:board_conn mipi -> cam/phy RESTORED 1 pinned value\(s\) its preset reset: FMT=RAW8\(want RAW12\)$",
        'set ::ipcfg::req [dict create cam {{CONFIG.FMT RAW12 RAW8 spec}}]; set ::v [dict create CONFIG.FMT RAW12]; '
        'proc get_bd_intf_pins {args} {return /cam/phy}; proc get_bd_intf_nets {args} {return {}}; '
        'proc get_board_part_interfaces {args} {return mipi}; proc current_bd_design {args} {return d}; '
        'proc apply_board_connection {args} {dict set ::v CONFIG.FMT RAW8}; proc get_bd_cells {args} {return /cam}; '
        'proc get_property {args} {return [dict get $::v [lindex $args end-1]]}; '
        'proc ipcfg::apply_dict {cell d {allow 0}} {dict for {k x} $d {dict set ::v $k $x}; return SUCCESS:applied}; '
        'proc ipcfg::ledger_record {args} {}'),
    "board_conn_restore_must_stick": (
        "ipiasm::connect_board_intf mipi cam/phy",
        r"^BOARD_CONN_FAIL:mipi -> cam/phy reset pinned value\(s\) FMT=RAW8\(want RAW12\), and restoring them failed: ",
        'set ::ipcfg::req [dict create cam {{CONFIG.FMT RAW12 RAW8 spec}}]; set ::v [dict create CONFIG.FMT RAW12]; '
        'proc get_bd_intf_pins {args} {return /cam/phy}; proc get_bd_intf_nets {args} {return {}}; '
        'proc get_board_part_interfaces {args} {return mipi}; proc current_bd_design {args} {return d}; '
        'proc apply_board_connection {args} {dict set ::v CONFIG.FMT RAW8}; proc get_bd_cells {args} {return /cam}; '
        'proc get_property {args} {return [dict get $::v [lindex $args end-1]]}; '
        'proc ipcfg::apply_dict {args} {return SUCCESS:applied}; proc ipcfg::ledger_record {args} {}'),
    "source_reported_failure": ("ipcfg::source_reported bad.tcl", r"^SCRIPT_FAIL:bad\.tcl: boom$",
                                'set channel [open bad.tcl w]; puts $channel {error boom}; close $channel', 1),
    "source_reported_success": ("ipcfg::source_reported ok.tcl", r"^7$",
                                'set channel [open ok.tcl w]; puts $channel {set value 7}; close $channel'),
    "feasibility_sources": ('ipiasm::feasibility_fingerprint {}', r'FEASIBILITY_FAIL:missing', '', 1),
    "feasibility_gate": ('ipiasm::check_feasibility {} scratch {} {}', r'FEASIBILITY_FAIL:'),
    "resume_configuration": ('ipiasm::_resume_configuration missing {} {set result SUCCESS:checked}', r'^SUCCESS:checked$'),
    "workflow_api": ("list [ipcfg::requirements_data] [dict exists [ipiasm::api_inventory] ipcfg::require] [ipcfg::_config_satisfied absent {CONFIG.VALUE 0}] [dict get [ipiasm::api_preflight {}] status]", r"^\{\} 1 0 PASS$"),
    "checked_python_path": ("ipcfg::python_script missing.py", r"^PYTHON_SCRIPT_FAIL:", "", 1),
    "evidence_path": ("ipcfg::write_evidence relative.tcldict {}", r"^EVIDENCE_FAIL:", "", 1),
    "capture_path": ("ipcfg::capture relative.tcldict {}", r"^CAPTURE_FAIL:", "", 1),
    "capture_extension": ("ipcfg::capture /tmp/receipt_without_extension {}", r"^CAPTURE_FAIL:evidence path must end in \.tcldict", "", 1),
    "capture_counts": ("ipcfg::_message_counts", r"^CAPTURE_FAIL:invalid native message count", "", 1),
    "capture_snapshot": ("ipcfg::_message_snapshot /nonexistent/snapshot.log", r".*write_messages.*", "", 1),
    "workflow_context": ("ipiasm::begin_design", r"^SUCCESS:ipiasm ready"),
    "workflow_plan": (
        "set operation [dict create id fixture op call body [list ipiasm::stage fixture] expect {STAGE:}]; set entry [dict create name fixture operations [list $operation] checkpoint 0]; list [ipiasm::_dispatch_plan digest {fixture} [list $entry] {}] [dict get [ipiasm::stage_execution_status] status]",
        r"ASSEMBLY_PLAN:PASS stages=1 operations=1.*PASS",
    ),
    "normalization": ("list [ipcfg::_norm { TRUE }] [ipcfg::_norm 100.000] [ipcfg::_bool off] [ipcfg::_is_num 2.5]", r"^1 100 0 1$"),
    "neighbor_clamp": ("list [ipcfg::_neighbor 99.5 100] [ipcfg::_clamp 33 1 32] [ipcfg::classify_change 100 0 99.5] [ipcfg::classify_change 100 0 99.5 {} 1%] [ipcfg::_tolerance_ok 2%] [ipcfg::_within_tolerance 99 100 0.5] [ipcfg::_miss_text NEAREST 4095 4096]", r"^1 32 NEAREST RESOLVED 1 0 nearest-legal:=4095,want=4096$"),
    "range_parse": ("ipcfg::parse_range {Value 33 is out of the range '1' to '32'}", r"^1 32$"),
    "key_lines": ("ipcfg::_key_lines CONFIG.WIDTH {WIDTH failed}", r"^WIDTH failed$"),
    "mode": ("list [ipcfg::set_mode assemble] [ipcfg::get_mode] [ipcfg::cleanup absent]", r"^SUCCESS:mode=assemble assemble .*"),
    "bd_file": ("ipcfg::set_bd_file sample.bd; ipcfg::_bd_file", r"^sample.bd$"),
    "part_noop": ("list [ipcfg::ensure_part fixture_part] [ipcfg::restore_part fixture_part]", r"^NOSWAP:fixture_part NOSWAP:fixture_part$"),
    "device_names": ("list [ipcfg::_ip_name vendor:ip:gpio:1.0] [ipcfg::_devices {chip-a chip-b other-a}] [ipcfg::_suggest_parts {chip-a chip-b other-a} a 2]", r"^gpio \{chip other\} \{chip-a other-a\}$"),
    "catalog_absent": ("list [ipcfg::vlnv_ok vendor:ip:missing] [ipcfg::resolve_vlnv missing] [ipcfg::ip_availability {missing other} fixture_part]", r"^0 .*VLNV_NONE:.*IPAVAIL:"),
    "configuration_empty": ("list [ipcfg::apply_dict missing {}] [ipcfg::verify_stuck missing {}] [ipcfg::verify_intent missing {}] [ipcfg::audit_intent missing {}]", r"^SUCCESS:no-params \{\} \{\} \{EXACT:\{\} RESOLVED:\{\} BAD:\{\}\}$"),
    "enablers_empty": ("list [ipcfg::check_enablers missing {}] [ipcfg::feature_flags missing {reset}]", r"^\{\} FLAGS:none$"),
    "snapshots_empty": ("list [ipcfg::snapshot missing {}] [ipcfg::snapshot_all missing] [ipcfg::_cfg_snapshot missing] [ipcfg::param_format missing CONFIG.VALUE]", r"^\{\} \{\} \{\} \{\}$"),
    "requirements_empty": ("list [ipcfg::require missing {ODD}] [ipcfg::requirements] [ipcfg::audit_requirements] [ipcfg::forget_requirements]", r"REQ_ERR:odd-length.*REQ:empty.*REQ:cleared all"),
    "board_missing": ("list [ipcfg::board_hooks absent] [ipcfg::board_enablers absent] [ipcfg::board_bind absent {CONFIG.BOARD test}]", r"^\{\} \{\} \{BOARD_FAIL:no such cell absent\}$"),
    "board_ledger_empty": ("list [ipcfg::_board_conflicts absent {}] [llength [ipcfg::_board_merge absent {}]] [ipcfg::board_owned absent] [ipcfg::audit_board absent] [ipcfg::forget_board absent]", r"^\{\} 2 \{\} \{BOARD:none nothing was bound from the board\} \{BOARD:cleared absent\}$"),
    "native_absent": ("list [ipcfg::native_caps] [ipcfg::has_native can_connect] [ipcfg::param_deps absent]", r".*0.*PARAM_DEPS_NA"),
    "nested_missing": ("list [ipcfg::cell_dict_keys absent SETTINGS] [ipcfg::resolve_subkeys absent SETTINGS {}] [ipcfg::probe_subkey_range absent SETTINGS VALUE]", r"CDK_ERR:no cell.*RSK_ERR:no cell.*"),
    "synonyms": ("ipcfg::_synonyms CAN-FD", r"CAN-FD CAN_FD CANFD CANF CAN"),
    "dict_value_empty": ("ipcfg::_dict_val absent CONFIG.SETTINGS", r"^$"),
    "gate_roundtrip": ("ipiasm::gate_reset; ipiasm::_gate_set board 1 test; list [ipiasm::_gate_get board] [ipiasm::_need board create] [ipiasm::gate_override inventory test] [ipiasm::gate_status]", r"^1 \{\} .*board=1.*inventory=1"),
    "init_policy": ("ipiasm::init; list [ipiasm::policy unattended] [ipiasm::_need_inventory connect] [ipiasm::timings]", r"POLICY:unattended.*GATE_FAIL:connect.*TIMINGS:"),
    "checkpoint": ("ipiasm::checkpoint contract", r"^CHECKPOINT:contract GATES:"),
    "wrapper_boundary": ("set f [file join $root top.sv]; set c [open $f w]; puts $c {design_1_wrapper u (.a(x));}; close $c; list [ipiasm::check_wrapper_boundary $f design_1_wrapper] [ipiasm::name_port nope other]", r"BOUNDARY_FAIL:1 of 1.*PORT_NAME_FAIL:no port nope"),
    "ps_noc_na": ("ipiasm::check_ps_noc_masters", r"^PS_NOC:NA"),
    "create_port_invalid": ("ipiasm::create_port p Z", r"^PORT_FAIL:p direction 'Z'"),
    "api_index": ("list [string match *DELIVERED* [ipiasm::api]] [ipiasm::api gate_status]", r"^1 \{ipiasm::gate_status"),
    "clock_contract_reuse": ("list [ipiasm::_verified_clock_contracts checkpoint] [ipiasm::_verified_clock_contracts stage] [set e [ipiasm::_mutation_epoch]] [catch {set_property A B C}] [expr {[ipiasm::_mutation_epoch] > $e}] [llength [ipiasm::_clock_verification_state]]",
                             r"^\{CLOCK_CONTRACTS:NA not registered\} \{CLOCK_CONTRACTS:NA not registered\} \d+ 1 1 4$"),
    "reference_search_no_project": ("list [string match */* [ipiasm::_ced_cache_dir]] [string match REFERENCE_SEARCH* [ipiasm::reference_search $root/rs x]] [string match *ced_index* [set ::env(IPIASM_CACHE_DIR) $root/c; catch {ipiasm::_ced_index} r; set r]]", r"^1 1 "),
    "ced_reference_no_project": ("list [ipiasm::ced_reference x $root/c] [ipiasm::_write_bd_records stdout]", r"^\{?CED_REF_FAIL:.* 0$"),
    "instantiate_ced_needs_plan": ("list [ipiasm::instantiate_ced x] [ipiasm::remove_cell x]", r"^\{GATE_FAIL:instantiate_ced x requires the 'board' gate.*\} \{SKIP:no cell x\}$"),
    "native_log_discovery": ("list [set ::ipcfg::native_log_path /x/v.log; ipcfg::discover_native_log] [set ::ipcfg::native_log_path {}; string length [ipcfg::discover_native_log]]", r"^/x/v\.log \d+$"),
    "reference_coverage_na": ("ipiasm::check_reference_coverage $root", r"^REFERENCE_COVERAGE:(NA|FAIL)"),
    "draft_from_missing_reference": ("string length [ipiasm::_draft_from_reference $root/none.json $root/d]", r"^0$"),
    "leaf_ends_empty": ("llength [ipiasm::_leaf_ends x]", r"^0$"),
    "deliver_ced_needs_search": ("ipiasm::deliver_ced $root/o x", r"^DELIVER_CED_REFUSED:no reference_search.json"),
    "export_needs_gate": ("ipiasm::_export_delivery $root/o", r"^DELIVER_FAIL:DELIVERY_GUARD_FAIL"),
    "ced_fit_needs_files": ("ipiasm::ced_fit $root/o", r"^CED_FIT_FAIL:no ced_reference.json"),
    "validate_reuse_marker": ("list [ipiasm::_validate_reused] [llength [ipiasm::_validate_state]]", r"^\d+ [05]$"),
    "inventory_missing": ("ipiasm::gate_inventory {axi_gpio 1}", r"^INVENTORY_FAIL:short=\{axi_gpio:0/1\}"),
    "bram_empty": ("ipiasm::check_bram_backing", r"^BRAM:OK 0 interface"),
    "cell_config_empty": ("ipiasm::check_cell_config absent {}", r"^CELL_CONFIG_FAIL:"),
    "source_files_empty": ("ipiasm::check_source_files {} fixture_top", r"^SOURCE_FILES_FAIL:"),
    "interface_routes_connected": (
        "ipiasm::check_interface_routes {{/producer/M_INI /consumer/S_INI}}",
        r"^INTERFACE_ROUTES:OK n=1$",
        'proc get_bd_intf_pins {quiet path} {return [list $path]}; '
        'proc find_bd_objs {args} {return /consumer/S_INI}'),
    "creation_gate": ("ipiasm::create_cell vendor:ip missing", r"^GATE_FAIL:create_cell"),
    "module_reference_gate": ("ipiasm::create_module_ref rtl_block missing {missing.v}", r"^GATE_FAIL:create_module_ref"),
    "semantic_missing": ("ipiasm::ensure_semantic_cell_name absent mailbox", r"^ROLE_NAME_FAIL:no cell absent$"),
    "connections_missing": ("list [ipiasm::connect_intf absent/A absent/B] [ipiasm::connect_net absent/A absent/B] [ipiasm::connect_axi_auto absent/A absent/B] [ipiasm::connect_external_axi absent absent/B]", r"INTF_FAIL:no source.*NET_FAIL:no source.*MANUAL:INTF_FAIL.*EXT_AXI_FAIL:no top-level"),
    "native_connections_absent": ("list [ipiasm::check_connect absent/A absent/B] [ipiasm::legal_intf_targets absent/A] [ipiasm::legal_pin_targets absent/A]", r"^NATIVE_NA NATIVE_NA NATIVE_NA$"),
    "clock_domains_empty": ("dict get [ipiasm::_same_domain_verdict {} {} {} {}] same_domain", r"^0$"),
    "edge_metadata_missing": ("dict get [ipiasm::_edge_domain_facts absent/A absent/B] resolved", r"^0$"),
    "preflight_missing": ("ipiasm::preflight_connect absent/A absent/B", r"^PREFLIGHT_FAIL:no such endpoint"),
    "disconnect_missing": ("ipiasm::disconnect_intf_edge absent/A absent/B", r"^DISCONNECT_FAIL:no source"),
    "frequency_missing": ("ipiasm::sync_intf_freq_hz absent/A absent/B", r"^FREQ_SYNC_FAIL:no interface"),
    "wiring_empty": ("list [ipiasm::_intf_wired absent/A] [ipiasm::_net_wired absent/A] [ipiasm::_scalar_in_wired_intf absent/A] [ipiasm::_scalar_is_intf_member absent/A]", r"^0 0 0 0$"),
    "plan_missing": ("list [ipiasm::assemble_from_plan missing.json] [ipiasm::plan_execution_status] [ipiasm::_discover_assembly_plans]", r"ASSEMBLY_PLAN_FAIL:no plan.*PLAN_EXECUTION_FAIL:no assembly plan.*"),
    "regate_and_ledger_without_project": ("list [ipiasm::_regate_gate ipiasm::gate_inventory] [ipiasm::_regate_gate ipcfg::apply_dict] [ipcfg::ledger_clear]", r"^inventory \{\} \{\}$"),
    "isp_resources_empty":("ipiasm::check_isp_resources", r"^ISP_RESOURCES:NA no ISP in the design$"),
    "plan_receipts_missing": ("list [ipiasm::plan_execution_status /nonexistent/plan.json] [ipiasm::_staged_completion /nonexistent/plan.json {}] [ipiasm::_invalidate_plan_receipts /nonexistent/plan.json test]", r"^\{PLAN_EXECUTION_FAIL:no assembly plan reached ASSEMBLY_PLAN:PASS and no assemble_from_plan receipt exists .*\} \{\} 1$"),
    "bandwidth": ("list [ipiasm::bw_axi 100 128] [ipiasm::bw_mipi 4 1000] [ipiasm::bw_video 1920 1080 60 8]", r"^1600 500 137$"),
}

CASES.update({
    "address_gate": ("list [ipiasm::auto_address] [ipiasm::add_addr_seg space seg window 0 65536] [ipiasm::place_named_windows {}]", r"GATE_FAIL:auto_address.*GATE_FAIL:add_addr_seg.*GATE_FAIL:place_named_windows"),
    "address_missing": ("list [ipiasm::restrict_address_space absent {}] [ipiasm::restrict_master absent {}] [ipiasm::restrict_window_to_spaces absent {}] [ipiasm::check_window_scope absent {} 0 65536] [ipiasm::check_visibility absent {}] [ipiasm::enforce_window_scope window absent {} 0 65536]", r"RESTRICT_FAIL:no address space.*RESTRICT_FAIL:no such cell.*WINDOW_SCOPE_FAIL:.*WINDOW_SCOPE_FAIL:.*VISIBILITY_FAIL:.*WINDOW_SCOPE_FAIL:"),
    "address_empty_helpers": ("list [ipiasm::_all_slave_segs] [ipiasm::_fixed_segs] [ipiasm::_excluded_segs] [ipiasm::_unassigned_segs] [ipiasm::_mapped_seg_count] [ipiasm::_mapped_seg_targets absent absent] [ipiasm::_resolve_addr_spaces {}] [ipiasm::_unassign_spaces {}] [ipiasm::_prune_unaddressable {}]", r"^\{\} \{\} \{\} \{\} 0 1 \{\} \{\} \{\}$"),
    "size_conversion": ("list [ipiasm::size_to_range 64K] [ipiasm::size_to_range 1G] [ipiasm::size_to_range invalid]", r"^0x10000 0x40000000 \{\}$"),
    "noc_missing": ("list [ipiasm::add_noc_si absent DDR source 100 100] [ipiasm::set_qos absent DDR 100 100] [ipiasm::qos_owner absent]", r"NOC_FAIL:.*QOS_FAIL:no interface.*unresolved"),
    "noc_qos_empty": ("list [ipiasm::check_qos] [ipiasm::check_noc_solution] [ipiasm::check_noc_legality] [ipiasm::apply_qos_plan {}] [ipiasm::noc_traffic_spec] [ipiasm::qos_sweep {} {}]", r"QOS:NA.*NOC:NA.*NOCLEGAL:NA.*QOS_SWEEP_FAIL:"),
    "noc_enumeration": ("list [ipiasm::_all_noc_cells] [ipiasm::_noc_ddr_segs] [ipiasm::_dram_masters] [dict get [ipiasm::_dram_paths] declared] [ipiasm::_mc_noc_axi_ingress] [ipiasm::_unbraced_noc_decls] [ipiasm::_requested_mc]", r"^\{\} \{\} \{\} 0 \{\} \{\} 0$"),
    "noc_remove_missing": ("list [ipiasm::plan_remove_noc_path absent/S00_AXI] [ipiasm::remove_noc_path absent/S00_AXI]", r"REMOVE_PLAN_FAIL:.*REMOVE_FAIL:"),
    "noc_clock_missing": ("list [ipiasm::_noc_clk_for_port absent S00_AXI] [ipiasm::wire_mem_noc_sysclk absent] [ipiasm::check_mem_noc_sysclk absent]", r"MEM_SYSCLK_FAIL:no such cell.*MEM_SYSCLK:NA"),
    "memory_missing": ("list [ipiasm::check_memory_subsystem 1] [ipiasm::check_required_dram_masters {dma}] [ipiasm::check_noc_pre_address 1] [ipiasm::check_noc_post_address]", r"MEM_FAIL:.*DRAM.*NOC_PRE.*NOC_POST"),
    "board_empty": ("list [ipiasm::discover_board_parts] [ipiasm::require_board_preset] [ipiasm::board_interfaces] [ipiasm::check_board_hooks]", r"BOARDS:.*ASK.*BOARD_INTFS.*HOOKS:"),
    "board_helpers_empty": ("list [ipiasm::_board_candidates {}] [ipiasm::_board_memory_intfs] [ipiasm::_bound_mem_intfs] [ipiasm::_board_hooks_for absent] [llength [ipiasm::_cell_hook_props absent]] [ipiasm::_board_camera_geometry]", r"^\{\} \{\} \{\} \{\} 2 \{\}$"),
    "board_attach_missing": ("list [ipiasm::attach_board_memory absent 1] [ipiasm::connect_board_intf missing absent/GPIO]", r"MEM_ATTACH_FAIL:.*BOARD_CONN_FAIL:"),
    "board_attach_odd_regions": ("ipiasm::attach_board_memory absent 4 {MC_CHAN_REGION1}", r"^MEM_ATTACH_FAIL:"),
    "aie_missing": ("list [ipiasm::_aie_cells] [ipiasm::check_aie_config] [ipiasm::attach_aie_config absent noc/M00_AXI]", r"^\{\} .*AIE:NA.*AIE_FAIL:"),
    "ps_missing": ("list [ipiasm::_ps_cells] [ipiasm::_ps_gt_quad] [ipiasm::_ps_hsm_refclk] [ipiasm::check_ps_fabric_irqs absent 2]", r"^\{\} \{\} \{\} .*IRQ_CAPACITY_FAIL:"),
    "camera_empty": ("list [ipiasm::_camera_tile_links] [ipiasm::check_camera_geometry] [ipiasm::check_camera_isp_format] [ipiasm::check_camera_line_buffer] [ipiasm::check_mipi_video_clocks]", r"GEOMETRY:none.*FORMAT:none.*LINEBUF:none.*VIDEOCLK:NA"),
    "camera_properties_missing": ("list [ipiasm::_receiver_board_intf absent] [ipiasm::_tile_format_prop absent 0] [ipiasm::_tile_res_hor_prop absent 0] [ipiasm::_tile_res_ver_prop absent 0]", r"^\{\} \{\} \{\} \{\}$"),
    "camera_notes": ("list [ipiasm::camera_control_note] [ipiasm::camera_retune_note] [ipiasm::display_note]", r"CAMCTRL:none.*RETUNE:none.*DISPLAY:none"),
    "clock_missing": ("list [ipiasm::wire_hier_clk absent/clk source/clk] [ipiasm::wire_clk_rst_auto source/clk {}] [ipiasm::ensure_reset_domain reset absent/clk absent/reset {}]", r"CLK_FAIL:no inner pin.*CLK_FAIL:no clkrst rule.*RESET.*FAIL:"),
    "clock_empty": ("list [ipiasm::build_clocks {}] [ipiasm::fanout_domain 100 {}] [ipiasm::check_interface_clock_domains]", r"CLK.*CLOCK_DOMAIN:"),
    "interface_metadata_empty": ("list [dict get [ipiasm::_interface_clock_facts absent] domain] [ipiasm::_associated_busifs absent] [ipiasm::_contract_vlnv absent] [ipiasm::_clock_pin_hz absent]", r"^\{\} \{\} external \{\}$"),
    "interface_contracts_empty": ("list [ipiasm::set_interface_contracts {}] [ipiasm::preflight_interface_contracts] [ipiasm::externalize_interface {}]", r"INTF_CONTRACTS:SET count=0.*INTF_PREFLIGHT:NA.*EXTERNAL_FAIL:"),
    "interface_helpers_empty": ("list [ipiasm::_intf_neighbors absent] [ipiasm::_intf_peer_cells absent] [ipiasm::_intf_source absent] [ipiasm::_open_in_pins absent clk] [ipiasm::_cells_connected absent other] [ipiasm::_connected_intf_count aximm]", r"^\{\} \{\} \{\} \{\} 0 0$"),
    "infrastructure_empty": ("list [ipiasm::externalize_oos {}] [ipiasm::instantiate_n vendor:ip sample 0] [ipiasm::build_control_fanout absent {}] [ipiasm::normalize_sim_model tlm]", r"SIM_MODEL:tlm applied_to=\{\}"),
    "automation_missing": ("ipiasm::connect_automation absent/S_AXI rule", r"^AUTO_FAIL:"),
    "validation_empty": ("list [ipiasm::validate] [ipiasm::validate_bundle] [ipiasm::final_contract_gate {}] [ipiasm::delivery_guard]", r"VALIDATE:PASS.*BUNDLE_FAIL:.*FINAL_CONTRACT_FAIL:.*DELIVERY_GUARD_FAIL:"),
    "delivery_context_wrong": ("ipiasm::assert_delivery_context wrong $root", r"^DELIVERY_CONTEXT_FAIL:"),
    "reconcile_missing": ("ipiasm::reconcile_spec missing.json missing.py", r"^RECONCILE_FAIL:no manifest"),
    "autofix_empty": ("list [ipiasm::autofix_domains {}] [ipiasm::autofix_address {}] [ipiasm::autofix {} {} {}] [ipiasm::close_gaps {} {} {}]", r"CLOSED|NEEDS_USER", "ipiasm::_gate_set inventory 1 fixture"),
    "autofix_closed_inventory": (
        "list [ipiasm::autofix {fixture 100} {} {fixture/pin}] $repair_calls",
        r"^\{GATE_FAIL:autofix requires the 'inventory' gate -> .*\} 0$",
        "set repair_calls 0; foreach helper {autofix_domains autofix_address externalize_oos} {trace add execution ipiasm::$helper enter {apply {{args} {incr ::repair_calls}}}}",
    ),
    "autofix_closed_qos": (
        "list [ipiasm::autofix {fixture 100} {} {fixture/pin}] $repair_calls",
        r"^\{GATE_FAIL:autofix requires the 'qos' gate -> .*\} 0$",
        "ipiasm::_gate_set inventory 1 fixture; proc ipiasm::_all_noc_cells {} {return noc}; set repair_calls 0; foreach helper {autofix_domains autofix_address externalize_oos} {trace add execution ipiasm::$helper enter {apply {{args} {incr ::repair_calls}}}}",
    ),
    "report_empty": ("ipiasm::assembly_report", r"^ASSEMBLY:"),
    "overmap_empty": ("ipiasm::check_overmap 10", r"^OVERMAP:NA"),
    "fabric_detection": ("list [ipiasm::detect_fabric_coupling {} {}] [ipiasm::_is_fabric absent] [ipiasm::_is_dram_seg absent] [ipiasm::_is_master_addr_space absent] [ipiasm::_part_family fixture_part]", r"FABRIC:.*0 0 0 fixture_part"),
    "qos_math": ("list [ipiasm::qos_classes] [ipiasm::noc_channel_mbs 1000 256 16]", r"^\{BEST_EFFORT ISOCHRONOUS LOW_LATENCY\} 941$"),
    "raw_format": ("list [ipiasm::_raw_bits RAW12] [ipiasm::_raw_bits unsupported] [ipiasm::_vercmp 1.10 1.2] [ipiasm::_vlnv_ip vendor:ip:gpio:1.0]", r"^12 \{\} 1 vendor:ip:gpio$"),
    "noc_connection_shape": ("list [dict get [ipiasm::_conn_braced {DDR {read_bw 100}}] DDR read_bw] [ipiasm::_mipi_clock_margin_mhz]", r"^100 0.0$"),
    "diagnostics_empty": ("list [ipiasm::illegal_msg_ids] [ipiasm::clock_reset_msg_ids] [ipiasm::_msg_count {BD 41-1}] [ipiasm::_msg_snapshot] [ipiasm::_illegal_noc_findings {}]", r"BD.*"),
    "fingerprints_empty": ("list [ipiasm::_design_fp] [ipiasm::_delivery_fingerprint]", r"^0:0:0:0 00000000:0$"),
    "noc_report_missing": ("list [ipiasm::_ncr_path] [ipiasm::_ncr_is_fresh missing.ncr] [ipiasm::_ncr_parse missing.ncr]", r"^\{\} 0 \{UNKNOWN \{\}\}$"),
})

PARAMETER_FIXTURE = r'''
set channel [open parameters.txt w]
puts $channel {Parameter Name: WIDTH
Parameter ID: PARAM_VALUE.WIDTH
Current Value: 8
Range Minimum: 1 Maximum: 32
*****
Parameter Name: MODE
Parameter ID: PARAM_VALUE.MODE
Current Value: Active_Low
Range List: Active_Low low
 Active_High high
*****
Parameter Name: SETTINGS
Parameter ID: PARAM_VALUE.SETTINGS
Current Value: ENABLE 1
*****
digraph G {
1[label="PARAM_VALUE.USE_RESET"]
2[label="MODELPARAM_VALUE.C_USE_RESET"]
3[label="PORT_ENABLEMENT.RESETN"]
4[label="DICT_PARAM_KEY.SETTINGS<ENABLE>"]
1->2
2->3
}}
close $channel
'''

CASES.update({
    "dump_ranges": ("list [ipcfg::dump_params parameters.txt WIDTH] [ipcfg::param_range parameters.txt WIDTH] [ipcfg::check_value parameters.txt WIDTH 33] [ipcfg::check_value parameters.txt MODE active-low]", r"DUMP_PARAMS:CONFIG.WIDTH RANGE_NUM:1:32.*VAL_OUT_OF_RANGE:WIDTH=33 legal=1..32.*did_you_mean=Active_Low", PARAMETER_FIXTURE),
    "dump_graph": ("list [ipcfg::port_enablers parameters.txt RESETN] [ipcfg::enablement_ports parameters.txt] [ipcfg::dict_keys parameters.txt SETTINGS] [ipcfg::dict_find parameters.txt ENABLE] [ipcfg::param_dict parameters.txt SETTINGS ENABLE]", r"^PE:RESETN:USE_RESET PE_PORTS:RESETN DK:SETTINGS:ENABLE DF:SETTINGS<ENABLE> PD:ENABLE=1$", PARAMETER_FIXTURE),
    "dump_plan": ("ipcfg::plan parameters.txt WIDTH ENABLE", r"PLAN:.*CONFIG.WIDTH \[1..32\].*SETTINGS.*ENABLE", PARAMETER_FIXTURE),
    "name_disambiguation": ("list [ipcfg::_best_name WIDTH {CONFIG.WIDTH CONFIG.DEPTH}] [ipcfg::_best_name CLOCK {CLOCK_A CLOCK_B}]", r"^CONFIG.WIDTH \{AMBIGUOUS:\{CLOCK_A CLOCK_B\}\}$"),
    "python_bridge": ("ipcfg::python_reset; list [ipcfg::python_exec -c {print(6 * 7)}] [file executable [ipcfg::python_exe]] [ipcfg::python_status] [ipiasm::_file_sha256 sample.txt]", r"^42 1 .*PYTHON:OK.*ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad$", 'set channel [open sample.txt w]; puts -nonewline $channel abc; close $channel'),
    "python_roots": ("ipcfg::_python_bundle_candidates; llength [ipcfg::_python_vivado_roots]", r"^1$", 'unset -nocomplain ::env(XILINX_VIVADO) ::env(VIVADO_PATH); set ::env(IPCFG_VIVADO_ROOT) $root'),
    "cache_roundtrip": ('ipcfg::cache_put fixture width CONFIG.WIDTH scalar {} user fixture 1.0; list [ipcfg::cache_get fixture width] [ipcfg::cache_dump]', r'"param":\s*"CONFIG.WIDTH".*"fixture"'),
    "discovery_missing": ("list [ipcfg::configure_feature absent missing width {WIDTH 8}] [ipcfg::find_params absent WIDTH] [ipcfg::discover_params missing WIDTH [file join $root discover]]", r"CF:FAIL:WRONG_IP_NAME:.*FP_ERR:no cell.*DISCOVER_ERR:FIXTURE_CREATE_REJECTED", 'proc create_ip {args} {error FIXTURE_CREATE_REJECTED}'),
    "configuration_rejection": ("list [ipcfg::create_cell missing absent] [ipcfg::create_cell_cfg missing second {CONFIG.WIDTH 8}] [ipcfg::autofix_apply absent {}]", r"CREATE_ERROR:.*CREATE_ERROR:.*AUTOFIX:applied=\{all\}", 'proc create_bd_cell {args} {error FIXTURE_CREATE_REJECTED}'),
    "automation_rejection": ("list [ipcfg::try_automation absent rule] [ipcfg::run_automation absent rule] [ipcfg::add_stub absent S_AXI interface]", r"AUTOMATION_ERROR:FIXTURE_AUTOMATION_REJECTED.*AUTOMATION_ERROR:FIXTURE_AUTOMATION_REJECTED.*STUB_ERROR:", 'proc apply_bd_automation {args} {error FIXTURE_AUTOMATION_REJECTED}'),
    "diff_reconciliation": ("list [ipcfg::config_diff absent {}] [ipcfg::reconcile_disabled absent {}]", r"^\{\} \{OMIT:\{\} DIFFER:\{\}\}$"),
    "report_coverage": ("ipcfg::coverage_report {clock CONFIG.CLK applied camera CONFIG.CAMERA unapplied:missing}", r"^COVERAGE:PARTIAL.*clock.*camera.*unapplied:missing"),
    "intent_classification": ("list [ipcfg::is_system_intent {processing system fabric clock}] [ipcfg::is_system_intent multiplier]", r"^SYSTEM:ps-fabric-clocks STANDALONE$"),
    "diagnostic_parsing": ("list [ipcfg::find_disabled {disabled parameter WIDTH ignored}] [ipcfg::range_from_log {PARAM WIDTH is out of range { 1,32 }} WIDTH]", r"RANGE:WIDTH=\{1,32\}"),
    "rule_parsing": ('ipcfg::rule_for_vlnv {"vendor:ip:block:1.0":["Fixture block", "fixture", "rule.tcl"]} vendor:ip:block', r"^RULE:xilinx.com:bd_rule:fixture desc=\{Fixture block\}$"),
    "rule_schema": ("ipcfg::automation_config_schema rule.tcl", r"^SCHEMA:keys=\{clock\} defaults=\{clock None\}", 'set channel [open rule.tcl w]; puts $channel {dict get $opts clock}; puts $channel {clock_none "None"}; close $channel'),
    "api_listing": ("string match *apply_dict* [ipcfg::api apply]", r"^1$"),
    "qos_impl_empty": ("ipiasm::_check_qos_impl", r"^QOS:OK"),
    "qos_unused_empty": ("ipiasm::set_unused_qos_routes {}", r"^QOS_UNUSED:SET count=0"),
    "reach_empty": ("ipiasm::check_required_reachability {/ps/* DRAM}", r"^REACH_FAIL:1 .*no matching address space"),
    "undriven_clk_empty": ("ipiasm::check_undriven_clocks", r"^UNDRIVEN_CLK:OK 0 "),
    "addr_space_count_missing": ("ipcfg::_addr_space_count absent", r"^0$"),
    "required_ports_empty": ("list [ipiasm::check_required_ports {}] [ipiasm::check_required_ports {{name clk_250 type clk}}] [ipiasm::check_required_ports {{dir O}}]",
                             r"^\{PORTS:OK 0\} \{PORTS_FAIL:1 problem\(s\) in 1 required port\(s\)\n  clk_250 missing\} \{PORTS_FAIL:entry without name"),
    "mem_region_empty": ("ipiasm::check_memory_regions {legacy 0x80000000}", r"^MEM_REGION_FAIL:1/1 "),
    "clock_driver_empty": ("list [ipiasm::_clock_driver /x/clk] [ipiasm::_undriven_clock_hint /x/clk]", r"^\{\} \{carries no interface\}$"),
    "bram_edge_empty": ("ipiasm::_bram_edge /a/BRAM_PORTA /b/BRAM_PORTA", r"^0$"),
    # Naming the log directory never creates it, so reading a plan under an unwritable root is safe.
    "plan_log_dir_names_only": ("list [ipiasm::_plan_log_dir /nonexistent/plan.json] "
                                "[file isdirectory /nonexistent/logs]", r"^/nonexistent/logs 0$"),
    "undriven_deferral_empty": ("list [ipiasm::_before_wiring_stage] [ipiasm::_undriven_clock_deferred /x/clk]", r"^0 0$"),
    "noc_port_clock_empty": ("ipiasm::_attach_noc_port_clock /noc S00_AXI /m/M_AXI", r"^WARN:clock-unassociated\(0 clocks"),
    "compare_intf_clocks_empty": ("ipiasm::_compare_intf_clocks /a/S_AXI /b/M_AXI", r"^unresolved$"),
    "intf_name_tokens_direct": ("ipiasm::_intf_name_tokens /ps/M_AXI_HPC0_FPD", r"^hpc0 fpd$"),
    "noc_port_clock_ok_no_net": ("ipiasm::_noc_port_clock_ok /noc S00_AXI {}", r"^0$"),
    "attach_noc_port_clocks_empty": ("ipiasm::attach_noc_port_clocks", r"^NOC_CLK:OK checked=0 fixed=0 escalate=0$"),
    # An interface used signal by signal (a GPIO's gpio_io_o) has no interface net but is wired.
    "intf_wired_through_member": ("ipiasm::_intf_members_wired /g/GPIO", r"^1$",
                                  'proc get_bd_intf_pins {args} {return /g/GPIO}; '
                                  'proc get_bd_pins {args} {return /g/gpio_io_o}; proc get_bd_nets {args} {return n}'),
    "intf_unwired_members": ("ipiasm::_intf_members_wired /u/UART", r"^0$",
                             'proc get_bd_intf_pins {args} {return /u/UART}; '
                             'proc get_bd_pins {args} {return {/u/rx /u/tx}}; proc get_bd_nets {args} {return {}}'),
    # A memory's clka travels inside its BRAM interface, whatever the pin is called.
    "clock_inside_wired_intf": ("ipiasm::_scalar_in_wired_intf /m/clka", r"^1$",
                                'proc get_bd_pins {args} {return /m/clka}; proc get_bd_cells {args} {return /m}; '
                                'proc get_bd_intf_pins {args} {return /m/BRAM_PORTA}; proc get_bd_intf_nets {args} {return n}'),
    "declare_open_needs_reason": ("ipiasm::declare_open /u/UART { }", r"^OPEN_FAIL:give the reason"),
    "declare_open_records": ("ipiasm::declare_open /u/UART {spec: no pins}", r"^OPEN:OK declared=1 total=1$",
                             'proc get_bd_intf_pins {args} {return /u/UART}; proc get_bd_intf_nets {args} {return {}}; '
                             'proc get_bd_pins {args} {return /u/tx}; proc get_bd_nets {args} {return {}}'),
    # A declaration cannot hide a pin the design does use.
    "declare_open_refuses_wired": ("ipiasm::declare_open /g/GPIO {unused}", r"^OPEN_FAIL:/g/GPIO is wired$",
                                   'proc get_bd_intf_pins {args} {return /g/GPIO}; proc get_bd_intf_nets {args} {return {}}; '
                                   'proc get_bd_pins {args} {return /g/gpio_io_o}; proc get_bd_nets {args} {return n}'),
    # Delivery verdicts are printed as well as returned, so the session log holds them.
    "logged_verdict": ("ipiasm::_logged [join {{A:OK x} rest} \\n]", r"^A:OK x\nrest$"),
    "delivery_guard_logged": ("ipiasm::delivery_guard", r"^DELIVERY_GUARD_FAIL:final_contract_gate has not passed$"),
    "delivery_context_logged": ("ipiasm::assert_delivery_context design $root",
                                r"^DELIVERY_GUARD_FAIL:final_contract_gate has not passed$"),
    "final_gate_from_plan_reruns_visibility": (
        "close [open $root/p.json w]; close [open $root/s.json w]; "
        "proc ipcfg::python_script {args} {return {windows {} bundle {0 {} 0 1 {}} dram_masters {} reachability {} "
        "visibility {{/u/S/Reg {/cpu/Data}} {/v/S/Reg {}}}}}; "
        "proc ipiasm::check_visibility {slave {spaces {}}} {expr {$slave eq {/v/S/Reg} ? {VISIBILITY_FAIL:extra=1} : \"VISIBILITY:OK slave=$slave\"}}; "
        "proc ipiasm::final_contract_gate {args} {return GATE_RAN}; "
        "list [ipiasm::final_contract_gate_from_plan $root/p.json $root/s.json] [dict keys $::ipiasm::recorded_evidence]",
        r"^FINAL_CONTRACT_FAIL:VISIBILITY_FAIL:extra=1 plan_visibility_1$"),
    "final_gate_from_plan_needs_one_plan": ("ipiasm::final_contract_gate_from_plan",
                                            r"^FINAL_CONTRACT_FAIL:expected one assembly_plan\.json"),
    "final_gate_from_plan_needs_spec": ("ipiasm::final_contract_gate_from_plan [file join $root plan.json]",
                                        r"^FINAL_CONTRACT_FAIL:no file .*design_spec\.json$",
                                        'close [open [file join $root plan.json] w]'),
    "declare_open_refuses_missing":("ipiasm::declare_open /x/Y {unused}", r"^OPEN_FAIL:/x/Y not found$",
                                     'proc get_bd_intf_pins {args} {return {}}; proc get_bd_pins {args} {return {}}'),
    "export_proven_wizard_cell": ("list [ipcfg::export_proven [file join $root proven.json] [list [dict create cell ps role platform apply_via ps_wizard evidence [list $ev] spec_pointers {/blocks/0}]]] [ipcfg::export_proven proven.json {}] [ipcfg::export_proven [file join $root proven.json] {}]",
                                  r"^\{EXPORT:OK n=1 json=.*proven\.json tcl=.*proven\.tcl\} \{EXPORT_FAIL:absolute output path required\} \{EXPORT_FAIL:output exists",
                                  'proc get_bd_cells {args} {return [lindex $args end]}\nset ev [file join $root capture.json]\nclose [open $ev w]'),
})


def make_test(name, case):
    def test(self):
        self.run_case(name, *case)
    return test


for case_name, case_spec in CASES.items():
    setattr(ProcedureContracts, "test_" + case_name, make_test(case_name, case_spec))


class PreAddressChecks(unittest.TestCase):
    FIXTURE = r'''
proc get_bd_cells {args} {
    if {[lsearch $args *axi_noc*] >= 0} {return /noc}
    return {/a /b}
}
proc get_bd_addr_segs {args} {
    if {[lsearch $args -addressable] >= 0} {return [dict get $::reach [lindex $args end]]}
    return $::ddr
}
proc get_property {args} {
    set name [lindex $args end-1]; set obj [lindex $args end]
    if {$name eq "RANGE"} {return [dict get $::ranges $obj]}
    if {[dict exists $::props $obj $name]} {return [dict get $::props $obj $name]}
    return {}
}
proc get_bd_addr_spaces {args} {return [lsearch -all -inline $::spaces [lindex $args end]]}
proc get_bd_intf_pins {args} {
    set obj [lindex $args end]
    if {[dict exists $::space_intf $obj]} {return [dict get $::space_intf $obj]}
    return {}
}
proc get_bd_intf_ports {args} {return {}}
proc get_bd_pins {args} {return [dict get $::clk_pins [lindex $args end-2]]}
proc find_bd_objs {args} {return [dict get $::peers [lindex $args end]]}
proc ipiasm::_scalar_in_wired_intf {pin} {return [expr {$pin eq "/b/refclk_p"}]}
set ddr {/noc/DDR_MC_PORTS/DDR_CH0_LEGACYx4}
set ranges {/noc/DDR_MC_PORTS/DDR_CH0_LEGACYx4 0x80000000 /noc/DDR_MC_PORTS/DDR_CH0_MEDx4 0x800000000}
set spaces {/ps/cpu_0 /ps/cpu_1 /isp/NMU}
set space_intf {/ps/cpu_0 /ps/FPD0 /ps/cpu_1 /ps/FPD1 /isp/NMU /isp/NMU}
set reach {/ps/FPD0 {/noc/DDR_MC_PORTS/DDR_CH0_LEGACYx4 /gpio/S_AXI/Reg} /ps/FPD1 {} /isp/NMU {/noc/DDR_MC_PORTS/DDR_CH0_LEGACYx4}}
set clk_pins {/a {/a/aclk} /b {/b/aclk /b/refclk_p}}
set peers {/a/aclk {/ps/pl0_clk} /b/aclk {} /b/refclk_p {}}
set props {/ps/pl0_clk {CLASS bd_pin DIR O}}
'''

    def run_tcl(self, body):
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n" + self.FIXTURE + body
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_memory_regions_reject_missing_segment_and_accept_published(self):
        out = self.run_tcl(r'''
puts [ipiasm::check_memory_regions {legacy 0x80000000 medium 0x380000000}]
set ddr {/noc/DDR_MC_PORTS/DDR_CH0_LEGACYx4 /noc/DDR_MC_PORTS/DDR_CH0_MEDx4}
puts [ipiasm::check_memory_regions {legacy 0x80000000 medium 0x380000000}]
puts [ipiasm::check_memory_regions {}]
''')
        self.assertIn("MEM_REGION_FAIL:1/2", out)
        self.assertIn("medium size=0x380000000", out)
        self.assertIn("MEM_REGION:OK 2 region(s)", out)
        self.assertIn("MEM_REGION:NA", out)

    def test_reachability_names_each_unreachable_space(self):
        out = self.run_tcl(r'''
puts [ipiasm::check_required_reachability {/ps/cpu_* DRAM /isp/NMU DRAM}]
puts [ipiasm::check_required_reachability {/ps/cpu_0 /gpio/S_AXI/Reg /isp/NMU DRAM}]
puts [ipiasm::check_required_reachability {/missing/* DRAM}]
puts [ipiasm::check_required_reachability {}]
''')
        self.assertIn("REACH_FAIL:1 required path(s)", out)
        self.assertIn("ps/cpu_1 -> DRAM", out)
        self.assertNotIn("ps/cpu_0 -> DRAM", out)
        self.assertIn("REACH:OK 2 space/target path(s)", out)
        self.assertIn("/missing/* -> DRAM (no matching address space)", out)
        self.assertIn("REACH:NA", out)

    def test_undriven_clocks_ignore_wired_interface_members(self):
        out = self.run_tcl(r'''
puts [ipiasm::check_undriven_clocks]
dict set peers /b/aclk {/ps/pl0_clk}
puts [ipiasm::check_undriven_clocks]
''')
        self.assertIn("UNDRIVEN_CLK_FAIL:1/3 clock input(s)", out)
        self.assertIn("\n  b/aclk", out)
        self.assertNotIn("refclk_p", out)
        self.assertIn("UNDRIVEN_CLK:OK 3 clock input(s)", out)

    def test_required_ports_name_the_port_to_rename(self):
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n" + r'''
set ports {/clk_249997500 {NAME clk_249997500 DIR O TYPE clk CONFIG.FREQ_HZ 249997500}
           /resetn_300 {NAME resetn_300 DIR O TYPE rst CONFIG.FREQ_HZ {}}}
set nets {/clk_249997500 n1 /resetn_300 {}}
proc get_bd_ports {args} {
    if {[regexp {NAME == "([^"]+)"} $args -> n]} {
        return [expr {[dict exists $::ports /$n] ? "/$n" : ""}]
    }
    return [dict keys $::ports]
}
proc get_bd_intf_ports {args} {return {}}
proc get_bd_intf_nets {args} {return {}}
proc get_bd_nets {args} {return [dict get $::nets [lindex $args end]]}
proc get_property {args} {
    set args [lsearch -all -inline -not -exact $args -quiet]
    lassign $args prop obj
    return [dict get $::ports $obj $prop]
}
puts [ipiasm::check_required_ports {{name clk_250 dir O type clk freq_hz 249997500} {name resetn_300 type clk}}]
puts [ipiasm::check_required_ports {{name clk_249997500 freq_hz 249997500}}]
'''
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PORTS_FAIL:3 problem(s) in 2 required port(s)", result.stdout)
        self.assertIn("clk_250 missing (same signal exists as {clk_249997500}: rename it)", result.stdout)
        self.assertIn("resetn_300 TYPE=rst, required clk", result.stdout)
        self.assertIn("resetn_300 unconnected", result.stdout)
        self.assertIn("PORTS:OK 1", result.stdout)

    def test_required_ports_reject_a_vector_where_one_bit_is_required(self):
        # An RTL top declaring separate one-bit interrupt inputs does not bind to
        # one wide port. Name and direction match, the BD validates, and nothing
        # says so until synthesis of the wrapper.
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n" + r'''
set ports {/pl_fpd_irq1 {NAME pl_fpd_irq1 DIR I LEFT 3 RIGHT 0}
           /pl_fpd_irq2 {NAME pl_fpd_irq2 DIR I LEFT {} RIGHT {}}}
proc get_bd_ports {args} {
    if {[regexp {NAME == "([^"]+)"} $args -> n]} {
        return [expr {[dict exists $::ports /$n] ? "/$n" : ""}]
    }
    return [dict keys $::ports]
}
proc get_bd_intf_ports {args} {return {}}
proc get_bd_intf_nets {args} {return {}}
proc get_bd_nets {args} {return n1}
proc get_property {args} {
    set args [lsearch -all -inline -not -exact $args -quiet]
    lassign $args prop obj
    return [dict get $::ports $obj $prop]
}
puts [ipiasm::check_required_ports {{name pl_fpd_irq1 dir I width 1} {name pl_fpd_irq2 dir I width 1}}]
'''
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PORTS_FAIL:1 problem(s) in 2 required port(s)", result.stdout)
        self.assertIn("pl_fpd_irq1 is 4 bit(s), required 1", result.stdout)
        self.assertIn("one port per bit", result.stdout)
        self.assertNotIn("pl_fpd_irq2 is", result.stdout)


class ClockResetChecks(unittest.TestCase):
    FIXTURE = r'''
proc get_bd_cells {args} {
    if {[lsearch $args -of_objects] >= 0} {return [file dirname [lindex $args end]]}
    if {[string match {*TYPE == ip &&*} $args]} {return $::ip_cells}
    if {[string match *smartconnect* $args]} {return $::sc_cells}
    return $::ip_cells
}
proc get_bd_pins {args} {
    set i [lsearch $args -of_objects]
    if {$i >= 0} {return [dict get $::cell_pins [lindex $args [expr {$i + 1}]]]}
    return [lindex $args end]
}
proc get_bd_intf_pins {args} {
    set i [lsearch $args -of_objects]
    if {$i >= 0} {return [dict get $::cell_intfs [lindex $args [expr {$i + 1}]]]}
    return [lindex $args end]
}
proc get_bd_intf_ports {args} {return {}}
proc get_bd_intf_nets {args} {return net}
proc get_bd_nets {args} {
    set obj [lindex $args end]
    if {[dict exists $::nets $obj]} {return [dict get $::nets $obj]}
    return {}
}
proc get_property {args} {
    set name [lindex $args end-1]; set obj [lindex $args end]
    if {[dict exists $::props $obj $name]} {return [dict get $::props $obj $name]}
    return {}
}
proc find_bd_objs {args} {
    set obj [lindex $args end]
    if {[dict exists $::peers $obj]} {return [dict get $::peers $obj]}
    return {}
}
proc set_property {args} {lappend ::sets $args}
proc connect_bd_net {a b} {
    lappend ::connects [list $a $b]
    set net [expr {[dict exists $::nets $a] ? [dict get $::nets $a] : "net_$a"}]
    dict set ::nets $a $net; dict set ::nets $b $net
}
proc ipiasm::_scalar_in_wired_intf {pin} {return 0}
set sets {}; set connects {}; set nets {}; set sc_cells {}
set ip_cells {/noc}
set cell_pins {/noc {/noc/aclk1} /ps {/ps/m_axi_fpd_aclk /ps/pl0_ref_clk}}
set cell_intfs {/noc {/noc/S01_AXI} /ps {/ps/M_AXI_FPD}}
set props {
    /noc/aclk1 {CLASS bd_pin DIR I TYPE clk CONFIG.ASSOCIATED_BUSIF S01_AXI}
    /ps/m_axi_fpd_aclk {CLASS bd_pin DIR I TYPE clk CONFIG.ASSOCIATED_BUSIF M_AXI_FPD}
    /ps/pl0_ref_clk {CLASS bd_pin DIR O TYPE clk}
}
set peers {
    /noc/S01_AXI {/noc/S01_AXI /ps/M_AXI_FPD}
    /ps/m_axi_fpd_aclk {/ps/pl0_ref_clk}
}
'''

    def run_tcl(self, body):
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n" + self.FIXTURE + body
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_undriven_clock_names_the_clock_its_interface_peer_runs_on(self):
        out = self.run_tcl(r'''
puts [ipiasm::check_undriven_clocks]
dict set peers /noc/aclk1 {/ps/pl0_ref_clk}
puts [ipiasm::check_undriven_clocks]
''')
        self.assertIn("UNDRIVEN_CLK_FAIL:1/1 clock input(s)", out)
        self.assertIn("noc/aclk1 carries {S01_AXI} -> drive from ps/pl0_ref_clk", out)
        self.assertIn("-> drive each pin from the clock its interfaces' peers run on", out)
        self.assertIn("UNDRIVEN_CLK:OK 1 clock input(s) driven", out)

    def test_undriven_clock_hint_reports_unresolved_peer(self):
        out = self.run_tcl(r'''
dict set peers /noc/S01_AXI {/noc/S01_AXI}
puts [ipiasm::check_undriven_clocks]
''')
        self.assertIn("noc/aclk1 carries {S01_AXI} -> peer clock unresolved", out)

    def test_checkpoint_refuses_undriven_clocks_from_the_clock_stage(self):
        out = self.run_tcl(r'''
set saved 0
proc save_bd_design {} {incr ::saved}
proc ipiasm::check_undriven_clocks {} {return $::clk}
set clk "UNDRIVEN_CLK_FAIL:1/1 clock input(s) have no driver"
puts "A [ipiasm::checkpoint cells] saved=$saved"
puts "B [ipiasm::checkpoint clocks] saved=$saved"
puts "C [ipiasm::checkpoint plan_connections] saved=$saved"
puts "D [ipiasm::checkpoint pre_remove_noc_S01_AXI] saved=$saved"
set clk "UNDRIVEN_CLK:OK 3 clock input(s) driven"
puts "E [ipiasm::checkpoint qos] saved=$saved"
''')
        self.assertRegex(out, r"A CHECKPOINT:cells .* saved=1")
        self.assertIn("B CHECKPOINT_FAIL:clocks UNDRIVEN_CLK_FAIL:1/1 clock input(s) have no driver saved=1", out)
        self.assertIn("C CHECKPOINT_FAIL:plan_connections UNDRIVEN_CLK_FAIL", out)
        self.assertRegex(out, r"D CHECKPOINT:pre_remove_noc_S01_AXI .* saved=2")
        self.assertRegex(out, r"E CHECKPOINT:qos .* saved=3")

    def test_noc_port_gets_its_own_aclk_driven_by_the_master_clock(self):
        out = self.run_tcl(r'''
proc ipiasm::_noc_clk_for_port {noc port} {return aclk3}
puts "A [ipiasm::_attach_noc_port_clock /noc S08_AXI /ps/M_AXI_FPD]"
puts "sets=$sets"
puts "connects=$connects"
puts "B [ipiasm::_attach_noc_port_clock /noc S08_AXI /ps/M_AXI_FPD] connects=[llength $connects]"
dict set nets /noc/aclk3 other_net
puts "C [ipiasm::_attach_noc_port_clock /noc S08_AXI /ps/M_AXI_FPD]"
dict set props /ps/m_axi_fpd_aclk CONFIG.ASSOCIATED_BUSIF {}
puts "D [ipiasm::_attach_noc_port_clock /noc S08_AXI /ps/M_AXI_FPD]"
dict set props /ps/m_axi_fpd_aclk CONFIG.ASSOCIATED_BUSIF M_AXI_FPD
dict unset peers /ps/m_axi_fpd_aclk
puts "E [ipiasm::_attach_noc_port_clock /noc S08_AXI /ps/M_AXI_FPD]"
''')
        self.assertIn("A clk:S08_AXI<-aclk3<-ps/pl0_ref_clk", out)
        self.assertIn("CONFIG.ASSOCIATED_BUSIF S08_AXI /noc/aclk3", out)
        self.assertIn("connects={/ps/pl0_ref_clk /noc/aclk3}", out)
        self.assertIn("B clk:S08_AXI<-aclk3<-ps/pl0_ref_clk connects=1", out)
        self.assertIn("C NOC_FAIL:/noc/aclk3 is on other_net, not the master clock ps/pl0_ref_clk", out)
        self.assertIn("D WARN:clock-unassociated(0 clocks list M_AXI_FPD", out)
        self.assertIn("E WARN:clock-unassociated(ps/m_axi_fpd_aclk has no driver yet)", out)

    def test_noc_clock_lookup_never_shrinks_an_unreadable_noc(self):
        out = self.run_tcl(r'''
proc get_bd_cells {args} {return [dict get $::cells [lindex $args end]]}
set cells {/noc /noc noc /noc /missing {}}
dict set props /noc CONFIG.NUM_CLKS 2
dict set props /noc/aclk0 CONFIG.ASSOCIATED_BUSIF S00_AXI
dict set props /noc/aclk1 CONFIG.ASSOCIATED_BUSIF S01_AXI
proc ipiasm::_net_wired {pin} {return 1}
puts "A [ipiasm::_noc_clk_for_port noc S01_AXI]"
puts "B [ipiasm::_noc_clk_for_port /noc S08_AXI] sets=$sets"
set sets {}
puts "C {[ipiasm::_noc_clk_for_port /missing S08_AXI]} sets=$sets"
dict set props /noc CONFIG.NUM_CLKS {}
puts "D {[ipiasm::_noc_clk_for_port /noc S08_AXI]} sets=$sets"
''')
        self.assertIn("A aclk1", out)
        self.assertIn("B aclk2 sets={CONFIG.NUM_CLKS 3 /noc}", out)
        self.assertIn("C {} sets=", out)
        self.assertIn("D {} sets=\n", out + "\n")

    def test_noc_port_clock_refusal_changes_nothing(self):
        out = self.run_tcl(r'''
proc ipiasm::_noc_clk_for_port {noc port} {return aclk3}
dict set nets /noc/aclk3 other_net
puts "A [ipiasm::_attach_noc_port_clock /noc S08_AXI /ps/M_AXI_FPD] sets=[llength $sets] connects=[llength $connects]"
''')
        self.assertIn("A NOC_FAIL:/noc/aclk3 is on other_net", out)
        self.assertIn("sets=0 connects=0", out)

    def test_clock_domains_checked_on_non_smartconnect_connections(self):
        out = self.run_tcl(r'''
set ip_cells {/ps /noc}
set cell_intfs {/ps {/ps/M_AXI_FPD} /noc {/noc/S01_AXI}}
set peers {/ps/M_AXI_FPD {/ps/M_AXI_FPD /noc/S01_AXI} /noc/S01_AXI {/noc/S01_AXI /ps/M_AXI_FPD}}
proc ipiasm::_interface_clock_facts {intf} {return [dict create domain [dict get $::domains $intf] hz 100 clocks {}]}
set domains {/ps/M_AXI_FPD d_pl0 /noc/S01_AXI d_pl0}
puts "A [ipiasm::check_interface_clock_domains]"
set domains {/ps/M_AXI_FPD d_pl0 /noc/S01_AXI d_pl1}
puts "B [ipiasm::check_interface_clock_domains]"
set domains {/ps/M_AXI_FPD d_pl0 /noc/S01_AXI {}}
puts "C [ipiasm::check_interface_clock_domains]"
dict set props /noc VLNV xilinx.com:ip:smartconnect:1.0
set ip_cells {/ps}
set domains {/ps/M_AXI_FPD d_pl0 /noc/S01_AXI d_pl1}
puts "D [ipiasm::check_interface_clock_domains]"
set ip_cells {}
puts "E [ipiasm::check_interface_clock_domains]"
''')
        self.assertIn("A CLOCK_DOMAIN:OK checked=1 unresolved=0 interface peer relations", out)
        self.assertIn("B CLOCK_DOMAIN_FAIL:1/1 interface peer relation(s) mismatched", out)
        self.assertIn("/ps/M_AXI_FPD(domain=d_pl0 freq=100)", out)
        self.assertIn("C CLOCK_DOMAIN:OK checked=0 unresolved=1", out)
        self.assertIn("D CLOCK_DOMAIN:NA no connected interfaces", out)
        self.assertIn("E CLOCK_DOMAIN:NA no connected interfaces", out)

    def test_validate_fails_on_reset_association_messages(self):
        out = self.run_tcl(r'''
proc ipiasm::preflight_interface_contracts {} {return INTF_PREFLIGHT:OK}
proc ipiasm::check_interface_clock_domains {} {return CLOCK_DOMAIN:OK}
proc ipiasm::check_mipi_video_clocks {} {return VIDEOCLK:NA}
proc ipiasm::_illegal_noc_findings {args} {return {}}
set counts {}
proc ipiasm::_msg_count {id} {
    if {[dict exists $::counts $id]} {return [dict get $::counts $id]}
    return 0
}
proc validate_bd_design {args} {foreach id $::emit {dict incr ::counts $id}}
foreach id {{BD 41-1343} {BD 41-1347} {BD 41-1660}} {
    set emit [list $id]
    puts [ipiasm::validate]
}
set emit {}
puts [ipiasm::validate]
''')
        self.assertIn("VALIDATE:FAIL:CLOCK_RESET_SYNC [BD 41-1343] x1 -- a reset is synchronous to a different clock; add a proc_sys_reset", out)
        self.assertIn("[BD 41-1347] x1 -- a reset pin is driven by an asynchronous source; use peripheral_aresetn", out)
        self.assertIn("[BD 41-1660] x1 -- an external reset is not associated with its clock; set CONFIG.ASSOCIATED_RESET", out)
        self.assertTrue(out.rstrip().endswith("VALIDATE:PASS"), out)


class IdempotentRepairs(unittest.TestCase):
    FIXTURE = r'''
namespace eval ipcfg {
    proc vlnv_ok {vlnv} {return 1}
    proc create_cell {vlnv cell} {return SUCCESS:$cell}
}
proc get_bd_pins {args} {return [lindex $args end]}
proc get_bd_intf_pins {args} {return [lindex $args end]}
proc get_bd_cells {args} {return [lindex $args end]}
proc get_bd_nets {args} {
    set obj [lindex $args end]
    if {[dict exists $::nets $obj]} {return [dict get $::nets $obj]}
    return {}
}
proc get_bd_intf_nets {args} {
    set obj [lindex $args end]
    if {[dict exists $::intf_nets $obj]} {return [dict get $::intf_nets $obj]}
    return {}
}
proc get_property {args} {
    set name [lindex $args end-1]; set obj [lindex $args end]
    if {[dict exists $::props $obj $name]} {return [dict get $::props $obj $name]}
    return {}
}
proc set_property {args} {}
proc connect_bd_net {a b} {
    lappend ::connects [list $a $b]
    set net [expr {[dict exists $::nets $a] ? [dict get $::nets $a] : "net_$a"}]
    dict set ::nets $a $net; dict set ::nets $b $net
}
proc connect_bd_intf_net {a b} {
    lappend ::connects [list $a $b]
    dict set ::intf_nets $a inet_$a; dict set ::intf_nets $b inet_$a
}
proc ipiasm::check_connect {src dst} {return LEGAL:1}
proc ipiasm::_net_wired {pin} {return [dict exists $::nets $pin]}
set nets {}; set intf_nets {}; set connects {}; set props {}
'''

    def run_tcl(self, body):
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n" + self.FIXTURE + body
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_connect_net_skips_pins_already_on_one_net(self):
        out = self.run_tcl(r'''
puts "A [ipiasm::connect_net /ps/clk /gpio/aclk] connects=[llength $connects]"
puts "B [ipiasm::connect_net /ps/clk /gpio/aclk] connects=[llength $connects]"
dict set nets /uart/aclk other_net
puts "C [ipiasm::connect_net /ps/clk /uart/aclk] connects=[llength $connects]"
''')
        self.assertIn("A SUCCESS:net /ps/clk -> /gpio/aclk connects=1", out)
        self.assertIn("B SKIP:already connected /ps/clk<->/gpio/aclk connects=1", out)
        self.assertIn("C SUCCESS:net /ps/clk -> /uart/aclk connects=2", out)

    def test_control_fanout_rerun_adds_no_connections(self):
        out = self.run_tcl(r'''
set r1 [ipiasm::build_control_fanout /ps/M_AXI {/gpio/S_AXI} sc xilinx.com:ip:smartconnect /ps/clk /rst/out]
set n1 [llength $connects]
set r2 [ipiasm::build_control_fanout /ps/M_AXI {/gpio/S_AXI} sc xilinx.com:ip:smartconnect /ps/clk /rst/out]
puts "R1 $r1 connects=$n1"
puts "R2 $r2 connects=[llength $connects]"
''')
        self.assertIn("R1 SUCCESS:control_fanout sc SI=1 MI=1 wired=1 clkrst={aclk<-/ps/clk aresetn<-/rst/out} connects=4", out)
        self.assertIn("R2 SUCCESS:control_fanout sc SI=1 MI=1 wired=1 clkrst={aclk<-/ps/clk aresetn<-/rst/out} connects=4", out)

    def test_shared_clock_net_proves_same_domain_despite_stale_labels(self):
        out = self.run_tcl(r'''
proc ipiasm::_interface_clock_facts {intf} {return [dict get $::facts $intf]}
set facts {/a/M_AXI {domain stale_a hz 100 clocks /a/clk} /b/S_AXI {domain stale_b hz 100 clocks /b/clk}}
set nets {/a/clk clk_net /b/clk clk_net}
puts "A [ipiasm::_compare_intf_clocks /a/M_AXI /b/S_AXI]"
set nets {/a/clk clk_net /b/clk other_net}
puts "B [ipiasm::_compare_intf_clocks /a/M_AXI /b/S_AXI]"
''')
        self.assertIn("A ok", out)
        self.assertIn("B bad {/a/M_AXI(domain=stale_a freq=100)<->", out)


class ClockPlanPreflight(unittest.TestCase):
    """Read-only pre-flight, batched contract defects and cross-session restore."""

    FIXTURE = r'''
proc current_project {} {return project}
proc current_bd_design {} {return design}
proc get_bd_ports {args} {return {}}
proc get_bd_pins {args} {
    set path [lindex $args end]
    if {[dict exists $::props $path]} {return [list $path]}
    return {}
}
proc get_bd_cells {args} {
    set path [lindex $args end]
    if {[dict exists $::props $path]} {return [list $path]}
    return {}
}
proc list_property {object} {
    if {$object eq "project"} {return DIRECTORY}
    if {[dict exists $::props $object]} {return [dict keys [dict get $::props $object]]}
    return {}
}
proc get_property {name object} {
    if {$object eq "project" && $name eq "DIRECTORY"} {return /scratch}
    if {[dict exists $::props $object $name]} {return [dict get $::props $object $name]}
    return {}
}
proc ipiasm::feasibility_fingerprint {sources} {return fixture_digest}
set props {}
'''

    def run_tcl(self, body):
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n"
        script += self.FIXTURE
        script += "if {[catch {\n" + body + "\n} detail]} {puts stderr $detail; exit 1}\n"
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_preflight_names_actual_frequency_and_polarity_in_one_pass(self):
        # The two facts a clock stage otherwise learns one failed source at a
        # time. 99999001 never satisfies a contract written for 100000000.
        out = self.run_tcl(r'''
dict set props /gen/clk_out1 CONFIG.FREQ_HZ 99999001
dict set props /ref/resetn TYPE rst
dict set props /ref/resetn CONFIG.POLARITY ACTIVE_HIGH
set check {id c0 kind clock_frequency subject s expected 100000000 source f stage clocks
           driver /gen/clk_out1 input i reference r reference_hz 100000000 tolerance_hz 0}
set reset {cell /rst clock /gen/clk_out1 source /ref/resetn source_polarity ACTIVE_LOW
           lock /gen/locked sinks {{/c/resetn peripheral_aresetn}}}
set result [ipiasm::preflight_clock_plan [list $check] [list $reset]]
if {![string match {CLOCK_PREFLIGHT_FAIL:2 defect(s):*} $result]} {error "not batched: $result"}
foreach fragment {{driver reports 99999001Hz} {delta -999} {source carries ACTIVE_HIGH}} {
    if {[string first $fragment $result] < 0} {error "missing '$fragment' in: $result"}
}
puts $result
''')
        self.assertIn("CLOCK_PREFLIGHT_FAIL:2 defect(s)", out)

    def test_preflight_counts_uncreated_pins_as_pending_not_defects(self):
        # Safe to run before the stage builds anything.
        self.run_tcl(r'''
set check {id c0 kind clock_frequency subject s expected 100000000 source f stage clocks
           driver /not/yet input i reference r reference_hz 100000000 tolerance_hz 0}
set result [ipiasm::preflight_clock_plan [list $check] {}]
if {$result ne "CLOCK_PREFLIGHT:OK proven=0 pending=1"} {error $result}
''')

    def test_set_clock_contracts_reports_every_defect_at_once(self):
        self.run_tcl(r'''
set a {id c0 kind clock_frequency subject s expected 100 source f stage clocks
       driver /gen/a input i reference r reference_hz 100 tolerance_hz 0}
set b {id c0 kind wrong_kind subject s expected 100 source f stage cells
       driver /gen/b input i reference r reference_hz 100 tolerance_hz 0}
set reset {cell /rst clock /nowhere source /ref/resetn source_polarity SIDEWAYS
           lock /gen/locked sinks {{/c/resetn not_a_reset_output}}}
set result [ipiasm::set_clock_contracts [list $a $b] [list $reset] {} fixture_digest]
if {![string match {CLOCK_CONTRACT_FAIL:6 defect(s):*} $result]} {error "not batched: $result"}
foreach fragment {
    {clock c0: expected clock_frequency check}
    {clock c0: clock contract must belong to clocks}
    {clock c0: duplicate clock ID}
    {reset /rst: invalid reset polarity SIDEWAYS}
    {reset /rst: reset clock has no frequency contract}
    {reset sink must be {pin proc_sys_reset_output}}
} {
    if {[string first $fragment $result] < 0} {error "missing '$fragment' in: $result"}
}
''')

    def test_clock_contracts_survive_a_session_through_a_file(self):
        with tempfile.TemporaryDirectory(prefix="clock contracts ") as directory:
            path = os.path.join(directory, "clock_contracts.tcldict")
            self.run_tcl(rf'''
set check {{id c0 kind clock_frequency subject s expected 100000000 source f stage clocks
            driver /gen/clk_out1 input i reference r reference_hz 100000000 tolerance_hz 0}}
set registered [ipiasm::set_clock_contracts [list $check] {{}} {{}} fixture_digest]
if {{![string match CLOCK_CONTRACTS:SET* $registered]}} {{error $registered}}
set saved [ipiasm::save_clock_contracts {{{path}}}]
if {{![string match CLOCK_CONTRACTS:SAVED* $saved]}} {{error $saved}}

# A new session starts with nothing registered.
set ::ipiasm::clock_contracts {{}}
set restored [ipiasm::load_clock_contracts {{{path}}}]
if {{$restored ne "CLOCK_CONTRACTS:RESTORED clocks=1 resets=0"}} {{error $restored}}
if {{[dict get $::ipiasm::clock_contracts fingerprint] ne "fixture_digest"}} {{
    error "restore lost the fingerprint"
}}

# Restoring into a different design is not a retry.
set ::ipiasm::clock_contracts {{}}
proc current_bd_design {{}} {{return other_design}}
set refused [ipiasm::load_clock_contracts {{{path}}}]
if {{![string match CLOCK_CONTRACTS_LOAD_FAIL:* $refused]}} {{error $refused}}
''')

    def test_save_refuses_when_nothing_is_registered(self):
        self.run_tcl(r'''
set result [ipiasm::save_clock_contracts /tmp/never_written.tcldict]
if {$result ne "CLOCK_CONTRACTS_SAVE_FAIL:nothing registered"} {error $result}
if {[file exists /tmp/never_written.tcldict]} {error "refusal still wrote a file"}
''')


if __name__ == "__main__":
    unittest.main()