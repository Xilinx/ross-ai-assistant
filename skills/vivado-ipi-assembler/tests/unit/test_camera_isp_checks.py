# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Camera/ISP checks judge the ISP instance that owns the camera's stream, and never invent geometry."""

from pathlib import Path
import subprocess
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]

# A RAW12 receiver wired into tile 0 input stream $::stream; ISP0 takes the first
# C_TILE0_ISP0_LIVE_INPUTS streams, ISP1 the rest (PG432).
FIXTURE = r'''
set ::stream 0
set ::props {
    /cam {VLNV xilinx.com:ip:mipi_csi2_rx_subsystem:6.0 CMN_PXL_FORMAT RAW12 NAME cam}
    /isp {VLNV xilinx.com:ip:visp_ss:1.0 NAME isp C_TILE0_ISP0_LIVE_INPUTS 4
          C_TILE0_ISP0_BASIC_DATA_FORMAT 12 C_TILE0_ISP1_BASIC_DATA_FORMAT 16
          C_TILE0_ISP0_IBA0_RES_HOR 1920 C_TILE0_ISP0_IBA0_RES_VER 1080}
    /cam/video_out {MODE Master}
}
proc get_bd_cells {args} {
    if {[string match *mipi* $args]} { return /cam }
    if {[string match *visp* $args]} { return /isp }
    set want [lindex $args end]
    return [expr {$want in {/cam /isp} ? $want : ""}]
}
proc get_bd_intf_pins {args} {
    if {[lindex $args end] eq "/cam"} { return /cam/video_out }
    if {[lindex $args end] eq "net0"} { return [list /cam/video_out /isp/TILE0_ISP_MIPI_VIDIN$::stream] }
    return {}
}
proc get_bd_intf_nets {args} {return net0}
proc list_property {args} {
    return [lmap k [dict keys [dict get $::props /isp]] {string cat CONFIG. $k}]
}
proc get_property {args} {
    set name [string map {CONFIG. ""} [lindex $args end-1]]; set object [lindex $args end]
    if {[dict exists $::props $object $name]} { return [dict get $::props $object $name] }
    return {}
}
proc get_board_part_interfaces {args} {return {}}
proc current_board_part {args} {return {}}
'''


def run(body):
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
    """) + FIXTURE + textwrap.dedent(body)
    result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=60)
    return result.stdout + result.stderr


class CameraIspFormatTests(unittest.TestCase):
    def test_only_the_instance_owning_the_stream_must_match(self):
        self.assertIn("FORMAT:OK 1/1", run("puts [ipiasm::check_camera_isp_format]"))

    def test_wrong_format_on_the_owning_instance_fails(self):
        out = run("dict set ::props /isp C_TILE0_ISP0_BASIC_DATA_FORMAT 16; puts [ipiasm::check_camera_isp_format]")
        self.assertIn("FORMAT_FAIL:1/1", out)
        self.assertIn("C_TILE0_ISP0_BASIC_DATA_FORMAT", out)

    def test_a_stream_past_isp0_live_inputs_belongs_to_isp1(self):
        out = run("set ::stream 4; puts [ipiasm::check_camera_isp_format]")
        self.assertIn("FORMAT_FAIL:1/1", out)
        self.assertIn("C_TILE0_ISP1_BASIC_DATA_FORMAT", out)


class CameraGeometryTests(unittest.TestCase):
    def test_no_stated_geometry_is_not_applicable_rather_than_a_failure(self):
        out = run("puts [ipiasm::check_camera_geometry]")
        self.assertIn("GEOMETRY:NA no DesignSpec or bound board geometry to compare", out)
        self.assertNotIn("GEOMETRY_FAIL", out)

    def test_stated_geometry_larger_than_the_tile_still_fails(self):
        self.assertIn("GEOMETRY_FAIL:1/1", run("puts [ipiasm::check_camera_geometry {/cam 3840 2160}]"))
        self.assertIn("GEOMETRY:OK 1/1", run("puts [ipiasm::check_camera_geometry {/cam 1920 1080}]"))


# One ISP; $::sel holds each instance's IIC/GPIO select, $::pins the PL interfaces the IP
# built, and $::wired the ones with a net. PG432 codes: IIC PL=8 Disable=9, GPIO PL=2 Disable=3.
RESOURCE_FIXTURE = r'''
set ::isps /isp
set ::sel {C_TILE0_ISP0_IIC_SELECT 8 C_TILE0_ISP0_GPIO_SELECT 2 C_TILE0_ISP1_IIC_SELECT 9 C_TILE0_ISP1_GPIO_SELECT 3}
set ::pins {/isp/TILE0_ISP0_PL_IIC /isp/TILE0_ISP0_PL_GPIO}
set ::wired {}
proc get_bd_cells {args} { return $::isps }
proc list_property {args} { return [lmap k [dict keys $::sel] {string cat CONFIG. $k}] }
proc get_property {args} {
    set name [string map {CONFIG. ""} [lindex $args end-1]]
    return [expr {[dict exists $::sel $name] ? [dict get $::sel $name] : ""}]
}
proc get_bd_intf_pins {args} { set p [lindex $args end]; expr {$p in $::pins ? $p : ""} }
proc get_bd_intf_nets {args} { expr {[lindex $args end] in $::wired ? "net" : ""} }
'''


class IspResourceTests(unittest.TestCase):
    def check(self, body=""):
        return run(RESOURCE_FIXTURE + body + "\nputs [ipiasm::check_isp_resources]")

    def test_built_but_unwired_cores_fail_with_the_disable_fix(self):
        out = self.check()
        self.assertIn("ISP_RESOURCES_FAIL:2 ISP PL I2C/GPIO core(s) built but unused", out)
        self.assertIn("/isp CONFIG.C_TILE0_ISP0_IIC_SELECT=8 (PL) but TILE0_ISP0_PL_IIC reaches nothing", out)
        self.assertIn("ipcfg::apply_dict /isp {CONFIG.C_TILE0_ISP0_GPIO_SELECT 3 CONFIG.C_TILE0_ISP0_IIC_SELECT 9}", out)

    def test_cores_the_design_connects_pass(self):
        out = self.check("set ::wired $::pins")
        self.assertIn("ISP_RESOURCES:OK 2 PL I2C/GPIO core(s) in use", out)

    def test_disable_and_ps_selections_are_not_cores(self):
        out = self.check("set ::sel {C_TILE0_ISP0_IIC_SELECT 3 C_TILE0_ISP0_GPIO_SELECT 0 C_TILE0_ISP1_IIC_SELECT 9}")
        self.assertIn("ISP_RESOURCES:OK 0 PL I2C/GPIO core(s) in use", out)

    def test_pl_select_without_a_built_interface_is_not_flagged(self):
        out = self.check("set ::pins {}")
        self.assertIn("ISP_RESOURCES:OK 0", out)

    def test_one_unused_core_beside_a_used_one(self):
        out = self.check("set ::wired /isp/TILE0_ISP0_PL_IIC")
        self.assertIn("ISP_RESOURCES_FAIL:1 ", out)
        self.assertIn("ipcfg::apply_dict /isp {CONFIG.C_TILE0_ISP0_GPIO_SELECT 3}", out)

    def test_no_isp_is_not_applicable(self):
        self.assertIn("ISP_RESOURCES:NA no ISP in the design", self.check("set ::isps {}"))


if __name__ == "__main__":
    unittest.main()
