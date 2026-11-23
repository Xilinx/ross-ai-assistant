# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""ipiasm::api must name only real helpers and every gate token, in order."""

from pathlib import Path
import re
import subprocess
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]
SKILL = SKILLS / "vivado-ipi-assembler"


class ApiIndexTests(unittest.TestCase):
    def test_steps_name_real_helpers_and_the_gate_chain(self):
        script = textwrap.dedent(f"""
            source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
            source {{{SKILL / 'lib/ipiasm.tcl'}}}
            puts "PROCS [lsort [info procs ::ipiasm::*]]"
            puts "IPCFG [lsort [info procs ::ipcfg::*]]"
            puts [ipiasm::api]
        """)
        result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=60)
        output = result.stdout
        procs = {name.split("::")[-1] for name in re.search(r"^PROCS (.*)$", output, re.M).group(1).split()}
        steps = output.split("STEPS", 1)[1]
        body = "\n".join(line.split("->")[-1] for line in steps.splitlines()[1:])
        # A step may name a configurator helper, written in full as ipcfg::<name>.
        ipcfg = {name.split("::")[-1] for name in re.search(r"^IPCFG (.*)$", output, re.M).group(1).split()}
        qualified = set(re.findall(r"\bipcfg::(\w+)", body))
        self.assertEqual(qualified - ipcfg, set(), "ipiasm::api names ipcfg helpers that do not exist")
        body = re.sub(r"\bipcfg::\w+", "", body)
        named = set(re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", body))
        self.assertTrue(named, output + result.stderr)
        self.assertEqual(named - procs, set(), "ipiasm::api names helpers that do not exist")
        chain = re.search(r"`(SPEC_OK -> .*? -> DELIVERED)`", (SKILL / "SKILL.md").read_text()).group(1)
        self.assertEqual(re.findall(r"-> ([A-Z_]+)", steps), chain.split(" -> "))


if __name__ == "__main__":
    unittest.main()
