# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The wrapper boundary matches the RTL that instantiates it, and exported ports can be renamed to match."""

from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]

# Two board-named memory interfaces, a tristate GPIO interface and one scalar port.
FIXTURE = r'''
set ::intf {mem_c0 mem_c1 GPIO_A}
set ::scalar {mem_c0_dq mem_c0_ck mem_c1_dq mem_c1_ck GPIO_A_tri_i GPIO_A_tri_o GPIO_A_tri_t irq1}
proc current_bd_design {args} {return design_1}
proc get_bd_intf_ports {args} {
    set want [lindex $args end]
    if {$want eq "-quiet"} { return [lmap n $::intf {string cat / $n}] }
    return [expr {$want in $::intf ? "/$want" : ""}]
}
proc get_bd_ports {args} {
    set want [lindex $args end]
    if {$want eq "-quiet"} { return [lmap n [concat $::intf $::scalar] {string cat / $n}] }
    return [expr {$want in $::scalar ? "/$want" : ""}]
}
proc set_property {name value object} {
    set old [string trimleft $object /]
    # Members first: renaming an interface renames its member ports too.
    foreach var {::scalar ::intf} {
        set $var [lmap n [set $var] {expr {$n eq $old ? $value
            : ([string match "${old}_*" $n] && $var eq "::scalar" && $old in $::intf) ? "$value[string range $n [string length $old] end]" : $n}}]
    }
}
'''

RTL = """
module top(input clk);
  // design_1_wrapper old_inst (.unused(x));
  design_1_wrapper design_1_wrapper_inst
       (.BANK0_dq(a), .BANK0_ck(b), .BANK1_dq(c), .BANK1_ck(d),
        .GPIO_A_tri_io(e), .irq1(f));
endmodule
"""


def run(body, rtl=RTL):
    with tempfile.TemporaryDirectory() as root:
        path = Path(root, "top.sv")
        path.write_text(rtl)
        script = textwrap.dedent(f"""
            source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
            source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
            set ::rtl {{{path}}}
        """) + FIXTURE + textwrap.dedent(body)
        result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=60)
    return result.stdout + result.stderr


class WrapperBoundaryTests(unittest.TestCase):
    def test_board_names_are_reported_with_the_rename_that_fixes_each(self):
        out = run("puts [ipiasm::check_wrapper_boundary $::rtl]")
        self.assertIn("BOUNDARY_FAIL:4 of 6 port(s)", out)
        self.assertIn("missing: BANK0_ck BANK0_dq BANK1_ck BANK1_dq", out)
        self.assertIn("renames (ipiasm::name_port): mem_c0 -> BANK0; mem_c1 -> BANK1", out)

    def test_renaming_as_suggested_makes_the_boundary_match(self):
        out = run("""
            puts [ipiasm::name_port mem_c0 BANK0]
            puts [ipiasm::name_port mem_c1 BANK1]
            puts [ipiasm::name_port mem_c1 BANK1]
            puts [ipiasm::check_wrapper_boundary $::rtl]
        """)
        self.assertIn("PORT_NAME:OK mem_c0 -> BANK0", out)
        self.assertIn("PORT_NAME:OK BANK1 (already named)", out)
        self.assertIn("BOUNDARY:OK ports=6", out)

    def test_rename_refuses_ambiguity_and_unknown_ports(self):
        out = run("puts [ipiasm::name_port mem_c0 mem_c1]; puts [ipiasm::name_port nope other]")
        self.assertIn("PORT_NAME_FAIL:both mem_c0 and mem_c1 exist", out)
        self.assertIn("PORT_NAME_FAIL:no port nope or other", out)

    def test_both_tristate_forms_count_and_comments_are_ignored(self):
        rtl = "design_1_wrapper u (.GPIO_A_tri_i(a), .GPIO_A_tri_o(b), .GPIO_A_tri_t(c), .irq1(d));"
        self.assertIn("BOUNDARY:OK ports=4", run("puts [ipiasm::check_wrapper_boundary $::rtl]", rtl))

    def test_connections_of_the_next_instance_are_not_counted(self):
        rtl = ("design_1_wrapper u (.irq1(a), .GPIO_A_tri_io(b), .BANK0_dq(f(c)));\n"
               "user_logic #(.W(8)) v (.not_a_wrapper_port(d), .other(e));\n")
        out = run("puts [ipiasm::check_wrapper_boundary $::rtl]", rtl)
        self.assertIn("BOUNDARY_FAIL:1 of 3 port(s)", out)
        self.assertIn("missing: BANK0_dq", out)
        self.assertNotIn("not_a_wrapper_port", out)

    def test_file_without_the_wrapper_is_not_applicable(self):
        out = run("puts [ipiasm::check_wrapper_boundary $::rtl]", "module other(); endmodule\n// design_1_wrapper x (.a(b));")
        self.assertIn("BOUNDARY:NA no instance of design_1_wrapper", out)


if __name__ == "__main__":
    unittest.main()
