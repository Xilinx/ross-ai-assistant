# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import copy
from pathlib import Path
import sys
import subprocess
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from plan_clocks import plan_clock_architecture, tcl_data
from validate_feasibility import FeasibilityError


def fixture():
    return {
        "schema_version": 1,
        "fingerprint": {name: "fixture-" + name for name in ("tool", "part", "board", "ip", "sources")},
        "sources": {"fabric": {"hz": 99999001, "max_outputs": 4,
                               "vlnv": "test:ip:clock:1.0", "evidence": "synthetic discovery"}},
        "clocks": [{"id": name, "source": "fabric", "hz": hz, "tolerance_hz": 0,
                    "sinks": [name + "/clk"], "resets": [], "requirement": "spec#/" + name}
                   for name, hz in (("display", 74251394), ("audio", 22579371), ("logic", 249997500))],
    }


class ClockPlanningTests(unittest.TestCase):
    def test_plan_tcl_data_preserves_values_without_execution(self):
        value = tcl_data({"text": "literal $name [error unsafe] {braces} ; spaced", "nested": [{"hz": 74251394}]})
        script = 'set data [read stdin]\nputs [dict get $data text]\nputs [dict get [lindex [dict get $data nested] 0] hz]\n'
        # Pass data as an argument, so it cannot be evaluated as Tcl code.
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl") as source:
            source.write(script)
            source.flush()
            result = subprocess.run(["tclsh", source.name], input=value, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["literal $name [error unsafe] {braces} ; spaced", "74251394"])

    def test_nominal_setting_does_not_replace_exact_requirement(self):
        request = fixture()
        request["clocks"][0]["requested_hz"] = 74250000
        output = plan_clock_architecture(request)["candidates"][0]["generators"][0]["outputs"][0]
        self.assertEqual(output["requested_hz"], 74250000)
        self.assertEqual(output["hz"], 74251394)
        self.assertEqual(output["tolerance_hz"], 0)

    def test_exact_rates_partition_and_output_order(self):
        request = fixture()
        result = plan_clock_architecture(request)
        self.assertEqual(result, plan_clock_architecture(request))
        self.assertEqual(result["hardware_status"], "UNVERIFIED")
        candidates = result["candidates"]
        self.assertEqual(candidates[0]["generators"][0]["input_hz"], 99999001)
        self.assertEqual(candidates[0]["generators"][0]["outputs"][0]["hz"], 74251394)
        self.assertTrue(any(len(candidate["generators"]) > 1 for candidate in candidates))
        single = [candidate for candidate in candidates if len(candidate["generators"]) == 1]
        self.assertGreater(len(single), 1)
        self.assertTrue(all(candidate["status"] == "PENDING_LIVE" for candidate in candidates))
        self.assertEqual(result["reset_domains"], [])

    def test_capacity_and_coherent_groups(self):
        request = fixture()
        request["sources"]["fabric"]["max_outputs"] = 2
        for clock in request["clocks"][:2]:
            clock["coherent_group"] = "media"
        result = plan_clock_architecture(request)
        for candidate in result["candidates"]:
            self.assertGreaterEqual(len(candidate["generators"]), 2)
            group = next(group for group in candidate["generators"]
                         if any(output["clock"] == "audio" for output in group["outputs"]))
            self.assertEqual({output["clock"] for output in group["outputs"]}, {"audio", "display"})

    def test_reset_identity_not_rate(self):
        request = fixture()
        reset = {"sink": "display/resetn", "source": "ps/resetn", "source_polarity": "ACTIVE_LOW",
                 "output": "peripheral_aresetn", "release": "proc_sys_reset", "lock": "own_generator"}
        request["clocks"][0]["resets"] = [reset, dict(reset, sink="display/interconnect", output="interconnect_aresetn")]
        request["clocks"][1]["hz"] = request["clocks"][0]["hz"]
        request["clocks"][1]["resets"] = [dict(reset, sink="audio/resetn")]
        domains = plan_clock_architecture(request)["reset_domains"]
        self.assertEqual(len(domains), 2)
        self.assertEqual(len(domains[0]["sinks"]), 2)
        request["clocks"][0]["resets"].append(dict(reset, sink="other/resetn", source="other/reset"))
        self.assertEqual(len(plan_clock_architecture(request)["reset_domains"]), 3)

    def test_reset_on_a_clock_without_lock_output(self):
        request = fixture()
        reset = {"sink": "display/resetn", "source": "ps/resetn", "source_polarity": "ACTIVE_LOW",
                 "output": "peripheral_aresetn", "release": "proc_sys_reset", "lock": ""}
        request["clocks"][0]["resets"] = [reset, dict(reset, sink="display/locked_resetn", lock="own_generator")]
        domains = plan_clock_architecture(request)["reset_domains"]
        # An empty lock is its own lock policy, so it never shares a controller with a locked one.
        self.assertEqual(sorted(domain["lock"] for domain in domains), ["", "own_generator"])
        request["clocks"][0]["resets"][0]["source"] = ""
        with self.assertRaises(FeasibilityError):
            plan_clock_architecture(request)

    def test_bad_requirements_fail_closed(self):
        mutations = [
            lambda request: request["clocks"][0].update(hz=74.251394),
            lambda request: request["clocks"][0].update(hz=True),
            lambda request: request["clocks"][0].update(tolerance_hz=-1),
            lambda request: request["clocks"][0].update(phase=90),
            lambda request: request["sources"]["fabric"].update(max_outputs=0),
            lambda request: request["sources"]["fabric"].update(evidence=""),
            lambda request: request["clocks"].append(copy.deepcopy(request["clocks"][0])),
        ]
        for mutate in mutations:
            request = fixture()
            mutate(request)
            with self.subTest(request=request), self.assertRaises(FeasibilityError):
                plan_clock_architecture(request)

    def test_bounded_search_never_claims_impossible(self):
        result = plan_clock_architecture(fixture(), search_limit=1)
        self.assertTrue(result["search_truncated"])
        self.assertEqual(result["status"], "CANDIDATES_ONLY")
        self.assertEqual(result["candidates"], [])
        self.assertLessEqual(len(plan_clock_architecture(fixture(), limit=2)["candidates"]), 2)


if __name__ == "__main__":
    unittest.main()