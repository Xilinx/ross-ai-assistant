# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The final gate's arguments come from the executed plan and its DesignSpec, nothing else."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import final_gate_inputs as inputs


def call(proc, *args, ident="x"):
    return {"id": ident, "op": "call", "proc": "ipiasm::" + proc, "args": list(args)}


SPEC = {
    "blocks": [{"name": "ddr", "workload_type": "memory_controller"},
               {"name": "scratch", "workload_type": "control_logic"},
               {"name": "uart", "workload_type": "control_logic"}],
    "functional_contracts": {
        "fixed_address_windows": [
            {"name": "win_ddr", "target": "ddr.low", "block": "ddr", "offset": "0x0", "range": "0x80000000"},
            {"name": "win_scratch", "target": "scratch", "block": "scratch", "offset": "0x80000000", "range": "0x2000"},
            {"name": "win_uart", "target": "uart.control", "block": "uart", "offset": "0xA0000000", "range": "0x10000"},
        ],
        "memory_reachability": [{"master": "cpu", "memory": "ddr.low"}, {"master": "dma", "memory": "scratch"}],
    },
}

PLAN = {"stages": [
    {"name": "address", "operations": [
        call("check_window_scope", "/noc/MC/DDR_LOW", ["/ps/cpu", "/dma/M"], "0x0", "0x80000000", ident="window_a"),
        call("check_window_scope", "/bram/S/Mem0", ["/ps/cpu", "/dma/M"], "0x80000000", 8192, ident="window_b"),
        call("check_window_scope", "/uart/S/Reg", ["/ps/cpu"], "0xa0000000", "0x10000", ident="window_c"),
    ]},
    {"name": "validate", "operations": [
        call("check_required_reachability", ["/ps/cpu", "/uart/S/Reg"]),
        call("validate_bundle", 2, "ps", 8, 1, 30),
    ]},
]}


class FinalGateInputs(unittest.TestCase):
    def test_windows_take_the_spec_name_with_the_same_offset_and_range(self):
        derived = inputs.derive(PLAN, SPEC)
        self.assertEqual([window[0] for window in derived["windows"]], ["win_ddr", "win_scratch", "win_uart"])
        self.assertEqual(derived["windows"][2], ["win_uart", "/uart/S/Reg", ["/ps/cpu"], "0xa0000000", "0x10000"])

    def test_dram_masters_come_only_from_memory_controller_windows(self):
        # The scratch BRAM is a required memory too, but it is not DRAM.
        self.assertEqual(inputs.derive(PLAN, SPEC)["dram_masters"], ["/ps/cpu", "/dma/M"])

    def test_bundle_and_reachability_are_the_plans_own(self):
        derived = inputs.derive(PLAN, SPEC)
        self.assertEqual(derived["bundle"], [2, "ps", 8, 1, 30])
        self.assertEqual(derived["reachability"], ["/ps/cpu", "/uart/S/Reg"])
        self.assertEqual(derived["visibility"], [])

    def test_plan_dram_master_checks_add_their_patterns(self):
        plan = {"stages": PLAN["stages"] + [{"name": "address2", "operations": [
            call("check_required_dram_masters", ["/tg0/Data", "/ps/cpu"])]}]}
        self.assertEqual(inputs.derive(plan, SPEC)["dram_masters"], ["/ps/cpu", "/dma/M", "/tg0/Data"])

    def test_visibility_checks_are_rerun_by_the_gate(self):
        plan = {"stages": PLAN["stages"] + [{"name": "visibility", "operations": [
            call("check_visibility", "/uart/S/Reg", ["/ps/cpu"]), call("check_visibility", "/bram/S/Mem0")]}]}
        self.assertEqual(inputs.derive(plan, SPEC)["visibility"],
                         [["/uart/S/Reg", ["/ps/cpu"]], ["/bram/S/Mem0", []]])

    def test_a_plan_window_without_a_spec_window_is_refused(self):
        plan = {"stages": [{"name": "address", "operations": [
            call("check_window_scope", "/x/S/Reg", ["/ps/cpu"], "0xB0000000", "0x1000", ident="window_x")]}]}
        with self.assertRaisesRegex(ValueError, r"window_x \(/x/S/Reg 0xB0000000/0x1000\) matches none"):
            inputs.derive(plan, SPEC)

    def test_windows_sharing_offset_and_range_are_told_apart_by_master(self):
        spec = {"blocks": [{"name": "mc", "workload_type": "memory_controller"}],
                "functional_contracts": {"fixed_address_windows": [
                    {"name": "s0", "target": "mc.S0_AXI", "block": "mc", "masters": ["tg_0"],
                     "offset": "0x0", "range": "0x80000000"},
                    {"name": "s1", "target": "mc.S1_AXI", "block": "mc", "masters": ["tg_1"],
                     "offset": "0x0", "range": "0x80000000"}]}}
        plan = {"stages": [{"name": "address", "operations": [
            call("check_window_scope", "/mc/MC_S1_AXI/BLOCK", ["/tg_1/Data"], "0x0", "0x80000000", ident="w1"),
            call("check_window_scope", "/mc/MC_S0_AXI/BLOCK", ["/tg_0/Data"], "0x0", "0x80000000", ident="w0")]}]}
        self.assertEqual([w[0] for w in inputs.derive(plan, spec)["windows"]], ["s1", "s0"])
        for window in spec["functional_contracts"]["fixed_address_windows"]:
            window.pop("masters")
        self.assertEqual([w[0] for w in inputs.derive(plan, spec)["windows"]], ["s1", "s0"])

    def test_windows_alike_in_every_field_are_still_refused(self):
        spec = {"functional_contracts": {"fixed_address_windows": [
            {"name": "a", "offset": "0x0", "range": "0x1000"}, {"name": "b", "offset": "0x0", "range": "0x1000"}]}}
        plan = {"stages": [{"name": "address", "operations": [
            call("check_window_scope", "/m/S/Reg", ["/cpu/Data"], "0x0", "0x1000", ident="w")]}]}
        with self.assertRaisesRegex(ValueError, r"w \(/m/S/Reg 0x0/0x1000\) matches a, b"):
            inputs.derive(plan, spec)

    def test_tcl_quoting_keeps_each_record_one_word(self):
        self.assertEqual(inputs.tcl(["win a", "/s", ["/p/q"], "0x0", 4]), "{{win a} /s {/p/q} 0x0 4}")


if __name__ == "__main__":
    unittest.main()
