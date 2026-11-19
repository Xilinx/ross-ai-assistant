# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import compile_assembly_plan as compiler


def call(op_id, proc, *args):
    return {"id": op_id, "op": "call", "proc": proc, "args": list(args)}


REGIONS = ["low", "0x80000000", "high", "0x380000000"]
ATTACH = call("attach", "ipiasm::attach_board_memory", "mem", 4,
              ["MC_CHAN_REGION0", "REGION_A", "MC_CHAN_REGION1", "REGION_B"])
ROUTE = call("route", "ipiasm::add_noc_si", "mem", "MC_0", "ps/M_NOC", 100, 100)
CHECK = call("regions", "ipiasm::check_memory_regions", REGIONS)


def plan(*stages):
    return {"schema_version": 1,
            "stages": [{"name": name, "operations": list(ops)} for name, ops in stages]}


class MemoryPlanRules(unittest.TestCase):
    def test_regions_checked_after_route_with_selectors_compiles(self):
        tcl = compiler.compile_plan(plan(("cells", [ATTACH]), ("connect", [ROUTE, CHECK])))
        self.assertIn("ipiasm::check_memory_regions", tcl)

    def test_attach_without_region_check_is_rejected(self):
        with self.assertRaisesRegex(compiler.PlanError, "later ipiasm::check_memory_regions"):
            compiler.compile_plan(plan(("cells", [ATTACH]), ("connect", [ROUTE])))

    def test_region_check_before_the_first_route_does_not_count(self):
        with self.assertRaisesRegex(compiler.PlanError, "after the first NoC route.*place it after operation 'route' \\(found only before it: regions\\)"):
            compiler.compile_plan(plan(("cells", [ATTACH, CHECK]), ("connect", [ROUTE])))

    def test_selectors_must_cover_the_checked_regions(self):
        bare = copy.deepcopy(ATTACH)
        bare["args"] = ["mem", 4]
        with self.assertRaisesRegex(compiler.PlanError, "expects 2 region\\(s\\) but attach_board_memory selects 1"):
            compiler.compile_plan(plan(("cells", [bare]), ("connect", [ROUTE, CHECK])))

    def test_single_region_needs_no_selector(self):
        bare = copy.deepcopy(ATTACH)
        bare["args"] = ["mem", 4]
        single = call("regions", "ipiasm::check_memory_regions", ["low", "0x80000000"])
        compiler.compile_plan(plan(("cells", [bare]), ("connect", [ROUTE, single])))

    def test_empty_region_list_is_rejected(self):
        empty = call("regions", "ipiasm::check_memory_regions", [])
        with self.assertRaisesRegex(compiler.PlanError, "non-empty"):
            compiler.compile_plan(plan(("cells", [ATTACH]), ("connect", [ROUTE, empty])))

    def test_plans_without_board_memory_are_unaffected(self):
        compiler.compile_plan(plan(("connect", [ROUTE])))

    def test_designspec_region_objects_count_as_selectors(self):
        spec = [{"name": "low", "base": "0x0", "size": "0x80000000"},
                {"name": "high", "base": "0x800000000", "size": "0x380000000"}]
        attach = call("attach", "ipiasm::attach_board_memory", "mem", 4, spec)
        check = call("regions", "ipiasm::check_memory_regions", spec)
        compiler.compile_plan(plan(("cells", [attach]), ("connect", [ROUTE, check])))
        bare = copy.deepcopy(attach)
        bare["args"] = ["mem", 4]
        with self.assertRaisesRegex(compiler.PlanError, "DesignSpec regions"):
            compiler.compile_plan(plan(("cells", [bare]), ("connect", [ROUTE, check])))


if __name__ == "__main__":
    unittest.main()
