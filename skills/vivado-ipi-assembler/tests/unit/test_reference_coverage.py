# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""scripts/reference_coverage.py: a reference's functional IP is built or explained."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/reference_coverage.py"


def cell(vlnv):
    return json.dumps({"kind": "cell", "path": "/x", "vlnv": vlnv, "config": {}})


class ReferenceCoverageTests(unittest.TestCase):
    def run_check(self, reference, built, deviations=None, where="example_reference.json"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / where
            target.parent.mkdir(parents=True, exist_ok=True)
            if reference is not None:
                target.write_text("\n".join(cell(f"xilinx.com:ip:{ip}:1.0") for ip in reference) + "\n")
            (root / "cells.txt").write_text("\n".join(f"xilinx.com:ip:{ip}:1.0" for ip in built) + "\n")
            if deviations is not None:
                (root / "reference_deviations.json").write_text(json.dumps(deviations))
            result = subprocess.run([sys.executable, str(SCRIPT), "--built", str(root / "cells.txt"),
                                     "--spec-dir", str(root)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_no_reference_is_not_applicable(self):
        self.assertIn("REFERENCE_COVERAGE:NA", self.run_check(None, ["axi_gpio"]))

    def test_dropped_functional_ip_fails_until_explained(self):
        ref = ["mipi_csi2_rx_subsystem", "v_hdmi_txss1", "axi_gpio", "axi_gpio", "xlconcat", "smartconnect"]
        built = ["mipi_csi2_rx_subsystem", "axi_gpio"]
        out = self.run_check(ref, built)
        self.assertIn("REFERENCE_COVERAGE:FAIL 2", out)
        self.assertIn("UNEXPLAINED v_hdmi_txss1", out)
        self.assertNotIn("xlconcat", out)
        out = self.run_check(ref, built, [{"ip": "v_hdmi_txss1", "count": 1, "reason": "no HDMI on this board"},
                                          {"ip": "axi_gpio", "count": 1, "reason": "request asks for one GPIO"}])
        self.assertIn("REFERENCE_COVERAGE:OK", out)

    def test_unexplained_line_names_the_entry_to_add(self):
        out = self.run_check(["axi_uartlite"] * 3, ["axi_uartlite"])
        self.assertIn('{"ip": "axi_uartlite", "count": 2, "reason": ', out)

    def test_a_deviation_keyed_by_vlnv_counts_by_name(self):
        out = self.run_check(["axi_gpio", "axi_gpio"], ["axi_gpio"],
                             [{"ip": "xilinx.com:ip:axi_gpio:2.0", "count": 1, "reason": "one GPIO bank requested"}])
        self.assertIn("REFERENCE_COVERAGE:OK", out)

    def test_a_deviation_without_a_reason_does_not_count(self):
        out = self.run_check(["axi_timer"], [], [{"ip": "axi_timer", "count": 1, "reason": " "}])
        self.assertIn("REFERENCE_COVERAGE:FAIL", out)

    def test_ced_reference_one_level_below_is_found(self):
        out = self.run_check(["axi_timer", "microblaze_riscv"], ["microblaze_riscv"], where="ced/ced_reference.json")
        self.assertIn("UNEXPLAINED axi_timer", out)

    def test_simulation_only_models_are_exempt(self):
        self.assertIn("REFERENCE_COVERAGE:OK", self.run_check(["lpddrmc", "ddr_responder", "clk_gen_sim", "sim_trig"], ["lpddrmc"]))


if __name__ == "__main__":
    unittest.main()
