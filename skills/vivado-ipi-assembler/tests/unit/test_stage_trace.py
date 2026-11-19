# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]
LIB = SKILLS / "vivado-ipi-assembler/lib"
sys.path.insert(0, str(SKILLS / "vivado-ipi-assembler/scripts"))
import timing_report as report_trace  # noqa: E402


def run_tcl(body):
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
        source {{{LIB / 'stage_trace.tcl'}}}
        if {{[catch {{{body}}} message]}} {{puts "TCL_ERROR $message"}}
    """)
    result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120)
    return result.stdout + result.stderr


class StageTraceTests(unittest.TestCase):
    def test_calls_are_labelled_with_the_stage_that_was_entered(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root, "outputs/trace.jsonl")
            out = run_tcl(f"""
                puts [ipiasm::trace_start {{{log}}}]
                ipiasm::stage board
                ipiasm::api_inventory ::ipiasm::stage
                ipiasm::stage cells
                ipiasm::api_inventory ::ipiasm::gate_status
                puts [ipiasm::trace_stop]
            """)
            self.assertNotIn("TCL_ERROR", out, out)
            self.assertIn("TRACE:ON", out)
            records = report_trace.load(log)
        calls = [(r["stage"], r["name"]) for r in records if r["kind"] == "tcl"]
        self.assertIn(("board", "::ipiasm::api_inventory"), calls)
        self.assertIn(("cells", "::ipiasm::api_inventory"), calls)
        self.assertFalse([name for _, name in calls if name.startswith("::ipiasm::trace")])
        self.assertEqual([r["stage"] for r in records if r["kind"] == "stage"], ["board", "cells"])
        agg = report_trace.aggregate(records)
        self.assertEqual(list(agg), ["pre", "board", "cells"])
        self.assertIn("::ipiasm::api_inventory", agg["cells"]["calls"])
        self.assertIn("## cells", report_trace.render_markdown(agg, 5, {"cells": 6}))

    def test_python_is_named_profiled_and_its_output_is_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root, "outputs/trace.jsonl")
            probe = Path(root, "probe.py")
            probe.write_text("import sys\ndef work():\n    total = 0\n    for i in range(300000):\n"
                             "        total += i\n    return total % 1000\n"
                             "print('ANSWER', work(), sys.argv[1:], __name__)\n")
            out = run_tcl(f"""
                set ::env(IPIASM_TRACE_PYTHON) 1
                ipiasm::trace_start {{{log}}}
                ipiasm::stage clocks
                puts [ipcfg::python_script {{{probe}}} a b]
                puts [ipiasm::trace_stop]
                puts "after=[ipcfg::python_exec {{{probe}}} c]"
            """)
            self.assertNotIn("TCL_ERROR", out, out)
            self.assertIn("ANSWER 0 ['a', 'b'] __main__", out)
            self.assertIn("after=ANSWER 0 ['c'] __main__", out)
            records = report_trace.load(log)
            profiles = report_trace.load(log.with_suffix(".py.jsonl"))
        python = [r for r in records if r["kind"] == "python"]
        self.assertEqual([(r["stage"], r["name"]) for r in python], [("clocks", "py:probe.py")])
        self.assertEqual(len(profiles), 1, "the untraced call after trace_stop must not be profiled")
        self.assertEqual(profiles[0]["argv"], ["a", "b"])
        self.assertTrue(any(":work" in row["fn"] for row in profiles[0]["top"]))
        agg = report_trace.aggregate(records, profiles)
        self.assertTrue(any("work" in name for name in agg["clocks"]["python"]))

    def test_agent_native_commands_and_scripts_are_timed_once(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root, "outputs/trace.jsonl")
            stage_script = Path(root, "s3_connect.tcl")
            stage_script.write_text("validate_bd_design\nipiasm::api_inventory ::ipiasm::stage\n")
            out = run_tcl(f"""
                proc validate_bd_design {{args}} {{after 30; return ok}}
                proc assign_bd_address {{args}} {{after 10; return ok}}
                proc ipiasm::uses_native {{}} {{assign_bd_address; return done}}
                ipiasm::trace_start {{{log}}}
                ipiasm::stage connect
                validate_bd_design
                ipiasm::uses_native
                source {{{stage_script}}}
                puts [ipiasm::trace_stop]
            """)
            self.assertNotIn("TCL_ERROR", out, out)
            records = report_trace.load(log)
        native = [(r["name"], r.get("script")) for r in records if r["kind"] == "native"]
        # validate typed by the agent, and the sourced stage script once (not the validate inside
        # it); assign_bd_address inside a library call belongs to that call, not to the agent.
        self.assertEqual(native, [("validate_bd_design", None), ("source", "s3_connect.tcl")])
        summary = report_trace.summarize(records)
        self.assertGreater(summary["native_ms"], 30)
        self.assertGreaterEqual(summary["wall_ms"], summary["busy_ms"])
        self.assertIn("connect", report_trace.render_markdown(report_trace.aggregate(records), 5))

    def test_report_uses_the_skill_stage_names(self):
        names = [report_trace.stage_name(s) for s in ("pre", "board", "cells", "inventory", "clocks",
                                                       "connect_axi", "qos", "address", "validate", "deliver", "mystery")]
        self.assertEqual(names, [report_trace.PRE, "0b BOARD", "1 CELLS", "1b INVENTORY", "2 CLOCKS", "3 CONNECT",
                                 "3b QOS", "4 ADDRESS", "5 VALIDATE", "6 DELIVER", "other: mystery"])

    def test_refuses_to_write_under_inputs(self):
        with tempfile.TemporaryDirectory() as root:
            out = run_tcl(f"puts [ipiasm::trace_start {{{Path(root, 'inputs/x.jsonl')}}}]")
            self.assertIn("TRACE_FAIL:refusing to write under inputs/", out)
            self.assertFalse(Path(root, "inputs").exists())

    def test_environment_starts_the_trace_when_the_library_loads(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root, "outputs/auto.jsonl")
            script = textwrap.dedent(f"""
                source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
                source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
                ipiasm::stage cells
                ipiasm::api_inventory ::ipiasm::stage
                puts [ipiasm::trace_stop]
            """)
            env = {**__import__("os").environ, "IPIASM_TRACE": str(log)}
            result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120, env=env)
            self.assertIn("TRACE:ON", result.stdout, result.stdout + result.stderr)
            records = report_trace.load(log)
        self.assertIn(("cells", "::ipiasm::api_inventory"),
                      [(r["stage"], r.get("name")) for r in records if r["kind"] == "tcl"])

    def test_untraced_library_stays_silent(self):
        script = textwrap.dedent(f"""
            source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
            source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
            puts [ipiasm::stage board]
            puts "wrapper=<$::ipcfg::python_wrapper>"
        """)
        result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120)
        self.assertIn("STAGE:board", result.stdout, result.stdout + result.stderr)
        self.assertIn("wrapper=<>", result.stdout)


if __name__ == "__main__":
    unittest.main()
