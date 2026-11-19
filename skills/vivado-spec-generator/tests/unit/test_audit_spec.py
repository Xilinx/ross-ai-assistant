# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


class AuditSpecTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "requirements.txt"
        self.source.write_text("The design exposes a status function.\n")
        self.manifest = self.root / "sources.json"
        self.manifest.write_text(json.dumps([{
            "path": self.source.name,
            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
        }]))
        self.candidate = self.root / "design_spec.json"
        self.spec = {
            "name": "status_design",
            "target_device": "test_device",
            "blocks": [{"name": "status", "workload_type": "custom"}],
            "functional_contracts": {name: [] for name in (
                "connections", "control_reachability", "interrupts",
                "memory_reachability", "fixed_address_windows",
                "visibility_policy", "clock_reset", "software",
            )},
        }

    def run_audit(self, expected_success, extra=(), raw_spec=None):
        self.candidate.write_text(json.dumps(self.spec) if raw_spec is None else raw_spec)
        original = self.candidate.read_bytes()
        result = subprocess.run([
            sys.executable, "-B", str(SCRIPTS / "audit_spec.py"),
            "--spec", str(self.candidate), "--source-root", str(self.root),
            "--sources", str(self.manifest), "--out", str(self.root / "audit"),
            *extra,
        ], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0 if expected_success else 1, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "SPEC: OK" if expected_success else "SPEC: FAILED")
        report = json.loads((self.root / "audit/report.json").read_text())
        self.assertEqual(report["acceptance"], expected_success)
        self.assertEqual(report["status"], "PASS" if expected_success else "FAIL")
        self.assertEqual(report["summary"]["accepted"], expected_success)
        self.assertEqual(self.candidate.read_bytes(), original)
        self.assertNotIn("request_reviewed_requirements", result.stdout)
        return report

    def test_clean_spec_passes_without_policy(self):
        (self.root / "audit").mkdir()
        report = self.run_audit(True)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["agent_feedback"]["next_action"], "accept")
        self.assertIn("do not prove prose completeness", report["summary"]["coverage_note"])

    def test_schema_defect_fails_with_repair_feedback(self):
        del self.spec["target_device"]
        report = self.run_audit(False)
        self.assertEqual(report["findings"][0]["id"], "SCHEMA")
        self.assertTrue(report["agent_feedback"]["repair_permitted"])

    def test_unresolved_intent_still_blocks(self):
        self.spec["global_constraints"] = {"unresolved_requirements": ["Which status signal?"]}
        report = self.run_audit(False)
        self.assertEqual(report["findings"][0]["id"], "UNRESOLVED")
        self.assertIn("Which status signal?", report["findings"][0]["message"])
        self.assertFalse(report["agent_feedback"]["repair_permitted"])
        self.assertEqual(report["findings"][0]["action"]["type"], "clarify_requirement")

    def add_source(self, name, text):
        table = self.root / name
        table.write_text(text)
        manifest = json.loads(self.manifest.read_text())
        manifest.append({"path": table.name, "sha256": hashlib.sha256(table.read_bytes()).hexdigest()})
        self.manifest.write_text(json.dumps(manifest))

    def add_address_map(self, rows):
        self.add_source("address_map.csv", "policy,master,master_count,target,base,size\n" + "".join(row + "\n" for row in rows))

    def window(self, name, masters, offset, size, target="status", block="status"):
        return {"name": name, "target": target, "block": block, "masters": masters, "offset": offset,
                "range": size, "reason": "address map row"}

    def cpu_group(self, *members):
        self.spec["global_constraints"] = {"external_actors": ["cpu0", "cpu1", "cpu2"],
                                          "reference_groups": {"cpu": list(members)}}

    def test_every_address_map_row_needs_a_window(self):
        self.add_address_map(["exact,cpu,2,status.regs,0x1000,0x100", "exact,dma,1,status.regs,0x1000,0x100"])
        self.spec["functional_contracts"]["fixed_address_windows"] = [self.window("regs", ["cpu"], "0x1000", "0x100")]
        report = self.run_audit(False)
        coverage = [finding for finding in report["findings"] if finding["id"] == "SOURCE_COVERAGE"]
        self.assertEqual(len(coverage), 1)
        self.assertIn("address_map.csv:3 master=dma target=status.regs base=0x1000 size=0x100", coverage[0]["message"])

    def test_address_map_rows_match_by_master_offset_and_range(self):
        self.add_address_map(["exact,cpu,2,status.regs,0x1000,0x100", "exact,dma,1,other,0x0,0x1000"])
        self.cpu_group("cpu0", "cpu1")
        self.spec["functional_contracts"]["fixed_address_windows"] = [
            self.window("regs", ["cpu"], "0x1000", "0x100"),
            self.window("low", ["system.dma"], "0x0", "4k", target="status.low"),
        ]
        self.run_audit(True)

    def test_address_map_target_must_map_one_to_one(self):
        self.add_address_map(["exact,cpu,2,status.regs,0x1000,0x100", "exact,cpu,2,other,0x0,0x1000",
                              "exact,dma,1,status.regs,0x1000,0x100"])
        self.cpu_group("cpu0", "cpu1")
        self.spec["functional_contracts"]["fixed_address_windows"] = [
            self.window("regs", ["cpu"], "0x1000", "0x100"),
            self.window("low", ["cpu"], "0x0", "0x1000"),
            self.window("dma_regs", ["dma"], "0x1000", "0x100", target="status.low"),
        ]
        report = self.run_audit(False)
        messages = [finding["message"] for finding in report["findings"] if finding["id"] == "SOURCE_TARGET"]
        self.assertEqual(len(messages), 2, messages)
        self.assertIn("target status.regs is split across spec targets ['status', 'status.low']", messages[0])
        self.assertIn("spec target status serves different address_map.csv targets ['other', 'status.regs']", " ".join(messages))

    def test_address_map_master_count_uses_nested_groups(self):
        self.add_address_map(["exact,cpu,3,status.regs,0x1000,0x100"])
        self.cpu_group("cpu.pair", "cpu2")
        self.spec["global_constraints"]["reference_groups"]["cpu.pair"] = ["cpu0", "cpu1"]
        self.spec["functional_contracts"]["fixed_address_windows"] = [self.window("regs", ["cpu"], "0x1000", "0x100")]
        self.run_audit(True)
        shutil.rmtree(self.root / "audit")
        self.spec["global_constraints"]["reference_groups"]["cpu.pair"] = ["cpu0"]
        report = self.run_audit(False)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["SOURCE_MASTER_COUNT"])
        self.assertIn("master cpu has master_count 3, but it resolves to 2 master(s) ['cpu0', 'cpu2']",
                      report["findings"][0]["message"])

    def test_address_map_target_needs_one_declared_block(self):
        self.add_address_map(["exact,cpu,1,status.regs,0x1000,0x100", "exact,dma,1,status.regs,0x1000,0x100"])
        self.spec["blocks"].append({"name": "spare", "workload_type": "custom"})
        regs, dma_regs = (self.window("regs", ["cpu"], "0x1000", "0x100"),
                          self.window("dma_regs", ["dma"], "0x1000", "0x100", block="spare"))
        del regs["block"]
        self.spec["functional_contracts"]["fixed_address_windows"] = [regs, dma_regs]
        report = self.run_audit(False)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["SOURCE_BLOCK"])
        self.assertIn("target(s) ['status.regs'] have window(s) without a block", report["findings"][0]["message"])
        shutil.rmtree(self.root / "audit")
        regs["block"] = "status"
        report = self.run_audit(False)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["SOURCE_BLOCK"])
        self.assertIn("split across blocks ['spare', 'status']", report["findings"][0]["message"])
        shutil.rmtree(self.root / "audit")
        dma_regs["block"] = "missing"
        report = self.run_audit(False)
        self.assertEqual(report["findings"][0]["id"], "SCHEMA")
        self.assertIn(".block: expected the name of a declared block", report["findings"][0]["message"])

    def test_processor_may_not_own_another_units_aperture(self):
        self.add_address_map(["exact,cpu,1,interrupt.control,0x1000,0x100"])
        self.spec["blocks"].append({"name": "processing_system", "workload_type": "custom_ps"})
        window = self.window("intc", ["cpu"], "0x1000", "0x100",
                             target="interrupt.control", block="processing_system")
        self.spec["functional_contracts"]["fixed_address_windows"] = [window]
        report = self.run_audit(False)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["SOURCE_OWNER"])
        self.assertIn("owned by the processor block processing_system", report["findings"][0]["message"])
        self.assertIn("declare the interrupt unit", report["findings"][0]["message"])
        shutil.rmtree(self.root / "audit")
        # The named unit, declared as its own block, owns it.
        self.spec["blocks"].append({"name": "interrupt_controller", "workload_type": "custom"})
        window["block"] = "interrupt_controller"
        self.run_audit(True)

    def test_owner_is_named_with_the_sources_term(self):
        self.add_source("requirements.md", "Frames land in the frame buffer through the DMA.\n")
        self.add_address_map(["exact,cpu,1,dma.control,0x1000,0x100", "exact,cpu,1,dma.buffer,0x2000,0x100"])
        self.spec["blocks"].append({"name": "transfer_engine", "workload_type": "custom"})
        self.spec["global_constraints"] = {"reference_groups": {"dma.control": ["transfer_engine"]}}
        windows = [self.window(member, ["cpu"], offset, "0x100", target="dma." + member, block="transfer_engine")
                   for member, offset in (("control", "0x1000"), ("buffer", "0x2000"))]
        self.spec["functional_contracts"]["fixed_address_windows"] = windows
        report = self.run_audit(False)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["SOURCE_NAME"])
        self.assertIn("targets dma.* are owned by block transfer_engine", report["findings"][0]["message"])
        self.assertIn("for example 'dma'", report["findings"][0]["message"])
        # Either the map's label or a phrase the requirement text uses is the sources' term.
        del self.spec["global_constraints"]
        for owner in ("dma", "dma_0", "frame_buffer"):
            with self.subTest(owner=owner):
                shutil.rmtree(self.root / "audit")
                self.spec["blocks"][-1]["name"] = owner
                for window in windows:
                    window["block"] = owner
                self.run_audit(True)

    def test_address_map_family_binds_one_block_or_one_block_each(self):
        self.add_address_map(["exact,cpu,1,unit.a,0x1000,0x100", "exact,cpu,1,unit.b,0x2000,0x100",
                              "exact,cpu,1,unit.c,0x3000,0x100"])
        self.spec["blocks"] = [{"name": name, "workload_type": "custom"} for name in ("unit", "unit_b", "unit_c")]

        def bind(*blocks):
            self.spec["functional_contracts"]["fixed_address_windows"] = [
                self.window(member, ["cpu"], hex(0x1000 * (index + 1)), "0x100", target="unit." + member, block=block)
                for index, (member, block) in enumerate(zip("abc", blocks))]

        for blocks in (("unit", "unit", "unit"), ("unit", "unit_b", "unit_c")):
            with self.subTest(blocks=blocks):
                bind(*blocks)
                self.run_audit(True)
                shutil.rmtree(self.root / "audit")
        bind("unit", "unit_b", "unit_b")
        report = self.run_audit(False)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["SOURCE_SPLIT"])
        self.assertIn("targets unit.* bind 3 apertures to 2 blocks", report["findings"][0]["message"])

    def test_empty_contracts_do_not_pass_a_supplied_address_map(self):
        self.add_address_map(["exact,cpu,2,status.regs,0x1000,0x100"])
        report = self.run_audit(False)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["SOURCE_COVERAGE"])

    def test_every_missing_row_is_listed_with_the_nearest_window(self):
        self.add_address_map([f"exact,cpu,1,status.r{index},{hex(0x1000 * index)},0x100" for index in range(1, 31)])
        self.spec["functional_contracts"]["fixed_address_windows"] = [
            self.window("r1", ["cpu"], "0x1000", "0x200", target="status.r1")]
        report = self.run_audit(False)
        coverage = [finding["message"] for finding in report["findings"] if finding["id"] == "SOURCE_COVERAGE"]
        self.assertEqual(len(coverage), 30)
        self.assertIn("nearest window r1: range is 0x200", coverage[0])
        self.assertIn("no window quotes this target or has this offset", coverage[-1])
        extra = [finding["message"] for finding in report["findings"] if finding["id"] == "SOURCE_EXTRA"]
        self.assertEqual(len(extra), 1)
        self.assertIn("window r1 quotes address_map.csv target status.r1", extra[0])

    def test_markdown_address_table_is_checked_like_a_csv(self):
        self.add_source("design.md", "Intro.\n\n| Master | Target | Base | Size |\n|---|---|---|---|\n"
                                     "| `cpu` | `status.regs` | `0x1000` | 4 KiB |\n")
        self.spec["functional_contracts"]["fixed_address_windows"] = [
            self.window("regs", ["cpu"], "0x1000", "0x800", target="status.regs")]
        report = self.run_audit(False)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["SOURCE_COVERAGE", "SOURCE_EXTRA"])
        self.assertIn("design.md:5 master=cpu target=status.regs", report["findings"][0]["message"])
        shutil.rmtree(self.root / "audit")
        self.spec["functional_contracts"]["fixed_address_windows"][0]["range"] = "0x1000"
        self.run_audit(True)

    def test_stated_instance_count_fixes_the_blocks_of_a_family(self):
        self.add_address_map(["exact,cpu,1,unit.a,0x1000,0x100", "exact,cpu,1,unit.b,0x2000,0x100",
                              "exact,cpu,1,unit.c,0x3000,0x100"])
        self.add_source("design.md", "| Target | Instances |\n|---|---|\n| `unit.*` | 1 |\n")
        self.spec["blocks"] = [{"name": name, "workload_type": "custom"} for name in ("unit", "unit_b", "unit_c")]

        def bind(*blocks):
            self.spec["functional_contracts"]["fixed_address_windows"] = [
                self.window(member, ["cpu"], hex(0x1000 * (index + 1)), "0x100", target="unit." + member, block=block)
                for index, (member, block) in enumerate(zip("abc", blocks))]

        bind("unit", "unit_b", "unit_c")
        report = self.run_audit(False)
        self.assertEqual([finding["id"] for finding in report["findings"]], ["SOURCE_INSTANCES"])
        self.assertIn("design.md:3 states 1 instance(s) of unit.*, but its windows bind 3 block(s)",
                      report["findings"][0]["message"])
        shutil.rmtree(self.root / "audit")
        bind("unit", "unit", "unit")
        self.run_audit(True)

    def test_duplicate_interface_still_fails_consistency(self):
        interface = {"name": "signal", "protocol": "signal", "direction": "out"}
        self.spec["blocks"][0]["interfaces"] = [interface, interface]
        report = self.run_audit(False)
        self.assertIn("DUPLICATE_INTERFACE", [finding["id"] for finding in report["findings"]])

    def test_unresolved_reference_group_still_fails(self):
        self.spec["global_constraints"] = {"reference_groups": {"unknown": ["missing"]}}
        report = self.run_audit(False)
        self.assertIn("REFERENCE_GROUP", [finding["id"] for finding in report["findings"]])

    def test_changed_source_hash_fails(self):
        self.source.write_text("Changed requirements\n")
        report = self.run_audit(False)
        self.assertEqual(report["findings"][0]["id"], "CHECK_INPUT")

    def test_used_audit_directory_is_refused(self):
        self.run_audit(True)
        self.candidate.write_text(json.dumps(self.spec))
        result = subprocess.run([
            sys.executable, "-B", str(SCRIPTS / "audit_spec.py"), "--spec", str(self.candidate),
            "--source-root", str(self.root), "--sources", str(self.manifest), "--out", str(self.root / "audit"),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Use a new audit output directory", result.stderr)

    def test_missing_source_fails(self):
        self.source.unlink()
        self.run_audit(False)

    def test_malformed_spec_fails(self):
        self.run_audit(False, raw_spec="{")

    def test_missing_manifest_fails(self):
        self.manifest.unlink()
        self.run_audit(False)

    def test_explicit_invalid_policy_is_not_ignored(self):
        policy = self.root / "policy.json"
        policy.write_text("{}")
        self.run_audit(False, extra=("--policy", str(policy)))

    def test_schema_helper_failures_use_failed(self):
        for candidate in ("{", "{}", json.dumps({**self.spec, "global_constraints": {
            "unresolved_requirements": ["Which status signal?"],
        }})):
            with self.subTest(candidate=candidate):
                self.candidate.write_text(candidate)
                result = subprocess.run([
                    sys.executable, "-B", str(SCRIPTS / "validate_spec.py"), str(self.candidate),
                ], capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("SPEC: FAILED", result.stdout)
                self.assertNotIn("SPEC:UNRESOLVED", result.stdout)

    def test_schema_helper_does_not_claim_final_acceptance(self):
        self.candidate.write_text(json.dumps(self.spec))
        result = subprocess.run([
            sys.executable, "-B", str(SCRIPTS / "validate_spec.py"), str(self.candidate),
        ], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SPEC:SCHEMA_OK", result.stdout)
        self.assertNotIn("SPEC: OK", result.stdout)
        self.assertNotIn("fidelity", result.stdout)


if __name__ == "__main__":
    unittest.main()