# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Synthetic RAVE2 boundary evidence; no hardware observations or Vivado calls."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def rave2_fixture():
    fingerprints = {
        name: hashlib.sha256(("synthetic-rave2-" + name).encode()).hexdigest()
        for name in ("spec", "tool", "part", "board", "ip")
    }
    checks = {}
    observations = {}

    def add(check_id, kind, value_type, value, subject, source, setup, stage="connect"):
        checks[check_id] = {
            "kind": kind, "subject": subject,
            "expected": {"type": value_type, "value": value}, "source": source,
            "derivation": "connection", "setup_requirements": setup,
            "delivery": {"stage": stage, "owner": "delivery_" + stage,
                         "coverage_ids": [check_id + ".delivery"]},
        }
        observations[check_id] = {
            "supported": True, "subject": subject, "type": value_type,
            "value": copy.deepcopy(value), "evidence": "synthetic fixture: " + check_id,
        }

    irq_source = "outputs/design_spec.json#/blocks/interrupt/parameters/fabric_controller_inputs/width"
    add("irq.count", "config", "integer", 22, "interrupt/CONFIG.C_NUM_INTR_INPUTS",
        irq_source, ["irq_source"])
    add("irq.width", "pin_width", "integer", 22, "interrupt/intr",
        irq_source, ["irq_source"])
    add("irq.interface", "interface", "interface",
        {"vlnv": "xilinx.com:interface:aximm_rtl:1.0", "mode": "Slave"},
        "interrupt/s_axi", "outputs/design_spec.json#/blocks/interrupt/interfaces/control",
        ["irq_source"])
    for region, base, size in (("legacy", 0, 0x80000000),
                               ("medium", 0x800000000, 0x380000000)):
        check_id = "noc." + region
        add(check_id, "aperture_capacity", "range", {"base": base, "bytes": size},
            "lpddr5/" + region,
            "outputs/design_spec.json#/blocks/lpddr5/parameters/regions/" + region,
            ["memory_route"])
        checks[check_id]["selector"] = {
            "property": "CONFIG.MC_CHAN_REGION" + ("0" if region == "legacy" else "1"),
            "value": "DDR_CH0" if region == "legacy" else "DDR_CH0_MED",
            "source": ("outputs/.ipiasm/20260925_stage4_functional_cells.tcl: CONFIG.MC_CHAN_REGION1"
                       if region == "medium" else "synthetic legacy selector; requires live discovery"),
        }
        observations[check_id]["selector"] = copy.deepcopy(checks[check_id]["selector"])
        checks[check_id]["assigned_window"] = {
            "type": "range", "value": {"base": base, "bytes": size},
            "source": checks[check_id]["source"], "stage": "address",
            "owner": "rave2_address_readback", "coverage_id": check_id + ".assigned_window",
        }
    coverage = list(checks)
    for check in checks.values():
        coverage.extend(check["delivery"]["coverage_ids"])
        if "assigned_window" in check:
            coverage.append(check["assigned_window"]["coverage_id"])
    expected = {
        "schema_version": 1, "fingerprints": fingerprints, "coverage_ids": coverage,
        "setup": {
            "irq_source": {"kind": "connection", "description": "Connect a 22-bit source to intr; read propagated count and pin width"},
            "memory_route": {"kind": "connection", "description": "Apply board memory preset and region selector; connect minimal functional memory route before querying target segments"},
        },
        "checks": checks,
    }
    manifest = {
        "schema_version": 1, "origin": "synthetic", "fingerprints": copy.deepcopy(fingerprints),
        "setup": {name: {"completed": True, "evidence": "synthetic setup: " + name}
                  for name in expected["setup"]},
        "observations": observations,
    }
    return expected, manifest


class TclFeasibilityTests(unittest.TestCase):
    def clock_setup(self):
        return r'''
proc get_bd_pins {args} {return [lindex $args end]}
proc get_bd_cells {args} {return [lindex $args end]}
proc get_bd_ports {args} {return {}}
proc find_bd_objs {args} {
    return [dict get $::drivers [lindex $args end]]
}
set drivers {/generator/input /reference/out /consumer/clock /generator/output}
set clock_properties {
    /reference/out {TYPE clk DIR O CLASS bd_pin CONFIG.FREQ_HZ 99999001}
    /generator/input {TYPE clk DIR I CLASS bd_pin CONFIG.FREQ_HZ 99999001}
    /generator/output {TYPE clk DIR O CLASS bd_pin CONFIG.FREQ_HZ 74251394}
    /consumer/clock {TYPE clk DIR I CLASS bd_pin CONFIG.FREQ_HZ 74251394}
}
rename get_property fixture_get_property
proc get_property {args} {
    set name [lindex $args end-1]
    set object [lindex $args end]
    if {[dict exists $::clock_properties $object]} {
        return [dict get $::clock_properties $object $name]
    }
    return [fixture_get_property $name $object]
}
proc list_property {object} {return [dict keys [dict get $::clock_properties $object]]}
set clock_check {id output.clock kind clock_frequency subject /consumer/clock expected 74251394 source spec#/clock stage clocks driver /generator/output input /generator/input reference /reference/out reference_hz 99999001 tolerance_hz 0}
'''
    def test_achieved_clock_requires_connected_reference_and_sink(self):
        setup = self.clock_setup()
        cases = {
            '': 'FEASIBILITY:OK',
            'dict set clock_properties /generator/output CONFIG.FREQ_HZ 74251302': 'achieved frequency mismatch',
            'dict set drivers /generator/input {}': 'wrong or missing driver',
            'dict set drivers /consumer/clock /reference/out': 'wrong or missing driver',
            'dict set clock_properties /consumer/clock CONFIG.FREQ_HZ 100000000': 'stale sink frequency',
            'dict set clock_properties /reference/out CONFIG.FREQ_HZ 100000000': 'achieved frequency mismatch',
            'dict unset clock_properties /generator/output CONFIG.FREQ_HZ': 'not published',
            'dict set clock_check stage cells': 'after clock connection',
            'dict unset clock_check tolerance_hz': 'missing clock field',
            'dict set clock_check tolerance_hz -1': 'unsigned 64-bit',
            'dict set clock_check tolerance_hz 74251394': 'permits zero frequency',
            'dict set clock_check expected 0': 'must be positive',
            'dict set clock_check tolerance_hz 100; dict set clock_check expected 74251300': 'FEASIBILITY:OK',
        }
        for change, expected in cases.items():
            with self.subTest(change=change):
                result = self.run_gate(setup + '\n' + change + '\nipiasm::check_feasibility [list $clock_check] scratch $sources $fingerprint')
                self.assertIn(expected, result)

    def test_top_level_reference_is_a_driver(self):
        setup = self.clock_setup() + r'''
proc get_bd_pins {args} {
    set path [lindex $args end]
    if {$path eq "/reference/out"} {return /reference_pin_view}
    return $path
}
proc get_bd_ports {args} {
    if {[lindex $args end] eq "/reference/out"} {return /reference/out}
    return {}
}
dict set clock_properties /reference_pin_view {TYPE clk DIR I CLASS bd_pin CONFIG.FREQ_HZ 100000000}
dict set clock_properties /reference/out CLASS bd_port
dict set clock_properties /reference/out DIR I
'''
        self.assertIn('FEASIBILITY:OK', self.run_gate(setup + '\nipiasm::check_feasibility [list $clock_check] clocks $sources $fingerprint'))

    def test_registered_contracts_are_immutable_and_rechecked(self):
        setup = self.clock_setup() + r'''
set registered [ipiasm::set_clock_contracts [list $clock_check] {} $sources $fingerprint]
if {![string match CLOCK_CONTRACTS:SET* $registered]} {error $registered}
'''
        cases = {
            'ipiasm::check_clock_contracts': 'CLOCK_CONTRACTS:OK',
            'dict set clock_properties /generator/output CONFIG.FREQ_HZ 100000000; ipiasm::check_clock_contracts': 'achieved frequency mismatch',
            'dict set properties PART changed; ipiasm::check_clock_contracts': 'stale fingerprints',
            'proc version {args} {if {[llength $args]} {return 2026.1}; return changed_build}; ipiasm::check_clock_contracts': 'stale fingerprints',
            'proc ipiasm::context_identity {} {return elsewhere}; ipiasm::check_clock_contracts': 'context changed',
            'dict set clock_check tolerance_hz 1000; ipiasm::set_clock_contracts [list $clock_check] {} $sources $fingerprint': 'already registered',
            'ipiasm::set_clock_contracts {} {} $sources $fingerprint': 'empty clock contracts',
            'ipiasm::set_clock_contracts [list $clock_check] {} $sources $fingerprint': 'CLOCK_CONTRACTS:SET',
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertIn(expected, self.run_gate(setup + '\n' + command))

    def test_reset_contract_checks_domain_lock_and_polarity(self):
        setup = self.clock_setup() + r'''
dict set clock_properties /reset {VLNV xilinx.com:ip:proc_sys_reset:5.0 CONFIG.C_EXT_RESET_HIGH 0}
dict set clock_properties /reset/slowest_sync_clk {TYPE clk DIR I CLASS bd_pin}
dict set clock_properties /reset/ext_reset_in {TYPE rst DIR I CLASS bd_pin}
dict set clock_properties /reset/dcm_locked {TYPE data DIR I CLASS bd_pin}
dict set clock_properties /reset/peripheral_aresetn {TYPE rst DIR O CLASS bd_pin}
dict set clock_properties /reference/resetn {TYPE rst DIR O CLASS bd_pin CONFIG.POLARITY ACTIVE_LOW}
dict set clock_properties /generator/locked {TYPE data DIR O CLASS bd_pin}
dict set clock_properties /consumer/resetn {TYPE rst DIR I CLASS bd_pin CONFIG.POLARITY ACTIVE_LOW}
dict set drivers /reset/slowest_sync_clk /generator/output
dict set drivers /reset/ext_reset_in /reference/resetn
dict set drivers /reset/dcm_locked /generator/locked
dict set drivers /consumer/resetn /reset/peripheral_aresetn
set reset {cell /reset clock /generator/output source /reference/resetn source_polarity ACTIVE_LOW lock /generator/locked sinks {{/consumer/resetn peripheral_aresetn}}}
set registered [ipiasm::set_clock_contracts [list $clock_check] [list $reset] $sources $fingerprint]
if {![string match CLOCK_CONTRACTS:SET* $registered]} {error $registered}
'''
        cases = {
            '': 'CLOCK_CONTRACTS:OK',
            'dict set drivers /reset/slowest_sync_clk /reference/out': 'wrong or missing driver',
            'dict set drivers /reset/dcm_locked {}': 'wrong or missing driver',
            'dict set clock_properties /reference/resetn CONFIG.POLARITY ACTIVE_HIGH': 'source polarity mismatch',
            'dict set clock_properties /reset CONFIG.C_EXT_RESET_HIGH 1': 'has not propagated',
            'dict set clock_properties /consumer/resetn CONFIG.POLARITY ACTIVE_HIGH': 'sink polarity mismatch',
            'dict set clock_properties /reset VLNV test:ip:unknown:1.0': 'unsupported reset synchronizer',
        }
        for change, expected in cases.items():
            with self.subTest(change=change):
                self.assertIn(expected, self.run_gate(setup + '\n' + change + '\nipiasm::check_clock_contracts'))

    def test_reset_on_a_clock_without_lock_output(self):
        # A processing-system reference clock has no lock output: the contract's lock is empty
        # and dcm_locked must stay unconnected, which proc_sys_reset reads as locked.
        setup = self.clock_setup() + r'''
dict set clock_properties /reset {VLNV xilinx.com:ip:proc_sys_reset:5.0 CONFIG.C_EXT_RESET_HIGH 0}
dict set clock_properties /reset/slowest_sync_clk {TYPE clk DIR I CLASS bd_pin}
dict set clock_properties /reset/ext_reset_in {TYPE rst DIR I CLASS bd_pin}
dict set clock_properties /reset/dcm_locked {TYPE data DIR I CLASS bd_pin}
dict set clock_properties /reset/peripheral_aresetn {TYPE rst DIR O CLASS bd_pin}
dict set clock_properties /reference/resetn {TYPE rst DIR O CLASS bd_pin CONFIG.POLARITY ACTIVE_LOW}
dict set clock_properties /generator/locked {TYPE data DIR O CLASS bd_pin}
dict set clock_properties /consumer/resetn {TYPE rst DIR I CLASS bd_pin CONFIG.POLARITY ACTIVE_LOW}
dict set drivers /reset/slowest_sync_clk /generator/output
dict set drivers /reset/ext_reset_in /reference/resetn
dict set drivers /reset/dcm_locked {}
dict set drivers /consumer/resetn /reset/peripheral_aresetn
set reset {cell /reset clock /generator/output source /reference/resetn source_polarity ACTIVE_LOW lock {} sinks {{/consumer/resetn peripheral_aresetn}}}
'''
        register = '\nipiasm::set_clock_contracts [list $clock_check] [list $reset] $sources $fingerprint'
        cases = {
            register + '\nipiasm::check_clock_contracts': 'CLOCK_CONTRACTS:OK',
            'dict set drivers /reset/dcm_locked /generator/locked' + register + '\nipiasm::check_clock_contracts':
                'dcm_locked is driven by generator/locked, expected no driver',
            # The field is still required; only its value may be empty, and only for lock.
            'dict unset reset lock' + register: 'missing reset field lock',
            'dict set reset source {}' + register: 'missing reset field source',
            # A reset on a contract's reference clock is covered: the reference frequency is
            # checked exactly. A clock no contract names is not.
            'dict set reset clock /reference/out' + register: 'CLOCK_CONTRACTS:SET',
            'dict set reset clock /unknown/clk' + register: 'reset clock has no frequency contract',
        }
        for change, expected in cases.items():
            with self.subTest(change=change):
                self.assertIn(expected, self.run_gate(setup + '\n' + change))

    def reset_contract_setup(self):
        return self.clock_setup() + r'''
dict set clock_properties /reset {VLNV xilinx.com:ip:proc_sys_reset:5.0 CONFIG.C_EXT_RESET_HIGH 0}
dict set clock_properties /reset/slowest_sync_clk {TYPE clk DIR I CLASS bd_pin}
dict set clock_properties /reset/ext_reset_in {TYPE rst DIR I CLASS bd_pin}
dict set clock_properties /reset/dcm_locked {TYPE data DIR I CLASS bd_pin}
dict set clock_properties /reset/peripheral_aresetn {TYPE rst DIR O CLASS bd_pin}
dict set clock_properties /reference/resetn {TYPE rst DIR O CLASS bd_pin CONFIG.POLARITY ACTIVE_LOW}
dict set clock_properties /generator/locked {TYPE data DIR O CLASS bd_pin}
dict set clock_properties /consumer/resetn {TYPE rst DIR I CLASS bd_pin CONFIG.POLARITY ACTIVE_LOW}
dict set drivers /reset/slowest_sync_clk /generator/output
dict set drivers /reset/ext_reset_in /reference/resetn
dict set drivers /reset/dcm_locked /generator/locked
dict set drivers /consumer/resetn /reset/peripheral_aresetn
set reset {cell /reset clock /generator/output source /reference/resetn source_polarity ACTIVE_LOW lock /generator/locked sinks {{/consumer/resetn peripheral_aresetn}}}
set registered [ipiasm::set_clock_contracts [list $clock_check] [list $reset] $sources $fingerprint]
if {![string match CLOCK_CONTRACTS:SET* $registered]} {error $registered}
'''

    def test_one_contract_pass_asks_vivado_each_question_once(self):
        counted = self.reset_contract_setup() + r'''
set ::asked {}
foreach command {get_bd_ports list_property get_property} {
    rename $command counted_$command
    proc $command {args} [format {dict incr ::asked [list %1$s {*}$args]; counted_%1$s {*}$args} $command]
}
set result [ipiasm::check_clock_contracts]
list $result [lindex [lsort -integer -decreasing [dict values $::asked]] 0] \
    $::ipiasm::feasibility::memo $::ipiasm::feasibility::memo_depth
'''
        result, most, memo, depth = self.run_gate(counted).rsplit(" ", 3)
        self.assertIn("CLOCK_CONTRACTS:OK", result)
        self.assertEqual(most, "1", "a repeated read inside one pass was not remembered")
        self.assertEqual((memo, depth), ("{}", "0"), "the memo outlived the pass")

    def test_memo_never_carries_an_answer_into_the_next_pass(self):
        setup = self.reset_contract_setup()
        cases = {
            # The second pass must see the rate changed between passes.
            'ipiasm::check_clock_contracts; dict set clock_properties /generator/output CONFIG.FREQ_HZ 100000000; '
            'ipiasm::check_clock_contracts': 'achieved frequency mismatch',
            # A failing pass also empties the memo.
            'proc ipiasm::context_identity {} {return elsewhere}; ipiasm::check_clock_contracts; '
            'list $::ipiasm::feasibility::memo $::ipiasm::feasibility::memo_depth': '{} 0',
            # Nested scopes share one memo until the outermost one exits.
            'ipiasm::feasibility::with_memo {ipiasm::feasibility::with_memo {ipiasm::feasibility::endpoint /consumer/clock}; '
            'set ::inner [dict size $::ipiasm::feasibility::memo]}; list $::inner [dict size $::ipiasm::feasibility::memo]': '1 0',
            # Errors pass through unchanged.
            'list [catch {ipiasm::feasibility::with_memo {error boom}} message] $message': '1 boom',
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertIn(expected, self.run_gate(setup + '\n' + command))

    def run_gate(self, body):
        library = SCRIPTS.parent / "lib" / "feasibility.tcl"
        script = r'''
namespace eval ipiasm {}
proc ipiasm::context_identity {} {return {/scratch project design}}
proc ipiasm::_file_sha256 {path} {return fixture_digest}
proc version {args} {return 2026.1}
proc current_project {} {return project}
proc get_bd_cells {args} {return /fixture}
proc get_bd_pins {args} {return /fixture/intr}
proc get_bd_intf_pins {args} {return /fixture/s_axi}
proc get_bd_addr_segs {args} {return /fixture/DDR_CH0_MEDx4}
proc get_bd_nets {args} {return /irq_net}
set properties {PART fixture BOARD_PART board CONFIG.C_NUM_INTR_INPUTS 22 LEFT 21 RIGHT 0 VLNV xilinx.com:interface:aximm_rtl:1.0 MODE Slave CONFIG.MC_CHAN_REGION1 DDR_CH0_MED RANGE 0x380000000}
proc list_property {object} {return [dict keys $::properties]}
proc get_property {name object} {return [dict get $::properties $name]}
proc set_property {args} {error "read-only checker attempted mutation"}
'''
        script += f'\nsource {{{library}}}\n'
        script += f'source {{{library.parent / "clocks.tcl"}}}\n'
        script += f'set sources [dict create spec {{{library}}} board {{{library}}} ip {{{library}}}]\n'
        script += 'set fingerprint [ipiasm::feasibility_fingerprint $sources]\n'
        script += r'''
set config {id irq.count kind config subject /fixture property CONFIG.C_NUM_INTR_INPUTS type integer expected 22 source spec#/irq stage connect}
set width {id irq.width kind pin_width subject /fixture/intr expected 22 source spec#/irq stage connect}
set interface {id irq.interface kind interface subject /fixture/s_axi expected {VLNV xilinx.com:interface:aximm_rtl:1.0 MODE Slave} source spec#/irq stage cells}
set aperture {id noc.medium kind aperture_capacity subject /fixture/DDR_CH0_MEDx4 expected 0x380000000 source spec#/memory stage connect selector {cell /fixture property CONFIG.MC_CHAN_REGION1 value DDR_CH0_MED}}
set checks [list $config $width $interface $aperture]
'''
        script += '\nif {[catch {\n' + body + '\n} result options]} {puts stderr $result; exit 1}\nputs $result\n'
        result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_typed_scratch_and_owned_delivery_checks(self):
        self.assertEqual(self.run_gate('ipiasm::check_feasibility $checks scratch $sources $fingerprint'),
                         'FEASIBILITY:OK checks=4 phase=scratch')
        self.assertEqual(self.run_gate('ipiasm::check_feasibility $checks connect $sources $fingerprint'),
                         'FEASIBILITY:OK checks=3 phase=connect')
        self.assertEqual(self.run_gate('ipiasm::check_feasibility $checks cells $sources $fingerprint'),
                         'FEASIBILITY:OK checks=1 phase=cells')

    def test_fail_closed_boundaries(self):
        cases = {
            'dict set properties CONFIG.C_NUM_INTR_INPUTS 21': 'value mismatch',
            'dict unset properties CONFIG.C_NUM_INTR_INPUTS': 'not published',
            'dict set properties LEFT 20': 'width mismatch',
            'proc get_bd_nets {args} {return {}}': 'unconnected',
            'dict set properties MODE Master': 'interface MODE mismatch',
            'dict set properties RANGE 0x80000000': 'aperture too small',
            'dict set properties CONFIG.MC_CHAN_REGION1 wrong': 'selector mismatch',
            'dict set properties PART changed': 'stale fingerprints',
            'proc ipiasm::_file_sha256 {path} {return changed}': 'stale fingerprints',
            'set checks {}': 'empty checks',
            'lappend checks $config': 'duplicate check',
            'dict set width stage cells; set checks [list $width]': 'belongs after connection',
            'dict set config subject /*; set checks [list $config]': 'exact subject',
            'dict unset config source; set checks [list $config]': 'missing check field source',
            'dict set config expected -1; set checks [list $config]': 'unsigned 64-bit',
            'proc get_bd_pins {args} {return {}}': 'missing or ambiguous',
        }
        for setup, message in cases.items():
            with self.subTest(message=message):
                result = self.run_gate(setup + '\nipiasm::check_feasibility $checks scratch $sources $fingerprint')
                self.assertTrue(result.startswith('FEASIBILITY_FAIL:'), result)
                self.assertIn(message, result)

    def test_sources_are_required_and_phase_must_own_checks(self):
        self.assertIn('no checks owned', self.run_gate('ipiasm::check_feasibility $checks address $sources $fingerprint'))
        self.assertIn('missing fingerprint sources', self.run_gate('ipiasm::check_feasibility $checks scratch {} $fingerprint'))
        self.assertIn('invalid phase', self.run_gate('ipiasm::check_feasibility $checks unknown $sources $fingerprint'))


class FeasibilityTests(unittest.TestCase):
    def setUp(self):
        import validate_feasibility
        self.validator = validate_feasibility
        self.expected, self.manifest = rave2_fixture()

    def reject(self, message):
        with self.assertRaisesRegex(self.validator.FeasibilityError, message):
            self.validator.validate_manifest(self.manifest, self.expected)

    def test_missing_required_observation(self):
        del self.manifest["observations"]["noc.medium"]
        self.reject("coverage")

    def test_unsupported_capability(self):
        self.manifest["observations"]["irq.width"]["supported"] = False
        self.reject("unsupported")

    def test_changed_each_fingerprint(self):
        for name in self.expected["fingerprints"]:
            with self.subTest(name=name):
                self.manifest = rave2_fixture()[1]
                self.manifest["fingerprints"][name] = "0" * 64
                self.reject("fingerprint")

    def test_missing_fingerprint(self):
        del self.manifest["fingerprints"]["board"]
        self.reject("fingerprint")

    def test_malformed_fingerprint(self):
        self.manifest["fingerprints"]["ip"] = "unknown"
        self.reject("fingerprint")

    def test_wrong_observed_type(self):
        self.manifest["observations"]["irq.width"]["type"] = "string"
        self.reject("type")

    def test_boolean_is_not_integer(self):
        self.manifest["observations"]["irq.width"]["value"] = True
        self.reject("integer")

    def test_numeric_string_is_not_width(self):
        self.manifest["observations"]["irq.width"]["value"] = "22"
        self.reject("integer")

    def test_wrong_width_and_derived_count(self):
        for name in ("irq.width", "irq.count"):
            with self.subTest(name=name):
                self.manifest = rave2_fixture()[1]
                self.manifest["observations"][name]["value"] = 1
                self.reject("mismatch")

    def test_wrong_interface(self):
        self.manifest["observations"]["irq.interface"]["value"]["mode"] = "Master"
        self.reject("mismatch")

    def test_missing_or_incomplete_connection(self):
        for replacement in ({}, {"irq_source": {"completed": False, "evidence": "not connected"}}):
            with self.subTest(replacement=replacement):
                self.manifest["setup"] = replacement
                self.reject("setup")

    def test_connection_readback_cannot_belong_to_cells(self):
        self.expected["checks"]["irq.count"]["delivery"]["stage"] = "cells"
        self.reject("connection")

    def test_connection_setup_cannot_be_omitted(self):
        self.expected["checks"]["irq.width"]["setup_requirements"] = []
        self.reject("connection")

    def test_aperture_too_small(self):
        self.manifest["observations"]["noc.medium"]["value"]["bytes"] = 0x80000000
        self.reject("capacity")

    def test_aperture_wrong_base(self):
        self.manifest["observations"]["noc.medium"]["value"]["base"] = 0
        self.reject("capacity")

    def test_negative_range(self):
        self.manifest["observations"]["noc.medium"]["value"]["bytes"] = -1
        self.reject("range")

    def test_missing_or_wrong_selector(self):
        del self.manifest["observations"]["noc.medium"]["selector"]
        self.reject("selector")

    def test_wrong_selector_value(self):
        self.manifest["observations"]["noc.medium"]["selector"]["value"] = "DDR_CH0"
        self.reject("selector mismatch")

    def test_missing_assigned_window_owner(self):
        del self.expected["checks"]["noc.medium"]["assigned_window"]["owner"]
        self.reject("owner")

    def test_assigned_window_cannot_be_gated_at_cells(self):
        self.expected["checks"]["noc.medium"]["assigned_window"]["stage"] = "cells"
        self.reject("address stage")

    def test_assigned_window_larger_than_required_aperture(self):
        self.expected["checks"]["noc.medium"]["assigned_window"]["value"]["bytes"] *= 2
        self.reject("outside required aperture")

    def test_assigned_window_is_mandatory(self):
        del self.expected["checks"]["noc.medium"]["assigned_window"]
        self.reject("missing assigned_window")

    def test_missing_evidence(self):
        self.manifest["observations"]["irq.count"]["evidence"] = ""
        self.reject("evidence")

    def test_missing_setup_evidence(self):
        self.manifest["setup"]["memory_route"]["evidence"] = ""
        self.reject("evidence")

    def test_missing_requirement_source(self):
        self.expected["checks"]["irq.width"]["source"] = ""
        self.reject("source")

    def test_invented_hardware_pass_field(self):
        self.manifest["hardware_status"] = "PASS"
        self.reject("unknown")

    def test_extra_observation(self):
        self.manifest["observations"]["extra"] = copy.deepcopy(self.manifest["observations"]["irq.width"])
        self.reject("coverage")

    def test_missing_coverage_obligation(self):
        self.expected["coverage_ids"].remove("irq.width")
        self.reject("coverage")

    def test_malformed_nested_observation(self):
        self.manifest["observations"]["irq.width"] = []
        self.reject("object")

    def test_unsupported_schema(self):
        self.manifest["schema_version"] = True
        self.reject("schema_version")

    def test_missing_delivery_owner(self):
        del self.expected["checks"]["irq.width"]["delivery"]["owner"]
        self.reject("owner")

    def test_dropped_delivery_coverage(self):
        self.expected["checks"]["irq.width"]["delivery"]["coverage_ids"] = []
        self.reject("coverage")

    def test_duplicate_coverage(self):
        self.expected["coverage_ids"].append("irq.width")
        self.reject("coverage")

    def test_unknown_field(self):
        self.manifest["observations"]["irq.width"]["widht"] = 22
        self.reject("unknown")

    def test_unknown_kind(self):
        self.expected["checks"]["irq.width"]["kind"] = "generic_pass"
        self.reject("kind")

    def test_empty_manifest_cannot_pass(self):
        self.manifest = {}
        self.reject("manifest")

    def test_empty_expected_cannot_pass(self):
        self.expected = {}
        self.reject("expected")

    def test_pending_observation_is_not_supported(self):
        self.manifest["observations"]["irq.width"]["supported"] = "pending"
        self.reject("unsupported")

    def test_aperture_capacity_not_assigned_window(self):
        self.manifest["observations"]["noc.medium"]["value"]["bytes"] = 0x400000000
        result = self.validator.validate_manifest(self.manifest, self.expected)
        self.assertEqual(result["status"], "OFFLINE_EVIDENCE_OK")
        self.assertEqual(result["hardware_status"], "UNVERIFIED")
        self.assertEqual(result["checks"], 5)
        self.assertEqual(result["coverage_ids"], sorted(self.expected["coverage_ids"]))
        pending = {item["coverage_id"]: item for item in result["pending_live"]}
        self.assertEqual(pending["noc.medium.assigned_window"]["expected"]["value"]["bytes"], 0x380000000)
        self.assertEqual(pending["noc.medium.assigned_window"]["stage"], "address")
        self.assertEqual(pending["noc.medium.assigned_window"]["owner"], "rave2_address_readback")
        self.assertEqual(pending["irq.count.delivery"]["stage"], "connect")
        self.assertEqual(len(pending), 12)

    def test_configured_scalar_types(self):
        for value_type, value in (("string", "enabled"), ("boolean", False), ("integer", 0)):
            with self.subTest(value_type=value_type):
                check = self.expected["checks"]["irq.count"]
                check["expected"] = {"type": value_type, "value": value}
                check["derivation"] = "configured"
                check["delivery"]["stage"] = "cells"
                self.manifest["observations"]["irq.count"].update(type=value_type, value=value)
                self.assertEqual(self.validator.validate_manifest(self.manifest, self.expected)["checks"], 5)

    def test_bounded_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.json"
            path.write_bytes(b" " * (self.validator.MAX_BYTES + 1))
            with self.assertRaisesRegex(self.validator.FeasibilityError, "1 MiB"):
                self.validator.load_json(path)

    def test_bounded_check_count(self):
        self.expected["checks"] = {str(index): {} for index in range(self.validator.MAX_CHECKS + 1)}
        self.reject("bounded")

    def test_nonstandard_json_constant(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nan.json"
            path.write_text('{"value": NaN}')
            with self.assertRaisesRegex(self.validator.FeasibilityError, "constant"):
                self.validator.load_json(path)

    def test_cli_success_is_offline_only(self):
        with tempfile.TemporaryDirectory() as directory:
            expected_path = Path(directory) / "expected.json"
            manifest_path = Path(directory) / "manifest.json"
            expected_path.write_text(json.dumps(self.expected))
            manifest_path.write_text(json.dumps(self.manifest))
            result = subprocess.run([sys.executable, str(SCRIPTS / "validate_feasibility.py"),
                                     "--expected", str(expected_path), "--manifest", str(manifest_path)],
                                    text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["hardware_status"], "UNVERIFIED")

    def test_recorded_evidence_still_never_proves_hardware(self):
        self.manifest["origin"] = "recorded"
        result = self.validator.validate_manifest(self.manifest, self.expected)
        self.assertEqual(result["hardware_status"], "UNVERIFIED")
        self.assertEqual(len(result["pending_live"]), 12)

    def test_cli_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            expected_path = Path(directory) / "expected.json"
            manifest_path = Path(directory) / "manifest.json"
            expected_path.write_text(json.dumps(self.expected))
            manifest_path.write_text('{"schema_version":1,"schema_version":1}')
            result = subprocess.run([sys.executable, str(SCRIPTS / "validate_feasibility.py"),
                                     "--expected", str(expected_path), "--manifest", str(manifest_path)],
                                    text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "OFFLINE_EVIDENCE_FAIL")
        self.assertIn("duplicate", result.stdout)


class LiveFeasibilityTclTests(unittest.TestCase):
    def run_tcl(self, script):
        helper = SCRIPTS.parent / "lib" / "feasibility.tcl"
        result = subprocess.run(
            ["tclsh"], input=f"source {{{helper}}}\n" + script,
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stderr, "", result.stderr)
        return result.stdout.strip()

    def test_live_scalar_normalization(self):
        self.assertEqual(self.run_tcl(
            "puts [ipiasm::feasibility::scalar integer 0x16]"
        ), "22")

    def test_live_scalar_rejects_invalid_uint(self):
        self.assertEqual(self.run_tcl('''
foreach value {-1 18446744073709551616 22x true} {
    puts [catch {ipiasm::feasibility::scalar integer $value}]
}
'''), "1\n1\n1\n1")

    def test_live_property_requires_publication(self):
        self.assertEqual(self.run_tcl('''
proc list_property {object} {return CONFIG.KNOWN}
proc get_property {name object} {error "must not read missing property"}
puts [catch {ipiasm::feasibility::property /cell CONFIG.UNKNOWN} message]
puts $message
'''), "1\nFEASIBILITY_FAIL:property CONFIG.UNKNOWN is not published on /cell")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--write-fixture":
        destination = Path(sys.argv[2])
        destination.mkdir(parents=True, exist_ok=False)
        expected, manifest = rave2_fixture()
        for name, document in (("expected.json", expected), ("manifest.json", manifest)):
            (destination / name).write_text(json.dumps(document, indent=2) + "\n")
        print(json.dumps({"origin": "synthetic", "hardware_status": "UNVERIFIED", "fixture": str(destination)}))
    else:
        unittest.main()