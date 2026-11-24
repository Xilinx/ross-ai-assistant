# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import compile_assembly_plan as compiler  # noqa: E402


def call(proc, *args):
    return {"id": f"op_{args[1]}", "op": "call", "proc": proc, "args": list(args)}


PLAN = {"schema_version": 1, "stages": [{"name": "cells", "operations": [
    call("ipiasm::create_cell", "xilinx.com:ip:axi_bram_ctrl:4.1", "mailbox_ctrl", {}),
    call("ipiasm::create_cell", "xilinx.com:ip:xlconstant:1.1", "tie_off", {}),
    call("ipiasm::instantiate_n", "xilinx.com:ip:axi_gpio:2.0", "gpio", 2, {}),
    call("ipiasm::create_cell", "${bind.blocks.isp.vlnv}", "isp", {}),
]}]}

SNAPSHOT = {"catalog": {
    "mailbox_bram_controller": {"resolved_vlnv": "xilinx.com:ip:axi_bram_ctrl:4.1"},
    "gpio": {"resolved_vlnv": "xilinx.com:ip:axi_gpio:2.0"},
}}


class LiveBindingTests(unittest.TestCase):
    def check(self, plan, snapshot=SNAPSHOT, tcldict=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            if snapshot is not None:
                (tmp / "binding_snapshot.json").write_text(json.dumps(snapshot))
            if tcldict:
                (tmp / "clock.tcldict").write_text(tcldict)
            compiler.check_live_binding(plan, tmp)

    def with_bram(self):
        plan = json.loads(json.dumps(PLAN))
        plan["stages"][0]["operations"].append(
            call("ipiasm::create_cell", "xilinx.com:ip:blk_mem_gen:8.4", "mailbox_bram", {}))
        return plan

    def test_probed_plumbing_and_placeholders_pass(self):
        self.check(PLAN)

    def test_unprobed_helper_is_refused_with_op_id(self):
        with self.assertRaisesRegex(compiler.PlanError,
                                    r"BINDING_FAIL:.*xilinx.com:ip:blk_mem_gen \(ops op_mailbox_bram\)"):
            self.check(self.with_bram())

    def test_all_missing_ips_reported_together(self):
        with self.assertRaisesRegex(compiler.PlanError, "axi_gpio.*blk_mem_gen|blk_mem_gen.*axi_gpio"):
            self.check(self.with_bram(), {"catalog": {"m": {"resolved_vlnv": "xilinx.com:ip:axi_bram_ctrl:4.1"}}})

    def test_clock_search_record_proves_its_vlnv(self):
        plan = json.loads(json.dumps(PLAN))
        plan["stages"][0]["operations"].append(
            call("ipiasm::create_cell", "xilinx.com:ip:clk_wizard:1.0", "clocks", {}))
        with self.assertRaises(compiler.PlanError):
            self.check(plan)
        self.check(plan, tcldict="schema_version 1 data {status PASS vlnv xilinx.com:ip:clk_wizard:1.0 x 1}")

    def test_no_snapshot_is_not_checked(self):
        self.check(self.with_bram(), snapshot=None)


if __name__ == "__main__":
    unittest.main()
