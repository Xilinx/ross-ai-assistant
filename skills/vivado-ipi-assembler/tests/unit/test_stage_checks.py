# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Each plan stage carries the checks its design needs, and is never asked for others."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import compile_assembly_plan as compiler


def call(proc, *args):
    return {"op": "call", "proc": "ipiasm::" + proc, "args": list(args)}


def create(ip, name):
    return call("create_cell", "xilinx.com:ip:" + ip, name, {})


SPEC = {"functional_contracts": {
    "clock_reset": [{"target": "camera"}],
    "interrupts": [{"source": "camera"}],
    "control_reachability": [{"master": "cpu", "target": "camera"}],
}}

# A camera receiver feeding an ISP, with board memory behind a NoC: every row applies.
FULL = {"schema_version": 1,
        "binding_context": {"board_part": "vendor:board:part0:1.0"},
        "stages": [
            {"name": "board", "operations": [call("require_board_preset"), create("axi_noc2", "noc"),
                                             call("attach_board_memory", "noc", 1, [])]},
            {"name": "cells", "operations": [create("mipi_csi2_rx_subsystem", "camera"), create("visp_ss", "isp"),
                                             call("gate_inventory", {})]},
            {"name": "clocks", "operations": [call("set_clock_contracts"), call("check_interface_clock_domains"),
                                              call("check_undriven_clocks"), call("check_mipi_video_clocks"),
                                              call("check_mem_noc_sysclk")]},
            {"name": "connect", "operations": [call("check_irq_paths", []),
                                               call("check_camera_isp_format"), call("attach_noc_port_clocks"),
                                               call("check_undriven_clocks")]},
            {"name": "qos", "operations": [call("check_noc_pre_address"), call("check_memory_subsystem", 1),
                                           call("check_required_reachability", [])]},
        ]}


def without(plan, stage, proc):
    plan = copy.deepcopy(plan)
    entry = next(entry for entry in plan["stages"] if entry["name"] == stage)
    entry["operations"] = [op for op in entry["operations"] if op.get("proc") != "ipiasm::" + proc]
    return plan


class StageChecks(unittest.TestCase):
    def test_a_plan_carrying_every_needed_check_passes(self):
        compiler.check_stage_gates(SPEC, FULL)

    def test_a_missing_check_names_its_stage_and_reason(self):
        for stage, proc, reason in (
                ("connect", "check_irq_paths", "interrupt contracts"),
                ("connect", "check_camera_isp_format", "MIPI CSI-2 receiver and an ISP"),
                ("clocks", "check_mipi_video_clocks", "MIPI CSI-2 receiver"),
                ("qos", "check_noc_pre_address", "creates a NoC"),
                ("qos", "check_memory_subsystem", "board memory"),
                ("connect", "attach_noc_port_clocks", "port aclks follow the peers"),
                ("board", "require_board_preset", "targets a board")):
            with self.subTest(proc=proc):
                with self.assertRaisesRegex(compiler.PlanError,
                                            f"STAGE_GATE_FAIL:{stage} lacks ipiasm::{proc} \\(.*{reason}"):
                    compiler.check_stage_gates(SPEC, without(FULL, stage, proc))

    def test_every_missing_check_is_reported_at_once(self):
        plan = without(without(FULL, "connect", "check_irq_paths"), "qos", "check_noc_pre_address")
        with self.assertRaisesRegex(compiler.PlanError, "check_irq_paths.*; qos lacks ipiasm::check_noc_pre_address"):
            compiler.check_stage_gates(SPEC, plan)

    def test_a_design_that_does_not_need_a_check_is_never_asked_for_it(self):
        # No camera, ISP, NoC or board; no interrupt, reachability or clock contracts.
        plan = {"schema_version": 1, "stages": [
            {"name": "cells", "operations": [create("axi_gpio", "gpio"), call("gate_inventory", {})]},
            {"name": "clocks", "operations": [call("check_undriven_clocks")]},
            {"name": "connect", "operations": [call("check_undriven_clocks")]},
            {"name": "qos", "operations": [call("set_qos")]},
        ]}
        compiler.check_stage_gates({"functional_contracts": {}}, plan)

    def test_only_stages_the_plan_has_are_checked(self):
        partial = {"schema_version": 1, "stages": [
            {"name": "cells", "operations": [create("mipi_csi2_rx_subsystem", "camera"), call("gate_inventory", {})]},
            {"name": "wiring", "operations": [call("connect_net", "a", "b")]},
        ]}
        compiler.check_stage_gates(SPEC, partial)

    def test_a_bound_placeholder_vlnv_counts_as_the_ip_it_resolves_to(self):
        plan = copy.deepcopy(FULL)
        plan["resolved_bindings"] = {"blocks": {"cam": {"vlnv": "xilinx.com:ip:mipi_csi2_rx_subsystem:6.0"}}}
        plan["stages"][1]["operations"][0] = call("create_cell", "${bind.blocks.cam.vlnv}", "camera", {})
        compiler.check_stage_gates(SPEC, plan)
        with self.assertRaisesRegex(compiler.PlanError, "check_mipi_video_clocks"):
            compiler.check_stage_gates(SPEC, without(plan, "clocks", "check_mipi_video_clocks"))


class BoardStage(unittest.TestCase):
    """The board stage leaves what the board owns: its processing system, preset and memory."""

    def plan(self, board_ops, cells_ops):
        plan = copy.deepcopy(FULL)
        plan["stages"][0]["operations"] = [call("require_board_preset")] + board_ops
        plan["stages"][1]["operations"] = cells_ops + plan["stages"][1]["operations"]
        return plan

    PS = [create("ps_wizard", "ps"), {"op": "apply_automation", "id": "ps_preset"},
          {"op": "call", "proc": "ipcfg::preset_drift", "args": ["ps"]}]
    MEMORY = [create("axi_noc2", "noc"), call("attach_board_memory", "noc", 1, [])]

    def test_a_board_stage_with_its_processing_system_proven_passes(self):
        compiler.check_stage_gates(SPEC, self.plan(self.PS + self.MEMORY, []))

    def test_a_processing_system_left_to_the_cells_stage_is_refused(self):
        with self.assertRaisesRegex(compiler.PlanError, r"board lacks ipcfg::preset_drift \(.*processing system"):
            compiler.check_stage_gates(SPEC, self.plan(self.MEMORY, self.PS))

    def test_board_memory_attached_in_the_cells_stage_is_refused(self):
        with self.assertRaisesRegex(compiler.PlanError, r"board lacks ipiasm::attach_board_memory"):
            compiler.check_stage_gates(SPEC, self.plan(self.PS, self.MEMORY))

    def test_a_board_design_without_a_processing_system_is_not_asked_for_a_preset_check(self):
        compiler.check_stage_gates(SPEC, self.plan(self.MEMORY, []))

    def test_a_readback_before_its_create_is_refused(self):
        stages = [{"name": "cells", "operations": [call("check_cell_config", "noc", {"CONFIG.NUM_SI": "1"}),
                                                  create("axi_noc2", "noc")]}]
        with self.assertRaisesRegex(compiler.PlanError, r"reads noc before .* creates it"):
            compiler._check_reads_after_create(stages)
        compiler._check_reads_after_create([{"name": "cells", "operations": stages[0]["operations"][::-1]}])
        # A cell the plan never creates (an existing one) is not judged.
        compiler._check_reads_after_create([{"name": "cells", "operations": stages[0]["operations"][:1]}])


    def test_a_port_used_before_its_create_is_refused(self):
        wire = call("ensure_reset_domain", "/sync", "/clocks/out", "/ps/resetn", ["/resetn_out", "/cell/aresetn"])
        port = call("create_port", "resetn_out", "O", "rst", "sync/peripheral_aresetn")
        with self.assertRaisesRegex(compiler.PlanError, r"uses top-level port /resetn_out before .* creates it"):
            compiler._check_ports_after_create([{"name": "clocks", "operations": [wire, port]}])
        compiler._check_ports_after_create([{"name": "clocks", "operations": [port, wire]}])
        # A port the plan never creates (an existing one) is not judged, and a pin of a
        # cell that shares the port's name is not the port.
        compiler._check_ports_after_create([{"name": "clocks", "operations": [wire]}])
        compiler._check_ports_after_create([{"name": "clocks", "operations": [
            call("connect_net", "resetn_out/pin", "cell/aresetn"), port]}])


class PlanOrder(unittest.TestCase):
    def test_an_extra_stage_between_canonical_stages_is_refused(self):
        stages = [{"name": name, "operations": []} for name in ("board", "cells", "inventory", "clocks")]
        with self.assertRaisesRegex(compiler.PlanError, r"STAGE_SEQUENCE_FAIL:stage\(s\) inventory sit before clocks"):
            compiler._check_stage_sequence(stages)
        # After the last canonical stage, an extra stage blocks nothing the lab runs by name.
        compiler._check_stage_sequence([{"name": name, "operations": []}
                                        for name in ("board", "cells", "clocks", "connect", "qos", "validate")])

    def test_bram_wiring_after_the_inventory_gate_is_refused(self):
        gate = call("gate_inventory", ["x:ip:axi_bram_ctrl", 1])
        wire = call("connect_intf", "mailbox/BRAM_PORTA", "memory/BRAM_PORTA")
        with self.assertRaisesRegex(compiler.PlanError, r"connects a BRAM port after .*move the connection"):
            compiler._check_bram_before_inventory([{"name": "cells", "operations": [gate]},
                                                   {"name": "connect", "operations": [wire]}])
        compiler._check_bram_before_inventory([{"name": "cells", "operations": [wire, gate]}])


class PrematureChecks(unittest.TestCase):
    """A check copied into a stage before its own judges work that stage has not done."""

    def early(self, stage, proc, *args):
        plan = copy.deepcopy(FULL)
        entry = next(entry for entry in plan["stages"] if entry["name"] == stage)
        entry["operations"].append(call(proc, *args))
        return plan

    def test_a_check_placed_before_its_stage_names_the_stage_that_does_the_work(self):
        for stage, proc, belongs in (("clocks", "check_memory_subsystem", "qos"),
                                     ("cells", "check_undriven_clocks", "clocks"),
                                     ("clocks", "check_irq_paths", "connect")):
            with self.subTest(proc=proc):
                with self.assertRaisesRegex(
                        compiler.PlanError,
                        f"STAGE_GATE_FAIL:{stage} runs ipiasm::{proc} before {belongs},"):
                    compiler.check_stage_gates(SPEC, self.early(stage, proc, 1))

    def test_a_check_that_belongs_to_two_stages_is_judged_by_the_earlier_one(self):
        # check_undriven_clocks is canonical in both clocks and connect, so connect is fine.
        compiler.check_stage_gates(SPEC, FULL)
        with self.assertRaisesRegex(compiler.PlanError, "cells runs ipiasm::check_undriven_clocks before clocks"):
            compiler.check_stage_gates(SPEC, self.early("cells", "check_undriven_clocks"))

    def test_a_helper_that_is_not_a_canonical_check_is_placed_where_its_author_wants(self):
        compiler.check_stage_gates(SPEC, self.early("cells", "connect_net", "a", "b"))

    def test_a_late_copy_of_a_check_is_the_authors_own(self):
        # Running a clocks check again in connect re-proves it; only early copies are refused.
        compiler.check_stage_gates(SPEC, self.early("connect", "check_mipi_video_clocks"))

    def test_clock_contracts_are_registered_in_clocks_and_checked_after_validation(self):
        # The registration belongs to clocks; the check reads values Vivado sets only when the
        # design validates, which no build stage reaches.
        with self.assertRaisesRegex(compiler.PlanError, r"clocks lacks ipiasm::set_clock_contracts"):
            compiler.check_stage_gates(SPEC, without(FULL, "clocks", "set_clock_contracts"))
        for stage in ("clocks", "connect", "qos"):
            with self.subTest(stage=stage):
                with self.assertRaisesRegex(compiler.PlanError,
                                            f"{stage} runs ipiasm::check_clock_contracts before the design validates"):
                    compiler.check_stage_gates(SPEC, self.early(stage, "check_clock_contracts"))
        plan = copy.deepcopy(FULL)
        plan["stages"].append({"name": "validate", "operations": [call("check_clock_contracts"),
                                                                  call("validate_bundle")]})
        with self.assertRaisesRegex(compiler.PlanError, "validate runs ipiasm::check_clock_contracts before"):
            compiler.check_stage_gates(SPEC, plan)
        plan["stages"][-1]["operations"].reverse()
        compiler.check_stage_gates(SPEC, plan)


class DefaultExpect(unittest.TestCase):
    def test_every_plan_callable_helpers_success_is_accepted_by_default(self):
        # An operation without "expect" is judged against DEFAULT_EXPECT, so a helper whose
        # documented success token is missing there fails every plan that calls it.
        import re
        skills = Path(__file__).resolve().parents[3]
        index = "".join((skills / name / "FUNCTIONS.md").read_text()
                        for name in ("vivado-ipi-assembler", "vivado-ip-configurator"))
        missed = {}
        for match in re.finditer(r"^- `((?:ipiasm|ipcfg)::\w+)[^`]*`\n(?:  .*\n)*?  ok: ([^\n]*?)(?:   fail:|$)",
                                 index, re.M):
            proc, tokens = match.group(1), match.group(2).split()
            if proc in compiler.ALLOWED_PROCS and not any(
                    token.startswith(prefix) for token in tokens for prefix in compiler.DEFAULT_EXPECT):
                missed[proc] = tokens
        self.assertEqual(missed, {})


class ReachabilityCoverage(unittest.TestCase):
    SPEC = {"global_constraints": {"reference_groups": {"cpu": ["ps.cpu0", "ps.cpu1"]}},
            "functional_contracts": {
                "control_reachability": [{"master": "cpu", "target": "uart"}],
                "memory_reachability": [{"master": "cpu", "memory": "ddr.low"},
                                        {"master": "cpu", "memory": "host.buffers"}]}}

    def plan(self, *pairs):
        return {"resolved_bindings": {"blocks": {
                    "ps": {"instance": "ps", "spec_block": "ps", "vlnv": "x:ip:ps_wizard:1.0"},
                    "uart": {"instance": "uart", "spec_block": "uart", "vlnv": "x:ip:axi_uartlite:2.0"},
                    "dma": {"instance": "dma", "spec_block": "dma", "vlnv": "x:ip:axi_dma:7.1"},
                    "memory_noc": {"instance": "ddr_noc", "spec_block": "ddr", "vlnv": "x:ip:axi_noc2:1.1"}}},
                "stages": [{"name": "connect", "operations": [
                    call("check_required_reachability", [item for pair in pairs for item in pair])]}]}

    def test_every_contract_needs_a_pair_from_its_own_master(self):
        compiler.check_reachability_coverage(self.SPEC, self.plan(("ps/cpu*", "uart/S_AXI/Reg"), ("ps/cpu*", "DRAM")))
        with self.assertRaisesRegex(compiler.PlanError, r"REACHABILITY_FAIL:1 .*: cpu->uart\. Add"):
            compiler.check_reachability_coverage(self.SPEC, self.plan(("ps/cpu*", "DRAM")))
        # A pair from another master's address space does not cover the cpu's contract.
        with self.assertRaisesRegex(compiler.PlanError, "cpu->uart"):
            compiler.check_reachability_coverage(self.SPEC, self.plan(("dma/Data", "uart/S_AXI/Reg"), ("ps/cpu*", "DRAM")))

    def test_absolute_vivado_paths_name_their_cells(self):
        compiler.check_reachability_coverage(self.SPEC, self.plan(("/ps/ps11_0_cortexa78_0", "/uart/S_AXI/Reg"),
                                                                  ("/ps/ps11_0_cortexa78_0", "DRAM")))

    def test_a_list_of_pairs_is_refused_with_its_fix(self):
        nested = call("check_required_reachability", [["ps/cpu0", "DRAM"], ["ps/cpu0", "uart/S_AXI/Reg"]])
        with self.assertRaisesRegex(compiler.PlanError, r"takes one flat list .*Flatten it"):
            compiler._compile_call(nested)
        with self.assertRaisesRegex(compiler.PlanError, "odd count"):
            compiler._compile_call(call("check_required_reachability", ["ps/cpu0"]))
        compiler._compile_call(call("check_required_reachability", ["ps/cpu0", "DRAM"]))

    def test_a_target_outside_the_design_is_not_this_plans_to_check(self):
        # host.buffers has no cell in the plan; only ddr.low and uart are checked.
        compiler.check_reachability_coverage(self.SPEC, self.plan(("ps/*", "uart/S_AXI/Reg"), ("ps/*", "DRAM")))


class BoundaryCoverage(unittest.TestCase):
    """A top_level spec interface is a port the plan reads back, or nothing would notice it missing."""

    SPEC = {
        "blocks": [
            {"name": "clocks", "interfaces": [{"name": "clk_aux", "scope": "top_level"},
                                              {"name": "reference", "scope": "internal"}]},
            {"name": "resets", "interfaces": [{"name": "bit3", "scope": "top_level"}]},
        ],
        "functional_contracts": {"connections": [
            {"source": "resets.bit3", "destination": "sensor_resetn", "required": True},
            {"source": "clocks.reference", "destination": "resets.bit3", "required": True},
        ]},
    }

    @staticmethod
    def plan(*names):
        ports = [{"name": name, "dir": "O"} for name in names]
        return {"stages": [{"name": "validate", "operations": [call("check_required_ports", ports)]}]}

    def test_every_top_level_interface_read_back_passes(self):
        # A port named after the interface, or after the bare name it connects to, both count.
        compiler.check_boundary_coverage(self.SPEC, self.plan("clk_aux", "sensor_resetn"))
        compiler.check_boundary_coverage(self.SPEC, self.plan("clk_aux", "bit3"))

    def test_a_top_level_interface_no_port_check_names_is_refused(self):
        with self.assertRaisesRegex(compiler.PlanError,
                                    r"BOUNDARY_FAIL:1 top_level .*resets\.bit3 \(port bit3 or sensor_resetn\)"):
            compiler.check_boundary_coverage(self.SPEC, self.plan("clk_aux"))

    def test_a_design_without_top_level_interfaces_needs_no_port_check(self):
        spec = {"blocks": [{"name": "uart", "interfaces": [{"name": "serial", "scope": "internal"}]}]}
        compiler.check_boundary_coverage(spec, {"stages": []})


if __name__ == "__main__":
    unittest.main()
