# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""scripts/draft_spec.py turns a recorded reference into a schema-valid DesignSpec draft."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SKILLS = Path(__file__).resolve().parents[3]
SCRIPT = SKILLS / "vivado-ipi-assembler/scripts/draft_spec.py"
spec = importlib.util.spec_from_file_location("validate_spec", SKILLS / "vivado-spec-generator/scripts/validate_spec.py")
validate_spec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate_spec)

RECORDS = [
    {"kind": "ced", "name": "xilinx.com:design:mb_v:2.0", "board": "xilinx.com:b:part0:1.0"},
    {"kind": "cell", "path": "/cpu", "vlnv": "xilinx.com:ip:microblaze_riscv:1.0",
     "config": {"CONFIG.G_TEMPLATE_LIST": "1"}, "user": {"CONFIG.G_TEMPLATE_LIST": "1"}},
    {"kind": "cell", "path": "/gpio_0", "vlnv": "xilinx.com:ip:axi_gpio:2.0", "config": {}, "user": {"CONFIG.C_GPIO_WIDTH": "8"}},
    {"kind": "cell", "path": "/hier/gpio_1", "vlnv": "xilinx.com:ip:axi_gpio:2.0", "config": {}, "user": {}},
    {"kind": "cell", "path": "/intc", "vlnv": "xilinx.com:ip:axi_intc:4.1", "config": {}, "user": {}},
    {"kind": "cell", "path": "/concat", "vlnv": "xilinx.com:inline_hdl:ilconcat:1.0", "config": {}, "user": {}},
    {"kind": "cell", "path": "/sc", "vlnv": "xilinx.com:ip:smartconnect:1.0", "config": {}, "user": {}},
    {"kind": "cell", "path": "/rst", "vlnv": "xilinx.com:ip:proc_sys_reset:5.0", "config": {}, "user": {}},
    {"kind": "map", "master": "/cpu", "space": "Data", "slave": "/gpio_0", "slave_intf": "S_AXI", "offset": "0x40000000", "range": "0x00010000"},
    {"kind": "map", "master": "/cpu", "space": "Data", "slave": "/hier/gpio_1", "slave_intf": "S_AXI", "offset": "0x40010000", "range": "0x00010000"},
    {"kind": "map", "master": "/cpu", "space": "Data", "slave": "/intc", "slave_intf": "s_axi", "offset": "0x41200000", "range": "0x00010000"},
    {"kind": "link", "a": "/gpio_0/GPIO", "b": "port:leds", "vlnv": "xilinx.com:interface:gpio_rtl:1.0", "mode": "Master"},
    {"kind": "irq", "src": "/gpio_0/ip2intc_irpt", "dst": "/concat/In0"},
    {"kind": "irq", "src": "/hier/gpio_1/ip2intc_irpt", "dst": "/concat/In1"},
    {"kind": "irq", "src": "/concat/dout", "dst": "/intc/intr"},
    {"kind": "clk", "pin": "/cpu/Clk", "freq": "200000000", "src": "/clk/clk_out1"},
    {"kind": "clk", "pin": "/gpio_0/s_axi_aclk", "freq": "200000000", "src": "/clk/clk_out1"},
]


class DraftSpecTests(unittest.TestCase):
    def draft(self, records):
        with tempfile.TemporaryDirectory() as directory:
            ref = Path(directory) / "ced_reference.json"
            ref.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            result = subprocess.run([sys.executable, str(SCRIPT), "--reference", str(ref), "--out", directory,
                                     "--part", "xcsu35p", "--request", "IO expander"],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                return result.stdout, None, None
            return (result.stdout, json.loads((Path(directory) / "design_spec.draft.json").read_text()),
                    (Path(directory) / "reference_digest.txt").read_text())

    def test_draft_is_schema_valid_and_carries_the_reference(self):
        out, draft, digest = self.draft(RECORDS)
        self.assertIn("DRAFT_SPEC:OK", out)
        self.assertEqual(validate_spec.validate(draft), [])
        names = {b["name"] for b in draft["blocks"]}
        # Functional cells only: no concat, interconnect or reset.
        self.assertEqual(names, {"cpu", "gpio_0", "gpio_1", "intc"})
        cpu = next(b for b in draft["blocks"] if b["name"] == "cpu")
        self.assertEqual(cpu["parameters"]["reference_config"], {"CONFIG.G_TEMPLATE_LIST": "1"})
        windows = {w["target"]: (w["offset"], w["range"]) for w in draft["functional_contracts"]["fixed_address_windows"]}
        self.assertEqual(windows["gpio_1"], ("0x40010000", "0x00010000"))
        # Interrupts followed through the concat to the controller.
        irqs = {(i["source"], i["destination"]) for i in draft["functional_contracts"]["interrupts"]}
        self.assertEqual(irqs, {("gpio_0", "intc"), ("gpio_1", "intc")})
        self.assertEqual(draft["board_part"], "xilinx.com:b:part0:1.0")
        self.assertIn({"target": "cpu", "clock_mhz": 200.0, "reset_polarity": "interface_defined",
                       "synchronized": True, "required": True}, draft["functional_contracts"]["clock_reset"])
        gpio = next(b for b in draft["blocks"] if b["name"] == "gpio_0")
        self.assertTrue(any(i["scope"] == "external" and i["protocol"] == "GPIO" for i in gpio["interfaces"]))
        self.assertIn("Processor cpu (microblaze_riscv): G_TEMPLATE_LIST=1", digest)
        self.assertIn("200.0 MHz", digest)

    def test_rtl_example_drafts_its_ip_instances(self):
        out, draft, digest = self.draft([
            {"kind": "example", "ip": "xilinx.com:ip:lpddrmc:1.0", "top": "example_top"},
            {"kind": "ip", "name": "exref_lpddrmc", "vlnv": "xilinx.com:ip:lpddrmc:1.0",
             "config": {"CONFIG.A": "1", "CONFIG.B": "2"}, "user": {"CONFIG.A": "1"}},
            {"kind": "ip", "name": "perf_axi_tg_0", "vlnv": "xilinx.com:ip:perf_axi_tg:1.0", "config": {}, "user": {}},
            {"kind": "ip", "name": "sim_trig_0", "vlnv": "xilinx.com:ip:sim_trig:1.0", "config": {}, "user": {}},
        ])
        self.assertIn("DRAFT_SPEC:OK", out)
        self.assertEqual(validate_spec.validate(draft), [])
        blocks = {b["name"]: b for b in draft["blocks"]}
        self.assertEqual(set(blocks), {"exref_lpddrmc", "perf_axi_tg_0", "sim_trig_0"})
        self.assertEqual(blocks["exref_lpddrmc"]["parameters"]["reference_config"], {"CONFIG.A": "1"})
        self.assertIn("example_top", draft["description"])
        self.assertIn("RTL example: example_top", digest)

    def test_reference_without_cells_or_ips_is_refused(self):
        out, draft, _ = self.draft([{"kind": "example", "ip": "x"}])
        self.assertIn("DRAFT_SPEC_FAIL", out)
        self.assertIsNone(draft)


if __name__ == "__main__":
    unittest.main()
