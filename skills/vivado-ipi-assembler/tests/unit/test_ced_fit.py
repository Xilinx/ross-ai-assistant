# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""scripts/ced_fit.py sorts a CED into EXACT, CONFIG_ONLY or STRUCTURAL for a request."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ced_fit.py"
CED = [
    {"kind": "ced", "name": "xilinx.com:design:microblaze_v_preset:2.0"},
    {"kind": "cell", "path": "/cpu", "vlnv": "xilinx.com:ip:microblaze_riscv:1.0", "config": {"CONFIG.G_TEMPLATE_LIST": "1"}},
    {"kind": "cell", "path": "/clk_wiz_1", "vlnv": "xilinx.com:ip:clk_wiz:6.0", "config": {}},
    {"kind": "cell", "path": "/gpio_0", "vlnv": "xilinx.com:ip:axi_gpio:2.0", "config": {}},
    {"kind": "cell", "path": "/gpio_1", "vlnv": "xilinx.com:ip:axi_gpio:2.0", "config": {}},
    {"kind": "cell", "path": "/rst", "vlnv": "xilinx.com:ip:proc_sys_reset:5.0", "config": {}},
    {"kind": "clk", "pin": "/cpu/Clk", "freq": "100000000", "src": "/clk_wiz_1/clk_out1"},
]


class CedFitTests(unittest.TestCase):
    def fit(self, checklist):
        with tempfile.TemporaryDirectory() as directory:
            ref, check = Path(directory, "ced_reference.json"), Path(directory, "ced_checklist.json")
            ref.write_text("\n".join(json.dumps(r) for r in CED) + "\n")
            check.write_text(json.dumps(checklist))
            out = Path(directory, "fit.json")
            result = subprocess.run([sys.executable, str(SCRIPT), "--reference", str(ref), "--checklist", str(check),
                                     "--out", str(out)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout, json.loads(out.read_text())

    def test_exact_when_every_item_holds(self):
        text, record = self.fit({"ip_counts": {"microblaze_riscv": 1}, "clocks_mhz": {"microblaze_riscv": 100},
                                 "config": {"microblaze_riscv": {"CONFIG.G_TEMPLATE_LIST": "1"}}})
        self.assertEqual(record["verdict"], "EXACT")
        self.assertIn("CED_FIT:EXACT", text)

    def test_clock_difference_is_a_clock_wizard_change(self):
        text, record = self.fit({"ip_counts": {"microblaze_riscv": 1}, "clocks_mhz": {"microblaze_riscv": 200}})
        self.assertEqual(record["verdict"], "CONFIG_ONLY")
        self.assertEqual(record["changes"], {"/clk_wiz_1": {"CONFIG.CLKOUT1_REQUESTED_OUT_FREQ": "200"}})
        self.assertIn("CHANGES_TCL {/clk_wiz_1} {{CONFIG.CLKOUT1_REQUESTED_OUT_FREQ} {200}}", text)

    def test_config_difference_is_config_only(self):
        _, record = self.fit({"config": {"microblaze_riscv": {"CONFIG.G_TEMPLATE_LIST": "2"}}})
        self.assertEqual(record["verdict"], "CONFIG_ONLY")
        self.assertEqual(record["changes"], {"/cpu": {"CONFIG.G_TEMPLATE_LIST": "2"}})

    def test_count_difference_or_missing_ip_is_structural(self):
        self.assertEqual(self.fit({"ip_counts": {"axi_gpio": 3}})[1]["verdict"], "STRUCTURAL")
        self.assertEqual(self.fit({"ip_counts": {"axi_iic": 1}})[1]["verdict"], "STRUCTURAL")

    def test_exclusive_request_refuses_extra_functional_ip(self):
        _, record = self.fit({"ip_counts": {"microblaze_riscv": 1}, "exclusive": True})
        self.assertEqual(record["verdict"], "STRUCTURAL")
        self.assertTrue(any("axi_gpio" in line for line in record["structural"]))
        # Infrastructure (clocking, resets) never counts as extra.
        self.assertFalse(any("proc_sys_reset" in line or "clk_wiz" in line for line in record["structural"]))


if __name__ == "__main__":
    unittest.main()
