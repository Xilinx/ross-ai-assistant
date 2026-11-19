# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A STRUCTURAL CED fit drafts a plan that starts from the CED and leaves almost nothing to write.

The fixture is an SCU35 I/O expander (three GPIO, three I2C, one UART, timer, interrupt
controller at 200 MHz) against the MicroBlaze V CED (four GPIO, four I2C, three UARTs at
100 MHz). Its plan, drafted by this code, built and delivered in Vivado 2026.2 unchanged.
"""

import copy
import json
from pathlib import Path
import sys
import unittest

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / "scripts"))
import bind_design_spec as binder  # noqa: E402
import compile_assembly_plan as compiler  # noqa: E402
import draft_plan  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "draft_plan_ced"


def load(name):
    return json.loads((FIXTURE / name).read_text())


def operations(template, stage=None):
    return [op for s in template["stages"] if stage in (None, s["name"]) for op in s["operations"]]


class CedSeededDraftTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.intent, self.snapshot = (load("design_spec.json"), load("binding_intent.json"),
                                                 load("binding_snapshot.json"))
        records = [json.loads(line) for line in (FIXTURE / "ced_reference.json").read_text().splitlines() if line]
        self.template, self.todo = draft_plan.draft(self.spec, self.intent, self.snapshot, [], records,
                                                    "/abs/out/clock_fingerprint.tcldict")

    def test_cells_start_from_the_ced_and_drop_only_surplus_units(self):
        cells = operations(self.template, "cells")
        self.assertEqual(cells[0]["proc"], "ipiasm::instantiate_ced")
        removed = sorted(op["args"][0] for op in cells if op.get("proc") == "ipiasm::remove_cell")
        self.assertEqual(removed, ["/axi_gpio_2", "/axi_iic_3", "/axi_uartlite_1", "/axi_uartlite_2"])
        # Every block the CED already holds is kept, not created again.
        self.assertFalse([op for op in cells if op.get("proc") == "ipiasm::create_cell"])

    def test_clock_rate_the_ced_misses_is_pinned_and_contracted(self):
        cells = operations(self.template, "cells")
        pins = [op for op in cells if op.get("proc") == "ipcfg::require"
                and "CONFIG.CLKOUT1_REQUESTED_OUT_FREQ" in op["args"][1]]
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0]["args"][1]["CONFIG.CLKOUT1_REQUESTED_OUT_FREQ"], "200.000")
        register = next(op for op in operations(self.template, "clocks")
                        if op.get("proc") == "ipiasm::register_clock_contracts")
        checks, fingerprint = register["args"]
        self.assertEqual(fingerprint, "/abs/out/clock_fingerprint.tcldict")
        self.assertTrue(checks and all(check["expected"] == 200000000 and check["cell"].startswith("/")
                                       for check in checks), checks)

    def test_interrupts_and_fixed_windows_come_from_the_ced(self):
        procs = [op.get("proc") for op in operations(self.template, "connect")]
        self.assertIn("ipiasm::set_irq_contracts", procs)
        self.assertIn("ipiasm::check_irq_paths", procs)
        scopes = [op for op in operations(self.template, "address") if op.get("proc") == "ipiasm::check_window_scope"]
        windows = self.spec["functional_contracts"]["fixed_address_windows"]
        self.assertEqual(len(scopes), len(windows))
        # A soft-processor design has no processing system: no OVERMAP:NA op to fail on.
        self.assertNotIn("ipiasm::check_overmap", [op.get("proc") for op in operations(self.template)])

    def test_window_the_ced_maps_elsewhere_is_moved_before_its_scope_check(self):
        window = next(w for w in self.spec["functional_contracts"]["fixed_address_windows"]
                      if w["target"] == "axi_iic_2")
        window["offset"] = "0x40830000"
        records = [json.loads(line) for line in (FIXTURE / "ced_reference.json").read_text().splitlines() if line]
        template, todo = draft_plan.draft(self.spec, self.intent, self.snapshot, [], records, "/abs/fp.tcldict")
        address = [op for op in operations(template, "address") if "axi_iic_2" in op["id"]]
        self.assertEqual([op["proc"] for op in address], ["ipiasm::enforce_window_scope", "ipiasm::check_window_scope"])
        self.assertEqual(address[0]["args"][3], "0x40830000")
        self.assertFalse([item for item in todo if "[address]" in item], todo)

    def test_bound_draft_compiles_against_the_spec_with_no_gate_gap(self):
        self.assertEqual(len(self.todo), 1, self.todo)
        self.assertIn("validate_bundle", self.todo[0])
        gaps = draft_plan.compiler_gaps(self.spec, self.intent, self.snapshot, self.template)
        self.assertEqual(gaps, [])
        plan = binder.bind(self.spec, self.intent, self.snapshot, copy.deepcopy(self.template))
        self.assertIn("ipiasm::instantiate_ced", compiler.compile_plan(plan))


if __name__ == "__main__":
    unittest.main()
