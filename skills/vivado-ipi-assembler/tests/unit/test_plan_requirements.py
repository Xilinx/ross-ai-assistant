# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""plan_requirements.py lists, up front, exactly what the plan compiler will demand."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import compile_assembly_plan as compiler  # noqa: E402
import plan_requirements as req  # noqa: E402

SPEC = {
    "blocks": [
        {"name": "sensor_rx", "parameters": {"lanes": 2, "modes": ["a", "b"], "extra": {}}},
        {"name": "fabric_bus", "parameters": {"width": 32}},
        {"name": "status_io", "parameters": {"bits": 3},
         "interfaces": [{"name": "status", "scope": "top_level"}]},
        {"name": "host_cpu", "parameters": {}},
    ],
    "functional_contracts": {
        "connections": [{"source": "status_io.status", "destination": "status_bit0"},
                        {"source": "status_io.status", "destination": "fabric_bus.in"}],
        "clock_reset": [{"target": "sensor_rx"}],
        "interrupts": [{"source": "sensor_rx"}],
        "control_reachability": [{"master": "host_cpu", "target": "status_io"},
                                 {"master": "offchip_host", "target": "not_built"}],
        "memory_reachability": [{"master": "sensor_rx", "memory": "fabric_bus"}],
    },
}

INTENT = {"schema_version": 1, "blocks": {
    "sensor_rx": {"spec_block": "sensor_rx", "vlnv_candidates": ["vendor.com:ip:mipi_csi2_rx_subsystem"]},
    "fabric_bus": {"spec_block": "fabric_bus", "vlnv_candidates": ["vendor.com:ip:axi_noc2"]},
    "status_io": {"spec_block": "status_io", "vlnv_candidates": ["vendor.com:ip:axi_gpio"]},
    "status_mem": {"spec_block": "status_io", "vlnv_candidates": ["vendor.com:ip:emb_mem_gen"]},
    "host_cpu": {"spec_block": "host_cpu", "vlnv_candidates": ["vendor.com:ip:ps_wizard"]},
}}

SNAPSHOT = {
    "environment": {"part": "xcpart", "board_part": "vendor:board:part0:1.0"},
    "catalog": {role: {"resolved_vlnv": block["vlnv_candidates"][0] + ":1.0", "config_properties": ["CONFIG.K"],
                       "interfaces": [], "pins": []}
                for role, block in INTENT["blocks"].items()},
}

ROLES_TCL = """
namespace eval ::addrmap {
    variable target_roles {
        {status_io gpio_status S_AXI}
    }
}
proc ::addrmap::required_roles {} {
    set result {cpu_main}
    return $result
}
"""

FRAGMENT = {"cells": [{"role": "sensor_rx", "requested": {"CONFIG.K": "2"},
                       "spec_pointers": ["/blocks/0/parameters/lanes"]}]}


def call(proc, *args):
    return {"op": "call", "proc": proc, "args": list(args)}


class PlanRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.found = req.requirements(SPEC, INTENT, SNAPSHOT, ROLES_TCL, [FRAGMENT])

    def test_roles_resolve_to_the_instances_binding_creates(self):
        self.assertEqual(self.found["roles"]["status_io"]["instance"], "status_io")
        self.assertEqual(self.found["roles"]["status_mem"]["instance"], "status_mem")

    def test_a_plan_with_the_listed_stage_checks_passes_the_stage_gate(self):
        stages = {}
        for stage, proc, _why in self.found["stages"]:
            stages.setdefault(stage, []).append(call(proc))
        creates = [call("ipiasm::create_cell", block["vlnv"], block["instance"], {})
                   for block in self.found["roles"].values()]
        plan = {"binding_context": {"board_part": "vendor:board:part0:1.0"},
                "stages": [{"name": "board", "operations": creates + stages["board"]}]
                + [{"name": name, "operations": stages[name]} for name in compiler.STAGE_ORDER[1:]]}
        compiler.check_stage_gates(SPEC, plan)
        for stage, proc, _why in self.found["stages"]:
            if proc == "ipiasm::attach_board_memory":
                continue
            reduced = json.loads(json.dumps(plan))
            entry = next(item for item in reduced["stages"] if item["name"] == stage)
            entry["operations"] = [op for op in entry["operations"] if op["proc"] != proc]
            with self.assertRaises(compiler.PlanError, msg=f"{stage} {proc}"):
                compiler.check_stage_gates(SPEC, reduced)

    def test_board_memory_checks_are_marked_conditional(self):
        conditional = [proc for _stage, proc, why in self.found["stages"] if "only if the plan calls" in why]
        self.assertIn("ipiasm::check_mem_noc_sysclk", conditional)
        self.assertNotIn("ipiasm::gate_inventory", conditional)
        self.assertEqual(self.found["after_validation"][0][0], "ipiasm::check_clock_contracts")

    def test_boundary_names_are_those_the_compiler_accepts(self):
        self.assertEqual(self.found["boundary"], [("status_io.status", ["status", "status_bit0"])])
        plan = {"stages": [{"name": "connect", "operations": [
            call("ipiasm::check_required_ports", [{"name": "status_bit0"}])]}]}
        compiler.check_boundary_coverage(SPEC, plan)

    def test_reachability_pairs_satisfy_the_compiler(self):
        self.assertEqual([(m, t) for m, t, _ms, _ts in self.found["reach"]],
                         [("sensor_rx", "fabric_bus"), ("host_cpu", "status_io")])
        pairs = [[masters[0], targets[0]] for _m, _t, masters, targets in self.found["reach"]]
        self.assertIn(["sensor_rx", "DRAM"], pairs)
        plan = {"resolved_bindings": {"blocks": self.found["roles"]},
                "stages": [{"name": "qos", "operations": [call("ipiasm::check_required_reachability", *pairs)]}]}
        compiler.check_reachability_coverage(SPEC, plan)

    def test_required_roles_come_from_the_roles_script(self):
        self.assertEqual(self.found["required_roles"], ["cpu_main", "gpio_status"])

    def test_coverage_skeleton_keys_every_unproven_leaf_with_its_value(self):
        self.assertEqual(self.found["proven"], ["/blocks/0/parameters/lanes"])
        skeleton = req.coverage_skeleton(self.found["cover"])["parameter_coverage"]
        self.assertEqual(skeleton["/blocks/0/parameters/modes/1"]["value"], "b")
        self.assertEqual(skeleton["/blocks/0/parameters/extra"]["value"], {})
        self.assertNotIn("/blocks/0/parameters/lanes", skeleton)
        everything = dict(skeleton, **{"/blocks/0/parameters/lanes": {"value": 2}})
        self.assertEqual(set(everything), set(req.parameter_leaves(SPEC)))

    def test_cli_prints_one_line_per_requirement_and_writes_the_skeleton(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name, data in (("spec", SPEC), ("intent", INTENT), ("snap", SNAPSHOT), ("g1", FRAGMENT)):
                (tmp / f"{name}.json").write_text(json.dumps(data))
            (tmp / "roles.tcl").write_text(ROLES_TCL)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = req.main(["--spec", str(tmp / "spec.json"), "--intent", str(tmp / "intent.json"),
                                 "--snapshot", str(tmp / "snap.json"), "--roles-from", str(tmp / "roles.tcl"),
                                 "--fragments", str(tmp / "g1.json"), "--coverage-out", str(tmp / "cov.json")])
            self.assertEqual(code, 0)
            text = out.getvalue()
            self.assertIn("BOUNDARY status_io.status port=status|status_bit0", text)
            self.assertIn("REQUIRED_ROLE gpio_status", text)
            self.assertTrue(text.strip().splitlines()[-1].startswith("PLAN_REQ:OK roles=5"))
            self.assertIn("/blocks/1/parameters/width",
                          json.loads((tmp / "cov.json").read_text())["parameter_coverage"])

    def test_an_unbindable_role_fails_with_the_binder_reason(self):
        snapshot = json.loads(json.dumps(SNAPSHOT))
        snapshot["catalog"]["status_io"]["resolved_vlnv"] = ""
        with self.assertRaises(req.RequirementError):
            req.requirements(SPEC, INTENT, snapshot)


if __name__ == "__main__":
    unittest.main()
