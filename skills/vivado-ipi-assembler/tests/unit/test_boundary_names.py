# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A top_level interface the RTL names differently gets that name into the DesignSpec."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import boundary_names
import compile_assembly_plan as compiler


RTL = """
module top;
  // wrap_x u_old (.ignored(a));
  wrap_x u (
     .alpha_irq_out(irq_o),   // renamed scalar
     .beta_in1(1'b0), .beta_in2(1'b0),
     /* .commented(x), */
     .SER_A_rxd(rx), .SER_A_txd(tx)
  );
endmodule
"""

SPEC = {
    "blocks": [
        {"name": "alpha", "interfaces": [{"name": "irq", "direction": "out", "scope": "top_level"}]},
        {"name": "beta", "interfaces": [{"name": "in1_2", "direction": "in", "protocol": "Interrupt",
                                         "scope": "top_level"}]},
        {"name": "ser", "interfaces": [{"name": "SER_A", "scope": "top_level"},
                                       {"name": "inner", "scope": "internal"}]},
    ],
    "functional_contracts": {"connections": []},
}


def plan(*names):
    ports = [{"name": name} for name in names]
    return {"stages": [{"name": "connect", "operations": [
        {"op": "call", "proc": "ipiasm::check_required_ports", "args": [ports]}]}]}


class BoundaryNames(unittest.TestCase):
    def test_instance_ports_skip_comments(self):
        self.assertEqual(boundary_names.rtl_instance_ports(RTL, "wrap_x"),
                         ["SER_A_rxd", "SER_A_txd", "alpha_irq_out", "beta_in1", "beta_in2"])
        self.assertIsNone(boundary_names.rtl_instance_ports(RTL, "other_wrapper"))

    def test_check_names_each_unmatched_interface(self):
        out = boundary_names.check(SPEC, RTL, "wrap_x")
        self.assertIn("BOUNDARY_NAMES_FAIL:2 of 3", out)
        self.assertIn("alpha.irq accepts {irq}; RTL ports that look related: {alpha_irq_out}", out)
        self.assertIn("beta.in1_2 accepts {in1_2}", out)
        self.assertNotIn("ser.SER_A", out)

    def test_add_makes_check_and_compiler_accept_rtl_names(self):
        spec = copy.deepcopy(SPEC)
        mappings = boundary_names.parse_mappings(["alpha.irq=alpha_irq_out", "beta.in1_2=beta_in1,beta_in2"])
        self.assertEqual(boundary_names.add(spec, mappings), 3)
        self.assertEqual(boundary_names.add(spec, mappings), 0)
        incoming = [c for c in spec["functional_contracts"]["connections"] if c["destination"] == "beta.in1_2"]
        self.assertEqual([c["source"] for c in incoming], ["beta_in1", "beta_in2"])
        self.assertEqual(incoming[0]["protocol"], "Interrupt")
        self.assertTrue(boundary_names.check(spec, RTL, "wrap_x").startswith("BOUNDARY_NAMES:OK 3"))
        compiler.check_boundary_coverage(spec, plan("alpha_irq_out", "beta_in1", "beta_in2", "SER_A"))

    def test_add_refuses_unknown_interface_and_non_bare_port(self):
        with self.assertRaisesRegex(ValueError, "not a top_level interface"):
            boundary_names.add(copy.deepcopy(SPEC), {"ser.inner": ["p"]})
        with self.assertRaisesRegex(ValueError, "not a bare port name"):
            boundary_names.add(copy.deepcopy(SPEC), {"alpha.irq": ["blk.pin"]})
        with self.assertRaises(ValueError):
            boundary_names.parse_mappings(["alpha.irq"])

    def test_cli_writes_spec_in_place(self):
        with tempfile.TemporaryDirectory() as root:
            spec_path, rtl_path = Path(root) / "spec.json", Path(root) / "top.sv"
            spec_path.write_text(json.dumps(SPEC))
            rtl_path.write_text(RTL)
            check = ["check", "--spec", str(spec_path), "--rtl", str(rtl_path), "--wrapper", "wrap_x"]
            self.assertEqual(boundary_names.main(check), 1)
            self.assertEqual(boundary_names.main(["add", "--spec", str(spec_path), "alpha.irq=alpha_irq_out",
                                                  "beta.in1_2=beta_in1,beta_in2"]), 0)
            self.assertEqual(len(json.loads(spec_path.read_text())["functional_contracts"]["connections"]), 3)
            self.assertEqual(boundary_names.main(check), 0)


if __name__ == "__main__":
    unittest.main()
