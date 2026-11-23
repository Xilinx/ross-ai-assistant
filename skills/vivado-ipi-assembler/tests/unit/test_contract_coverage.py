# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

SKILLS = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SKILLS / "vivado-ipi-assembler/scripts"))
import contract_coverage  # noqa: E402

SPEC = {
    "blocks": [{"name": "processing_system"}, {"name": "camera_i2c"}, {"name": "memory"}],
    "functional_contracts": {
        "connections": [{"source": "a.x", "destination": "b.y", "required": True}],
        "control_reachability": [{"master": "realtime", "target": "camera_i2c", "required": True}],
        "memory_reachability": [{"master": "application", "memory": "memory.low", "required": True}],
        "interrupts": [
            {"source": "camera_i2c", "outputs": ["tile1_irq", "tile2_irq", "tile0_irq"],
             "destination": "processing_system.fabric_irq", "required": True},
            {"source": "endpoint", "outputs": ["done"], "destination": "host", "required": True},
        ],
        "fixed_address_windows": [{"name": "low_memory", "target": "memory.low", "masters": ["application"],
                                   "offset": "0x0", "range": "0x1000"}],
        "visibility_policy": [{"action": "deny", "masters": ["application"], "targets": ["camera_i2c"]}],
        "clock_reset": [{"target": "camera_i2c", "clock_mhz": 100, "required": True}],
        "software": [{"os": "linux"}],
    },
    "clock_domains": [{"name": "control", "members": ["camera_i2c"]}],
}

FULL = """CLOCK_CONTRACTS:OK clocks=1 resets=1 modeled_hz_only=1
PLAN_EXECUTION:OK path=/p/assembly_plan.json sha256=00
DRAM_REACH:OK 1/1 role patterns
low_memory WINDOW_SCOPE:OK required=1 present=1 extra=0 moved=0
IRQ_PATHS:OK n=4 covered={camera_i2c.tile1_irq camera_i2c.tile2_irq camera_i2c.tile0_irq}
EVIDENCE:reach categories={control_reachability visibility_policy} REACH:OK 1 space/target path(s) addressable
"""


class ContractCoverageTests(unittest.TestCase):
    def test_every_required_contract_becomes_an_obligation_and_nonhardware_is_deferred(self):
        required, deferred = contract_coverage.obligations(SPEC)
        ids = [item["id"] for item in required]
        self.assertIn("functional_contracts.interrupts[0]", ids)
        self.assertIn("clock_domains[0]", ids)
        self.assertEqual(len(required), 8)
        self.assertEqual({item["id"] for item in deferred},
                         {"functional_contracts.interrupts[1]", "functional_contracts.software[0]"})

    def test_complete_evidence_passes(self):
        result = contract_coverage.check(SPEC, FULL)
        self.assertTrue(result.startswith("COVERAGE:OK obligations=8"))
        self.assertIn("checked_each=2 checked_by_category=6 categories={clock_reset(2) connections(1) "
                      "control_reachability(1) memory_reachability(1) visibility_policy(1)}", result)

    def test_unregistered_clocks_fail_even_when_everything_else_passes(self):
        evidence = FULL.replace("CLOCK_CONTRACTS:OK clocks=1 resets=1 modeled_hz_only=1",
                                "CLOCK_CONTRACTS:NA not registered")
        result = contract_coverage.check(SPEC, evidence)
        self.assertTrue(result.startswith("COVERAGE_FAIL:2/8"), result)
        self.assertIn("functional_contracts.clock_reset[0]: needs ipiasm::set_clock_contracts", result)
        self.assertIn("clock_domains[0]", result)

    def test_an_interrupt_table_missing_one_source_does_not_cover_the_contract(self):
        evidence = FULL.replace(" camera_i2c.tile0_irq}", "}")
        result = contract_coverage.check(SPEC, evidence)
        self.assertIn("COVERAGE_FAIL:1/8", result)
        self.assertIn("interrupts[0]: needs ipiasm::set_irq_contracts labels {camera_i2c.tile1_irq", result)

    def test_dram_reachability_cannot_stand_in_for_control_reachability(self):
        evidence = FULL.replace("EVIDENCE:reach categories={control_reachability visibility_policy} REACH:OK", "")
        result = contract_coverage.check(SPEC, evidence)
        self.assertIn("control_reachability[0]", result)
        self.assertIn("visibility_policy[0]", result)

    def test_missing_window_and_optional_contracts(self):
        spec = json.loads(json.dumps(SPEC))
        spec["functional_contracts"]["connections"].append({"source": "c", "destination": "d", "required": False})
        result = contract_coverage.check(spec, FULL.replace("low_memory WINDOW_SCOPE:OK", "other WINDOW_SCOPE:OK"))
        self.assertIn("COVERAGE_FAIL:1/8", result)
        self.assertIn("fixed_address_windows[0]: needs a 'low_memory WINDOW_SCOPE:OK'", result)


class GateCoverageTests(unittest.TestCase):
    """The real final_contract_gate with its unrelated checks mocked to pass."""

    def run_gate(self, setup, call="ipiasm::final_contract_gate {} 0 {} 0 1 1 auto"):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "outputs").mkdir()
            Path(root, "outputs/design_spec.json").write_text(json.dumps(SPEC))
            script = textwrap.dedent(f"""
                source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
                source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
                cd {{{root}}}
                proc ipiasm::validate_bundle {{args}} {{return BUNDLE:OK}}
                proc ipiasm::check_required_dram_masters {{args}} {{return "DRAM_REACH:OK 1/1 role patterns"}}
                proc ipiasm::check_window_scope {{args}} {{return "WINDOW_SCOPE:OK required=1 present=1 extra=0 moved=0"}}
                proc ipiasm::assembly_report {{}} {{return ASSEMBLY:CLEAN}}
                proc ipiasm::plan_execution_status {{args}} {{return "PLAN_EXECUTION:OK path=p sha256=0"}}
                proc ipiasm::_delivery_fingerprint {{}} {{return fp}}
                proc ipiasm::context_identity {{}} {{return {{/p project design}}}}
                proc ipcfg::audit_requirements {{args}} {{return "REQ:OK 1/1"}}
                proc ipiasm::check_clock_contracts {{}} {{return "CLOCK_CONTRACTS:OK clocks=1 resets=1"}}
                proc ipiasm::check_irq_contracts {{}} {{return "IRQ_PATHS:OK n=3 covered={{camera_i2c.tile1_irq camera_i2c.tile2_irq camera_i2c.tile0_irq}}"}}
                proc ipiasm::_discover_assembly_plans {{}} {{return [file join [pwd] outputs assembly_plan.json]}}
                ipiasm::record_evidence reach {{control_reachability visibility_policy}} "REACH:OK 1 space/target path(s) addressable"
                {setup}
                if {{[catch {{puts [{call}]}} message]}} {{puts "TCL_ERROR $message"}}
            """)
            result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120)
        self.assertNotIn("TCL_ERROR", result.stdout, result.stdout + result.stderr)
        return result.stdout

    WINDOWS = "{{low_memory /m/seg {/a} 0x0 0x1000}}"

    def test_complete_contracts_pass(self):
        out = self.run_gate("", f"ipiasm::final_contract_gate {self.WINDOWS} 0 {{}} 0 1 1 auto")
        self.assertIn("FINAL_CONTRACT:PASS", out)
        self.assertIn("COVERAGE:OK obligations=8", out)

    def test_omitted_clock_registration_fails(self):
        out = self.run_gate('proc ipiasm::check_clock_contracts {} {return "CLOCK_CONTRACTS:NA not registered"}',
                            f"ipiasm::final_contract_gate {self.WINDOWS} 0 {{}} 0 1 1 auto")
        self.assertIn("FINAL_CONTRACT_FAIL", out)
        self.assertIn("clock_reset[0]: needs ipiasm::set_clock_contracts", out)

    def test_swapped_interrupt_order_fails(self):
        out = self.run_gate(
            'proc ipiasm::check_irq_contracts {} {return "IRQ_PATHS_FAIL:2/3 slot(s)\\n  ps/pl_irq18: want i2c1/irq got i2c0/irq"}',
            f"ipiasm::final_contract_gate {self.WINDOWS} 0 {{}} 0 1 1 auto")
        self.assertIn("FINAL_CONTRACT_FAIL", out)
        self.assertIn("IRQ_PATHS_FAIL:2/3", out)
        self.assertIn("interrupts[0]: needs ipiasm::set_irq_contracts", out)

    def test_omitted_window_spec_and_stale_evidence_fail(self):
        out = self.run_gate('proc ipiasm::_delivery_fingerprint {} {return changed}')
        self.assertIn("FINAL_CONTRACT_FAIL", out)
        self.assertIn("fixed_address_windows[0]", out)
        self.assertIn("EVIDENCE_STALE:reach", out)

    def test_missing_plan_or_spec_fails(self):
        out = self.run_gate("proc ipiasm::_discover_assembly_plans {} {return {}}; file delete outputs/design_spec.json",
                            f"ipiasm::final_contract_gate {self.WINDOWS} 0 {{}} 0 1 1 auto")
        self.assertIn("PLAN_EXECUTION_FAIL:no assembly_plan.json found", out)
        self.assertIn("COVERAGE_FAIL:no design_spec.json found", out)

    def test_plan_none_needs_the_users_override(self):
        # A design built without a plan cannot declare 'none' on its own (three issue-design runs
        # did, and passed the gate with every evidence check switched off).
        out = self.run_gate("file delete outputs/design_spec.json",
                            f"ipiasm::final_contract_gate {self.WINDOWS} 0 {{}} 0 1 1 none {{}} {{}} none")
        self.assertIn("FINAL_CONTRACT_FAIL", out)
        self.assertIn("PLAN_EXECUTION_FAIL:plan 'none' is accepted only after ipiasm::gate_override plan", out)
        out = self.run_gate("file delete outputs/design_spec.json; ipiasm::gate_override plan {user: build it by hand}",
                            f"ipiasm::final_contract_gate {self.WINDOWS} 0 {{}} 0 1 1 none {{}} {{}} none")
        self.assertIn("FINAL_CONTRACT:PASS", out)
        self.assertIn("PLAN_EXECUTION:NONE approved by the user (OVERRIDE: user: build it by hand)", out)
        self.assertIn("COVERAGE:NONE declared by caller", out)

    def test_spec_none_without_a_designspec_needs_the_users_override(self):
        out = self.run_gate("file delete outputs/design_spec.json",
                            f"ipiasm::final_contract_gate {self.WINDOWS} 0 {{}} 0 1 1 auto {{}} {{}} none")
        self.assertIn("COVERAGE_FAIL:spec 'none' is accepted only after ipiasm::gate_override plan", out)
        self.assertNotIn("FINAL_CONTRACT:PASS", out)

    def test_spec_none_refused_when_a_designspec_exists(self):
        out = self.run_gate("ipiasm::gate_override plan {user: no plan}",
                            f"ipiasm::final_contract_gate {self.WINDOWS} 0 {{}} 0 1 1 none {{}} {{}} none")
        self.assertIn("COVERAGE_FAIL:spec 'none' refused: a DesignSpec exists", out)
        self.assertNotIn("FINAL_CONTRACT:PASS", out)


class NativeGuardTests(unittest.TestCase):
    """After ipiasm::init, typed create_bd_cell/write_hw_platform follow the plan gate and delivery_guard."""

    def run_tcl(self, body):
        script = textwrap.dedent(f"""
            proc create_bd_cell {{args}} {{return "native_cell:[lindex $args end]"}}
            proc write_hw_platform {{args}} {{return native_xsa}}
            source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
            source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
            """) + textwrap.dedent(body)
        result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [line for line in result.stdout.splitlines() if not line.startswith("LIBRARY:")]

    def test_installation_shared_data_scripts_are_trusted_read_only(self):
        # Block-automation rules and CEDs ship in <install>/data beside <install>/Vivado.
        with tempfile.TemporaryDirectory() as root:
            vivado = Path(root, "2026.2", "Vivado")
            rule = Path(root, "2026.2", "data", "rsb", "design_assist", "block", "x", "bd.tcl")
            rule.parent.mkdir(parents=True)
            vivado.mkdir(parents=True)
            rule.write_text("# rule\n")
            writable = Path(root, "2026.2", "data", "agent.tcl")
            writable.write_text("# agent\n")
            rule.chmod(0o444)
            elsewhere = Path(root, "other", "data", "rsb", "bd.tcl")
            elsewhere.parent.mkdir(parents=True)
            elsewhere.write_text("# rule\n")
            elsewhere.chmod(0o444)
            out = self.run_tcl(f"""
                set ::env(XILINX_VIVADO) {{{vivado}}}
                puts [ipiasm::_trusted_file {{{rule}}}]
                puts [ipiasm::_trusted_file {{{writable}}}]
                puts [ipiasm::_trusted_file {{{elsewhere}}}]
            """)
        self.assertEqual(out, ["1", "0", "0"])

    def test_typed_commands_follow_the_gates(self):
        with tempfile.TemporaryDirectory() as root:
            agent = Path(root, "agent_stage.tcl")
            agent.write_text("puts [catch {create_bd_cell -type ip -vlnv v from_script} m]\nputs $m\n")
            out = self.run_tcl(f"""
                proc current_project {{args}} {{return project}}
                proc get_property {{args}} {{if {{[lindex $args 0] eq "DIRECTORY"}} {{return $::projdir}}; return {{}}}}
                set ::projdir /work/out/.ipiasm/r1_probe/probe
                puts [catch {{create_bd_cell -type ip -vlnv v typed}} message]
                puts $message
                puts [ipiasm::_probe_create v probe]
                ipcfg::source_reported {{{agent}}}
                proc ipiasm::_delivery_guard_result {{}} {{return "DELIVERY_GUARD_FAIL:final_contract_gate has not passed"}}
                puts [catch {{write_hw_platform -fixed -file x.xsa}} message]
                puts $message
                set ::projdir /work/out/delivery
                puts [catch {{ipiasm::_probe_create v helper_in_delivery}} message]
                puts $message
                ipiasm::_gate_set plan 1 "PLAN:assembly_plan.json"
                puts [create_bd_cell -type ip -vlnv v after_plan]
                proc ipiasm::_delivery_guard_result {{}} {{return "DELIVERY_GUARD:OK fingerprint=f"}}
                puts [write_hw_platform -fixed -file x.xsa]
                puts [create_bd_cell -help]
            """)
        self.assertEqual(out[0], "1")
        self.assertTrue(out[1].startswith("GATE_FAIL:create_bd_cell requires the 'plan' gate"), out[1])
        self.assertEqual(out[2], "native_cell:probe")
        # A stage script run through a library helper is still the agent's own code.
        self.assertEqual(out[3], "1")
        self.assertTrue(out[4].startswith("GATE_FAIL:create_bd_cell requires the 'plan' gate"), out[4])
        self.assertEqual(out[5], "1")
        self.assertTrue(out[6].startswith("GATE_FAIL:write_hw_platform requires delivery_guard to pass"), out[6])
        # A helper making a cell in the delivery project before the plan is refused too.
        self.assertEqual(out[7], "1")
        self.assertIn("only in a scratch project under <out>/.ipiasm/", out[8])
        self.assertEqual(out[9], "native_cell:after_plan")
        self.assertEqual(out[10], "native_xsa")
        self.assertEqual(out[11], "native_cell:-help")

    def test_clock_contract_measured_fields_can_be_corrected(self):
        out = self.run_tcl("""
            proc ipiasm::context_identity {} {return {/p project design}}
            proc ipiasm::feasibility_fingerprint {sources} {return fp}
            proc check {ref tol expected} {
                return [list [dict create id c kind clock_frequency subject /vio/clk expected $expected \
                    source spec stage clocks driver /ps/pl0 input /ps/pl0 reference /ps/pl0 reference_hz $ref tolerance_hz $tol]]
            }
            puts [ipiasm::set_clock_contracts [check 300000000 0 300000000] {} {} fp]
            puts [ipiasm::set_clock_contracts [check 299999725 0 300000000] {} {} fp]
            puts [ipiasm::set_clock_contracts [check 299999725 300 300000000] {} {} fp]
            puts [ipiasm::set_clock_contracts [check 299999725 0 299999725] {} {} fp]
        """)
        self.assertEqual(out[0], "CLOCK_CONTRACTS:SET clocks=1 resets=0")
        self.assertEqual(out[1], "CLOCK_CONTRACTS:SET clocks=1 resets=0 updated=measured_fields")
        self.assertIn("(clock c: tolerance_hz 0 -> 300)", out[2])
        self.assertIn("(clock c: expected 300000000 -> 299999725)", out[3])


class PlanGateTests(unittest.TestCase):
    """The plan gate: after ipiasm::init, cells are built only by a compiled plan (or a user override)."""

    def run_tcl(self, body):
        script = textwrap.dedent(f"""
            source {{{SKILLS / 'vivado-ip-configurator/lib/ipcfg/ipcfg.tcl'}}}
            source {{{SKILLS / 'vivado-ipi-assembler/lib/ipiasm.tcl'}}}
            proc get_ipdefs {{args}} {{return vendor:ip}}
            proc ipcfg::create_cell_cfg {{args}} {{return SUCCESS:created}}
            """) + textwrap.dedent(body)
        result = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return "\n".join(line for line in result.stdout.splitlines() if not line.startswith("LIBRARY:"))

    def test_cells_and_build_stages_refused_until_a_plan_runs(self):
        out = self.run_tcl("""
            set ::ipiasm::initialized 1
            ipiasm::_gate_set board 1 test
            puts [ipiasm::gate_status]
            puts [ipiasm::create_cell vendor:ip c0]
            puts [ipiasm::instantiate_n vendor:ip c 2]
            puts [ipiasm::stage cells]
            puts [ipiasm::stage spec]
            ipiasm::_gate_set plan 1 "PLAN:assembly_plan.json sha256=0"
            puts [ipiasm::create_cell vendor:ip c1]
            puts [ipiasm::stage cells]
        """).splitlines()
        self.assertTrue(out[0].startswith("GATES:plan=0 board=1"), out[0])
        self.assertTrue(out[1].startswith("GATE_FAIL:create_cell c0 requires the 'plan' gate -> build the delivery design from a compiled plan"), out[1])
        self.assertIn("ipiasm::assemble_from_plan <abs-plan>", out[1])
        self.assertTrue(out[2].startswith("GATE_FAIL:instantiate_n c requires the 'plan' gate"), out[2])
        self.assertTrue(out[3].startswith("GATE_FAIL:stage cells requires the 'plan' gate"), out[3])
        self.assertTrue(out[4].startswith("STAGE:spec"), out[4])
        self.assertEqual(out[5], "SUCCESS:created")
        self.assertTrue(out[6].startswith("STAGE:cells"), out[6])

    def test_scratch_before_init_and_user_override(self):
        out = self.run_tcl("""
            ipiasm::_gate_set board 1 test
            puts [ipiasm::create_cell vendor:ip scratch_cell]
            set ::ipiasm::initialized 1
            puts [ipiasm::gate_override plan {}]
            puts [ipiasm::gate_override plan {user approved a hand build}]
            puts [ipiasm::create_cell vendor:ip c0]
            puts [ipiasm::gate_reset]
            puts [ipiasm::gate_status]
        """).splitlines()
        self.assertEqual(out[0], "SUCCESS:created")
        self.assertTrue(out[1].startswith("GATE_FAIL:gate_override needs the reason"), out[1])
        self.assertEqual(out[2], "GATE:plan overridden (user approved a hand build)")
        self.assertEqual(out[3], "SUCCESS:created")
        self.assertTrue(out[5].startswith("GATES:plan=0 "), out[5])


if __name__ == "__main__":
    unittest.main()
