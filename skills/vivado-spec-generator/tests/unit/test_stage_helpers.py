# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import address_contracts  # noqa: E402

CSV = """policy,master,master_count,target,base,size
exact,application,2,ddr.low,0x0,0x80000000
exact,application,2,gpio.a,0xB0000000,0x10000
exact,application,2,gpio.b,0xB0010000,0x10000
exact,pmc,3,ddr.low,0x0,0x80000000
exact,pmc,3,gpio.a,0xB0000000,0x10000
"""

BINDING = {
    "blocks": {"ddr": "memory", "gpio.a": "gpio_a", "gpio.b": "gpio_b"},
    "memory": ["ddr"],
    "groups": {"application": ["ps.apu0", "ps.apu1"], "pmc": ["ps.pmc"],
               "ps.pmc": ["ps.pmc_cpu", "ps.pmc_dma0", "ps.pmc_dma1"]},
}


def run(script, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


class AddressContractTests(unittest.TestCase):
    def rows(self):
        path = Path(self.tmp.name, "map.csv")
        path.write_text(CSV)
        return address_contracts.read_rows(path)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_one_window_per_aperture_with_every_master_that_uses_it(self):
        contracts, problems = address_contracts.generate(self.rows(), BINDING, "map.csv")
        self.assertEqual(problems, [])
        windows = {w["target"]: w for w in contracts["fixed_address_windows"]}
        self.assertEqual(windows["ddr.low"]["masters"], ["application", "pmc"])
        self.assertEqual(windows["ddr.low"]["block"], "memory")
        self.assertEqual((windows["gpio.b"]["offset"], windows["gpio.b"]["range"]), ("0xB0010000", "0x10000"))
        self.assertEqual({(m["master"], m["memory"]) for m in contracts["memory_reachability"]},
                         {("application", "ddr.low"), ("pmc", "ddr.low")})
        self.assertEqual({(c["master"], c["target"]) for c in contracts["control_reachability"]},
                         {("application", "gpio_a"), ("application", "gpio_b"), ("pmc", "gpio_a")})
        self.assertIn("ps.pmc", contracts["reference_groups"], "nested groups must be emitted too")

    def test_refuses_unbound_targets_and_wrong_group_sizes(self):
        binding = {"blocks": {"ddr": "memory"}, "memory": ["ddr"],
                   "groups": {"application": ["ps.apu0"], "pmc": ["ps.pmc"]}}
        contracts, problems = address_contracts.generate(self.rows(), binding, "map.csv")
        self.assertIsNone(contracts)
        self.assertIn("target gpio.a: no owning block in 'blocks'", problems)
        self.assertTrue(any(p.startswith("master application: resolves to 1") for p in problems))
        self.assertTrue(any(p.startswith("master pmc: resolves to 1") for p in problems))

    def test_cli_skeleton_then_bind(self):
        csv_path = Path(self.tmp.name, "map.csv")
        csv_path.write_text(CSV)
        skeleton = json.loads(run("address_contracts.py", "--csv", csv_path, "--skeleton").stdout)
        self.assertEqual(set(skeleton["blocks"]), {"ddr.low", "gpio.a", "gpio.b"})
        bind, out = Path(self.tmp.name, "bind.json"), Path(self.tmp.name, "out.json")
        bind.write_text(json.dumps(BINDING))
        result = run("address_contracts.py", "--csv", csv_path, "--bind", bind, "--out", out)
        self.assertIn("ADDRESS_CONTRACTS:OK windows=3", result.stdout, result.stderr)
        self.assertTrue(out.is_file())
        skeleton_bind = Path(self.tmp.name, "skeleton.json")
        skeleton_bind.write_text(json.dumps(skeleton))
        self.assertIn("ADDRESS_CONTRACTS_FAIL", run("address_contracts.py", "--csv", csv_path, "--bind", skeleton_bind).stdout)


class StageReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name, "outputs")

    def audit(self, status, accepted):
        folder = self.out / "audit"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.json").write_text(json.dumps(
            {"status": status, "spec_sha256": "ab", "summary": {"status": status, "accepted": accepted}}))
        return folder

    def test_done_only_when_every_gate_is_confirmed(self):
        self.assertIn("STAGE_REPORT:STARTED", run("stage_report.py", "start", "--stage", "1", "--out", self.out).stdout)
        log = self.out / "validate.log"
        log.write_text("SPEC:SCHEMA_OK blocks=3\n")
        result = run("stage_report.py", "finish", "--stage", "1", "--out", self.out,
                     "--audit", self.audit("PASS", True), "--gate", f"SPEC:SCHEMA_OK={log}")
        self.assertIn("STAGE_REPORT:DONE stage=1", result.stdout, result.stderr)
        report = json.loads((self.out / "stage1/report.json").read_text())
        self.assertEqual(report["status"], "done")
        self.assertEqual(len(report["gates"]), 2)
        self.assertTrue(all(g["sha256"] for g in report["gates"]))

    def test_unconfirmed_evidence_makes_the_stage_failed(self):
        run("stage_report.py", "start", "--stage", "1", "--out", self.out)
        result = run("stage_report.py", "finish", "--stage", "1", "--out", self.out,
                     "--audit", self.audit("FAIL", False), "--gate", f"SPEC:SCHEMA_OK={self.out / 'missing.log'}")
        self.assertEqual(result.returncode, 1)
        self.assertIn("STAGE_REPORT:FAILED", result.stdout)
        self.assertIn("unconfirmed=SPEC: OK,SPEC:SCHEMA_OK", result.stdout)

    def test_finish_needs_a_start_and_start_happens_once(self):
        self.assertIn("has no start", run("stage_report.py", "finish", "--stage", "2", "--out", self.out).stdout)
        run("stage_report.py", "start", "--stage", "2", "--out", self.out)
        self.assertIn("already started", run("stage_report.py", "start", "--stage", "2", "--out", self.out).stdout)


if __name__ == "__main__":
    unittest.main()


class ClockTargetRuleTests(unittest.TestCase):
    def findings(self, rows):
        import audit_spec
        spec = {"blocks": [{"name": "ps", "interfaces": [{"name": "video_clk"}, {"name": "audio_clk"}]}],
                "global_constraints": {}, "functional_contracts": {"clock_reset": rows}}
        return [f for f in audit_spec.reference_findings(spec) if f["id"] == "CLOCK_TARGET"]

    def test_one_interface_cannot_carry_two_frequencies(self):
        found = self.findings([{"target": "ps.video_clk", "clock_hz": 74251394},
                               {"target": "ps.video_clk", "clock_mhz": 148.502788}])
        self.assertEqual(len(found), 1)
        self.assertIn("2 different frequencies", found[0]["message"])

    def test_distinct_endpoints_and_repeated_rates_pass(self):
        self.assertEqual(self.findings([{"target": "ps.video_clk", "clock_hz": 74251394},
                                        {"target": "ps.video_clk", "clock_mhz": 74.251394},
                                        {"target": "ps.audio_clk", "clock_hz": 22579371}]), [])

    def test_bare_name_without_frequency_beside_a_stated_core_clock_fails(self):
        for core in ({"target": "ps.tile0_core", "clock_mhz": 719.98}, {"target": "ps", "clock_mhz": 719.98}):
            with self.subTest(core=core["target"]):
                found = self.findings([core, {"target": "ps", "reset_polarity": "active_low"}])
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["pointer"], "/functional_contracts/clock_reset/1/target")
                self.assertIn("reads as a second core clock", found[0]["message"])

    def test_reset_only_entries_need_no_frequency_when_no_core_clock_is_stated(self):
        # The common case: a reset contract per block, no core clock singled out.
        self.assertEqual(self.findings([{"target": "ps", "reset_polarity": "active_low"}]), [])
        # A stated core clock with its reset on the interface it governs.
        self.assertEqual(self.findings([{"target": "ps", "clock_mhz": 719.98},
                                        {"target": "ps.video_clk", "reset_polarity": "active_low"}]), [])
