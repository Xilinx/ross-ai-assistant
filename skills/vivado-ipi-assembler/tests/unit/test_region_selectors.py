# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]

# Synthetic parameter-dependency dump in the layout ::debug::dump_param_deps writes.
DUMP = """PARAM: 52
Parameter Name:   MC_CHAN_REGION0
Range List:       NONE NONE
                  REGION_LOW REGION LOW (0x0000 0000 0000 to 0x0000 7FFF FFFF; 2GB)
                  REGION_MID REGION MID (0x0008 0000 0000 to 0x000F FFFF FFFF; 32GB)
Type:             string
**********************************************************
PARAM: 53
Parameter Name:   MC_CHAN_REGION1
Range List:       NONE NONE
                  REGION_LOW REGION LOW (0x0000 0000 0000 to 0x0000 7FFF FFFF; 2GB)
                  REGION_MID REGION MID (0x0008 0000 0000 to 0x000F FFFF FFFF; 32GB)
                  REGION_WIDE REGION WIDE (0x0000 0000 0000 to 0x00FF FFFF FFFF; 1024GB)
Type:             string
**********************************************************
PARAM: 54
Parameter Name:   NUM_MC
Range List:       0 1 2 4
"""


def run_tcl(body):
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
        if {{[catch {{
    """) + textwrap.dedent(body) + "\n} message]} {puts \"TCL_ERROR $message\"}\n"
    with tempfile.NamedTemporaryFile("w", suffix=".tcl", delete=False) as handle:
        handle.write(script)
        path = handle.name
    result = subprocess.run(["tclsh", path], capture_output=True, text=True, timeout=60)
    Path(path).unlink()
    return result


class RegionSelectorTests(unittest.TestCase):
    def ranges(self):
        return f"set ranges [ipiasm::_region_ranges_from_dump {{{DUMP}}}]\n"

    def test_dump_parses_every_selector_with_its_range(self):
        result = run_tcl(self.ranges() + """
            puts "KEYS [dict keys $ranges]"
            puts "R1 [dict keys [dict get $ranges CONFIG.MC_CHAN_REGION1]]"
            puts "MID [dict get $ranges CONFIG.MC_CHAN_REGION1 REGION_MID]"
        """)
        self.assertNotIn("TCL_ERROR", result.stdout, result.stdout + result.stderr)
        self.assertIn("KEYS CONFIG.MC_CHAN_REGION0 CONFIG.MC_CHAN_REGION1", result.stdout)
        self.assertIn("R1 REGION_LOW REGION_MID REGION_WIDE", result.stdout)
        self.assertIn(f"MID {0x800000000} {0xFFFFFFFFF}", result.stdout)

    def test_regions_map_to_smallest_containing_range_in_base_order(self):
        result = run_tcl(self.ranges() + """
            set spec {{name high base 0x800000000 size 0x380000000} {name low base 0x0 size 0x80000000}}
            puts "PICK [ipiasm::_pick_region_selectors $ranges [ipiasm::_region_dicts $spec]]"
        """)
        self.assertIn("PICK CONFIG.MC_CHAN_REGION0 REGION_LOW CONFIG.MC_CHAN_REGION1 REGION_MID", result.stdout,
                      result.stdout + result.stderr)

    def test_unservable_and_too_many_regions_are_reported(self):
        result = run_tcl(self.ranges() + """
            catch {ipiasm::_pick_region_selectors $ranges [ipiasm::_region_dicts {{name odd base 0x7000000000 size 0x1000}}]} e1
            puts "E1 $e1"
            catch {ipiasm::_pick_region_selectors $ranges [ipiasm::_region_dicts {{name a base 0 size 1} {name b base 0x800000000 size 1} {name c base 0x900000000 size 1}}]} e2
            puts "E2 $e2"
        """)
        self.assertIn("E1 region odd 0x7000000000+0x1000 fits no CONFIG.MC_CHAN_REGION0 range", result.stdout)
        self.assertIn("E2 3 region(s) but the memory NoC has only 2 selector(s)", result.stdout)

    def test_sizes_only_form_is_not_region_dicts(self):
        result = run_tcl('puts "FORM <[ipiasm::_region_dicts {low 0x80000000 high 0x380000000}]>"')
        self.assertIn("FORM <>", result.stdout)

    def test_missing_segment_hint_separates_routing_from_selectors(self):
        # No DRAM segment is published. With the selectors already right the hint
        # must point at MC routing; with a wrong selector it must name the rebuild.
        result = run_tcl(self.ranges() + """
            proc get_bd_cells {args} {return /mem}
            proc get_bd_intf_pins {args} {return {}}
            proc get_bd_addr_segs {args} {return {}}
            proc list_property {args} {return {CONFIG.MC_CHAN_REGION0 CONFIG.MC_CHAN_REGION1}}
            proc get_property {args} {
                set key [lindex $args end-1]
                if {$key eq "CONFIG.NUM_MC"} {return 1}
                if {$key eq "VLNV"} {return fixture:ip:axi_noc:1.0}
                return [dict get $::current $key]
            }
            proc ipiasm::memory_region_ranges {noc} {return $::ranges}
            proc ipiasm::_all_noc_cells {} {return /mem}
            set spec {{name low base 0x0 size 0x80000000}}
            set current {CONFIG.MC_CHAN_REGION0 REGION_LOW CONFIG.MC_CHAN_REGION1 NONE}
            puts "RIGHT [ipiasm::check_memory_regions $spec]"
            set current {CONFIG.MC_CHAN_REGION0 REGION_MID CONFIG.MC_CHAN_REGION1 NONE}
            puts "WRONG [ipiasm::check_memory_regions $spec]"
        """)
        out = result.stdout
        self.assertNotIn("TCL_ERROR", out, out + result.stderr)
        right = out.split("RIGHT", 1)[1].split("WRONG", 1)[0]
        wrong = out.split("WRONG", 1)[1]
        self.assertIn("selectors are already correct", right)
        self.assertIn("routing gap", right)
        self.assertIn("ipiasm::add_noc_si mem MC_<n>", right)
        self.assertNotIn("rebuild its board bind", right)
        self.assertIn("rebuild its board bind", wrong)
        self.assertNotIn("already correct", wrong)


class QosSelectorGuardTests(unittest.TestCase):
    """apply_qos_plan must notice a selector the connectivity write reset.

    A CONFIG.CONNECTIONS write can return an MC_CHAN_REGION* selector to its IP
    default. The design still validates and the window still exists, so nothing
    fails until a master reads an address the channel no longer serves.
    """

    STUBS = """
        proc get_bd_addr_spaces {args} {return {}}
        proc get_bd_addr_segs {args} {return {}}
        proc get_bd_cells {args} {return /mem}
        proc list_property {args} {return {CONFIG.MC_CHAN_REGION0 CONFIG.MC_CHAN_REGION1}}
        proc get_property {args} {
            set key [lindex $args end-1]
            if {$key eq "VLNV"} {return fixture:ip:axi_noc:1.0}
            return [dict get $::current $key]
        }
        set ::current {CONFIG.MC_CHAN_REGION0 REGION_LOW CONFIG.MC_CHAN_REGION1 REGION_MID}
    """

    def test_reset_selector_fails_the_plan_and_names_the_change(self):
        result = run_tcl(self.STUBS + """
            proc ipiasm::set_qos {args} {
                set ::current {CONFIG.MC_CHAN_REGION0 REGION_LOW CONFIG.MC_CHAN_REGION1 NONE}
                return "SUCCESS:qos"
            }
            puts "OUT [ipiasm::apply_qos_plan {{/mem/S00_AXI DDR 1000 1000}}]"
        """)
        self.assertNotIn("TCL_ERROR", result.stdout, result.stdout + result.stderr)
        self.assertIn("QOS_FAIL:", result.stdout)
        self.assertIn("changed 1 channel region selector", result.stdout)
        self.assertIn("MC_CHAN_REGION1 REGION_MID -> NONE", result.stdout)
        self.assertNotIn("MC_CHAN_REGION0", result.stdout)
        self.assertIn("ipiasm::check_memory_regions", result.stdout)

    def test_preserved_selectors_leave_a_good_plan_passing(self):
        result = run_tcl(self.STUBS + """
            proc ipiasm::set_qos {args} {return "SUCCESS:qos"}
            puts "OUT [ipiasm::apply_qos_plan {{/mem/S00_AXI DDR 1000 1000}}]"
        """)
        self.assertNotIn("TCL_ERROR", result.stdout, result.stdout + result.stderr)
        self.assertIn("QOS:applied 1/1", result.stdout)
        self.assertNotIn("QOS_FAIL", result.stdout)


if __name__ == "__main__":
    unittest.main()
