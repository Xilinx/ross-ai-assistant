# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import compile_assembly_plan as compiler  # noqa: E402

ROLES_TCL = """namespace eval ::addrmap {
    variable target_roles {
        aie.array ai_engine S00_AXI/AIE_ARRAY_0
        isp.control isp_subsystem S_AXI_LITE/Reg
        gpio.tile0 isp_tile0_gpio S_AXI/Reg
        # a comment line
        isp.tile0 isp_subsystem TILE0_ISP_NSU/Reg
    }
}

proc ::addrmap::required_roles {} {
    variable target_roles
    set result {processing_system memory_noc isp_subsystem}
    foreach {logical role suffix} $target_roles {
        if {$role ni $result} {lappend result $role}
    }
    return $result
}
"""

LAB_SCRIPT = Path(__file__).resolve().parents[5] / "inputs" / "scripts" / "stage8_addressing.tcl"


def call(proc, *args):
    return {"id": f"{proc}_{args[1] if len(args) > 1 else ''}", "op": "call", "proc": proc, "args": list(args)}


PLAN = {"schema_version": 1, "stages": [{"name": "cells", "operations": [
    {"id": "bd", "op": "create_bd_design", "name": "design_1"},
    call("ipiasm::create_cell", "xilinx.com:ip:ps_wizard:1.0", "ps", {}),
    call("ipiasm::create_cell", "xilinx.com:ip:axi_noc2:1.1", "lpddr5", {}),
    call("ipiasm::create_cell", "xilinx.com:ip:ai_engine:2.0", "aie_array", {}),
    call("ipiasm::create_cell", "xilinx.com:ip:visp_ss:1.0", "isp", {}),
    call("ipiasm::instantiate_n", "xilinx.com:ip:axi_gpio:2.0", "tile_gpio", 3, {}),
]}]}

GOOD = """
| role | cell |
|---|---|
| processing_system | `ps` |
| memory_noc | `lpddr5` |
| isp_subsystem | `isp` |
| ai_engine | `aie_array` |
| isp_tile0_gpio | `tile_gpio_0` |
"""


class RoleTableTests(unittest.TestCase):
    def test_roles_follow_seed_then_triples_without_duplicates(self):
        self.assertEqual(compiler.required_roles_from_tcl(ROLES_TCL),
                         ["processing_system", "memory_noc", "isp_subsystem", "ai_engine", "isp_tile0_gpio"])

    @unittest.skipUnless(LAB_SCRIPT.is_file(), "lab addressing script not present")
    def test_lab_script_parses(self):
        roles = compiler.required_roles_from_tcl(LAB_SCRIPT.read_text())
        self.assertEqual(roles[:3], ["processing_system", "memory_noc", "isp_subsystem"])
        self.assertEqual(len(roles), len(set(roles)))
        self.assertIn("camera_receiver", roles)

    def test_missing_proc_is_refused(self):
        with self.assertRaisesRegex(compiler.PlanError, "ROLE_TABLE_FAIL"):
            compiler.required_roles_from_tcl("proc other {} {}")

    def roles(self):
        return compiler.required_roles_from_tcl(ROLES_TCL)

    def test_good_table_passes(self):
        compiler.check_role_table(GOOD, self.roles(), PLAN)

    def test_missing_row_names_the_role(self):
        with self.assertRaisesRegex(compiler.PlanError, "ai_engine has no"):
            compiler.check_role_table(GOOD.replace("| ai_engine | `aie_array` |\n", ""), self.roles(), PLAN)

    def test_cell_the_plan_does_not_create_is_refused(self):
        with self.assertRaisesRegex(compiler.PlanError, "memory_noc->noc is not a cell"):
            compiler.check_role_table(GOOD.replace("`lpddr5`", "`noc`"), self.roles(), PLAN)

    def test_vlnv_instead_of_instance_is_refused(self):
        with self.assertRaisesRegex(compiler.PlanError, "ai_engine->xilinx.com"):
            compiler.check_role_table(GOOD.replace("`aie_array`", "`xilinx.com:ip:ai_engine:2.0`"),
                                      self.roles(), PLAN)

    def test_shared_cell_is_refused(self):
        with self.assertRaisesRegex(compiler.PlanError, "isp is mapped by several roles"):
            compiler.check_role_table(GOOD.replace("`tile_gpio_0`", "`isp`"), self.roles(), PLAN)

    def test_backticked_role_and_later_matching_row(self):
        table = GOOD.replace("| ai_engine | `aie_array` |", "| ai_engine | `aie` |\n| `ai_engine` | `aie_array` |")
        compiler.check_role_table(table, self.roles(), PLAN)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "assembly_plan.json").write_text(json.dumps(PLAN))
            (tmp / "roles.tcl").write_text(ROLES_TCL)
            base = [str(tmp / "assembly_plan.json"), "--out", str(tmp / "out.tcl"),
                    "--roles-from", str(tmp / "roles.tcl")]
            (tmp / "plan.md").write_text(GOOD)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(compiler.main(base), 0)
            (tmp / "plan.md").write_text(GOOD.replace("| memory_noc | `lpddr5` |\n", ""))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(compiler.main(base), 1)
            self.assertIn("ASSEMBLY_PLAN_FAIL:ROLE_TABLE_FAIL:memory_noc", err.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(compiler.main(base[:3] + ["--role-table", str(tmp / "plan.md")]), 1)


if __name__ == "__main__":
    unittest.main()
