# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import spec_to_svg  # noqa: E402

NS = "{http://www.w3.org/2000/svg}"


def spec():
    return {
        "name": "demo",
        "target_device": "part",
        "blocks": [
            {"name": "sensor", "workload_type": "sensor_interface",
             "description": "Off-chip sensor module.",
             "interfaces": [{"name": "out", "direction": "out", "protocol": "MIPI CSI-2",
                             "scope": "external"}]},
            {"name": "receiver", "workload_type": "sensor_interface",
             "interfaces": [{"name": "in", "direction": "in", "protocol": "MIPI CSI-2"},
                            {"name": "ctl", "direction": "in", "protocol": "AXI4-Lite"}]},
            {"name": "processing_system", "workload_type": "custom_ps", "interfaces": []},
            {"name": "ddr", "workload_type": "memory_controller", "interfaces": []},
            {"name": "gpio", "workload_type": "control_logic",
             "interfaces": [{"name": "pins", "direction": "bidirectional",
                             "protocol": "GPIO", "scope": "external"}]},
        ],
        "functional_contracts": {
            "connections": [
                {"source": "sensor.out", "destination": "receiver.in", "protocol": "MIPI CSI-2"},
                {"source": "processing_system.m", "destination": "receiver.ctl",
                 "protocol": "AXI4-Lite"},
            ],
            "control_reachability": [{"master": "application", "target": "gpio"}],
            "memory_reachability": [{"master": "receiver", "memory": "ddr.region"}],
        },
        "clock_domains": [{"name": "core", "frequency_hz": 719980000}],
        "global_constraints": {"unresolved_requirements": [],
                               "implementation_deferred": ["topology"]},
    }


def rects(root):
    boxes = []
    for zone in root.iter(NS + "g"):
        if zone.get("data-zone"):
            r = zone.find(NS + "rect")
            boxes.append(tuple(float(r.get(k)) for k in ("x", "y", "width", "height")))
    return boxes


def on_edge(point, boxes):
    x, y = point
    for bx, by, bw, bh in boxes:
        horizontal = bx - 1 <= x <= bx + bw + 1 and min(abs(y - by), abs(y - by - bh)) <= 1
        vertical = by - 1 <= y <= by + bh + 1 and min(abs(x - bx), abs(x - bx - bw)) <= 1
        if horizontal or vertical:
            return True
    return False


class SpecToSvgTests(unittest.TestCase):
    def render(self, value, manifest=None):
        return ET.fromstring(spec_to_svg.generate_svg(value, sources_manifest=manifest)
                             .split("\n", 1)[1])

    def test_groups_blocks_into_reference_zones(self):
        root = self.render(spec())
        zones = {g.get("data-block"): None for g in root.iter(NS + "g") if g.get("data-block")}
        self.assertEqual(set(zones), {"sensor", "receiver", "processing_system", "ddr", "gpio"})
        found = {g.get("data-zone") for g in root.iter(NS + "g") if g.get("data-zone")}
        self.assertTrue({"off_chip", "ingest", "ps", "memory", "board_io",
                         "control_bar", "noc_bar"} <= found)

    def test_every_wire_starts_and_ends_on_a_group_edge(self):
        root = self.render(spec())
        boxes = rects(root)
        paths = [g.find(NS + "path") for g in root.iter(NS + "g") if g.get("data-edge")]
        self.assertTrue(paths)
        for path in paths:
            points = [tuple(map(float, p.split(",")))
                      for p in re.findall(r"[-\d.]+,[-\d.]+", path.get("d"))]
            self.assertTrue(on_edge(points[0], boxes), path.get("d"))
            self.assertTrue(on_edge(points[-1], boxes), path.get("d"))

    def test_header_reports_consumed_sources_and_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "inputs" / "requirements.md"
            source.parent.mkdir()
            source.write_text("demo\n")
            manifest = root / "outputs" / "spec_sources.json"
            manifest.parent.mkdir()
            manifest.write_text(json.dumps([{
                "path": "inputs/requirements.md",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }]))
            text = " ".join(t.text or "" for t in self.render(spec(), manifest).iter(NS + "text"))
        self.assertIn("inputs/requirements.md", text)
        self.assertIn("verified", text)
        self.assertIn("1/1", text)
        self.assertIn("Unresolved requirements: none", text)
        self.assertIn("Implementation Deferred (1): topology", text)
        self.assertIn("core: 719.98 MHz", text)

    def test_protocol_inference_fallback_without_contracts(self):
        value = spec()
        del value["functional_contracts"]
        text = " ".join(t.text or "" for t in self.render(value).iter(NS + "text"))
        self.assertIn("protocol-inferred", text)


if __name__ == "__main__":
    unittest.main()
