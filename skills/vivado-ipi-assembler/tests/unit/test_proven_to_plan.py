# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import compile_assembly_plan as compiler  # noqa: E402
import proven_to_plan as proven  # noqa: E402

GPIO = "xilinx.com:ip:axi_gpio:2.0"
CSI = "xilinx.com:ip:mipi_csi2_rx_subsystem:6.0"
PS = "xilinx.com:ip:ps_wizard:1.0"

SNAPSHOT = {
    "environment": {"part": "xcpart", "board_part": "amd.com:board:part0:1.3"},
    "catalog": {
        "gpio_leds": {"resolved_vlnv": GPIO, "requested": "xilinx.com:ip:axi_gpio",
                      "config_properties": ["CONFIG.C_GPIO_WIDTH", "CONFIG.C_ALL_OUTPUTS"]},
        "camera_receiver": {"resolved_vlnv": CSI, "requested": "xilinx.com:ip:mipi_csi2_rx_subsystem",
                            "config_properties": ["CONFIG.CMN_NUM_LANES", "CONFIG.CMN_PXL_FORMAT"]},
        "processing_system": {"resolved_vlnv": PS, "requested": "xilinx.com:ip:ps_wizard",
                              "config_properties": ["CONFIG.PS_UART0"]},
    },
}

SPEC = {"blocks": [
    {"name": "gpio_leds", "parameters": {"width": 8, "direction": "output"}},
    {"name": "camera_receiver", "parameters": {"lanes": 4, "format": "RAW12"}},
]}

TEMPLATE = {
    "schema_version": 1,
    "name": "t",
    "description": "d",
    "binding_context": {},
    "parameter_coverage": {
        "/blocks/1/parameters/lanes": {"value": 4, "reason": "lanes", "disposition": "hardware",
                                       "checks": ["camera_config_check"]},
        "/blocks/1/parameters/format": {"value": "RAW12", "reason": "fmt", "disposition": "external",
                                        "owner": "sensor"},
    },
    "stages": [
        {"name": "cells", "operations": [
            {"id": "cells_stage", "op": "call", "proc": "ipiasm::stage", "args": ["cells"]},
            {"id": "create_camera", "op": "call", "proc": "ipiasm::create_cell",
             "args": ["${bind.blocks.camera_receiver.vlnv_unversioned}",
                      "${bind.blocks.camera_receiver.instance}", {}]},
            {"id": "configure_camera", "op": "call", "proc": "ipcfg::require",
             "args": ["camera_receiver", {"CONFIG.CMN_NUM_LANES": 2}]},
            {"id": "camera_config_check", "op": "call", "proc": "ipiasm::check_cell_config",
             "args": ["camera_receiver", {"CONFIG.CMN_NUM_LANES": 2}]},
            {"id": "create_uart", "op": "call", "proc": "ipiasm::create_cell",
             "args": ["${bind.blocks.uart_debug.vlnv_unversioned}", "uart_debug", {}]},
            {"id": "ps_check", "op": "call", "proc": "ipiasm::check_cell_config",
             "args": ["processing_system", {"CONFIG.PS_UART0": 0}]},
        ], "checkpoint": True},
    ],
}


class ProvenToPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.evidence = self.dir / "cell.capture"
        self.evidence.write_text("{schema_version 1}\n")
        self.snapshot = self.write("binding_snapshot.json", SNAPSHOT)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, value):
        path = self.dir / name
        path.write_text(json.dumps(value))
        return path

    def cell(self, role, vlnv, requested, apply_via="create_cell", **extra):
        cell = {"role": role, "instance": role, "vlnv": vlnv, "apply_via": apply_via,
                "requested": requested, "derived": {}, "board_owned": [], "board_override": {},
                "readback": dict(requested), "spec_pointers": [], "evidence": [str(self.evidence)]}
        cell.update(extra)
        return cell

    def fragment(self, name, *cells, **env):
        doc = {"schema": "ipcfg.proven/1", "part": "xcpart", "board_part": "amd.com:board:part0:1.3",
               "cells": list(cells)}
        doc.update(env)
        return self.write(name, doc)

    def default_fragments(self):
        return [
            self.fragment("g1.json", self.cell("processing_system", PS, {"CONFIG.PS_UART0": "1"},
                                               apply_via="ps_wizard")),
            self.fragment("g2.json",
                          self.cell("camera_receiver", CSI,
                                    {"CONFIG.CMN_NUM_LANES": "4", "CONFIG.CMN_PXL_FORMAT": "RAW12"},
                                    spec_pointers=["/blocks/1"]),
                          self.cell("gpio_leds", GPIO, {"CONFIG.C_GPIO_WIDTH": "8"},
                                    spec_pointers=["/blocks/0"])),
        ]

    def assert_refused(self, fragments, text):
        with self.assertRaises(proven.ProvenError) as caught:
            proven.merge(fragments, self.snapshot)
        self.assertIn(text, str(caught.exception))

    def test_merge_pins_hashes(self):
        merged = proven.merge(self.default_fragments(), self.snapshot)
        self.assertEqual(merged["schema"], proven.MERGED_SCHEMA)
        self.assertEqual(merged["binding_snapshot_sha256"], proven.file_sha256(self.snapshot))
        self.assertEqual([c["role"] for c in merged["cells"]],
                         ["camera_receiver", "gpio_leds", "processing_system"])
        for cell in merged["cells"]:
            body = {k: v for k, v in cell.items() if k != "sha256"}
            self.assertEqual(cell["sha256"], proven.canonical_sha256(body))
            self.assertEqual(cell["evidence_sha256"], {str(self.evidence): proven.file_sha256(self.evidence)})

    def test_refusals(self):
        board = self.cell("gpio_leds", GPIO, {"CONFIG.C_GPIO_WIDTH": "8"}, board_owned=["CONFIG.C_GPIO_WIDTH"])
        cases = {
            "different target": [self.fragment("a.json", self.cell("gpio_leds", GPIO, {}), part="other")],
            "not in the binding snapshot": [self.fragment("b.json", self.cell("ghost", GPIO, {}))],
            "is not the bound": [self.fragment("c.json", self.cell("gpio_leds", CSI, {}))],
            "not CONFIG properties": [self.fragment("d.json", self.cell("gpio_leds", GPIO, {"CONFIG.X": "1"}))],
            "board-owned": [self.fragment("e.json", board)],
            "proven twice": [self.fragment("f.json", self.cell("gpio_leds", GPIO, {})),
                             self.fragment("g.json", self.cell("gpio_leds", GPIO, {}))],
            "evidence not found": [self.fragment("h.json", self.cell("gpio_leds", GPIO, {},
                                                                     evidence=[str(self.dir / "nope")]))],
            "apply_via": [self.fragment("i.json", self.cell("gpio_leds", GPIO, {}, apply_via="magic"))],
        }
        for why, fragments in cases.items():
            with self.subTest(why=why):
                self.assert_refused(fragments, why)

    def test_a_cell_proven_with_nothing_to_read_back_keeps_the_authors_readback(self):
        merged = proven.merge([self.fragment("empty.json", self.cell("camera_receiver", CSI, {}))], self.snapshot)
        plan = proven.apply_to_template(TEMPLATE, merged, "proven_ip_config.json")
        ids = [op["id"] for op in plan["stages"][0]["operations"]]
        self.assertIn("proven_create_camera_receiver", ids)
        self.assertIn("camera_config_check", ids)
        self.assertEqual(plan["parameter_coverage"]["/blocks/1/parameters/lanes"]["checks"], ["camera_config_check"])

    def test_board_owned_key_in_a_preset_cells_readback_needs_no_override(self):
        # A board-preset cell's dict is only read back, so a board-owned key in it overrides nothing.
        # The same key in a create_cell dict is refused (test_refusals, "board-owned").
        cell = self.cell("gpio_leds", GPIO, {"CONFIG.C_GPIO_WIDTH": "8"}, apply_via="board_preset",
                         board_owned=["CONFIG.C_GPIO_WIDTH"])
        merged = proven.merge([self.fragment("preset.json", cell)], self.snapshot)
        plan = proven.apply_to_template(TEMPLATE, merged, "proven_ip_config.json")
        ids = [op["id"] for op in plan["stages"][0]["operations"] if op.get("provenance", {}).get("role") == "gpio_leds"]
        self.assertEqual(ids, ["proven_check_gpio_leds"])

    def test_board_owned_key_with_override_is_accepted(self):
        cell = self.cell("gpio_leds", GPIO, {"CONFIG.C_GPIO_WIDTH": "8"}, board_owned=["CONFIG.C_GPIO_WIDTH"],
                         board_override={"CONFIG.C_GPIO_WIDTH": "spec /blocks/0 width"})
        merged = proven.merge([self.fragment("ok.json", cell)], self.snapshot)
        plan = proven.apply_to_template(TEMPLATE, merged, "proven_ip_config.json")
        configure = next(op for op in plan["stages"][0]["operations"] if op["id"] == "proven_configure_gpio_leds")
        self.assertEqual(configure["args"][3:], [{}, "CONFIG.C_GPIO_WIDTH: spec /blocks/0 width"])

    def test_template_rewrite(self):
        merged = proven.merge(self.default_fragments(), self.snapshot)
        plan = proven.apply_to_template(TEMPLATE, merged, "proven_ip_config.json", copy.deepcopy(SPEC))
        ops = plan["stages"][0]["operations"]
        ids = [op["id"] for op in ops]
        self.assertEqual(ids, [
            "cells_stage",
            "proven_create_gpio_leds", "proven_configure_gpio_leds", "proven_check_gpio_leds",
            "proven_create_camera_receiver", "proven_configure_camera_receiver", "proven_check_camera_receiver",
            "create_uart",
            "proven_check_processing_system",
        ])
        by_id = {op["id"]: op for op in ops}
        self.assertEqual(by_id["proven_configure_camera_receiver"]["args"][1],
                         {"CONFIG.CMN_NUM_LANES": "4", "CONFIG.CMN_PXL_FORMAT": "RAW12"})
        self.assertEqual(by_id["proven_create_camera_receiver"]["args"],
                         ["${bind.blocks.camera_receiver.vlnv_unversioned}",
                          "${bind.blocks.camera_receiver.instance}", {}])
        sha = {c["role"]: c["sha256"] for c in merged["cells"]}
        for op in ops:
            if op["id"].startswith("proven_"):
                role = op["provenance"]["role"]
                self.assertEqual(op["provenance"],
                                 {"source": "proven_ip_config.json", "role": role, "sha256": sha[role]})
        coverage = plan["parameter_coverage"]
        self.assertEqual(coverage["/blocks/1/parameters/lanes"]["checks"], ["proven_check_camera_receiver"])
        self.assertEqual(coverage["/blocks/1/parameters/format"]["disposition"], "external")
        self.assertEqual(coverage["/blocks/0/parameters/width"],
                         {"value": 8, "reason": "proven in Stage 2 by role gpio_leds",
                          "disposition": "hardware", "checks": ["proven_check_gpio_leds"]})
        self.assertEqual(plan["proven_ip_config"]["sha256"], proven.canonical_sha256(merged))
        self.assertEqual(TEMPLATE["stages"][0]["operations"][2]["args"][1], {"CONFIG.CMN_NUM_LANES": 2})

    def proven_plan(self):
        merged = proven.merge(self.default_fragments(), self.snapshot)
        (self.dir / "proven_ip_config.json").write_text(json.dumps(merged))
        return proven.apply_to_template(TEMPLATE, merged, "proven_ip_config.json")

    def test_compiler_accepts_rewritten_plan(self):
        plan = self.proven_plan()
        self.assertIn("ipiasm::_execute_plan", compiler.compile_plan(plan))
        compiler.check_proven_provenance(plan, self.dir)

    def test_compiler_provenance_refusals(self):
        def edit_value(plan):
            op = next(op for op in plan["stages"][0]["operations"] if op["id"] == "proven_check_gpio_leds")
            op["args"][1] = {"CONFIG.C_GPIO_WIDTH": "16"}

        def edit_sha(plan):
            plan["stages"][0]["operations"][1]["provenance"]["sha256"] = "0" * 64

        def unprovenanced_create(plan):
            plan["stages"][0]["operations"].append(
                {"id": "extra_gpio", "op": "call", "proc": "ipiasm::create_cell",
                 "args": ["${bind.blocks.gpio_leds.vlnv_unversioned}", "gpio_extra", {}]})

        def drop_check(plan):
            plan["stages"][0]["operations"] = [op for op in plan["stages"][0]["operations"]
                                               if op["id"] != "proven_check_camera_receiver"]

        def drop_reference(plan):
            del plan["proven_ip_config"]

        def edit_file(plan):
            path = self.dir / "proven_ip_config.json"
            merged = json.loads(path.read_text())
            merged["cells"][0]["requested"]["CONFIG.CMN_NUM_LANES"] = "2"
            path.write_text(json.dumps(merged))

        def edit_embedded(plan):
            plan["proven_ip_config"]["cells"][0]["requested"] = {"CONFIG.CMN_NUM_LANES": "2"}

        def drop_embedded(plan):
            del plan["proven_ip_config"]["cells"]

        cases = {"verbatim": edit_value, "different proof": edit_sha, "without provenance": unprovenanced_create,
                 "no provenanced ipiasm::check_cell_config": drop_check,
                 "does not reference it": drop_reference, "changed since": edit_file,
                 "disagree with": edit_embedded, "{path, sha256, cells}": drop_embedded}
        for why, mutate in cases.items():
            with self.subTest(why=why):
                plan = self.proven_plan()
                mutate(plan)
                with self.assertRaises(compiler.PlanError) as caught:
                    compiler.check_proven_provenance(plan, self.dir)
                self.assertIn("PROVEN_CONFIG_FAIL:", str(caught.exception))
                self.assertIn(why, str(caught.exception))

    def test_plan_is_self_contained_without_the_merged_file(self):
        # A grader or reviewer may copy the plan alone; the embedded cells still hold it.
        plan = self.proven_plan()
        (self.dir / "proven_ip_config.json").unlink()
        compiler.check_proven_provenance(plan, self.dir)
        op = next(op for op in plan["stages"][0]["operations"] if op["id"] == "proven_check_gpio_leds")
        op["args"][1] = {"CONFIG.C_GPIO_WIDTH": "16"}
        with self.assertRaises(compiler.PlanError) as caught:
            compiler.check_proven_provenance(plan, self.dir)
        self.assertIn("verbatim", str(caught.exception))

    def test_compiler_ignores_plans_without_proven_config(self):
        compiler.check_proven_provenance(copy.deepcopy(TEMPLATE), self.dir)

    def test_cli_writes_outputs(self):
        template = self.write("assembly_plan.template.json", TEMPLATE)
        spec = self.write("design_spec.json", SPEC)
        merged = self.dir / "proven_ip_config.json"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = proven.main(["--fragments", *map(str, self.default_fragments()), "--snapshot",
                                str(self.snapshot), "--template", str(template), "--merged", str(merged),
                                "--spec", str(spec)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("PROVEN_PLAN:OK roles=3", out.getvalue())
        plan = json.loads(template.read_text())
        self.assertEqual(plan["proven_ip_config"]["path"], "proven_ip_config.json")
        self.assertEqual(json.loads(merged.read_text())["schema"], proven.MERGED_SCHEMA)

    def test_cli_failure_writes_nothing(self):
        template = self.write("assembly_plan.template.json", TEMPLATE)
        before = template.read_text()
        merged = self.dir / "proven_ip_config.json"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = proven.main(["--fragments", str(self.fragment("x.json", self.cell("ghost", GPIO, {}))),
                                "--snapshot", str(self.snapshot), "--template", str(template),
                                "--merged", str(merged)])
        self.assertEqual(code, 1)
        self.assertIn("PROVEN_PLAN_FAIL:", out.getvalue())
        self.assertFalse(merged.exists())
        self.assertEqual(template.read_text(), before)


if __name__ == "__main__":
    unittest.main()
