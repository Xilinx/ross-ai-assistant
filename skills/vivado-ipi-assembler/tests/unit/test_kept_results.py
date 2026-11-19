# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]


def run_tcl(body):
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
    """) + textwrap.dedent(body)
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as handle:
        handle.write(script)
        path = handle.name
    result = subprocess.run(["tclsh", path], capture_output=True, text=True, timeout=60)
    Path(path).unlink()
    return result


class KeptResultTests(unittest.TestCase):
    """A long call's reply survives a client timeout in <out>/logs/<proc>.result.txt."""

    def test_returned_value_is_written_beside_the_out_directory(self):
        with tempfile.TemporaryDirectory() as out:
            result = run_tcl(f"""
                set r [ipiasm::assemble_from_plan {{{out}/assembly_plan.json}}]
                puts "RETURNED:$r"
            """)
            kept = Path(out, "logs", "assemble_from_plan.result.txt")
            self.assertTrue(kept.is_file(), result.stdout + result.stderr)
            returned = result.stdout.split("RETURNED:", 1)[1].strip()
            self.assertEqual(kept.read_text().strip(), returned)

    def test_an_error_is_kept_with_its_message(self):
        with tempfile.TemporaryDirectory() as out:
            result = run_tcl(f"""
                proc ipiasm::_raise_for_test {{}} {{error "boom"}}
                dict set ::ipiasm::kept_results _raise_for_test {{0 dir}}
                proc ipiasm::_raise_for_test {{dir}} {{error "boom in $dir"}}
                trace add execution ::ipiasm::_raise_for_test leave [list ipiasm::_keep_result _raise_for_test]
                catch {{ipiasm::_raise_for_test {{{out}}}}}
            """)
            kept = Path(out, "logs", "_raise_for_test.result.txt")
            self.assertTrue(kept.is_file(), result.stdout + result.stderr)
            self.assertEqual(kept.read_text(), f"ERROR: boom in {out}")

    def test_running_marker_exists_only_while_the_call_runs(self):
        with tempfile.TemporaryDirectory() as out:
            result = run_tcl(f"""
                dict set ::ipiasm::kept_results _slow_for_test {{0 dir}}
                proc ipiasm::_slow_for_test {{dir}} {{
                    return "seen=[file exists [file join $dir logs _slow_for_test.running]]"
                }}
                trace add execution ::ipiasm::_slow_for_test enter [list ipiasm::_mark_running _slow_for_test]
                trace add execution ::ipiasm::_slow_for_test leave [list ipiasm::_keep_result _slow_for_test]
                puts [ipiasm::_slow_for_test {{{out}}}]
            """)
            self.assertIn("seen=1", result.stdout, result.stderr)
            self.assertFalse(Path(out, "logs", "_slow_for_test.running").exists())
            self.assertEqual(Path(out, "logs", "_slow_for_test.result.txt").read_text(), "seen=1")


class AwaitResultTests(unittest.TestCase):
    """scripts/await_result.py returns a finished call's result and reports one still running."""

    SCRIPT = SKILLS / "vivado-ipi-assembler/scripts/await_result.py"

    def run_await(self, out, *args):
        result = subprocess.run(["python3", str(self.SCRIPT), out, *args],
                                capture_output=True, text=True, timeout=60)
        return result.returncode, result.stdout

    def test_finished_call_prints_its_result(self):
        with tempfile.TemporaryDirectory() as out:
            Path(out, "logs").mkdir()
            Path(out, "logs", "deliver.result.txt").write_text("DELIVERED xsa=/x.xsa")
            code, text = self.run_await(out)
            self.assertEqual(code, 0)
            self.assertIn("RESULT deliver", text)
            self.assertIn("DELIVERED xsa=/x.xsa", text)

    def test_call_still_running_at_the_timeout_is_reported(self):
        with tempfile.TemporaryDirectory() as out:
            Path(out, "logs").mkdir()
            Path(out, "logs", "deliver.result.txt").write_text("DELIVER_FAIL:an earlier call")
            Path(out, "logs", "deliver.running").write_text("0")
            code, text = self.run_await(out, "deliver", "--timeout", "0")
            self.assertEqual(code, 0)
            self.assertIn("STILL_RUNNING deliver", text)
            self.assertNotIn("an earlier call", text)

    def test_call_given_a_subdirectory_is_waited_for_from_out(self):
        with tempfile.TemporaryDirectory() as out:
            Path(out, "logs").mkdir()
            Path(out, "logs", "assemble_from_plan.result.txt").write_text("ASSEMBLY_PLAN:PASS stages=7")
            Path(out, "delivery", "logs").mkdir(parents=True)
            Path(out, "delivery", "logs", "deliver.running").write_text("0")
            code, text = self.run_await(out, "--timeout", "0")
            self.assertEqual(code, 0)
            self.assertIn("STILL_RUNNING deliver", text)
            self.assertNotIn("ASSEMBLY_PLAN", text)
            Path(out, "delivery", "logs", "deliver.running").unlink()
            Path(out, "delivery", "logs", "deliver.result.txt").write_text("DELIVERED xsa=/d.xsa")
            code, text = self.run_await(out)
            self.assertIn("RESULT deliver", text)
            self.assertIn("DELIVERED xsa=/d.xsa", text)

    def test_nothing_to_wait_for(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(self.run_await(out)[0], 2)


if __name__ == "__main__":
    unittest.main()
