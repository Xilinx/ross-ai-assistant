# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from pathlib import Path
import base64
import json
import re
import subprocess
import sys
import textwrap
import tempfile
import unittest

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS / "vivado-ipi-assembler/scripts"))
import compile_assembly_plan as compiler  # noqa: E402


def cell_property(properties, **extra):
    return {"id": "op", "op": "set_property", "object": {"kind": "cell", "path": "cam"},
            "properties": properties, **extra}


def automation(kind="cell", target="ps"):
    return {"id": "op", "op": "apply_automation", "rule": "xilinx.com:bd_rule:test",
            "target_kind": kind, "target": target, "config": {"board_preset": "Yes"}}


def run_body(lines, mocks):
    """Run compiled operation lines against mocked helpers; judge with the executor's checker."""
    script = textwrap.dedent(f"""
        source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
        proc get_bd_pins {{args}} {{return /cam/pin}}
        proc get_bd_intf_pins {{args}} {{return /cam/S_AXI}}
        proc set_property {{args}} {{return {{}}}}
        proc apply_bd_automation {{args}} {{return {{}}}}
    """) + mocks + "\n" + "\n".join(lines) + '\nputs "RESULT [ipcfg::result_status $__result {PROPERTY:OK AUTOMATION:OK}] $__result"\n'
    result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=60)
    return result.stdout + result.stderr


class PlanRoutingTests(unittest.TestCase):
    def test_interface_routes_compiles_with_default_status_and_exact_arity(self):
        edges = [["/producer/M_INI", "/consumer/S_INI"], ["/external", "/fabric/S_AXI"]]
        call = {"id": "routes", "op": "call", "proc": "ipiasm::check_interface_routes",
                "args": [edges]}
        plan = {"schema_version": 1, "stages": [{"name": "connect", "operations": [call]}]}
        self.assertIn(compiler._brace("INTERFACE_ROUTES:OK"), compiler.compile_plan(plan))
        body = "\n".join(compiler._compile_call(call))
        script = f"source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}\n" + f'''
if {{[catch {{
proc get_bd_intf_ports {{quiet path}} {{if {{$path eq "/external"}} {{return /external}}; return {{}}}}
proc get_bd_intf_pins {{quiet path}} {{return [list $path]}}
proc find_bd_objs {{args}} {{return {{/consumer/S_INI /fabric/S_AXI}}}}
{body}
if {{$__result ne "INTERFACE_ROUTES:OK n=2"}} {{error $__result}}
set prefixes {{{' '.join(compiler.DEFAULT_EXPECT)}}}
puts [ipcfg::result_status $__result $prefixes]
puts [ipcfg::result_status {{INTERFACE_ROUTES_FAIL:disconnected}} $prefixes]
}} detail]}} {{puts stderr $detail; exit 1}}
'''
        result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines()[-2:], ["PASS", "FAIL"])
        for args in ([], [edges, "extra"]):
            with self.subTest(args=args), self.assertRaisesRegex(compiler.PlanError, "takes 1..1"):
                compiler.compile_plan({"schema_version": 1, "stages": [
                    {"name": "connect", "operations": [{**call, "args": args}]}]})

    def test_source_files_compiles_with_default_status_and_exact_arity(self):
        call = {"id": "sources", "op": "call", "proc": "ipiasm::check_source_files",
                "args": [{"/fixture/top source.sv": "a" * 64}, "fixture_top"]}
        plan = {"schema_version": 1, "stages": [{"name": "deliver", "operations": [call]}]}
        self.assertIn(compiler._brace("SOURCE_FILES:OK"), compiler.compile_plan(plan))
        script = f"source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}\n" + f'''
set prefixes {{{' '.join(compiler.DEFAULT_EXPECT)}}}
puts [ipcfg::result_status {{SOURCE_FILES:OK 1 file(s) top=fixture_top}} $prefixes]
puts [ipcfg::result_status {{SOURCE_FILES_FAIL:wrong top}} $prefixes]
'''
        result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines()[-2:], ["PASS", "FAIL"])
        for args in ([], [call["args"][0]], [*call["args"], "extra"]):
            with self.subTest(args=args), self.assertRaisesRegex(compiler.PlanError, "takes 2..2"):
                compiler.compile_plan({"schema_version": 1, "stages": [
                    {"name": "deliver", "operations": [{**call, "args": args}]}]})

    def test_cli_rejects_changed_or_missing_bound_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = {"schema_version": 1, "binding_context": {"design_spec_sha256": "0" * 64},
                    "stages": [{"name": "cells", "operations": [
                        {"id": "check", "op": "call", "proc": "ipiasm::check_bram_backing", "args": []}
                    ]}]}
            (root / "plan.json").write_text(json.dumps(plan))
            spec = root / "design_spec.json"
            spec.write_text(json.dumps({"blocks": []}))
            arguments = [str(root / "plan.json"), "--out", str(root / "out.tcl")]
            self.assertEqual(compiler.main(arguments), 1)
            spec.unlink()
            self.assertEqual(compiler.main(arguments), 1)
            self.assertFalse((root / "out.tcl").exists())

    def test_cli_requires_adjacent_spec_coverage_without_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = {"schema_version": 1, "stages": [{"name": "cells", "operations": [
                {"id": "check", "op": "call", "proc": "ipiasm::check_bram_backing", "args": []}
            ]}]}
            (root / "plan.json").write_text(json.dumps(plan))
            (root / "design_spec.json").write_text(json.dumps({
                "blocks": [{"parameters": {"rate": 1500}}]
            }))
            self.assertEqual(compiler.main([str(root / "plan.json"), "--out", str(root / "out.tcl")]), 1)
            self.assertFalse((root / "out.tcl").exists())

    def test_parameter_coverage_rejects_omissions_and_stale_values(self):
        spec = {"blocks": [{"parameters": {"rate": 1500, "firmware": "sensor"}}]}
        plan = {"stages": [{"name": "cells", "operations": [
            {"id": "rate", "op": "call", "proc": "ipiasm::check_cell_config",
             "args": ["camera", {"CONFIG.RATE": 1500}]}
        ]}]}
        with self.assertRaisesRegex(compiler.PlanError, "missing="):
            compiler.check_parameter_coverage(spec, plan)
        plan["parameter_coverage"] = {
            "/blocks/0/parameters/rate": {"value": 1500, "disposition": "hardware",
                                           "reason": "receiver line rate", "checks": ["rate"]},
            "/blocks/0/parameters/firmware": {"value": "sensor", "disposition": "external",
                                               "reason": "sensor firmware owns setup", "owner": "firmware"},
        }
        compiler.check_parameter_coverage(spec, plan)
        plan["parameter_coverage"]["/blocks/0/parameters/rate"]["value"] = 800
        with self.assertRaisesRegex(compiler.PlanError, "stale"):
            compiler.check_parameter_coverage(spec, plan)
        plan["parameter_coverage"]["/blocks/0/parameters/rate"]["value"] = 1500
        plan["parameter_coverage"]["/blocks/0/parameters/rate"]["checks"] = ["missing"]
        with self.assertRaisesRegex(compiler.PlanError, "invalid readback"):
            compiler.check_parameter_coverage(spec, plan)

    def test_parameter_coverage_rejects_a_readback_that_never_expects_the_value(self):
        spec = {"blocks": [{"parameters": {"dphy": {"SupportLevel": 1, "clock_mask": 39}}}]}
        plan = {"stages": [{"name": "cells", "operations": [
            {"id": "camera", "op": "call", "proc": "ipiasm::check_cell_config",
             "args": ["camera", {"CONFIG.SupportLevel": 1}]}
        ]}], "parameter_coverage": {
            "/blocks/0/parameters/dphy/SupportLevel": {"value": 1, "disposition": "hardware",
                                                       "reason": "own dphy clocking", "checks": ["camera"]},
            "/blocks/0/parameters/dphy/clock_mask": {"value": 39, "disposition": "hardware",
                                                     "reason": "dphy clock mask", "checks": ["camera"]},
        }}
        with self.assertRaisesRegex(compiler.PlanError, "clock_mask value 39 is never expected"):
            compiler.check_parameter_coverage(spec, plan)
        plan["stages"][0]["operations"][0]["args"][1]["CONFIG.C_CLOCK_MASK"] = 39
        compiler.check_parameter_coverage(spec, plan)

    def test_parameter_coverage_reports_every_problem_at_once(self):
        spec = {"blocks": [{"parameters": {"a": 1, "b": 2, "c": 3}}]}
        plan = {"stages": [{"name": "cells", "operations": [
            {"id": "cfg", "op": "call", "proc": "ipiasm::check_cell_config", "args": ["x", {"CONFIG.A": 1}]}
        ]}], "parameter_coverage": {
            f"/blocks/0/parameters/{name}": {"value": value, "disposition": "hardware", "reason": "r", "checks": ["cfg"]}
            for name, value in (("a", 1), ("b", 2), ("c", 3))}}
        with self.assertRaises(compiler.PlanError) as raised:
            compiler.check_parameter_coverage(spec, plan)
        message = str(raised.exception)
        self.assertIn("/blocks/0/parameters/b value 2 is never expected", message)
        self.assertIn("/blocks/0/parameters/c value 3 is never expected", message)
        self.assertEqual(message.count("the readback must carry"), 1)

    def test_a_number_written_as_text_is_held_to_the_value_rule(self):
        # A hex address mapped to a capacity readback is a hollow claim, not a check.
        spec = {"blocks": [{"parameters": {"address": "0x80000008000", "depth": "0x800"}}]}
        plan = {"stages": [{"name": "cells", "operations": [
            {"id": "capacity", "op": "call", "proc": "ipiasm::check_cell_config",
             "args": ["mem", {"CONFIG.MEMORY_DEPTH": 2048}]}
        ]}], "parameter_coverage": {
            f"/blocks/0/parameters/{name}": {"value": value, "disposition": "hardware", "reason": "r",
                                             "checks": ["capacity"]}
            for name, value in (("address", "0x80000008000"), ("depth", "0x800"))}}
        with self.assertRaises(compiler.PlanError) as raised:
            compiler.check_parameter_coverage(spec, plan)
        # The address is unasserted; the depth matches 2048 even though it is written in hex.
        self.assertIn("address value '0x80000008000' is never expected", str(raised.exception))
        self.assertNotIn("depth", str(raised.exception))

    def test_asserted_as_names_the_hardware_form_a_readback_must_carry(self):
        # A tile index held as an enable key, and a nominal frequency realized exactly.
        spec = {"blocks": [{"parameters": {"enabled_tiles": [0], "requested_mhz": 74.25}}]}
        plan = {"stages": [{"name": "cells", "operations": [
            {"id": "isp", "op": "call", "proc": "ipiasm::check_cell_config",
             "args": ["isp", {"CONFIG.C_TILE0_ENABLE": True}]},
            {"id": "set_clocks", "op": "call", "proc": "ipiasm::set_clock_contracts", "args": [{"display": 74251394}]},
            {"id": "clocks", "op": "call", "proc": "ipiasm::check_clock_contracts", "args": []},
        ]}], "parameter_coverage": {
            "/blocks/0/parameters/enabled_tiles/0": {
                "value": 0, "disposition": "hardware", "reason": "tile 0 enabled", "checks": ["isp"],
                "asserted_as": {"CONFIG.C_TILE0_ENABLE": "true"}},
            "/blocks/0/parameters/requested_mhz": {
                "value": 74.25, "disposition": "hardware", "reason": "nominal, realized exactly",
                "checks": ["clocks"], "asserted_as": 74251394}}}
        compiler.check_parameter_coverage(spec, plan)
        # The named form must really be carried: a different value or key is refused.
        coverage = plan["parameter_coverage"]
        coverage["/blocks/0/parameters/enabled_tiles/0"]["asserted_as"] = {"CONFIG.C_TILE1_ENABLE": "true"}
        coverage["/blocks/0/parameters/requested_mhz"]["asserted_as"] = 74250000
        with self.assertRaises(compiler.PlanError) as raised:
            compiler.check_parameter_coverage(spec, plan)
        self.assertIn("C_TILE1_ENABLE", str(raised.exception))
        self.assertIn("asserted_as 74250000 is never expected", str(raised.exception))
        coverage["/blocks/0/parameters/requested_mhz"]["asserted_as"] = {"not_config": 1}
        with self.assertRaisesRegex(compiler.PlanError, "asserted_as must be"):
            compiler.check_parameter_coverage(spec, plan)

    def test_parameter_coverage_accepts_string_and_boolean_readbacks(self):
        spec = {"blocks": [{"parameters": {"format": "RAW12", "vfb": True}}]}
        plan = {"stages": [{"name": "cells", "operations": [
            {"id": "camera", "op": "call", "proc": "ipiasm::check_cell_config",
             "args": ["camera", {"CONFIG.CMN_PXL_FORMAT": "RAW12", "CONFIG.CMN_INC_VFB": True}]}
        ]}], "parameter_coverage": {
            "/blocks/0/parameters/format": {"value": "RAW12", "disposition": "hardware",
                                            "reason": "pixel format", "checks": ["camera"]},
            "/blocks/0/parameters/vfb": {"value": True, "disposition": "hardware",
                                         "reason": "video format bridge", "checks": ["camera"]},
        }}
        compiler.check_parameter_coverage(spec, plan)

    def test_parameter_coverage_reads_registration_backed_checks_through_their_setter(self):
        """check_clock_contracts takes no args; its values live in set_clock_contracts."""
        spec = {"blocks": [{"parameters": {"exact_hz": {"dc_1x": 74251394}}}]}
        register = {"op": "call", "proc": "ipiasm::set_clock_contracts",
                    "args": [[{"pin": "pl_mmi_dc_1x_clk", "hz": 74251394}], [], [], "fp"]}
        plan = {"stages": [{"name": "clocks", "operations": [
            register,
            {"id": "clocks_ok", "op": "call", "proc": "ipiasm::check_clock_contracts", "args": []},
        ]}], "parameter_coverage": {
            "/blocks/0/parameters/exact_hz/dc_1x": {"value": 74251394, "disposition": "hardware",
                                                    "reason": "display 1x", "checks": ["clocks_ok"]},
        }}
        compiler.check_parameter_coverage(spec, plan)
        register["args"][0][0]["hz"] = 100000000
        with self.assertRaisesRegex(compiler.PlanError, "dc_1x value 74251394 is never expected"):
            compiler.check_parameter_coverage(spec, plan)

    def test_parameter_coverage_leaves_structural_readbacks_alone(self):
        """A count verified by a readback that lists ports is not an unasserted value.

        Only readbacks that name their values - a CONFIG dict, or a paired
        ipiasm::set_* - can be held to carrying one. Holding a structural check
        to the same rule rejected two thirds of an otherwise sound plan.
        """
        spec = {"blocks": [{"parameters": {"tile_count": 3, "owner": "firmware", "present": True}}]}
        plan = {"stages": [{"name": "ports", "operations": [
            {"id": "ports", "op": "call", "proc": "ipiasm::check_required_ports",
             "args": [["tile0_vidout", "tile1_vidout", "tile2_vidout"]]},
        ]}], "parameter_coverage": {
            "/blocks/0/parameters/tile_count": {"value": 3, "disposition": "hardware",
                                                "reason": "three ISP tiles", "checks": ["ports"]},
            "/blocks/0/parameters/owner": {"value": "firmware", "disposition": "hardware",
                                           "reason": "runtime owner", "checks": ["ports"]},
            "/blocks/0/parameters/present": {"value": True, "disposition": "hardware",
                                             "reason": "port present", "checks": ["ports"]},
        }}
        compiler.check_parameter_coverage(spec, plan)

    def test_cell_configuration_goes_through_apply_dict(self):
        lines = compiler._compile_set_property(cell_property({"CONFIG.LANES": 4}))
        self.assertIn("ipcfg::apply_dict", lines[0])
        passed = run_body(lines, "proc ipcfg::apply_dict {cell d allow} {return SUCCESS:applied}")
        self.assertIn("RESULT PASS PROPERTY:OK cam", passed)
        refused = run_body(lines, "proc ipcfg::apply_dict {cell d allow} {return {CONFIGURE_FAIL:BOARD_OWNED:CONFIG.LANES}}")
        self.assertIn("RESULT FAIL CONFIGURE_FAIL:BOARD_OWNED", refused)

    def test_board_override_needs_an_explicit_reason(self):
        with self.assertRaisesRegex(compiler.PlanError, "non-empty reason"):
            compiler._compile_set_property(cell_property({"CONFIG.LANES": 4}, allow_board=True))
        lines = compiler._compile_set_property(cell_property({"CONFIG.LANES": 4}, allow_board=True, reason="spec needs 4"))
        out = run_body(lines, 'proc ipcfg::apply_dict {cell d allow} {return "SUCCESS:allow=$allow"}')
        self.assertIn("RESULT PASS", out)

    def test_board_interface_write_is_a_board_bind(self):
        lines = compiler._compile_set_property(cell_property(
            {"CONFIG.DPHYRX_BOARD_INTERFACE": "mipi_0", "CONFIG.LANES": 4}))
        self.assertIn("ipcfg::board_bind", lines[0])
        out = run_body(lines, 'proc ipcfg::board_bind {cell binding extra reason} {return "BOARD:bound $cell hooks=[dict size $binding] extra=[dict size $extra]"}')
        self.assertIn("RESULT PASS PROPERTY:OK cam", out)
        failed = run_body(lines, "proc ipcfg::board_bind {args} {return {BOARD_FAIL:cam has no board hook}}")
        self.assertIn("RESULT FAIL BOARD_FAIL", failed)

    def test_pin_properties_stay_plain(self):
        lines = compiler._compile_set_property({"id": "op", "op": "set_property",
                                                "object": {"kind": "pin", "path": "cam/pin"},
                                                "properties": {"CONFIG.POLARITY": "ACTIVE_LOW"}})
        self.assertTrue(any(line.startswith("set_property -dict") for line in lines))
        self.assertIn("RESULT PASS PROPERTY:OK cam/pin", run_body(lines, ""))

    def test_cell_automation_goes_through_run_automation(self):
        lines = compiler._compile_automation(automation())
        self.assertIn("ipcfg::run_automation", lines[0])
        out = run_body(lines, 'proc ipcfg::run_automation {cell rule config} {return "AUTOMATION:OK changed={} added_cells={}"}')
        self.assertIn("RESULT PASS AUTOMATION:OK", out)
        drift = run_body(lines, "proc ipcfg::run_automation {args} {return CONFIGURE_FAIL:PRESET_OVERRIDDEN:x}")
        self.assertIn("RESULT FAIL", drift)

    def test_interface_automation_is_unchanged(self):
        lines = compiler._compile_automation(automation("intf_pin", "cam/S_AXI"))
        self.assertTrue(any(line.startswith("apply_bd_automation") for line in lines))
        self.assertIn("RESULT PASS AUTOMATION:OK", run_body(lines, ""))

    def test_needed_helpers_compile_and_their_results_pass_strict_checks(self):
        for proc, result in (
                ("ipcfg::board_bind", "BOARD:bound ps hooks=1 owned=3"),
                ("ipcfg::run_automation", "AUTOMATION:OK changed={} added_cells={}"),
                ("ipiasm::check_required_reachability", "REACH:OK 2 space/target path(s) addressable"),
                ("ipiasm::enforce_window_scope", "WINDOW_SCOPE:OK required=1 present=1 extra=0 moved=0"),
                ("ipiasm::check_visibility", "VISIBILITY:OK slave=s allowed=1"),
                ("ipiasm::check_undriven_clocks", "UNDRIVEN_CLK:OK 4 clock input(s) driven"),
                ("ipiasm::check_noc_legality", "NOCLEGAL:OK"),
                ("ipiasm::check_memory_subsystem", "MEM:OK 4 controller(s)"),
                ("ipiasm::record_evidence", "EVIDENCE:OK recorded address categories={fixed_address_windows}")):
            with self.subTest(proc=proc):
                self.assertIn(proc, compiler.ALLOWED_PROCS)
                script = f"source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}\n" \
                         f"puts [ipcfg::result_status {{{result}}} {{{' '.join(compiler.DEFAULT_EXPECT)}}}]\n"
                out = subprocess.run(["tclsh"], input=script, capture_output=True, text=True).stdout
                self.assertIn("PASS", out.splitlines()[-1], result)

    def test_unneeded_helpers_stay_out(self):
        for proc in ("ipcfg::create_cell_cfg", "ipiasm::begin_design", "ipiasm::gate_override"):
            self.assertNotIn(proc, compiler.ALLOWED_PROCS)

    def test_list_ops_prints_the_whole_plan_format(self):
        result = subprocess.run([sys.executable, "-B", str(SKILLS / "vivado-ipi-assembler/scripts/compile_assembly_plan.py"),
                                 "--list-ops"], capture_output=True, text=True, check=True)
        for name in (*compiler.ALLOWED_PROCS, *compiler.DEFAULT_EXPECT, *compiler.OPERATION_SHAPES):
            self.assertIn(name, result.stdout)

    def test_board_stage_rules(self):
        hooks = {"id": "hooks", "op": "call", "proc": "ipiasm::check_board_hooks", "args": []}
        cell = {"id": "ps", "op": "call", "proc": "ipiasm::create_cell", "args": ["xilinx.com:ip:ps_wizard", "ps", {}]}
        design = {"id": "bd", "op": "create_bd_design", "name": "design_1"}
        plan = lambda *ops: {"schema_version": 1, "stages": [{"name": "board", "operations": list(ops)}]}
        with self.assertRaisesRegex(compiler.PlanError, "hooks: ipiasm::check_board_hooks .* after the cells"):
            compiler.compile_plan(plan(design, hooks, cell))
        tcl = compiler.compile_plan({**plan(design, cell, hooks),
                                     "binding_context": {"board_part": "amd.com:x:part0:1.0"}})
        self.assertIn("BD:OK", compiler.DEFAULT_EXPECT)
        decoded = " ".join(base64.b64decode(chunk).decode("utf-8", "replace")
                           for chunk in re.findall(r"\{([A-Za-z0-9+/=]{40,})\}", tcl))
        self.assertIn("$__actual_board ne {} && $__actual_board ne", decoded)

    def test_wrong_argument_count_is_rejected_at_compile_time(self):
        call = {"id": "rst", "op": "call", "proc": "ipiasm::ensure_reset_domain", "args": ["a", "b", "c", "d", "e", "f"]}
        with self.assertRaisesRegex(compiler.PlanError, r"rst: ipiasm::ensure_reset_domain got 6 argument\(s\), takes 4..5"):
            compiler.compile_plan({"schema_version": 1, "stages": [{"name": "clocks", "operations": [call]}]})


if __name__ == "__main__":
    unittest.main()
