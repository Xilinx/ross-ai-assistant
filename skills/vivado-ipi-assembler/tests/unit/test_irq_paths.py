# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]

# A mock block design: pins carry CLASS/DIR/LEFT/RIGHT, cells VLNV/TYPE/CONFIG,
# and ::peers holds each pin's connected_to set (edits in a test rewire it).
FIXTURE = r'''
proc _pin {name dir {width 1}} {
    dict set ::pins $name [dict create CLASS bd_pin DIR $dir LEFT [expr {$width - 1}] RIGHT 0]
}
proc _link {a b} {
    dict lappend ::peers $a $b
    dict lappend ::peers $b $a
}
proc _unlink {a b} {
    foreach {x y} [list $a $b $b $a] {
        if {[dict exists $::peers $x]} { dict set ::peers $x [lsearch -all -inline -not -exact [dict get $::peers $x] $y] }
    }
}
set pins {}; set peers {}
set cells {
    /ps      {VLNV x:ip:ps:1.0 TYPE ip}
    /intc    {VLNV x:ip:intc:1.0 TYPE ip}
    /concat  {VLNV xilinx.com:ip:xlconcat:2.1 TYPE ip CONFIG.NUM_PORTS 3 CONFIG.IN0_WIDTH 1 CONFIG.IN1_WIDTH 1 CONFIG.IN2_WIDTH 1}
    /zero    {VLNV xilinx.com:ip:xlconstant:1.1 TYPE ip CONFIG.CONST_VAL 0}
    /i2c1    {VLNV x:ip:iic:1.0 TYPE ip}
    /i2c2    {VLNV x:ip:iic:1.0 TYPE ip}
    /i2c0    {VLNV x:ip:iic:1.0 TYPE ip}
    /cam     {VLNV x:ip:rx:1.0 TYPE hier}
    /cam/rx  {VLNV x:ip:rx:1.0 TYPE ip}
}
foreach p {irq18 irq19 irq20 irq21} { _pin /ps/pl_$p I }
_pin /intc/intr I 3
_pin /intc/irq O
_pin /ps/pl_fpd_irq0 I
foreach k {0 1 2} { _pin /concat/In$k I }
_pin /concat/dout O 3
_pin /zero/dout O
foreach c {i2c1 i2c2 i2c0} { _pin /$c/iic2intc_irpt O }
_pin /cam/rx/irq O
_pin /cam/irq O
_pin /uart/interrupt O
dict set pins /irq_out [dict create CLASS bd_port DIR O NAME irq_out]
_link /i2c1/iic2intc_irpt /ps/pl_irq18
_link /i2c2/iic2intc_irpt /ps/pl_irq19
_link /i2c0/iic2intc_irpt /ps/pl_irq20
_link /zero/dout /ps/pl_irq21
_link /zero/dout /concat/In0
# Native: a hierarchy pin reports only outside peers; the inner net holds its driver.
set nets {/cam/rx_irq {/cam/rx/irq /cam/irq}}
_link /cam/irq /concat/In1
dict lappend peers /concat/In1 /cam/rx/irq
_link /zero/dout /concat/In2
_link /concat/dout /intc/intr
_link /intc/irq /ps/pl_fpd_irq0
_link /uart/interrupt /irq_out

proc _cell_of {pin} { return [expr {[dict exists $::pins $pin CLASS] && [dict get $::pins $pin CLASS] eq "bd_port" ? "" : [file dirname $pin]}] }
proc get_property {args} {
    set key [lindex $args end-1]; set obj [lindex $args end]
    if {[dict exists $::pins $obj $key]} { return [dict get $::pins $obj $key] }
    if {[dict exists $::cells $obj $key]} { return [dict get $::cells $obj $key] }
    return {}
}
proc get_bd_cells {args} {
    if {[lsearch $args -of_objects] >= 0} { return [_cell_of [lindex $args end]] }
    if {[lsearch $args -hier] >= 0} {
        return [lmap c [dict keys $::cells] {expr {[string match *concat* [dict get $::cells $c VLNV]] ? $c : [continue]}}]
    }
    set name "/[string trimleft [lindex $args end] /]"
    return [expr {[dict exists $::cells $name] ? $name : ""}]
}
proc get_bd_pins {args} {
    if {[lsearch $args -of_objects] >= 0} { return [dict get $::nets [lindex $args end]] }
    set name "/[string trimleft [lindex $args end] /]"
    return [expr {[dict exists $::pins $name] ? $name : ""}]
}
proc get_bd_ports {args} {
    set name "/[string trimleft [lindex $args end] /]"
    return [expr {[dict exists $::pins $name CLASS] && [dict get $::pins $name CLASS] eq "bd_port" ? $name : ""}]
}
proc get_bd_nets {args} {
    set pin [lindex $args end]
    if {[lsearch $args -of_objects] < 0} {
        return [lmap net [dict keys $::nets] {expr {[string match "/$pin" $net] ? $net : [continue]}}]
    }
    return [expr {[dict exists $::peers $pin] && [llength [dict get $::peers $pin]] ? "net" : ""}]
}
proc find_bd_objs {args} {
    set pin [lindex $args end]
    return [expr {[dict exists $::peers $pin] ? [dict get $::peers $pin] : {}}]
}
set table {
    {ps/pl_irq18 i2c1/iic2intc_irpt camera_i2c.tile1_irq}
    {ps/pl_irq19 i2c2/iic2intc_irpt camera_i2c.tile2_irq}
    {ps/pl_irq20 i2c0/iic2intc_irpt camera_i2c.tile0_irq}
    {ps/pl_irq21 CONST:0}
    {intc/intr[0] CONST:0}
    {intc/intr[1] cam/irq camera_receiver.irq}
    {intc/intr[2] CONST:0}
    {ps/pl_fpd_irq0 intc/irq}
    {irq_out uart/interrupt uart.irq}
}
'''


def run_tcl(body):
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
    """) + FIXTURE + "if {[catch {\n" + textwrap.dedent(body) + "\n} message]} {puts \"TCL_ERROR $message\"}\n"
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as handle:
        handle.write(script)
        path = handle.name
    result = subprocess.run(["tclsh", path], capture_output=True, text=True, timeout=60)
    Path(path).unlink()
    return result


class IrqPathTests(unittest.TestCase):
    def check(self, edits=""):
        result = run_tcl(edits + "\nputs [ipiasm::check_irq_paths $table]")
        self.assertNotIn("TCL_ERROR", result.stdout, result.stdout + result.stderr)
        return result.stdout

    def test_correct_wiring_passes_and_names_what_it_covers(self):
        out = self.check()
        self.assertIn("IRQ_PATHS:OK n=9", out)
        self.assertIn("covered={camera_i2c.tile1_irq camera_i2c.tile2_irq camera_i2c.tile0_irq camera_receiver.irq uart.irq}", out)

    def test_top_level_output_port_sink_is_traced(self):
        out = self.check("_unlink /uart/interrupt /irq_out")
        self.assertIn("irq_out: want uart/interrupt got UNCONNECTED (uart.irq)", out)

    def test_missing_concat_to_controller_link_fails_and_names_the_concat(self):
        out = self.check("_unlink /concat/dout /intc/intr")
        self.assertIn("IRQ_PATHS_FAIL:3/9", out)
        self.assertIn("intc/intr[1]: want cam/irq got UNCONNECTED (camera_receiver.irq)", out)
        self.assertIn("concat output(s) drive nothing: {concat}", out)

    def test_swapped_order_fails_and_says_where_each_source_belongs(self):
        out = self.check("""
            _unlink /i2c1/iic2intc_irpt /ps/pl_irq18
            _unlink /i2c0/iic2intc_irpt /ps/pl_irq20
            _link /i2c0/iic2intc_irpt /ps/pl_irq18
            _link /i2c1/iic2intc_irpt /ps/pl_irq20
        """)
        self.assertIn("IRQ_PATHS_FAIL:2/9", out)
        self.assertIn("ps/pl_irq18: want i2c1/iic2intc_irpt got i2c0/iic2intc_irpt", out)
        self.assertIn("i2c0/iic2intc_irpt belongs at ps/pl_irq20, found at ps/pl_irq18", out)
        self.assertNotIn("covered={camera_i2c", out)

    def test_source_replaced_by_constant_is_reported(self):
        out = self.check("""
            _unlink /cam/irq /concat/In1
            _unlink /cam/rx/irq /concat/In1
            _link /zero/dout /concat/In1
        """)
        self.assertIn("intc/intr[1]: want cam/irq got CONST:0 (camera_receiver.irq) -- tied to a constant", out)

    def test_floating_reserved_slot_outside_the_table_fails(self):
        result = run_tcl("""
            _unlink /zero/dout /concat/In2
            set partial [lsearch -all -inline -not -glob $table {intc/intr\\[2\\]*}]
            puts [ipiasm::check_irq_paths $partial]
            puts [ipiasm::check_irq_paths [linsert $partial end {intc/intr[2] OPEN}]]
        """)
        out = result.stdout
        self.assertNotIn("TCL_ERROR", out, out + result.stderr)
        self.assertIn("IRQ_PATHS_FAIL:1 floating interrupt input(s) not in the contract\n  concat/In2", out)
        self.assertIn("IRQ_PATHS:OK n=9", out)

    def test_bad_table_and_missing_pins_are_refused(self):
        out = self.check("""
            puts "EMPTY [ipiasm::check_irq_paths {}]"
            puts "DUP [ipiasm::check_irq_paths {{ps/pl_irq18 CONST:0} {ps/pl_irq18 CONST:0}}]"
            puts "NOPIN [ipiasm::check_irq_paths {{ps/pl_irq99 CONST:0}}]"
            puts "NOSRC [ipiasm::check_irq_paths {{ps/pl_irq18 nosuch/irq}}]"
        """)
        self.assertIn("EMPTY IRQ_PATHS_FAIL:no interrupt slots given", out)
        self.assertIn("DUP IRQ_PATHS_FAIL:sink ps/pl_irq18 listed twice", out)
        self.assertIn("ps/pl_irq99: no such pin", out)
        self.assertIn("ps/pl_irq18: expected source nosuch/irq does not exist", out)

    def test_registered_contract_is_rechecked_and_cannot_be_rewritten(self):
        result = run_tcl("""
            proc ipiasm::context_identity {} {return {/p project design}}
            puts "NA [ipiasm::check_irq_contracts]"
            puts "SET [ipiasm::set_irq_contracts $table]"
            puts "SAME [ipiasm::set_irq_contracts $table]"
            puts "REWRITE [ipiasm::set_irq_contracts [lrange $table 0 2]]"
            _unlink /concat/dout /intc/intr
            puts "CHECK [ipiasm::check_irq_contracts]"
        """)
        out = result.stdout
        self.assertIn("NA IRQ_CONTRACTS:NA not registered", out)
        self.assertIn("SET IRQ_CONTRACTS:SET slots=9", out)
        self.assertIn("SAME IRQ_CONTRACTS:SET slots=9", out)
        self.assertIn("REWRITE IRQ_CONTRACT_FAIL:contracts already registered", out)
        self.assertIn("CHECK IRQ_PATHS_FAIL:3/9", out)


if __name__ == "__main__":
    unittest.main()
