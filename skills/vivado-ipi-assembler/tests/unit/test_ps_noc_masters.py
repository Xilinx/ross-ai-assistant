# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Coherent PS NoC ports are interleaved: used at all, they must all reach one NoC the same way."""

from pathlib import Path
import subprocess
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]

# Three coherent ports, one non-coherent (ps_rpu) port that must be ignored.
FIXTURE = r'''
set ::props {
    /ps         {VLNV xilinx.com:ip:ps_wizard:1.0}
    /ps/CCI0    {NAME CCI0 CONFIG.MY_CATEGORY ps_cci}
    /ps/CCI1    {NAME CCI1 CONFIG.MY_CATEGORY ps_cci}
    /ps/CCI2    {NAME CCI2 CONFIG.MY_CATEGORY ps_cci}
    /ps/RPU0    {NAME RPU0 CONFIG.MY_CATEGORY ps_rpu}
    /noc/S00    {CONFIG.CONNECTIONS {MC_0 {read_bw {500}} M00_AXI {read_bw {5}}}}
    /noc/S01    {CONFIG.CONNECTIONS {MC_0 {read_bw {500}} M00_AXI {read_bw {5}}}}
    /noc/S02    {CONFIG.CONNECTIONS {M00_AXI {read_bw {5}} MC_0 {read_bw {500}}}}
    /other/S00  {CONFIG.CONNECTIONS {MC_0 {read_bw {500}} M00_AXI {read_bw {5}}}}
}
set ::links {/ps/CCI0 /noc/S00 /ps/CCI1 /noc/S01 /ps/CCI2 /noc/S02}
set ::cells {/ps}
proc get_bd_cells {args} {
    if {[set i [lsearch $args -of_objects]] >= 0} { return [file dirname [lindex $args $i+1]] }
    return $::cells
}
proc get_bd_intf_pins {args} {return {/ps/CCI0 /ps/CCI1 /ps/CCI2 /ps/RPU0}}
proc get_property {args} {
    set name [lindex $args end-1]; set object [lindex $args end]
    if {[dict exists $::props $object $name]} { return [dict get $::props $object $name] }
    return {}
}
proc find_bd_objs {args} {
    set pin [lindex $args end]
    if {[dict exists $::links $pin]} { return [list $pin [dict get $::links $pin]] }
    return [list $pin]
}
'''


def run(change=""):
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
    """) + FIXTURE + change + "\nputs RESULT=[ipiasm::check_ps_noc_masters]\n"
    result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=60)
    out = result.stdout + result.stderr
    return out[out.index("RESULT="):] if "RESULT=" in out else out


class PsNocMasterTests(unittest.TestCase):
    def test_full_set_on_one_noc_with_the_same_routes_passes(self):
        self.assertIn("RESULT=PS_NOC:OK coherent_ports=3", run())

    def test_partly_connected_set_fails_naming_the_unconnected_ports(self):
        out = run("dict unset ::links /ps/CCI1; dict unset ::links /ps/CCI2")
        self.assertIn("PS_NOC_FAIL", out)
        self.assertIn("coherent NoC ports {CCI1 CCI2} unconnected while {CCI0} are used", out)

    def test_set_split_across_noc_instances_fails(self):
        out = run("dict set ::links /ps/CCI2 /other/S00")
        self.assertIn("reach different NoC instances {/noc /other}", out)

    def test_ports_with_different_routes_fail(self):
        out = run("dict set ::props /noc/S02 CONFIG.CONNECTIONS {MC_0 {read_bw {500}}}")
        self.assertIn("route to different targets", out)

    def test_ports_on_different_ports_of_one_memory_controller_pass(self):
        # PG352's lowest-latency layout: each CCI port on its own DDRMC port (BD 41-3265
        # allows one MC port per NoC interface), all reaching the same targets.
        out = run("dict set ::props /noc/S01 CONFIG.CONNECTIONS {MC_2 {read_bw {500}} M00_AXI {read_bw {5}}}; "
                  "dict set ::props /noc/S02 CONFIG.CONNECTIONS {M00_AXI {read_bw {5}} MC_3 {read_bw {500}}}")
        self.assertIn("RESULT=PS_NOC:OK coherent_ports=3", out)

    def test_pl_target_on_only_some_ports_still_fails(self):
        out = run("dict set ::props /noc/S01 CONFIG.CONNECTIONS {MC_1 {read_bw {500}}}")
        self.assertIn("route to different targets", out)
        self.assertIn("CCI1->{MC}", out)

    def test_unused_set_and_non_coherent_ports_are_left_alone(self):
        self.assertIn("RESULT=PS_NOC:OK coherent_ports=3", run("set ::links {}"))

    def test_no_processing_system_is_not_applicable(self):
        self.assertIn("RESULT=PS_NOC:NA no processing system cell", run("set ::cells {}"))


if __name__ == "__main__":
    unittest.main()
