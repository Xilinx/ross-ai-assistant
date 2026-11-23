# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An undriven clock is reported once its driver is knowable, and never before."""

from pathlib import Path
import subprocess
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]

# A NoC whose aclk0 lists two ports and whose aclk1 lists none yet, a peripheral whose
# lite clock follows its AXI-Lite slave, and a reset block whose clock carries no
# interface. No clock has a driver; $::wired lists the interface pins that have a net.
FIXTURE = r'''
set ::wired {}
set ::cells {/noc /periph}
set ::clocks {
    /noc {/noc/aclk0 /noc/aclk1}
    /periph {/periph/s_axi_lite_aclk}
    /rst {/rst/slowest_sync_clk}
}
set ::intfs {
    /noc {/noc/S00_AXI /noc/S01_AXI /noc/M00_AXI}
    /periph {/periph/S_AXI_LITE}
    /rst {}
}
set ::busif {
    /noc/aclk0 S00_AXI:M00_AXI
    /periph/s_axi_lite_aclk S_AXI_LITE
}
proc get_bd_cells {args} {
    if {"-of_objects" in $args} { return /[lindex [split [lindex $args end] /] 1] }
    if {[string match *axi_noc* $args]} { return [lsearch -all -inline $::cells /noc] }
    return $::cells
}
proc get_bd_pins {args} {
    if {"-of_objects" in $args} { return [dict get $::clocks [lindex $args [expr {[lsearch $args -of_objects] + 1}]]] }
    return [lindex $args end]
}
proc get_bd_intf_pins {args} {
    # A cell's interfaces; a scalar pin outside any interface has none, as in Vivado.
    if {"-of_objects" in $args} {
        set of [lindex $args end]
        return [expr {[dict exists $::intfs $of] ? [dict get $::intfs $of] : {}}]
    }
    set want [lindex $args end]
    foreach {cell pins} $::intfs { if {$want in $pins} { return $want } }
    return {}
}
proc get_bd_intf_nets {args} {
    return [expr {[lindex $args end] in $::wired ? "net:[lindex $args end]" : ""}]
}
proc get_bd_nets {args} { return {} }
proc find_bd_objs {args} { return {} }
proc get_property {args} {
    set name [lindex $args end-1]; set object [lindex $args end]
    switch -- $name {
        CLASS { return bd_pin }
        DIR { return I }
        CONFIG.ASSOCIATED_BUSIF { return [expr {[dict exists $::busif $object] ? [dict get $::busif $object] : ""}] }
    }
    return {}
}
'''


def run(body, stage="clocks"):
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
        set ::ipiasm::stage_result [dict create stage {{{stage}}}]
    """) + FIXTURE + textwrap.dedent(body)
    result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=60)
    return result.stdout + result.stderr


ALL_WIRED = "set ::wired {/noc/S00_AXI /noc/S01_AXI /noc/M00_AXI /periph/S_AXI_LITE}\n"


class UndrivenClockDeferral(unittest.TestCase):
    def test_clocks_whose_interfaces_are_unwired_are_deferred_and_listed(self):
        out = run("puts [ipiasm::check_undriven_clocks]")
        self.assertIn("UNDRIVEN_CLK:OK 0/3 clock input(s) driven, 3 deferred", out)
        for pin in ("noc/aclk0", "noc/aclk1", "periph/s_axi_lite_aclk"):
            self.assertIn(pin, out)
        self.assertIn("attach_noc_port_clocks", out)

    def test_a_clock_that_carries_no_interface_is_never_deferred(self):
        out = run("lappend ::cells /rst\nputs [ipiasm::check_undriven_clocks]")
        self.assertIn("UNDRIVEN_CLK_FAIL:1/4", out)
        self.assertIn("rst/slowest_sync_clk carries no interface", out)
        self.assertNotIn("noc/aclk1", out)

    def test_once_wired_an_undriven_clock_fails(self):
        out = run(ALL_WIRED + "puts [ipiasm::check_undriven_clocks]")
        self.assertIn("UNDRIVEN_CLK_FAIL:3/3", out)
        for pin in ("noc/aclk0", "noc/aclk1", "periph/s_axi_lite_aclk"):
            self.assertIn(pin, out)

    def test_one_wired_interface_ends_the_deferral_of_its_clock(self):
        # aclk0 lists S00_AXI, now wired; aclk1 still waits for the unwired S01_AXI.
        out = run("set ::wired {/noc/S00_AXI}\nputs [ipiasm::check_undriven_clocks]")
        self.assertIn("UNDRIVEN_CLK_FAIL:1/3", out)
        self.assertIn("noc/aclk0 carries", out)
        self.assertNotIn("noc/aclk1", out)

    def test_an_unassociated_clock_on_a_cell_that_is_not_a_noc_is_not_deferred(self):
        out = run("dict set ::busif /periph/s_axi_lite_aclk {}\n"
                  "dict set ::clocks /periph {/periph/aclk_extra}\nputs [ipiasm::check_undriven_clocks]")
        self.assertIn("UNDRIVEN_CLK_FAIL:1/3", out)
        self.assertIn("periph/aclk_extra carries no interface", out)

    def test_from_the_connect_stage_on_an_unwired_interface_defers_nothing(self):
        # What connect leaves unwired stays unwired, so its clock is undriven for good;
        # the same holds at every later checkpoint, and when no stage is known at all.
        for stage in ("connect", "plan_qos", "validate", ""):
            with self.subTest(stage=stage):
                out = run("puts [ipiasm::check_undriven_clocks]", stage=stage)
                self.assertIn("UNDRIVEN_CLK_FAIL:3/3", out)
                self.assertNotIn("deferred", out)

    def test_a_manual_run_takes_its_stage_from_ipiasm_stage(self):
        out = run("set ::ipiasm::stage_result {}\nset ::ipiasm::stage_t {name clocks t 0}\n"
                  "puts [ipiasm::check_undriven_clocks]", stage="")
        self.assertIn("3 deferred", out)


if __name__ == "__main__":
    unittest.main()
