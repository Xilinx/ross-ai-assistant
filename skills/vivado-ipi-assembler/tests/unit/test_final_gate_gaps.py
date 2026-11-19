# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The compiler names what ipiasm::deliver's final gate will refuse before the build runs."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "scripts"))
import compile_assembly_plan as compiler  # noqa: E402


def spec():
    return {
        "name": "io",
        "blocks": [{"name": "cpu"}, {"name": "intc"}, {"name": "uart"}, {"name": "timer"}],
        "functional_contracts": {
            "interrupts": [
                {"source": "uart", "outputs": ["interrupt"], "destination": "intc", "required": True},
                {"source": "timer", "outputs": ["interrupt"], "destination": "host_cpu", "required": True},
            ],
            "fixed_address_windows": [
                {"name": "uart_window", "target": "uart", "masters": ["cpu"],
                 "offset": "0x40600000", "range": "0x00010000"},
                {"name": "optional_window", "target": "timer", "offset": "0x41c00000",
                 "range": "0x00010000", "required": False},
            ],
        },
    }


def call(op_id, proc, args):
    return {"id": op_id, "op": "call", "proc": proc, "args": args}


def plan(*operations):
    return {"schema_version": 1, "name": "io", "stages": [{"name": "cells", "operations": list(operations)}]}


COMPLETE = (
    call("pin", "ipcfg::require", ["/uart", {"CONFIG.C_BAUDRATE": "115200"}, "spec baud rate"]),
    call("irq", "ipiasm::set_irq_contracts", [[["/intc/intr[0]", "/uart/interrupt", "uart.interrupt"]]]),
    call("win", "ipiasm::check_window_scope", ["/uart/S_AXI/Reg", ["/cpu/Data"], "0x40600000", 65536]),
)


class FinalGateGapTests(unittest.TestCase):
    def test_complete_plan_has_no_gaps(self):
        self.assertEqual(compiler.final_gate_gaps(spec(), plan(*COMPLETE)), [])

    def test_each_missing_obligation_is_named(self):
        gaps = compiler.final_gate_gaps(spec(), plan())
        self.assertEqual([gap.split(":")[0] for gap in gaps], ["pins", "interrupts", "windows"])
        self.assertIn("REQ:empty", gaps[0])
        self.assertIn("uart.interrupt", gaps[1])
        # Delivered outside the design: contract_coverage.py defers it, so it is no gap here.
        self.assertNotIn("timer.interrupt", gaps[1])
        self.assertIn("uart_window", gaps[2])
        self.assertNotIn("optional_window", gaps[2])

    def test_window_matches_by_value_not_spelling(self):
        scoped = plan(COMPLETE[0], COMPLETE[1],
                      call("win", "ipiasm::check_window_scope", ["/uart/S_AXI/Reg", ["/cpu/Data"], 1080033280, "65536"]))
        self.assertEqual(compiler.final_gate_gaps(spec(), scoped), [])

    def test_visibility_rules_need_a_check_visibility_op(self):
        ruled = spec()
        ruled["functional_contracts"]["visibility_policy"] = [
            {"action": "allow", "masters": ["cpu"], "targets": ["uart"]}]
        gaps = compiler.final_gate_gaps(ruled, plan(*COMPLETE))
        self.assertEqual([gap.split(":")[0] for gap in gaps], ["visibility"])
        checked = plan(*COMPLETE, call("vis", "ipiasm::check_visibility", ["/uart/S_AXI/Reg", ["/cpu/Data"]]))
        self.assertEqual(compiler.final_gate_gaps(ruled, checked), [])

    def test_memory_without_a_controller_window_needs_a_dram_master_check(self):
        ruled = spec()
        ruled["blocks"].append({"name": "mc", "workload_type": "memory_controller"})
        ruled["functional_contracts"]["memory_reachability"] = [{"master": "cpu", "memory": "mc.ddr"}]
        gaps = compiler.final_gate_gaps(ruled, plan(*COMPLETE))
        self.assertEqual([gap.split(":")[0] for gap in gaps], ["memory"])
        self.assertIn("mc.ddr", gaps[0])
        checked = plan(*COMPLETE, call("dram", "ipiasm::check_required_dram_masters", [["/cpu/Data"]]))
        self.assertEqual(compiler.final_gate_gaps(ruled, checked), [])

    def test_recorded_evidence_closes_its_category(self):
        evidenced = plan(COMPLETE[0], call("ev", "ipiasm::record_evidence",
                                           ["address_map", "fixed_address_windows interrupts", "ADDR:OK"]))
        self.assertEqual(compiler.final_gate_gaps(spec(), evidenced), [])

    def test_unlabelled_irq_record_does_not_cover_an_output(self):
        unlabelled = plan(COMPLETE[0], COMPLETE[2],
                          call("irq", "ipiasm::set_irq_contracts", [[["/intc/intr[0]", "/uart/interrupt"]]]))
        gaps = compiler.final_gate_gaps(spec(), unlabelled)
        self.assertEqual(len(gaps), 1)
        self.assertTrue(gaps[0].startswith("interrupts:"))

    def test_cli_reports_gaps_on_the_ok_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "design_spec.json").write_text(json.dumps(spec()))
            (root / "assembly_plan.json").write_text(json.dumps(plan(COMPLETE[0])))
            run = subprocess.run([sys.executable, str(SKILL / "scripts" / "compile_assembly_plan.py"),
                                  str(root / "assembly_plan.json"), "--out", str(root / "plan.tcl")],
                                 capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
            lines = run.stdout.strip().splitlines()
            self.assertEqual([line.split(":")[0] for line in lines[:-1]], ["FINAL_GATE_GAP", "FINAL_GATE_GAP"])
            self.assertTrue(lines[-1].startswith("ASSEMBLY_PLAN:OK stages=1 tcl="))
            self.assertTrue(lines[-1].endswith(" final_gate_gaps=2"))


if __name__ == "__main__":
    unittest.main()
