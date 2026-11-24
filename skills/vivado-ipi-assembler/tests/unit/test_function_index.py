# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""FUNCTIONS.md is the index agents read instead of the source; it must be complete and current."""

from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]
BUILDER = SKILLS / "vivado-ipi-assembler/tests/support/build_function_index.py"


class FunctionIndexTests(unittest.TestCase):
    def test_every_public_helper_says_what_it_does(self):
        script = textwrap.dedent(f"""
            source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
            source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
            set purposes [ipcfg::_help_purposes]
            foreach ns {{::ipcfg ::ipiasm}} {{
                foreach p [info procs ${{ns}}::*] {{
                    if {{[string match _* [namespace tail $p]]}} continue
                    set n [string trimleft $p :]
                    if {{![dict exists $purposes $n] || [dict get $purposes $n] eq ""}} {{puts "MISSING $n"}}
                }}
            }}
        """)
        result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120)
        missing = [line.split()[1] for line in result.stdout.splitlines() if line.startswith("MISSING ")]
        self.assertEqual(missing, [], "add a one-line # comment directly above each proc")

    def test_function_index_is_current(self):
        result = subprocess.run([sys.executable, str(BUILDER), "--check"], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr + f"\nregenerate with: {BUILDER.name}")


if __name__ == "__main__":
    unittest.main()
