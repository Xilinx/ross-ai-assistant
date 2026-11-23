# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest


SKILLS = Path(__file__).resolve().parents[3]
CONFIGURATOR = SKILLS / "vivado-ip-configurator/lib/ipcfg/ipcfg.tcl"
ASSEMBLER = SKILLS / "vivado-ipi-assembler/lib/ipiasm.tcl"
sys.path.insert(0, str(SKILLS / "vivado-ipi-assembler/scripts"))
import compile_assembly_plan as compiler

MODULE_MOCKS = r'''
set exists 0
set registered {}
set source_writes 0
set compile_writes 0
set properties {TYPE ip REF_NAME {} VLNV xilinx.com:module_ref:rtl_block:1.0 CONFIG.WIDTH 1}
rename get_property base_get_property
proc get_property {key object} {
    if {[dict exists $::properties $key]} {return [dict get $::properties $key]}
    return [base_get_property $key $object]
}
proc current_fileset {} {return sources_1}
proc get_files {args} {return $::registered}
proc add_files {args} {
    assert {[lrange $args 0 2] eq {-norecurse -fileset sources_1}}
    incr ::source_writes
    set ::registered [concat $::registered [lindex $args end]]
}
proc update_compile_order {args} {
    assert {$args eq {-fileset sources_1}}
    incr ::compile_writes
}
proc create_bd_cell {args} {
    assert {$args eq {-type module -reference rtl_block cell}}
    mutate
    set ::exists 1
    return /cell
}
proc set_property {args} {
    mutate
    dict for {key value} [lindex $args 1] {dict set ::properties $key $value}
}
set source [file join $root {block file.v}]
ipiasm::_gate_set board 1 test
'''
I11_PROCEDURES = {
    "::ipcfg::" + name for name in (
        "attempt_call", "_attempt_save", "create_cell", "apply_dict",
        "autofix_apply", "board_bind", "try_automation", "run_automation",
        "add_stub", "create_cell_cfg", "resolve_subkeys", "probe_subkey_range",
        "_probe_subkey_range",
    )
} | {
    "::ipiasm::" + name for name in (
        "create_cell", "connect_automation", "connect_axi_auto", "connect_external_axi",
    )
}


class AttemptBudgetTests(unittest.TestCase):
    procedures = set()
    hits = set()
    tests_by_procedure = {}
    timings = {}

    @classmethod
    def tearDownClass(cls):
        destination = os.environ.get("IPCFG_COVERAGE_REPORT")
        if destination:
            report = {
                "metric": "Tcl procedure entry coverage, not statement or branch coverage",
                "scope": "test_attempt_budget.py; original library bodies only; mocked replacements excluded",
                "total": len(cls.procedures),
                "executed": len(cls.hits),
                "procedures": {name: name in cls.hits for name in sorted(cls.procedures)},
                "tests_by_procedure": {
                    name: sorted(cls.tests_by_procedure.get(name, set()))
                    for name in sorted(cls.procedures)
                },
                "i11_procedures": {name: name in cls.hits for name in sorted(I11_PROCEDURES)},
                "timings_us": cls.timings,
                "timing_scope": "Instrumented tclsh with mocked Vivado; inclusive and exclusive elapsed time, not live performance",
            }
            live = {}
            live_source = os.environ.get("IPCFG_LIVE_COVERAGE_REPORT")
            if live_source:
                for line in Path(live_source).read_text().splitlines():
                    name, count = line.split("\t")
                    live[name] = int(count)
                if set(live) != cls.procedures:
                    raise AssertionError("Live and unit procedure inventories differ")
                report["live_entry_counts"] = live
                report["combined_executed"] = sum(
                    name in cls.hits or live[name] > 0 for name in cls.procedures
                )
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2) + "\n")
            lines = [
                "# Tcl Procedure Coverage", "",
                "Metric: actual procedure entry, not statement, branch, or behavioral coverage.",
                "Mocked replacements are excluded; original library reloads remain traced.",
                "Only the retry suite is instrumented here; binder regressions run separately.", "",
                f"- Unit: {len(cls.hits)}/{len(cls.procedures)} procedures.",
                f"- I11 unit scope: {len(cls.hits & I11_PROCEDURES)}/{len(I11_PROCEDURES)} procedures.",
            ]
            if live_source:
                lines.extend([
                    f"- Live: {sum(count > 0 for count in live.values())}/{len(live)} procedures.",
                    f"- Combined: {report['combined_executed']}/{len(cls.procedures)} procedures.",
                    "- Live evidence: four isolated IP test groups on native Vivado 2026.1.",
                ])
            lines.extend([
                "", "Each procedure is listed below. A dash means no recorded execution.",
                "I11 names are the guarded configurator helpers and assembler creation/connection entry points.",
                "", "| Procedure | I11 | Unit Tests | Live Entries |",
                "| --- | --- | --- | --- |",
            ])
            for name in sorted(cls.procedures):
                tests = ", ".join(
                    test.rsplit(".", 1)[-1]
                    for test in sorted(cls.tests_by_procedure.get(name, set()))
                ) or "-"
                lines.append(
                    f"| `{name}` | {'yes' if name in I11_PROCEDURES else '-'} | "
                    f"{tests} | {live.get(name, '-')} |"
                )
            path.with_suffix(".md").write_text("\n".join(lines) + "\n")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_tcl(self, body, expected_exit=0):
        setup = r'''
set writes 0
set exists 1
set fail 0
set fast_fail 0
set hierarchy /
set design design
proc current_project {} {return project}
proc current_bd_design {} {return $::design}
proc current_bd_instance {arg} {return $::hierarchy}
proc get_property {key object} {
    if {$key eq "DIRECTORY"} {return $::root}
    if {$key eq "MODE"} {return Slave}
    return 0
}
proc get_bd_cells {args} {
    if {!$::exists} {return {}}
    set name [lindex $args end]
    if {$name eq "-quiet"} {return /cell}
    if {[string match {/*} $name]} {return $name}
    return "[string trimright $::hierarchy /]/$name"
}
proc mutate {} {
    incr ::writes
    if {$::fail} {error "simulated Vivado rejection"}
    return mutated
}
proc create_bd_cell {args} {
    if {$::fast_fail && "-set_param" in $args} {
        incr ::writes
        error "unsupported -set_param"
    }
    set result [mutate]
    set ::exists 1
    return $result
}
proc set_property {args} {mutate}
proc apply_bd_automation {args} {mutate}
proc get_bd_intf_pins {args} {return [lindex $args end]}
proc get_bd_pins {args} {return [lindex $args end]}
proc get_bd_intf_ports {args} {return stub}
proc create_bd_intf_port {args} {mutate}
proc connect_bd_intf_net {args} {return {}}
proc list_property {args} {return {}}
proc assert {expression} {
    if {![uplevel 1 [list expr $expression]]} {error "assertion failed: $expression"}
}
proc count_for {identity} {
    set channel [open [file join $::root .ipcfg attempts.tcldict]]
    set state [read $channel]
    close $channel
    return [dict get $state [list $::design $identity] count]
}
'''
        coverage = f'source {{{Path(__file__).resolve().parents[1] / "support/procedure_trace.tcl"}}}\n'
        script = (
            f'set root {{{self.root}}}\n'
            f'source {{{CONFIGURATOR}}}\n'
            f'source {{{ASSEMBLER}}}\n'
            + coverage
            + setup
            + '\nif {[catch {\n' + body
            + '\n} message options]} {puts stderr "$message\\n[dict get $options -errorinfo]"; exit 1}\n'
            + 'puts TEST_PASS\n'
        )
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        for line in result.stdout.splitlines():
            if line.startswith("COV_DECL "):
                self.procedures.add(line.split()[1])
            elif line.startswith("COV_HIT "):
                name = line.split()[1]
                self.hits.add(name)
                self.tests_by_procedure.setdefault(name, set()).add(self.id())
            elif line.startswith("COV_TIME "):
                _, name, inclusive, exclusive, code, *_ = line.split()
                self.timings.setdefault(name, []).append({
                    "test": self.id(), "inclusive": int(inclusive),
                    "exclusive": int(exclusive), "return_code": int(code),
                })
        self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
        if expected_exit == 0:
            self.assertIn("TEST_PASS", result.stdout)
        return result.stdout

    def test_shared_limit_across_configuration_automation_and_stub(self):
        self.run_tcl(r'''
set fail 1
for {set index 0} {$index < 10} {incr index} {
    switch [expr {$index % 3}] {
        0 {ipcfg::apply_dict cell {CONFIG.TEST 1}}
        1 {ipcfg::try_automation /cell rule}
        2 {ipcfg::add_stub cell S_AXI interface}
    }
}
assert {$writes == 10}
set result [ipcfg::apply_dict cell {CONFIG.TEST 1}]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 10 && [count_for /cell] == 10}
''')

    def test_replay_does_not_write_or_consume_attempts(self):
        self.run_tcl(r'''
set properties {CONFIG.TEST 0}
rename get_property base_get_property
proc get_property {key object} {
    if {[dict exists $::properties $key]} {return [dict get $::properties $key]}
    return [base_get_property $key $object]
}
proc list_property {args} {return [dict keys $::properties]}
proc set_property {args} {
    mutate
    dict for {key value} [lindex $args 1] {dict set ::properties $key $value}
}
ipcfg::require cell {CONFIG.TEST 1} first
ipcfg::require cell {CONFIG.TEST 1} replay
ipcfg::require cell {CONFIG.TEST 1} replay
puts "REPLAY_METRIC writes=$writes attempts=[count_for /cell]"
assert {$writes == 1 && [count_for /cell] == 1}
assert {[dict get [lindex [ipcfg::requirements_data cell] 0] base] eq "0"}
ipcfg::require cell {CONFIG.TEST 2} drift
assert {$writes == 2 && [count_for /cell] == 2}
''')

    def test_satisfied_exhausted_cell_is_read_only(self):
        self.run_tcl(r'''
proc list_property {args} {return CONFIG.TEST}
for {set index 0} {$index < 10} {incr index} {
    ipcfg::attempt_call cell configure {mutate}
}
set result [ipcfg::apply_dict cell {CONFIG.TEST 0}]
assert {[string match SUCCESS:* $result] && $writes == 10 && [count_for /cell] == 10}
set result [ipcfg::apply_dict cell {CONFIG.TEST 1}]
assert {[string match *ATTEMPT_LIMIT* $result] && $writes == 10}
''')

    def test_board_replay_preserves_ownership(self):
        self.run_tcl(r'''
set properties {CONFIG.GPIO_BOARD_INTERFACE Custom CONFIG.WIDTH 1}
rename get_property base_get_property
proc get_property {key object} {
    if {[dict exists $::properties $key]} {return [dict get $::properties $key]}
    return [base_get_property $key $object]
}
proc list_property {args} {return [dict keys $::properties]}
proc set_property {args} {
    mutate
    dict for {key value} [lindex $args 1] {dict set ::properties $key $value}
    dict set ::properties CONFIG.WIDTH 4
}
set result [ipcfg::board_bind cell {CONFIG.GPIO_BOARD_INTERFACE board_gpio}]
assert {[string match BOARD:bound* $result]}
set before $::ipcfg::board_owned
ipcfg::board_bind cell {CONFIG.GPIO_BOARD_INTERFACE board_gpio}
assert {$writes == 1 && [count_for /cell] == 1 && $::ipcfg::board_owned eq $before}
set ::ipcfg::board_owned {}
set result [ipcfg::board_bind cell {CONFIG.GPIO_BOARD_INTERFACE board_gpio}]
assert {[string match BOARD_FAIL:OWNERSHIP_UNKNOWN* $result] && $writes == 1}
''')

    def test_board_success_output_is_bounded(self):
        self.run_tcl(r'''
set properties {CONFIG.GPIO_BOARD_INTERFACE Custom}
rename get_property base_get_property
proc get_property {args} {
    lassign [lrange $args end-1 end] key object
    if {[dict exists $::properties $key]} {return [dict get $::properties $key]}
    return [base_get_property $key $object]
}
proc list_property {args} {return [dict keys $::properties]}
proc set_property {args} {
    mutate
    dict for {key value} [lindex $args 1] {dict set ::properties $key $value}
    for {set index 0} {$index < 1000} {incr index} {
        dict set ::properties CONFIG.PRESET_PARAMETER_$index $index
    }
}
set result [ipcfg::board_bind cell {CONFIG.GPIO_BOARD_INTERFACE board_gpio}]
puts "BOARD_OUTPUT_BYTES:[string bytelength $result]"
assert {[string match {BOARD:bound cell hooks=1 owned=1001*} $result]}
assert {[llength [lindex [dict get $::ipcfg::board_owned cell] 0]] == 1001}
assert {[string bytelength $result] < 256}
assert {$writes == 1 && [count_for /cell] == 1}
''')

    def test_module_reference_creation_config_and_repeat(self):
        (self.root / "block file.v").write_text("module rtl_block; endmodule\n")
        (self.root / "dependency.v").write_text("module dependency; endmodule\n")
        self.run_tcl(MODULE_MOCKS + r'''
set sources [list $source [file join $root dependency.v]]
assert {[string match SUCCESS:* [ipiasm::create_module_ref rtl_block cell $sources {CONFIG.WIDTH 8}]]}
assert {$source_writes == 1 && $writes == 2 && [count_for /cell] == 3}
assert {[llength $registered] == 2 && [dict get $properties CONFIG.WIDTH] == 8}
set previous_compile $compile_writes
assert {[string match SUCCESS:* [ipiasm::create_module_ref rtl_block cell $sources {CONFIG.WIDTH 8}]]}
assert {$source_writes == 1 && $writes == 2 && [count_for /cell] == 3 && $compile_writes == $previous_compile}
dict set properties VLNV xilinx.com:module_ref:another_module:1.0
assert {[string match CONFIGURE_FAIL:MODULE_REF:* [ipiasm::create_module_ref rtl_block cell $sources]]}
dict set properties VLNV xilinx.com:ip:rtl_block:1.0
assert {[string match CONFIGURE_FAIL:MODULE_REF:* [ipiasm::create_module_ref rtl_block cell $sources]]}
assert {$writes == 2 && $source_writes == 1}
''')

    def test_module_reference_gate_missing_sources_and_config(self):
        self.run_tcl(MODULE_MOCKS + r'''
ipiasm::_gate_set board 0
assert {[string match GATE_FAIL:* [ipiasm::create_module_ref rtl_block cell [list $source]]]}
ipiasm::_gate_set board 1 test
assert {[string match CONFIGURE_FAIL:MODULE_SOURCE:* [ipiasm::create_module_ref rtl_block cell [list $source]]]}
assert {[string match CONFIGURE_FAIL:MODULE_REF:* [ipiasm::create_module_ref rtl_block cell {}]]}
assert {[string match CONFIGURE_FAIL:MODULE_REF:* [ipiasm::create_module_ref rtl_block cell [list $source] {WIDTH 8}]]}
assert {$writes == 0 && $source_writes == 0 && $compile_writes == 0}
assert {![file exists [file join $root .ipcfg attempts.tcldict]]}
''')

    def test_module_reference_exhaustion_persists_across_processes(self):
        (self.root / "block file.v").write_text("module rtl_block; endmodule\n")
        self.run_tcl(MODULE_MOCKS + r'''
set fail 1
for {set index 0} {$index < 10} {incr index} {
    set result [ipiasm::create_module_ref rtl_block cell [list $source]]
}
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$source_writes == 1 && $writes == 9 && !$exists && [count_for /cell] == 10}
''')
        self.run_tcl(MODULE_MOCKS + r'''
set result [ipiasm::create_module_ref rtl_block cell [list $source]]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 0 && $source_writes == 0 && $compile_writes == 0 && [count_for /cell] == 10}
''')

    def test_compiled_module_reference_source_failure_stops_plan(self):
        plan = {"schema_version": 1, "stages": [{"name": "cells", "operations": [
            {"op": "call", "proc": "ipiasm::create_module_ref",
             "args": ["rtl_block", "cell", [str(self.root / "missing.v")]]},
            {"op": "call", "proc": "ipiasm::create_cell", "args": ["vendor:ip", "later"]},
        ]}]}
        path = self.root / "plan.tcl"
        path.write_text(compiler.compile_plan(plan))
        self.run_tcl(MODULE_MOCKS + f'''
assert {{[catch {{source {{{path}}}}} diagnostic] == 1}}
assert {{[string match *MODULE_SOURCE* $diagnostic]}}
assert {{$writes == 0 && $source_writes == 0 && $compile_writes == 0}}
''')

    def test_module_reference_source_registration_failure(self):
        (self.root / "block file.v").write_text("module rtl_block; endmodule\n")
        self.run_tcl(MODULE_MOCKS + r'''
proc add_files {args} {incr ::source_writes; error SOURCE_REGISTRATION_FAILED}
set result [ipiasm::create_module_ref rtl_block cell [list $source]]
assert {[string match *SOURCE_REGISTRATION_FAILED* $result]}
assert {$source_writes == 1 && $writes == 0 && $compile_writes == 0 && [count_for /cell] == 1}
''')

    def test_module_reference_silent_config_rejection(self):
        (self.root / "block file.v").write_text("module rtl_block; endmodule\n")
        self.run_tcl(MODULE_MOCKS + r'''
proc set_property {args} {mutate}
set result [ipiasm::create_module_ref rtl_block cell [list $source] {CONFIG.WIDTH 8}]
assert {[string match CONFIGURE_FAIL:MODULE_CONFIG:* $result]}
assert {$source_writes == 1 && $writes == 2 && [count_for /cell] == 3}
''')

    def test_compiled_module_reference_success_and_literal_arguments(self):
        source = self.root / "block file;[error INJECTED].v"
        source.write_text("module rtl_block; endmodule\n")
        plan = {"schema_version": 1, "stages": [{"name": "cells", "operations": [
            {"op": "call", "proc": "ipiasm::create_module_ref",
             "args": ["rtl_block", "cell", [str(source)], {"CONFIG.WIDTH": 8}]},
        ]}]}
        path = self.root / "plan.tcl"
        path.write_text(compiler.compile_plan(plan))
        self.run_tcl(MODULE_MOCKS + f'''
assert {{[string match ASSEMBLY_PLAN:PASS* [source {{{path}}}]]}}
assert {{$source_writes == 1 && $writes == 2 && [count_for /cell] == 3}}
assert {{[llength $registered] == 1 && [file tail [lindex $registered 0]] eq {{block file;[error INJECTED].v}}}}
''')

    def test_success_and_noop_accounting(self):
        self.run_tcl(r'''
ipcfg::create_cell vendor:ip cell
ipcfg::apply_dict cell {}
assert {$writes == 0}
for {set index 0} {$index < 10} {incr index} {
    assert {[ipcfg::apply_dict cell {CONFIG.TEST 1}] eq "SUCCESS:applied"}
}
assert {[string match *ATTEMPT_LIMIT* [ipcfg::apply_dict cell {CONFIG.TEST 2}]]}
assert {$writes == 10}
''')

    def test_nested_creation_fallback_counts_actual_attempts(self):
        self.run_tcl(r'''
set exists 0
set fast_fail 1
assert {[string match SUCCESS:* [ipcfg::create_cell_cfg vendor:ip cell {CONFIG.TEST 1}]]}
assert {$writes == 3 && [count_for /cell] == 3}
''')

    def test_fallback_cannot_write_after_tenth_attempt(self):
        self.run_tcl(r'''
for {set index 0} {$index < 9} {incr index} {ipcfg::apply_dict cell {CONFIG.TEST 1}}
set exists 0
set fast_fail 1
set result [ipcfg::create_cell_cfg vendor:ip cell {CONFIG.TEST 1}]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 10 && !$exists}
''')

    def test_autofix_cannot_bypass_limit(self):
        self.run_tcl(r'''
set fail 1
for {set index 0} {$index < 9} {incr index} {ipcfg::apply_dict cell {CONFIG.TEST 1}}
set result [ipcfg::autofix_apply cell {CONFIG.TEST 1 CONFIG.OTHER 2} {} 100]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 10}
''')

    def test_resume_new_process_and_resource_isolation(self):
        self.run_tcl(r'''
for {set index 0} {$index < 10} {incr index} {ipcfg::apply_dict cell {CONFIG.TEST 1}}
''')
        self.run_tcl(r'''
assert {[string match *ATTEMPT_LIMIT* [ipcfg::apply_dict /cell {CONFIG.TEST 1}]]}
assert {$writes == 0}
ipcfg::apply_dict other {CONFIG.TEST 1}
set hierarchy /subsystem
ipcfg::apply_dict cell {CONFIG.TEST 1}
assert {[count_for /subsystem/cell] == 1}
set design second_design
ipcfg::apply_dict /cell {CONFIG.TEST 1}
assert {$writes == 3 && [count_for /cell] == 1}
''')

    def test_reload_and_assembler_share_counter(self):
        self.run_tcl(f'''
for {{set index 0}} {{$index < 9}} {{incr index}} {{ipcfg::apply_dict cell {{CONFIG.TEST 1}}}}
source {{{CONFIGURATOR}}}
ipiasm::init
ipiasm::_gate_set plan 1 test
ipiasm::_gate_set board 1 test
proc get_ipdefs {{args}} {{return vendor:ip}}
set result [ipiasm::create_cell vendor:ip cell {{CONFIG.TEST 1}}]
assert {{[string match SUCCESS:* $result]}}
assert {{[string match *ATTEMPT_LIMIT* [ipcfg::apply_dict cell {{CONFIG.TEST 1}}]]}}
assert {{$writes == 10}}
''')

    def test_corrupt_or_locked_ledger_fails_closed(self):
        self.run_tcl(r'''
file mkdir [file join $root .ipcfg]
set channel [open [file join $root .ipcfg attempts.tcldict] w]
puts $channel broken
close $channel
assert {[string match *ATTEMPT_STATE* [ipcfg::apply_dict cell {CONFIG.TEST 1}]]}
assert {$writes == 0}
file delete [file join $root .ipcfg attempts.tcldict]
set channel [open [file join $root .ipcfg attempts.tcldict.lock] w]
close $channel
assert {[string match *ATTEMPT_STATE* [ipcfg::apply_dict cell {CONFIG.TEST 1}]]}
assert {$writes == 0}
''')

    def test_connection_automation_creates_and_connects(self):
        self.run_tcl(r'''
set cells {/master /cell}
set edges {}
proc get_bd_cells {args} {
    if {[lindex $args end] eq "-quiet"} {return $::cells}
    return /[string trimleft [lindex $args end] /]
}
proc apply_bd_automation {args} {
    assert {[lindex $args 1] eq "fixture:axi"}
    assert {[lindex $args 3] eq "Master /master/M_AXI"}
    assert {[lindex $args end] eq "cell/S_AXI"}
    lappend ::cells /interconnect
    lappend ::edges {/master/M_AXI /interconnect/S00_AXI} {/interconnect/M00_AXI /cell/S_AXI}
    incr ::writes
}
set result [ipiasm::connect_automation cell/S_AXI fixture:axi {Master /master/M_AXI}]
assert {$result eq "SUCCESS:automation fixture:axi on cell/S_AXI new_cells={/interconnect}"}
assert {$edges eq "{/master/M_AXI /interconnect/S00_AXI} {/interconnect/M00_AXI /cell/S_AXI}"}
assert {$writes == 1 && [count_for /cell] == 1}
''')

    def test_clock_reset_automation_forwards_configuration(self):
        self.run_tcl(r'''
set connection {}
proc get_bd_intf_pins {args} {return {}}
proc apply_bd_automation {args} {
    assert {[lindex $args 1] eq "fixture:clkrst"}
    assert {[lindex $args 3] eq "Clock /source/clk Reset /source/resetn"}
    set ::connection [list [lindex $args end] [lindex $args 3]]
    incr ::writes
}
set result [ipiasm::wire_clk_rst_auto cell/clk fixture:clkrst {Clock /source/clk Reset /source/resetn}]
assert {[string match SUCCESS:automation* $result]}
assert {$connection eq "cell/clk {Clock /source/clk Reset /source/resetn}"}
assert {$writes == 1 && [count_for /cell] == 1}
''')

    def test_autofix_recovers_transient_configuration_failure(self):
        self.run_tcl(r'''
set width 8
proc list_property {args} {return CONFIG.WIDTH}
proc get_property {key object} {
    if {$key eq "DIRECTORY"} {return $::root}
    if {$key eq "CONFIG.WIDTH"} {return $::width}
    return {}
}
proc set_property {args} {
    incr ::writes
    if {[lindex $args 0] eq "-dict"} {error "transient batch rejection"}
    assert {[lindex $args 0] eq "CONFIG.WIDTH"}
    set ::width [lindex $args 1]
}
set result [ipcfg::autofix_apply cell {CONFIG.WIDTH 16}]
assert {$result eq "AUTOFIX:applied={CONFIG.WIDTH} clamped={} omitted={} dropped_readonly={} ESCALATE={}"}
assert {$width == 16 && [ipcfg::verify_stuck cell {CONFIG.WIDTH 16}] eq ""}
assert {$writes == 2 && [count_for /cell] == 2}
''')

    def test_reset_domain_wires_source_directly_lock_and_targets(self):
        self.run_tcl(r'''
set edges {}
set removed {}
set properties {}
proc get_ipdefs {args} {return fixture_ip}
proc get_property {args} {
    set key [lindex $args end-1]
    if {$key eq "DIRECTORY"} {return $::root}
    if {$key eq "TYPE"} {return rst}
    if {$key eq "CONFIG.POLARITY"} {return ACTIVE_LOW}
    return {}
}
proc set_property {args} {set ::properties [lindex $args 1]}
proc get_bd_nets {args} {
    if {[lindex $args end] eq "cell/aresetn"} {return stale_reset}
    return {}
}
proc disconnect_bd_net {net pin} {lappend ::removed [list $net $pin]}
proc connect_bd_net {source destination} {lappend ::edges [list $source $destination]}
set result [ipiasm::ensure_reset_domain reset clock/out ps/resetn {cell/aresetn} clock/locked]
assert {[string match RESET_SYNC:WIRED* $result]}
assert {$properties eq ""}
assert {[llength $edges] == 4}
foreach edge {{clock/out reset/slowest_sync_clk} {ps/resetn reset/ext_reset_in} {clock/locked reset/dcm_locked} {reset/peripheral_aresetn cell/aresetn}} {
    assert {$edge in $edges}
}
assert {$removed eq "{stale_reset cell/aresetn}"}
''')

    def test_reset_domain_leaves_an_already_wired_port_alone(self):
        # A top-level output port created with the synchronized reset as its driver is already
        # wired; disconnecting and reconnecting it made Vivado reject a duplicate connection.
        self.run_tcl(r'''
set edges {}
set removed {}
proc get_ipdefs {args} {return fixture_ip}
proc get_property {args} {
    set key [lindex $args end-1]
    if {$key eq "DIRECTORY"} {return $::root}
    if {$key eq "TYPE"} {return rst}
    if {$key eq "CONFIG.POLARITY"} {return ACTIVE_LOW}
    return {}
}
proc get_bd_ports {args} {
    if {[lindex $args end] eq "resetn_out"} {return port:resetn_out}
    return {}
}
proc get_bd_nets {args} {
    switch -- [lindex $args end] {
        reset/peripheral_aresetn - port:resetn_out {return synchronized}
        default {return {}}
    }
}
proc disconnect_bd_net {net pin} {lappend ::removed [list $net $pin]}
proc connect_bd_net {source destination} {lappend ::edges [list $source $destination]}
set result [ipiasm::ensure_reset_domain reset clock/out ps/resetn {/resetn_out cell/aresetn}]
assert {[string match "RESET_SYNC:WIRED*resets={/resetn_out cell/aresetn}" $result]}
assert {"reset/peripheral_aresetn cell/aresetn" in $edges}
foreach edge $edges { assert {[lindex $edge 1] ne "port:resetn_out"} }
assert {$removed eq ""}
''')

    def test_reset_domain_wires_an_unconnected_port_as_a_port(self):
        self.run_tcl(r'''
set edges {}
proc get_ipdefs {args} {return fixture_ip}
proc get_property {args} {
    set key [lindex $args end-1]
    if {$key eq "DIRECTORY"} {return $::root}
    if {$key eq "TYPE"} {return rst}
    if {$key eq "CONFIG.POLARITY"} {return ACTIVE_LOW}
    return {}
}
proc get_bd_ports {args} {
    if {[lindex $args end] eq "resetn_out"} {return port:resetn_out}
    return {}
}
proc get_bd_nets {args} {return {}}
proc connect_bd_net {source destination} {lappend ::edges [list $source $destination]}
set result [ipiasm::ensure_reset_domain reset clock/out ps/resetn {/resetn_out}]
assert {[string match RESET_SYNC:WIRED* $result]}
assert {"reset/peripheral_aresetn port:resetn_out" in $edges}
''')

    def test_reset_domain_refuses_source_without_declared_polarity(self):
        self.run_tcl(r'''
set edges {}
set created 0
proc get_ipdefs {args} {return fixture_ip}
proc create_bd_cell {args} {incr ::created}
proc connect_bd_net {source destination} {lappend ::edges [list $source $destination]}
foreach {type polarity} {undef {} rst {} rst BOTH} {
    proc get_property [list args] [list \
        if {[lindex $args end-1] eq "TYPE"} [list return $type] \
        elseif {[lindex $args end-1] eq "CONFIG.POLARITY"} [list return $polarity] \
        else {return {}}]
    set result [ipiasm::ensure_reset_domain reset clock/out ps/resetn {cell/aresetn}]
    assert {[string match "RESET_SYNC_FAIL:ps/resetn is TYPE=*POLARITY=*" $result]}
}
assert {$edges eq "" && $created == 0}
''')

    def test_named_address_window_places_exact_mapping(self):
        self.run_tcl(r'''
ipiasm::_gate_set inventory 1 fixture
set mapping {}
proc get_bd_cells {args} {return {}}
proc get_bd_addr_spaces {args} {return [lindex $args end]}
proc get_bd_addr_segs {args} {return [lindex $args end]}
proc assign_bd_address {args} {
    assert {[lindex $args 0] eq "-target_address_space"}
    set ::mapping [list [lindex $args 1] [lindex $args 2] [lindex $args 4] [lindex $args 6]]
    incr ::writes
}
set result [ipiasm::place_named_windows {control master/Data peripheral/Reg 0xA0000000 64K}]
assert {[string match *control@0xA0000000* $result]}
assert {$mapping eq "master/Data peripheral/Reg 0xA0000000 0x10000"}
assert {$writes == 1}
''')

    def test_configuration_rejection_then_verified_recovery(self):
        self.run_tcl(r'''
set width 8
proc list_property {args} {return CONFIG.WIDTH}
proc get_property {key object} {
    if {$key eq "DIRECTORY"} {return $::root}
    if {$key eq "CONFIG.WIDTH"} {return $::width}
    return {}
}
proc set_property {args} {
    incr ::writes
    set requested [dict get [lindex $args 1] CONFIG.WIDTH]
    if {$requested > 32} {error "Value $requested is out of the range '1' to '32'"}
    set ::width $requested
}
set failed [ipcfg::apply_dict cell {CONFIG.WIDTH 33}]
assert {[string match CONFIGURE_FAIL:* $failed] && $width == 8}
assert {[ipcfg::apply_dict cell {CONFIG.WIDTH 16}] eq "SUCCESS:applied"}
assert {$width == 16 && [ipcfg::verify_stuck cell {CONFIG.WIDTH 16}] eq ""}
assert {[count_for /cell] == 2 && $writes == 2}
''')

    def test_connection_automation_stops_before_manual_fallback(self):
        self.run_tcl(r'''
proc ipiasm::_need_inventory {args} {return {}}
proc ipiasm::connect_intf {args} {error "manual fallback must not execute"}
set fail 1
for {set index 0} {$index < 9} {incr index} {ipcfg::apply_dict cell {CONFIG.TEST 1}}
set result [ipiasm::connect_axi_auto master/M_AXI cell/S_AXI rule]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 10}
set result [ipiasm::connect_external_axi ingress cell/S_AXI rule]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 10}
''')

    def test_board_and_automation_share_budget(self):
        self.run_tcl(r'''
proc ipcfg::board_hooks {cell} {return CONFIG.TEST_BOARD_INTERFACE}
proc ipcfg::board_enablers {cell} {return CONFIG.ENABLE}
set fail 1
for {set index 0} {$index < 8} {incr index} {ipcfg::apply_dict cell {CONFIG.TEST 1}}
ipcfg::run_automation cell rule
set result [ipcfg::board_bind cell {CONFIG.TEST_BOARD_INTERFACE board}]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 10}
''')

    def test_discovery_probe_restore_and_diagnostic_history(self):
        self.run_tcl(r'''
proc get_property {key object} {
    if {$key eq "DIRECTORY"} {return $::root}
    return {}
}
ipcfg::probe_subkey_range cell SETTINGS VALUE
assert {$writes == 2 && [count_for /cell] == 1}
for {set index 0} {$index < 9} {incr index} {
    ipcfg::resolve_subkeys cell SETTINGS {VALUE 1}
}
set result [ipcfg::probe_subkey_range cell SETTINGS VALUE]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 11}
set channel [open [file join $root .ipcfg attempts.tcldict]]
set state [read $channel]
close $channel
set history [dict get $state [list design /cell] history]
assert {[llength $history] == 10}
assert {[dict get [lindex $history 0] operation] eq "range_probe"}
assert {[dict get [lindex $history end] operation] eq "resolve_subkeys"}
''')

    def test_ledger_save_failure_before_mutation(self):
        self.run_tcl(r'''
file mkdir [file join $root .ipcfg]
file mkdir [file join $root .ipcfg attempts.tcldict.[pid].tmp]
set result [ipcfg::apply_dict cell {CONFIG.TEST 1}]
assert {[string match *ATTEMPT_STATE* $result]}
assert {$writes == 0}
assert {![file exists [file join $root .ipcfg attempts.tcldict.lock]]}
''')

    def test_ledger_save_failure_after_mutation_preserves_reservation(self):
        self.run_tcl(r'''
proc set_property {args} {
    incr ::writes
    file mkdir [file join $::root .ipcfg attempts.tcldict.[pid].tmp]
}
set result [ipcfg::apply_dict cell {CONFIG.TEST 1}]
assert {[string match *ATTEMPT_STATE* $result]}
assert {$writes == 1 && [count_for /cell] == 1}
set channel [open [file join $root .ipcfg attempts.tcldict]]
set state [read $channel]
close $channel
assert {[dict get $state [list design /cell] diagnostic] eq "pending"}
''')

    def test_interrupted_process_leaves_reserved_attempt_and_lock(self):
        self.run_tcl(r'''
ipcfg::attempt_call cell interruption {exit 73}
''', expected_exit=73)
        self.run_tcl(r'''
assert {[count_for /cell] == 1}
assert {[file exists [file join $root .ipcfg attempts.tcldict.lock]]}
assert {[string match *ATTEMPT_STATE* [ipcfg::apply_dict cell {CONFIG.TEST 1}]]}
assert {$writes == 0}
''')

    def test_relative_creation_and_absolute_resume_share_identity(self):
        self.run_tcl(r'''
set hierarchy /subsystem
set exists 0
assert {[string match SUCCESS:* [ipcfg::create_cell vendor:ip child]]}
ipcfg::apply_dict /subsystem/child {CONFIG.TEST 1}
assert {[count_for /subsystem/child] == 2}
''')

    def test_exhaustion_during_board_enable_recovery(self):
        self.run_tcl(r'''
proc ipcfg::board_hooks {cell} {return CONFIG.TEST_BOARD_INTERFACE}
proc ipcfg::board_enablers {cell} {return {CONFIG.ENABLE CONFIG.OTHER_ENABLE}}
for {set index 0} {$index < 8} {incr index} {ipcfg::apply_dict cell {CONFIG.TEST 1}}
set fail 1
set result [ipcfg::board_bind cell {CONFIG.TEST_BOARD_INTERFACE board}]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 10}
''')

    def test_representative_successful_sequence_consumes_budget(self):
        self.run_tcl(r'''
set exists 0
ipcfg::create_cell_cfg vendor:ip cell {CONFIG.TEST 1}
ipcfg::try_automation cell rule
ipcfg::run_automation cell rule
ipcfg::add_stub cell S_AXI interface
for {set index 0} {$index < 6} {incr index} {ipcfg::apply_dict cell {CONFIG.TEST $index}}
assert {$writes == 10}
assert {[string match *ATTEMPT_LIMIT* [ipcfg::apply_dict cell {CONFIG.TEST 11}]]}
assert {$writes == 10}
''')

    def test_external_axi_exhaustion_prevents_infrastructure_fallback(self):
        self.run_tcl(r'''
proc ipiasm::_need_inventory {args} {return {}}
for {set index 0} {$index < 10} {incr index} {ipcfg::apply_dict cell {CONFIG.TEST 1}}
set result [ipiasm::connect_external_axi external cell/S_AXI rule]
assert {[string match *ATTEMPT_LIMIT* $result]}
assert {$writes == 10}
''')

    def test_coverage_excludes_mock_and_reattaches_after_restore(self):
        output = self.run_tcl(f'''
set original_args [info args ipcfg::range_from_log]
set original_body [info body ipcfg::range_from_log]
proc ipcfg::range_from_log {{args}} {{return mocked}}
ipcfg::range_from_log ignored ignored
source {{{CONFIGURATOR}}}
assert {{[ipcfg::range_from_log ignored ignored] eq "mocked"}}
proc ipcfg::range_from_log $original_args $original_body
assert {{[ipcfg::range_from_log {{PARAM VALUE is out of range {{ 0,1 }}}} VALUE] eq "RANGE:VALUE={{0,1}}"}}
procedure_trace::uninstall
assert {{[ipcfg::range_from_log {{PARAM VALUE is out of range {{ 0,1 }}}} VALUE] eq "RANGE:VALUE={{0,1}}"}}
''')
        self.assertEqual(output.count("COV_HIT ::ipcfg::range_from_log\n"), 1)


if __name__ == "__main__":
    unittest.main()