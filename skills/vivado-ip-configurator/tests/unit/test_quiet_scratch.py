# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""lite::scratch: throwaway cells and mirrors are created with -quiet, and a failure still raises.

On Vivado 2026.2 an OpenCode + Gemma trial got a 312 KB describe reply for
ip-configurator-test-kit_21: 44 KB of reply and the rest the console output of throwaway
mipi_csi2_rx_subsystem probe cells (disabled-parameter warnings, `businfo` debug lines).
OpenCode cut the reply off. These tests pin the helper's contract with stub Vivado commands.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_lite import run_tcl

STUBS = r'''
set ::calls {}
set ::made {}
proc create_bd_cell {args} {
    lappend ::calls [expr {"-quiet" in $args ? "quiet" : "loud"}]
    if {[string match *bad* [lindex $args end]]} {
        if {"-quiet" in $args} {return}
        error "ERROR: \[BD 5-390\] IP definition not found"
    }
    lappend ::made [lindex $args end]
}
proc get_bd_cells {args} {if {[lindex $args end] in $::made} {return [lindex $args end]}}
'''


class QuietScratchTests(unittest.TestCase):
    def test_a_created_cell_is_created_once_and_quietly(self):
        result = run_tcl(STUBS + r'''
puts "NAME [lite::scratch cell -type ip -vlnv v:ip:x:1.0 probe_1]"
puts "CALLS $::calls"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NAME probe_1', result.stdout)
        self.assertIn('CALLS quiet', result.stdout)
        self.assertNotIn('loud', result.stdout)

    def test_a_failed_create_is_rerun_loudly_for_vivados_error(self):
        result = run_tcl(STUBS + r'''
puts "RC [catch {lite::scratch cell -type ip -vlnv v:ip:x:1.0 bad_1} e] $e"
puts "CALLS $::calls"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('RC 1 ERROR: [BD 5-390] IP definition not found', result.stdout)
        self.assertIn('CALLS quiet loud', result.stdout)


if __name__ == '__main__':
    unittest.main()
