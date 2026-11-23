# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import base64
import copy
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import bind_design_spec as binder
import compile_assembly_plan as compiler


class BindingTests(unittest.TestCase):
    def test_module_reference_compiles_without_raw_tcl_escape(self):
        operation = {
            "op": "call", "proc": "ipiasm::create_module_ref",
            "args": ["rtl_block", "block", ["rtl/block.v"], {"CONFIG.WIDTH": 8}],
        }
        plan = {"schema_version": 1, "stages": [{"name": "cells", "operations": [operation]}]}
        self.assertIn("ipiasm::create_module_ref", compiler.compile_plan(plan))
        for change in ({"proc": "source"}, {"tcl": "puts bypass"}, {"command": "puts bypass"}):
            with self.subTest(change=change):
                rejected = copy.deepcopy(plan)
                rejected["stages"][0]["operations"][0].update(change)
                with self.assertRaises(compiler.PlanError):
                    compiler.compile_plan(rejected)

    def setUp(self):
        self.intent = {
            "schema_version": 1,
            "blocks": {"device": {"vlnv_candidates": ["test:ip:unsupported", "test:ip:device"]}},
        }
        self.spec = {"blocks": [{"name": "device"}]}

    def test_roles_sharing_a_spec_block_get_distinct_cells(self):
        # A controller and the memory behind it both serve the "mailbox" block.
        spec = {"blocks": [{"name": "mailbox"}, {"name": "device"}]}
        roles = {"mailbox_controller": {"spec_block": "mailbox", "vlnv_candidates": ["test:ip:ctrl"]},
                 "mailbox_memory": {"spec_block": "mailbox", "vlnv_candidates": ["test:ip:mem"]},
                 "device": {"vlnv_candidates": ["test:ip:device"]}}
        snapshot = {"catalog": {role: {"resolved_vlnv": f"test:ip:{role}:1.0"} for role in roles}}

        def cells(**instances):
            intent = {"schema_version": 1, "blocks": copy.deepcopy(roles)}
            for role, name in instances.items():
                intent["blocks"][role]["instance"] = name
            resolved = binder.resolve_bindings(spec, intent, snapshot)["blocks"]
            return {role: block["instance"] for role, block in resolved.items()}

        # Unnamed roles on a shared block are named for the role; a sole role keeps the block name.
        self.assertEqual(cells(), {"mailbox_controller": "mailbox_controller",
                                   "mailbox_memory": "mailbox_memory", "device": "device"})
        # One role may claim the block's name explicitly.
        self.assertEqual(cells(mailbox_controller="mailbox")["mailbox_controller"], "mailbox")
        # Two roles claiming one cell is refused, naming both.
        with self.assertRaisesRegex(binder.BindError, "mailbox_controller mailbox_memory would all create cell 'mailbox'"):
            cells(mailbox_controller="mailbox", mailbox_memory="mailbox")
        with self.assertRaisesRegex(binder.BindError, "instance must be a cell name"):
            cells(mailbox_memory="1bad")

    def test_incomplete_probe_rejected(self):
        encoded = base64.b64encode(b"test-part").decode()
        with self.assertRaisesRegex(binder.BindError, "did not finish"):
            binder.parse_probe(f"IPIASM_BIND\tenvironment.part\t{encoded}\n")

    def test_legacy_creation_error_rejected(self):
        snapshot = {"catalog": {"device": {
            "resolved_vlnv": "test:ip:device:1.0", "create_error": "unsupported part",
        }}}
        with self.assertRaisesRegex(binder.BindError, "IP creation failed"):
            binder.resolve_bindings(self.spec, self.intent, snapshot)

    def test_endpoint_requirements(self):
        intent = copy.deepcopy(self.intent)
        intent["blocks"]["device"].update({
            "interfaces": {"control": {
                "aliases": ["S_AXI"], "mode": "Slave", "vlnv": "test:interface:aximm:1.0",
            }},
            "pins": {"events": {"aliases": ["events"], "direction": "I", "width": 8}},
        })
        snapshot = {"catalog": {"device": {
            "resolved_vlnv": "test:ip:device:1.0",
            "interfaces": [{"name": "S_AXI", "mode": "Slave", "vlnv": "test:interface:aximm:1.0"}],
            "pins": [{"name": "events", "direction": "I", "width": "8"}],
        }}}
        self.assertIn("device", binder.resolve_bindings(self.spec, intent, snapshot)["blocks"])
        for collection, key, wrong in (
            ("interfaces", "mode", "Master"),
            ("interfaces", "vlnv", "test:interface:axis:1.0"),
            ("pins", "direction", "O"),
            ("pins", "width", "1"),
        ):
            with self.subTest(key=key):
                changed = copy.deepcopy(snapshot)
                changed["catalog"]["device"][collection][0][key] = wrong
                with self.assertRaisesRegex(binder.BindError, "no endpoint satisfies"):
                    binder.resolve_bindings(self.spec, intent, changed)

    def test_embedded_placeholders_resolve_or_fail(self):
        snapshot = {"environment": {"part": "xcpart"}, "catalog": {"device": {"resolved_vlnv": "test:ip:device:1.0"}}}
        template = {"stages": [{"name": "connect", "operations": [{
            "op": "call", "proc": "ipiasm::connect_intf",
            "args": ["${bind.blocks.device.instance}/S_AXI", "on ${env.part}", "${keep}/x"]}]}]}
        plan = binder.bind(self.spec, self.intent, snapshot, template)
        self.assertEqual(plan["stages"][0]["operations"][0]["args"], ["device/S_AXI", "on xcpart", "${keep}/x"])
        for text, why in (("${bind.blocks.device.interfaces}/x", "not a scalar"),
                          ("${bind.blocks.ghost.instance}/x", "unresolved placeholder")):
            with self.subTest(text=text):
                broken = copy.deepcopy(template)
                broken["stages"][0]["operations"][0]["args"] = [text]
                with self.assertRaisesRegex(binder.BindError, why):
                    binder.bind(self.spec, self.intent, snapshot, broken)

    def test_bound_plan_compiles_against_its_spec_file(self):
        import json
        import tempfile
        snapshot = {"environment": {"part": "xcpart"}, "catalog": {"device": {"resolved_vlnv": "test:ip:device:1.0"}}}
        template = {"schema_version": 1, "stages": [{"name": "cells", "operations": [
            {"op": "call", "proc": "ipiasm::create_cell",
             "args": ["${bind.blocks.device.vlnv}", "${bind.blocks.device.instance}", {}]},
            {"op": "call", "proc": "ipiasm::gate_inventory", "args": [{"device": 1}]}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"spec": self.spec, "intent": self.intent, "snapshot": snapshot, "template": template}
            for name, value in files.items():
                (root / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")
            argv = ["bind"] + [a for name in files for a in (f"--{name}", str(root / f"{name}.json"))]
            self.assertEqual(binder.main(argv + ["--out", str(root / "plan.json")]), 0)
            self.assertEqual(compiler.main([str(root / "plan.json"), "--out", str(root / "plan.tcl"),
                                            "--spec", str(root / "spec.json")]), 0)
            (root / "spec.json").write_text(json.dumps(self.spec) + "\n")
            self.assertNotEqual(compiler.main([str(root / "plan.json"), "--out", str(root / "plan.tcl"),
                                               "--spec", str(root / "spec.json")]), 0)

    def test_legacy_aliases_still_work(self):
        self.assertEqual(binder._choose_endpoint([{"name": "control"}], ["control"], "device"), "control")

    def test_unknown_endpoint_check_rejected(self):
        with self.assertRaisesRegex(binder.BindError, "unknown endpoint requirements"):
            binder._choose_endpoint([], {"aliases": ["events"], "widht": 8}, "device")

    @unittest.skipUnless(shutil.which("tclsh"), "requires Tcl")
    def test_probe_tries_supported_candidate_and_numeric_version(self):
        harness = r'''
proc version {args} {return test-version}
proc current_project {} {return project}
proc get_board_part_interfaces {args} {return {}}
proc get_property {args} {return test-value}
proc get_ipdefs {args} {
    if {[string match *unsupported* [lindex $args end]]} {return {test:ip:unsupported:1.0}}
    return {test:ip:device:1.9 test:ip:device:1.10}
}
proc create_bd_cell {args} {
    if {[string match *unsupported* $args]} {error {unsupported part}}
    return [lindex $args end]
}
proc get_bd_cells {args} {return {}}
proc delete_bd_objs {objects} {
    if {![llength $objects]} {error {no objects to delete}}
}
proc list_property {args} {return {CONFIG.WIDTH}}
proc get_bd_intf_pins {args} {return {}}
proc get_bd_pins {args} {return {}}
'''
        result = subprocess.run(
            ["tclsh"], input=harness + binder.generate_probe_tcl(self.intent),
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stderr, "")
        snapshot = binder.parse_probe(result.stdout)
        observed = snapshot["catalog"]["device"]
        self.assertEqual(observed["resolved_vlnv"], "test:ip:device:1.10")
        self.assertIn("unsupported part", observed["attempt_1"])
        resolved = binder.resolve_bindings(self.spec, self.intent, snapshot)
        self.assertEqual(resolved["blocks"]["device"]["vlnv"], "test:ip:device:1.10")

    @unittest.skipUnless(shutil.which("tclsh"), "requires Tcl")
    def test_probe_cell_takes_reference_config_before_interfaces_are_read(self):
        harness = r'''
proc version {args} {return test-version}
proc current_project {} {return project}
proc get_board_part_interfaces {args} {return {}}
proc get_property {args} {
    if {[lindex $args 0] eq "NAME"} {return [lindex $args 1]}
    return test-value
}
proc get_ipdefs {args} {return {test:ip:device:1.0}}
proc create_bd_cell {args} {return [lindex $args end]}
proc get_bd_cells {args} {return {}}
proc delete_bd_objs {objects} {}
proc list_property {args} {return {CONFIG.C_IS_DUAL}}
set ::dual 0
proc set_property {args} {
    if {[lindex $args 0] eq "-quiet" && [dict get [lindex $args 2] CONFIG.C_IS_DUAL] eq "1"} {set ::dual 1}
}
proc get_bd_intf_pins {args} {return [expr {$::dual ? {GPIO GPIO2} : {GPIO}}]}
proc get_bd_pins {args} {return {}}
'''
        intent = {"schema_version": 1, "blocks": {"device": {
            "vlnv_candidates": ["test:ip:device"], "interfaces": {"gpio2": ["GPIO2"]}}}}
        spec = {"blocks": [{"name": "device", "parameters": {"reference_config": {"CONFIG.C_IS_DUAL": "1"}}}]}
        result = subprocess.run(["tclsh"], input=harness + binder.generate_probe_tcl(intent, spec=spec),
                                text=True, capture_output=True, check=True)
        snapshot = binder.parse_probe(result.stdout)
        resolved = binder.resolve_bindings(spec, intent, snapshot)
        self.assertEqual(resolved["blocks"]["device"]["interfaces"]["gpio2"], "GPIO2")
        unconfigured = subprocess.run(["tclsh"], input=harness + binder.generate_probe_tcl(intent),
                                      text=True, capture_output=True, check=True)
        with self.assertRaisesRegex(binder.BindError, "none of aliases"):
            binder.resolve_bindings(spec, intent, binder.parse_probe(unconfigured.stdout))

    def test_empty_catalog_result_preserved(self):
        complete = base64.b64encode(b"1").decode()
        snapshot = binder.parse_probe(
            "IPIASM_BIND\tcatalog.device.resolved_vlnv\t\n"
            f"IPIASM_BIND\tprobe.complete\t{complete}\n"
        )
        self.assertEqual(snapshot["catalog"]["device"]["resolved_vlnv"], "")
        with self.assertRaisesRegex(binder.BindError, "none of .* is available"):
            binder.resolve_bindings(self.spec, self.intent, snapshot)


if __name__ == "__main__":
    unittest.main()