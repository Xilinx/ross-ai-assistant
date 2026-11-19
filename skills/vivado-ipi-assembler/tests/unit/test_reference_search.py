# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""scripts/reference_search.py ranks the release's CEDs against a request."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/reference_search.py"


def ced(root, name, ips, boards="", parts="", options=None, description=""):
    repo = root / name
    repo.mkdir()
    (repo / "run.tcl").write_text("\n".join(f"create_bd_cell -type ip -vlnv xilinx.com:ip:{ip}:1.0 {ip}_0" for ip in ips))
    init = ""
    for opt, values in (options or {}).items():
        quoted = " ".join(f'"{v} {v}___label"' for v in values)
        init += f'lappend x [dict create name "{opt}" type "string" value "{values[0]}" value_list {{{quoted}}} enabled true]\n'
    (repo / "init.tcl").write_text(init)
    return {"name": f"xilinx.com:design:{name}:1.0", "display": name, "description": description,
            "boards": boards, "parts": parts, "repo": str(repo), "config": {}}


class ReferenceSearchTests(unittest.TestCase):
    def run_search(self, ceds, *args):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [ced(root, **c) for c in ceds]
            index = root / "index.json"
            index.write_text(json.dumps({"build": "test", "ceds": entries}))
            out = root / "out.json"
            result = subprocess.run([sys.executable, str(SCRIPT), "--index", str(index), "--out", str(out), *args],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout, json.loads(out.read_text())

    MBV = dict(name="microblaze_v_preset", ips=["microblaze_riscv", "axi_iic", "axi_gpio", "axi_uartlite", "axi_timer"],
               boards="xilinx.com:scu35:2.1 xilinx.com:kc705:1.7", options={"Preset": ["Microcontroller", "Real-time_Processor"]})
    KRIA = dict(name="kria_starter_kit", ips=["zynq_ultra_ps_e", "axi_gpio"], boards="xilinx.com:kv260_som:1.4")

    def test_supported_ced_covering_every_function_is_strong(self):
        text, record = self.run_search([self.MBV, self.KRIA], "--board", "xilinx.com:scu35:part0:2.1",
                                       "--part", "xcsu35p", "--text", "SCU35 I/O expander with 3 I2C, 3 GPIO, 1 UART")
        best = record["ranking"][0]
        self.assertEqual(best["ced"], "xilinx.com:design:microblaze_v_preset:1.0")
        self.assertEqual(best["verdict"], "STRONG")
        self.assertEqual(best["options"], {"Preset": ["Microcontroller", "Real-time_Processor"]})
        self.assertIn("REFERENCE_SEARCH:STRONG", text)

    def test_unsupported_board_is_never_strong(self):
        _, record = self.run_search([self.MBV], "--board", "xilinx.com:vck190:part0:3.4", "--text", "I2C GPIO UART")
        self.assertEqual(record["ranking"][0]["verdict"], "WEAK")
        self.assertFalse(record["ranking"][0]["supported"])

    def test_versal_ddr_counts_the_noc_controller(self):
        dma = dict(name="axi_dma", ips=["versal_cips", "axi_noc", "axi_dma"], boards="xilinx.com:vck190:3.4")
        _, record = self.run_search([dma], "--board", "xilinx.com:vck190:part0:3.4",
                                    "--text", "Versal CIPS with DDR4 through the NoC and an AXI DMA")
        self.assertEqual(record["ranking"][0]["missing"], [])

    def test_ip_example_request_is_reported(self):
        text, record = self.run_search([self.MBV], "--board", "xilinx.com:scu200:part0:1.2",
                                       "--text", "Recreate the IP example design for the MIPI CSI-2 RX subsystem")
        self.assertEqual(record["ip_example"]["ips"], ["mipi_csi2_rx_subsystem"])
        self.assertIn("IP_EXAMPLE ips=mipi_csi2_rx_subsystem", text)

    def test_no_board_chosen_leaves_board_based_support_open(self):
        _, record = self.run_search([self.KRIA], "--part", "xck26", "--text", "Zynq UltraScale+ PS with AXI GPIO")
        self.assertIsNone(record["ranking"][0]["supported"])
        self.assertEqual(record["ranking"][0]["verdict"], "PARTIAL")

    def test_bound_roles_replace_inferred_functions(self):
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "binding_snapshot.json"
            roles.write_text(json.dumps({"roles": {"io": "xilinx.com:ip:axi_quad_spi:3.2"}}))
            _, record = self.run_search([self.MBV], "--board", "xilinx.com:scu35:part0:2.1",
                                        "--text", "I2C GPIO UART", "--roles", str(roles))
        self.assertEqual(record["functions"], ["axi_quad_spi"])
        self.assertEqual(record["ranking"][0]["missing"], ["axi_quad_spi"])


if __name__ == "__main__":
    unittest.main()
