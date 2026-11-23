# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""ipiasm::create_port makes exactly the spec-named scalar port, safely re-runnable."""

from pathlib import Path
import subprocess
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]

FIXTURE = r'''
set ::ports {}
set ::nets {}
set ::created 0
proc get_bd_ports {args} {set n [lindex $args end]; expr {[dict exists $::ports $n] ? "/$n" : ""}}
proc create_bd_port {args} {
    incr ::created
    set ::options $args
    set dir [lindex $args [expr {[lsearch $args -dir] + 1}]]
    dict set ::ports [lindex $args end] $dir
    return /[lindex $args end]
}
set ::widths {/irq/out {} /ps/gpio_o {31 0}}
proc get_property {args} {
    set name [lindex $args end-1]; set object [lindex $args end]
    if {$name in {LEFT RIGHT}} { return [lindex [dict get $::widths $object] [expr {$name eq "RIGHT"}]] }
    dict get $::ports [string trimleft $object /]
}
proc set_property {args} {set ::props $args}
proc get_bd_pins {args} {set p [lindex $args end]; expr {$p in {irq/out ps/gpio_o} ? "/$p" : ""}}
proc get_bd_nets {args} {set o [lindex $args end]; expr {[dict exists $::nets $o] ? [dict get $::nets $o] : ""}}
proc connect_bd_net {a b} {dict set ::nets $a net0; dict set ::nets $b net0}
'''


def run(body):
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
    """) + FIXTURE + textwrap.dedent(body)
    result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=60)
    return result.stdout + result.stderr


class CreatePortTests(unittest.TestCase):
    def test_creates_the_exact_name_and_wires_it_once(self):
        out = run("""
            puts A=[ipiasm::create_port pl_fpd_irq1 O intr irq/out]
            puts B=[ipiasm::create_port pl_fpd_irq1 O intr irq/out]
            puts C=[ipiasm::create_port clk_250 O clk {} {CONFIG.FREQ_HZ 250000000}]
            puts "created=$::created props=$::props"
        """)
        self.assertIn("A=PORT:OK name=pl_fpd_irq1 dir=O pin=irq/out\n", out)
        self.assertIn("B=PORT:OK name=pl_fpd_irq1 dir=O pin=irq/out (already connected)", out)
        self.assertIn("C=PORT:OK name=clk_250 dir=O\n", out)
        self.assertIn("created=2 props=-dict {CONFIG.FREQ_HZ 250000000} /clk_250", out)

    def test_a_new_port_takes_the_width_of_its_pin(self):
        out = run("""
            ipiasm::create_port gpio_o_0 O {} ps/gpio_o
            puts "vector=$::options"
            ipiasm::create_port irq9 O intr irq/out
            puts "scalar=$::options"
        """)
        self.assertIn("vector=-dir O -from 31 -to 0 gpio_o_0", out)
        self.assertIn("scalar=-dir O -type intr irq9", out)

    def test_refuses_what_it_cannot_do_exactly(self):
        out = run("""
            ipiasm::create_port resetn_300 O rst
            puts A=[ipiasm::create_port resetn_300 I rst]
            puts B=[ipiasm::create_port x Z]
            puts C=[ipiasm::create_port x O vector]
            puts D=[ipiasm::create_port x O clk {} {CONFIG.FREQ_HZ}]
            puts E=[ipiasm::create_port y O intr missing/pin]
        """)
        self.assertIn("A=PORT_FAIL:resetn_300 exists with direction O, not I", out)
        self.assertIn("B=PORT_FAIL:x direction 'Z'", out)
        self.assertIn("C=PORT_FAIL:x type 'vector'", out)
        self.assertIn("D=PORT_FAIL:x properties must be a flat key/value list", out)
        self.assertIn("E=PORT_FAIL:y no pin missing/pin", out)


if __name__ == "__main__":
    unittest.main()
