# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""ipiasm::stage may skip the clock-contract pass that the preceding
ipiasm::checkpoint just ran, and only when nothing could have changed."""

import os
from pathlib import Path
import re
import subprocess
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]
LIB = SKILLS / "vivado-ipi-assembler/lib"

FIXTURE = r'''
set ::design design
set ::cells {/a /b}
set ::result "CLOCK_CONTRACTS:OK clocks=1 resets=1 modeled_hz_only=1"
set ::checks 0
proc current_project {args} {return project}
proc current_bd_design {args} {return $::design}
proc get_property {args} {return /fixture}
proc get_bd_cells {args} {return $::cells}
proc get_bd_nets {args} {return {n1 n2}}
proc get_bd_intf_nets {args} {return {i1}}
proc get_bd_addr_spaces {args} {return {s}}
proc get_bd_addr_segs {args} {return {g}}
proc save_bd_design {args} {return {}}
proc write_bd_tcl {args} {return {}}
proc report_bd_diffs {args} {return {}}
proc connect_bd_net {args} {return {}}
proc tweak_bd_cell {args} {return {}}
proc set_property {args} {return {}}
proc ipiasm::check_undriven_clocks {} {return UNDRIVEN_CLK:OK}
proc ipiasm::check_clock_contracts {} {incr ::checks; return $::result}
set ::ipiasm::clock_contracts {checks {c} resets {} context x}
# The re-checks below judge a validated design; before validation they do not run.
set ::ipiasm::design_validated 1
'''


def run(body, env=None):
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        source {{{LIB / 'ipiasm.tcl'}}}
    """) + FIXTURE + textwrap.dedent(body) + '\nputs "CHECKS=$::checks"\n'
    environment = {k: v for k, v in os.environ.items() if k != "IPIASM_NO_VERIFY_CACHE"}
    environment.update(env or {})
    result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=60, env=environment)
    output = result.stdout + result.stderr
    match = re.search(r"CHECKS=(\d+)", output)
    if not match:
        raise AssertionError(output)
    return int(match.group(1)), output


class VerificationCacheTests(unittest.TestCase):
    def checks(self, between, env=None, stage="qos"):
        count, _ = run(f"ipiasm::checkpoint plan_connect\n{between}\nipiasm::stage {stage}\n", env)
        return count

    def test_nothing_judges_clock_contracts_before_the_design_validates(self):
        # Propagated clock frequencies and reset polarity exist only after validation, so a
        # boundary before it neither runs the check nor fails on it.
        count, output = run("set ::ipiasm::design_validated 0\nset ::result CLOCK_CONTRACT_FAIL:unpropagated\n"
                            "puts [ipiasm::checkpoint plan_clocks]\nputs [ipiasm::stage qos]\n")
        self.assertEqual(count, 0)
        self.assertIn("CHECKPOINT:plan_clocks", output)
        self.assertIn("STAGE:qos", output)

    def test_stage_reuses_the_checkpoint_pass_on_an_unchanged_design(self):
        self.assertEqual(self.checks(""), 1)
        for stage in ("address", "validate", "deliver"):
            with self.subTest(stage=stage):
                self.assertEqual(self.checks("", stage=stage), 1)

    def test_any_mutation_forces_a_full_recheck(self):
        for mutation in (
            "connect_bd_net /a/x /b/y",
            "set_property CONFIG.FREQ_HZ 1 /a/clk",
            # A BD command no list names is assumed to mutate.
            "tweak_bd_cell /a",
            # So is one that did not exist when the checkpoint ran.
            "proc brand_new_bd_op {args} {}; brand_new_bd_op",
            # Redefining a traced command drops its trace; that is caught too.
            "proc connect_bd_net {args} {return {}}",
            # Sourced scripts are covered command by command.
            "set f [file join [pwd] .vc_[pid].tcl]; set c [open $f w]; puts $c {connect_bd_net /a /b}; "
            "close $c; source $f; file delete $f",
        ):
            with self.subTest(mutation=mutation):
                self.assertEqual(self.checks(mutation), 2)

    def test_reading_and_saving_do_not_invalidate(self):
        self.assertEqual(self.checks("get_bd_cells -hier; report_bd_diffs; write_bd_tcl x; "
                                     "save_bd_design; current_bd_design; ipiasm::gate_status"), 1)

    def test_state_changes_without_a_traced_command_force_a_recheck(self):
        for change in (
            "set ::cells {/a /b /c}",                        # design shape
            "set ::design other",                            # design context
            "set ::ipiasm::clock_contracts {checks {d}}",   # registered contracts
        ):
            with self.subTest(change=change):
                self.assertEqual(self.checks(change), 2)

    def test_only_a_pass_is_reused(self):
        for result in ("CLOCK_CONTRACT_FAIL:reset x: wrong driver", "CLOCK_CONTRACTS:NA not registered"):
            with self.subTest(result=result):
                count, output = run(f"set ::result {{{result}}}\n"
                                    "puts [ipiasm::checkpoint plan_connect]\nputs [ipiasm::stage qos]\n")
                self.assertEqual(count, 2)
                if result.startswith("CLOCK_CONTRACT_FAIL"):
                    self.assertIn("CHECKPOINT_FAIL:plan_connect", output)
                    self.assertIn("GATE_FAIL:qos CLOCK_CONTRACT_FAIL", output)

    def test_checkpoints_always_run_the_full_check(self):
        count, _ = run("ipiasm::checkpoint plan_connect\nipiasm::checkpoint plan_qos\n")
        self.assertEqual(count, 2)

    def test_environment_switch_disables_reuse(self):
        self.assertEqual(self.checks("", env={"IPIASM_NO_VERIFY_CACHE": "1"}), 2)
        self.assertEqual(self.checks("", env={"IPIASM_NO_VERIFY_CACHE": "0"}), 1)

    def test_reused_result_is_byte_identical(self):
        count, output = run("puts A=[ipiasm::_verified_clock_contracts checkpoint]\n"
                            "puts B=[ipiasm::_verified_clock_contracts stage]\n")
        self.assertEqual(count, 1)
        first = re.search(r"^A=(.*)$", output, re.M).group(1)
        self.assertEqual(re.search(r"^B=(.*)$", output, re.M).group(1), first)

    def test_without_a_design_fingerprint_nothing_is_reused(self):
        self.assertEqual(self.checks("proc get_bd_nets {args} {error {no design}}"), 2)

    def test_final_gates_never_use_the_cache(self):
        source = (LIB / "ipiasm.tcl").read_text()
        # delivery_guard logs the verdict of _delivery_guard_result, which does the checking.
        for proc in ("final_contract_gate", "_delivery_guard_result"):
            with self.subTest(proc=proc):
                body = source.split(f"proc ipiasm::{proc} ", 1)[1].split("\nproc ", 1)[0]
                self.assertIn("[ipiasm::check_clock_contracts]", body)
                self.assertNotIn("_verified_clock_contracts", body)


VALIDATE_FIXTURE = r'''
set ::validations 0
set ::vresult ok
proc validate_bd_design {args} {
    incr ::validations
    # Validation propagates parameters: it counts as a mutation itself.
    set_property CONFIG.FREQ_HZ 1 /a/clk
    if {$::vresult ne "ok"} {error "ERROR: \[BD 41-1\] $::vresult"}
    return {}
}
proc ipiasm::preflight_interface_contracts {} {return INTF_PREFLIGHT:OK}
proc ipiasm::clock_reset_msg_ids {} {return {}}
proc ipiasm::check_interface_clock_domains {} {return CLOCK_DOMAIN:OK}
proc ipiasm::check_mipi_video_clocks {} {return VIDEOCLK:NA}
proc ipiasm::_illegal_noc_findings {args} {return {}}
proc ipiasm::_mapped_seg_count {} {return 1}
'''


def run_validate(body, env=None):
    count, output = run(VALIDATE_FIXTURE + textwrap.dedent(body) + '\nputs "VALIDATIONS=$::validations"\n', env)
    return int(re.search(r"VALIDATIONS=(\d+)", output).group(1)), output


class ValidateReuseTests(unittest.TestCase):
    def twice(self, between="", env=None):
        count, output = run_validate(f"puts A=[ipiasm::validate]\n{between}\nputs B=[ipiasm::validate]\n", env)
        return count, output

    def test_unchanged_design_validates_once(self):
        count, output = self.twice()
        self.assertEqual(count, 1)
        self.assertIn("A=VALIDATE:PASS", output)
        self.assertIn("B=VALIDATE:PASS", output)
        self.assertIn("REUSED=1", run_validate("ipiasm::validate; ipiasm::validate; puts REUSED=$::ipiasm::validate_reuse_count")[1])

    def test_any_mutation_or_state_change_revalidates(self):
        for change in ("connect_bd_net /a/x /b/y", "set_property CONFIG.X 1 /a", "tweak_bd_cell /a",
                       "set ::cells {/a /b /c}", "set ::design other",
                       "set ::ipiasm::clock_contracts {checks {d}}"):
            with self.subTest(change=change):
                self.assertEqual(self.twice(change)[0], 2)

    def test_reading_does_not_revalidate(self):
        self.assertEqual(self.twice("get_bd_cells -hier; report_bd_diffs; write_bd_tcl x; save_bd_design")[0], 1)

    def test_failures_are_never_reused(self):
        count, output = run_validate(
            "set ::vresult bad\nputs A=[ipiasm::validate]\nputs B=[ipiasm::validate]\n")
        self.assertEqual(count, 2)
        self.assertIn("A=VALIDATE:FAIL", output)
        self.assertIn("B=VALIDATE:FAIL", output)

    def test_a_failure_clears_the_recorded_pass(self):
        count, output = run_validate("ipiasm::validate\nset ::vresult bad\nset ::cells {/a}\nipiasm::validate\n"
                                     "set ::vresult ok\nset ::cells {/a /b}\nputs C=[ipiasm::validate]\n")
        self.assertEqual(count, 3)
        self.assertIn("C=VALIDATE:PASS", output)

    def test_environment_switch_disables_reuse(self):
        self.assertEqual(self.twice(env={"IPIASM_NO_VERIFY_CACHE": "1"})[0], 2)


if __name__ == "__main__":
    unittest.main()
