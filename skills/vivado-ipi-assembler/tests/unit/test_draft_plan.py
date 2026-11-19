# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""draft_plan.py writes the derivable part of the plan template, so the model edits instead of writing it."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "scripts"))
import bind_design_spec as binder  # noqa: E402
import compile_assembly_plan as compiler  # noqa: E402
import draft_plan  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "draft_plan"


def load(name):
    return json.loads((FIXTURE / name).read_text())


class DraftPlanTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.intent, self.snapshot = load("design_spec.json"), load("binding_intent.json"), \
            load("binding_snapshot.json")

    def test_draft_holds_every_derivable_operation_and_compiles(self):
        template, todo = draft_plan.draft(self.spec, self.intent, self.snapshot, [])
        stages = [stage["name"] for stage in template["stages"]]
        self.assertEqual(stages, ["board", "cells", "clocks", "connect", "qos", "address", "validate"])
        ops = {(stage["name"], op.get("proc") or op["op"]) for stage in template["stages"] for op in stage["operations"]}
        self.assertIn(("board", "create_bd_design"), ops)
        self.assertIn(("board", "ipiasm::require_board_preset"), ops)
        self.assertIn(("board", "ipcfg::preset_drift"), ops)
        self.assertIn(("cells", "ipiasm::gate_inventory"), ops)
        self.assertIn(("address", "ipiasm::auto_address"), ops)
        self.assertIn(("validate", "ipiasm::validate_bundle"), ops)
        creates = [op for stage in template["stages"] for op in stage["operations"]
                   if op.get("proc") == "ipiasm::create_cell"]
        self.assertEqual(len(creates), len(self.intent["blocks"]))
        inventory = next(op for stage in template["stages"] for op in stage["operations"]
                         if op.get("proc") == "ipiasm::gate_inventory")
        self.assertEqual(sum(inventory["args"][0][1::2]), len(self.intent["blocks"]))
        # Every call fits its helper's signature, so the bound draft compiles to Tcl.
        plan = binder.bind(self.spec, self.intent, self.snapshot, copy.deepcopy(template))
        self.assertIn("ipiasm::create_cell", compiler.compile_plan(plan))
        # What is left needs a decision, and the TODO list says so.
        self.assertTrue(any("apply_automation" in item for item in todo))
        self.assertTrue(any("check_required_reachability" in item for item in todo))
        gaps = draft_plan.compiler_gaps(self.spec, self.intent, self.snapshot, template)
        compile_gaps = [gap for gap in gaps if not gap.startswith("final_gate: ")]
        self.assertTrue(compile_gaps and all("reachability" in gap.lower() for gap in compile_gaps), gaps)
        self.assertIn("final_gate: pins", [gap.split(":", 2)[0] + ":" + gap.split(":", 2)[1] for gap in gaps])

    def test_part_only_design_asks_for_the_board_override_instead_of_a_preset(self):
        self.snapshot["environment"]["board_part"] = ""
        template, todo = draft_plan.draft(self.spec, self.intent, self.snapshot, [])
        procs = [op.get("proc") for stage in template["stages"] for op in stage["operations"]]
        self.assertNotIn("ipiasm::require_board_preset", procs)
        self.assertNotIn("ipcfg::preset_drift", procs)
        self.assertTrue(any("gate_override board" in item for item in todo))

    def test_command_line_writes_template_and_todo_and_prints_one_status_line(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root, "assembly_plan.template.json")
            result = subprocess.run([sys.executable, str(SKILL / "scripts" / "draft_plan.py"),
                                     "--spec", str(FIXTURE / "design_spec.json"),
                                     "--intent", str(FIXTURE / "binding_intent.json"),
                                     "--snapshot", str(FIXTURE / "binding_snapshot.json"), "--out", str(out)],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(result.stdout.strip().splitlines()[-1].startswith("DRAFT_PLAN:OK ops="))
            self.assertTrue(Path(root, "assembly_plan.template.todo.md").is_file())
            self.assertEqual(json.loads(out.read_text())["schema_version"], 1)
            self.assertLess(len(result.stdout), 4000, "the status output stays short")


class ScratchBindTests(unittest.TestCase):
    def run_tcl(self, body):
        script = (f"source {{{SKILL.parent / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}\n"
                  f"source {{{SKILL / 'lib/ipiasm.tcl'}}}\n{body}\n")
        result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120)
        return result.stdout

    def test_refuses_without_intent_or_project(self):
        with tempfile.TemporaryDirectory() as root:
            out = self.run_tcl(f"puts [ipiasm::scratch_bind {{{root}}}]")
            self.assertIn("SCRATCH_BIND_FAIL:no binding_intent.json", out)
            Path(root, "binding_intent.json").write_text("{}")
            out = self.run_tcl(f"puts [ipiasm::scratch_bind {{{root}}}]")
            self.assertIn("SCRATCH_BIND_FAIL:open the scratch project and BD first", out)


if __name__ == "__main__":
    unittest.main()
