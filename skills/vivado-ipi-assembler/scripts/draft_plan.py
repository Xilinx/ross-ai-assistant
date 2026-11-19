#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Draft assembly_plan.template.json from the DesignSpec, binding intent and snapshot.

Usage: draft_plan.py --spec design_spec.json --intent binding_intent.json
                     --snapshot binding_snapshot.json [--fragments proven/*.json]
                     [--ced-fit ced_fit.json] [--clock-fingerprint clock_fingerprint.tcldict]
                     --out assembly_plan.template.json [--todo assembly_plan.todo.md]

Writes every operation the inputs determine, in the compiler's stage order, so the plan is
edited rather than written: the board stage (create_bd_design, require_board_preset, the
processing system, preset_drift), one create_cell per role, gate_inventory with the roles'
counts, connect_intf for each DesignSpec connection whose two interfaces the binding resolved,
check_required_ports for top-level interfaces, every check STAGE_CHECKS requires that needs no
design decision, the address and validate stages, and the parameter_coverage skeleton.
With a clock fingerprint (ipiasm::scratch_bind writes it), register_clock_contracts for the
DesignSpec's clock_reset targets.

With a STRUCTURAL --ced-fit, the plan starts from the CED its ced_reference.json records:
instantiate_ced, remove_cell for surplus units, create_cell only for blocks the CED lacks,
ipcfg::require for a clock rate or setting the CED does not meet, check_cell_config of each
kept cell's reference configuration, the interrupt table, reachability pairs and window
scopes the CED wires and maps (enforce_window_scope first for a window the CED maps at
another offset), and the parameter_coverage entries those operations prove.

What needs a decision (board preset automation config, clock/reset wiring and contracts, NoC
routes and QoS, reachability address spaces, parameter_coverage reasons) is listed, with what
the compiler will still demand, in the TODO file. The last line printed is
  DRAFT_PLAN:OK ops=<n> todo=<n> template=<path> todo_file=<path>
or DRAFT_PLAN_FAIL:<reason>. Then: fill the TODOs, run proven_to_plan.py (if there are proven
fragments), bind, and compile with --spec.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bind_design_spec as binder  # noqa: E402
import compile_assembly_plan as compiler  # noqa: E402
import plan_requirements as requirements  # noqa: E402


def _catalog(vlnv: str) -> str:
    parts = str(vlnv).split(":")
    return parts[2] if len(parts) >= 3 else str(vlnv)


def _ref(role: str, field: str) -> str:
    return "${bind.blocks.%s.%s}" % (role, field)


class Draft:
    def __init__(self) -> None:
        self.stages: dict[str, list[dict]] = {name: [] for name in ("board", "cells", "clocks", "connect", "qos",
                                                                    "address", "validate")}
        self.todo: list[str] = []
        self.ids: set[str] = set()

    def op(self, stage: str, op_id: str, body: dict) -> None:
        base, n = re.sub(r"[^A-Za-z0-9_]+", "_", op_id).strip("_") or "op", 1
        op_id = base
        while op_id in self.ids:
            n += 1
            op_id = f"{base}_{n}"
        self.ids.add(op_id)
        self.stages[stage].append({"id": op_id, **body})

    def call(self, stage: str, op_id: str, proc: str, args: list, expect: list | None = None) -> bool:
        """Add a call if its argument count fits the helper's signature."""
        minimum, maximum = compiler.SIGNATURES.get(proc, (0, -1))
        if len(args) < minimum or (maximum >= 0 and len(args) > maximum):
            self.todo.append(f"[{stage}] add {proc}: needs {minimum}..{maximum} arguments (ipcfg::help {proc})")
            return False
        body = {"op": "call", "proc": proc, "args": args}
        if expect:
            body["expect"] = expect
        self.op(stage, op_id, body)
        return True


def _hz(mhz: object) -> int | None:
    try:
        return int(round(float(mhz) * 1e6))
    except (TypeError, ValueError):
        return None


class CedSeed:
    """What a STRUCTURAL CED fit's recorded reference (ced_reference.json) settles in the plan.

    The plan instantiates the CED and keeps every cell whose name a DesignSpec block uses, so
    its wiring, interrupts, clocks and address map come from the CED and are only checked. A
    CED cell of an IP type the DesignSpec counts, but under no block's name, is surplus and is
    removed; a cell of a type the DesignSpec does not mention (clock wizard, reset, glue) stays.
    """

    def __init__(self, records: list[dict], roles: dict[str, dict]) -> None:
        head = next((r for r in records if r.get("kind") == "ced"), {})
        self.name = str(head.get("name") or "")
        self.options = head.get("options") if isinstance(head.get("options"), dict) else {}
        # Top-level cells only: a hierarchy's inner cells go with it.
        self.cells = {str(r["path"]).strip("/"): r for r in records
                      if r.get("kind") == "cell" and str(r.get("path", "")).count("/") == 1}
        self.clocks = {str(r["pin"]): (str(r.get("src") or ""), r.get("freq")) for r in records
                       if r.get("kind") == "clk" and r.get("pin")}
        self.irq = {str(r["src"]).strip("/"): str(r["dst"]) for r in records
                    if r.get("kind") == "irq" and r.get("src") and r.get("dst")}
        self.maps = [r for r in records if r.get("kind") == "map"]
        self.segments = [str(r.get("seg") or "") for r in records if r.get("kind") == "addr"]
        self.todo: list[str] = []
        by_catalog: dict[str, set] = {}
        for block in roles.values():
            by_catalog.setdefault(_catalog(block["vlnv"]), set()).add(str(block["instance"]).strip("/"))
        self.kept: set[str] = set()
        self.remove: list[str] = []
        for catalog, instances in sorted(by_catalog.items()):
            named = {name for name, cell in self.cells.items() if _catalog(cell.get("vlnv", "")) == catalog}
            if not named:
                continue
            if not instances & named:
                # The DesignSpec renamed this IP's units; which CED cell each stands for is a decision.
                self.todo.append(f"[cells] DesignSpec blocks {sorted(instances)} ({catalog}) use no CED cell name "
                                 f"({sorted(named)}): name each block after the CED cell it keeps, then rerun "
                                 "scratch_bind, or remove_cell the CED cells it replaces")
                continue
            self.kept |= instances & named
            self.remove += sorted(named - instances)

    def catalog_of(self, instance: str) -> str:
        return _catalog((self.cells.get(instance.strip("/")) or {}).get("vlnv", ""))

    def spaces(self, master: str, target: str) -> list[str]:
        """Address spaces of a master cell in which the CED maps target (or a cell inside it)."""
        target = target.strip("/")
        return sorted({"/" + str(r["master"]).strip("/") + "/" + str(r["space"]) for r in self.maps
                       if str(r.get("master", "")).strip("/") == master.strip("/") and r.get("space")
                       and (str(r.get("slave", "")).strip("/") + "/").startswith(target + "/")})

    def irq_sink(self, pin: str, destination: str) -> str:
        """The sink a check_irq_paths record names for a CED interrupt input.

        A concat input feeding an AXI interrupt controller is checked at the controller's
        intr bit (the concat's lower inputs' widths summed), so the path is proven to the
        controller and not only to the concat."""
        match = re.fullmatch(r"/?(.+)/In(\d+)", pin)
        concat = match and self.catalog_of(match[1]) in ("xlconcat", "ilconcat")
        if concat and self.catalog_of(destination) == "axi_intc":
            config = self.cells[match[1].strip("/")].get("config") or {}
            bit = sum(int(config.get(f"CONFIG.IN{k}_WIDTH", 1) or 1) for k in range(int(match[2])))
            return f"{destination}/intr[{bit}]"
        return pin.strip("/")


def draft(spec: dict, intent: dict, snapshot: dict, fragments: list[dict], ced: list[dict] | None = None,
          clock_fingerprint: str | None = None) -> tuple[dict, list[str]]:
    """Draft the plan template and its TODO list.

    ced: the records of ced_reference.json when ced_fit.json says STRUCTURAL; the plan is then
    seeded with the CED (CedSeed). clock_fingerprint: the scratch clock_fingerprint.tcldict, which
    lets the draft register the DesignSpec's clock contracts."""
    found = requirements.requirements(spec, intent, snapshot, None, fragments)
    roles: dict[str, dict] = found["roles"]
    env = snapshot.get("environment") or {}
    board_part = env.get("board_part") or ""
    d = Draft()
    seed = CedSeed(ced, roles) if ced else None
    if seed:
        d.todo.extend(seed.todo)
    kept_roles = {r for r, b in roles.items() if seed and str(b["instance"]).strip("/") in seed.kept}
    instance_of = {}
    for role, block in roles.items():
        instance_of.setdefault(str(block.get("spec_block") or block.get("instance")), []).append(
            str(block["instance"]).strip("/"))
    instance_of = {name: names[0] for name, names in instance_of.items() if len(names) == 1}
    blocks = {str(b.get("name")): (i, b) for i, b in enumerate(spec.get("blocks") or []) if isinstance(b, dict)}
    covered: dict[str, dict] = {}
    ps_roles = [r for r, b in roles.items() if _catalog(b["vlnv"]) in compiler.PROCESSING_SYSTEMS
                and r not in kept_roles]
    noc_roles = [r for r, b in roles.items() if _catalog(b["vlnv"]).startswith("axi_noc")]
    needed = {(stage, proc) for stage, proc, _ in found["stages"]}

    # board
    name = re.sub(r"[^A-Za-z0-9_]+", "_", str(spec.get("name") or "design_1")).strip("_") or "design_1"
    d.op("board", "bd_create", {"op": "create_bd_design", "name": name})
    if board_part:
        d.call("board", "board_preset", "ipiasm::require_board_preset", [board_part], ["READY:"])
    else:
        d.todo.append("[board] no board part: before assemble_from_plan, open the board gate with "
                      "ipiasm::gate_override board <the user's words that there is no board>")
    # A CED seeds an empty design, so with a seed the processing system follows it in cells.
    ps_stage = "cells" if seed else "board"
    if seed:
        d.call("cells", "ced_seed", "ipiasm::instantiate_ced", [seed.name, seed.options], ["CED:OK", "SKIP:"])
        for cell in seed.remove:
            d.call("cells", f"remove_{cell}", "ipiasm::remove_cell", [f"/{cell}"], ["SUCCESS:", "SKIP:"])
    for role in ps_roles:
        d.call(ps_stage, f"create_{role}", "ipiasm::create_cell",
               [_ref(role, "vlnv_unversioned"), _ref(role, "instance"), {}], ["SUCCESS:", "SKIP:"])
        if board_part:
            d.todo.append(f"[{ps_stage}] after create_{role}: add an apply_automation op for the processing system's "
                          f"board preset rule (rule and config: ipcfg::rule_for_vlnv / automation_rule_options)")
            if ("board", "ipcfg::preset_drift") in needed:
                d.call(ps_stage, f"preset_{role}", "ipcfg::preset_drift", [_ref(role, "instance")], ["PRESET:OK"])
    if ("board", "ipiasm::attach_board_memory") in needed:
        d.todo.append("[board] the board's memory is bound to a NoC: add ipiasm::attach_board_memory "
                      "(ipcfg::help attach_board_memory) unless the processing-system automation already did")

    # cells
    for role, block in roles.items():
        if role not in ps_roles and role not in kept_roles:
            d.call("cells", f"create_{role}", "ipiasm::create_cell",
                   [_ref(role, "vlnv_unversioned"), _ref(role, "instance"), {}], ["SUCCESS:", "SKIP:"])
    counts: dict[str, int] = {}
    for block in roles.values():
        key = ":".join(str(block["vlnv"]).split(":")[:3])
        counts[key] = counts.get(key, 0) + 1
    if roles:
        d.call("cells", "inventory", "ipiasm::gate_inventory",
               [[item for key, count in counts.items() for item in (key, count)]], ["INVENTORY:OK"])
    for role, block in roles.items():
        index, spec_block = blocks.get(str(block.get("spec_block")), (None, {}))
        if index is None:
            continue
        parameters = spec_block.get("parameters") or {}
        instance = str(block["instance"]).strip("/")
        if "catalog_ip" in parameters:
            covered[f"/blocks/{index}/parameters/catalog_ip"] = {
                "value": parameters["catalog_ip"], "disposition": "external",
                "owner": "technology binding" + (f" (CED seed {seed.name})" if role in kept_roles else ""),
                "reason": f"{instance} is bound to this IP; ipiasm::gate_inventory counts its VLNV"}
        if role not in kept_roles:
            continue
        preset = parameters.get("preset", parameters.get("block_automation_preset"))
        if preset is not None and str(preset) in {str(v) for v in seed.options.values()}:
            key = "preset" if "preset" in parameters else "block_automation_preset"
            covered[f"/blocks/{index}/parameters/{key}"] = {
                "value": preset, "disposition": "external", "owner": f"CED seed {seed.name} (ipiasm::instantiate_ced)",
                "reason": f"the CED is instantiated with this preset option, which configures {instance}"}
        config = parameters.get("reference_config")
        if config == {}:
            covered[f"/blocks/{index}/parameters/reference_config"] = {
                "value": {}, "disposition": "external", "owner": f"CED seed {seed.name}",
                "reason": f"the reference sets nothing on {instance} beyond the CED's own configuration"}
        if not isinstance(config, dict) or not config:
            continue
        # The CED holds the reference values already: pin only those it does not, then read all back.
        live = seed.cells.get(instance, {}).get("config") or {}
        differ = {key: value for key, value in config.items() if str(live.get(key)) != str(value)}
        if differ:
            d.call("cells", f"req_{instance}", "ipcfg::require",
                   [f"/{instance}", differ, f"DesignSpec {spec_block.get('name')} reference_config differs from the CED"])
        d.call("cells", f"cfg_{instance}", "ipiasm::check_cell_config", [f"/{instance}", config], ["CELL_CONFIG:OK"])
        for key, value in config.items():
            pointer = f"/blocks/{index}/parameters/reference_config/" + key.replace("~", "~0").replace("/", "~1")
            covered[pointer] = {"value": value, "disposition": "hardware", "checks": [f"cfg_{instance}"],
                                "reason": f"{instance} {key} is the reference configuration, read back on the live cell"}
    if any(role not in kept_roles for role in roles):
        d.todo.append("[cells] configuration values: run proven_to_plan.py with the proven fragments, or add "
                      "ipcfg::require / check_cell_config for each cell the spec configures")

    # clocks
    contracts = spec.get("functional_contracts") or {}
    clock_reset = [(i, item) for i, item in enumerate(contracts.get("clock_reset") or [])
                   if isinstance(item, dict) and item.get("required", True)]
    if seed:
        # The CED's clock generator runs at the CED's rates; a DesignSpec rate it does not meet
        # is pinned on the generator output that drives those cells.
        wants: dict[str, dict[int, list[str]]] = {}
        for _, item in clock_reset:
            instance, hz = instance_of.get(str(item.get("target"))), _hz(item.get("clock_mhz"))
            for pin, (driver, freq) in seed.clocks.items():
                if instance and hz and pin.startswith(f"/{instance}/") and driver and _hz(float(freq or 0) / 1e6) != hz:
                    wants.setdefault(driver, {}).setdefault(hz, []).append(instance)
        for driver, rates in sorted(wants.items()):
            cell, _, pin = driver.rpartition("/")
            output = re.fullmatch(r"clk_out(\d+)", pin)
            if len(rates) == 1 and output and seed.catalog_of(cell) == "clk_wiz":
                (hz, targets), = rates.items()
                d.call("cells", f"clk_{cell.strip('/')}_{pin}", "ipcfg::require",
                       [cell, {f"CONFIG.CLKOUT{output[1]}_REQUESTED_OUT_FREQ": f"{hz / 1e6:.3f}"},
                        f"DesignSpec clock_reset runs {', '.join(sorted(set(targets)))} at {hz / 1e6:g} MHz"])
            else:
                d.todo.append(f"[cells] {driver} must drive {sorted(rates)} Hz for the DesignSpec's clock_reset: "
                              "configure its generator (ipcfg::require on the output's frequency)")
    if clock_reset and (not seed or len(kept_roles) < len(roles)):
        d.todo.append("[clocks] wire clocks and resets (wire_clk_rst_auto or connect_net + ensure_reset_domain) "
                      "of every cell the CED does not provide, before register_clock_contracts")
    checks, unbound = [], []
    for i, item in clock_reset:
        instance, hz = instance_of.get(str(item.get("target"))), _hz(item.get("clock_mhz"))
        if not instance or not hz:
            unbound.append(str(item.get("target")))
            continue
        checks.append({"id": f"clk_{instance}", "cell": f"/{instance}", "expected": hz,
                       "source": f"accepted-spec#/functional_contracts/clock_reset/{i}"})
        index, spec_block = blocks.get(str(item.get("target")), (None, {}))
        if index is not None and "clock_mhz" in (spec_block.get("parameters") or {}):
            covered[f"/blocks/{index}/parameters/clock_mhz"] = {
                "value": spec_block["parameters"]["clock_mhz"], "disposition": "hardware", "asserted_as": hz,
                "checks": ["after_check_clock_contracts"],
                "reason": f"{instance}'s clock inputs are registered at {hz} Hz and re-checked after validation"}
    if checks and clock_fingerprint:
        d.call("clocks", "clock_contracts", "ipiasm::register_clock_contracts", [checks, clock_fingerprint],
               ["CLOCK_CONTRACTS:SET"])
    elif clock_reset:
        d.todo.append("[clocks] add ipiasm::register_clock_contracts {<checks>} <out>/clock_fingerprint.tcldict "
                      "(ipcfg::help register_clock_contracts); scratch_bind writes the fingerprint file when the "
                      "DesignSpec has clock_reset contracts")
    if unbound:
        d.todo.append(f"[clocks] clock_reset targets with no single bound cell or no clock_mhz: {unbound}; "
                      "add their checks to register_clock_contracts")
    if ("clocks", "ipiasm::check_interface_clock_domains") in needed:
        d.call("clocks", "clock_domains", "ipiasm::check_interface_clock_domains", [], ["CLOCK_DOMAIN:"])
    for role in noc_roles:
        if ("clocks", "ipiasm::check_mem_noc_sysclk") in needed:
            d.call("clocks", f"sysclk_{role}", "ipiasm::check_mem_noc_sysclk", [_ref(role, "instance")], ["MEM_SYSCLK:"])
    if ("clocks", "ipiasm::check_undriven_clocks") in needed:
        d.call("clocks", "clocks_undriven", "ipiasm::check_undriven_clocks", [], ["UNDRIVEN_CLK:OK"])

    # connect: DesignSpec connections between two bound interfaces
    by_block: dict[str, list[str]] = {}
    for role, block in roles.items():
        by_block.setdefault(str(block.get("spec_block") or block.get("instance")), []).append(role)

    def endpoint(text: str) -> str | None:
        block, _, interface = str(text).partition(".")
        if not interface or len(by_block.get(block, [])) != 1:
            return None
        role = by_block[block][0]
        actual = (roles[role].get("interfaces") or {}).get(interface)
        return f"{_ref(role, 'instance')}/{actual}" if actual else None

    kept_blocks = {str(roles[role].get("spec_block")) for role in kept_roles}
    for item in contracts.get("connections") or []:
        if not isinstance(item, dict) or not item.get("required", True):
            continue
        src, dst = endpoint(item.get("source", "")), endpoint(item.get("destination", ""))
        label = f"{item.get('source')} -> {item.get('destination')}"
        ends = {str(item.get(side, "")).partition(".")[0] for side in ("source", "destination")}
        if ends <= kept_blocks:
            # Both cells come from the CED, wired; the reachability and interrupt checks prove it.
            continue
        if src and dst:
            d.call("connect", "conn_" + label, "ipiasm::connect_intf", [src, dst], ["SUCCESS:", "SKIP:"])
        else:
            d.todo.append(f"[connect] {label}: not a direct interface pair the binding resolved (NoC route, "
                          "automation, top-level port or unnamed interface); add connect_axi_auto / add_noc_si / "
                          "connect_intf / externalize_interface as fits")
    ports = [{"name": names[0]} for _, names in found["boundary"]]
    if ports:
        d.todo.append("[connect] create or externalize the top-level ports before check_required_ports")
        d.call("connect", "required_ports", "ipiasm::check_required_ports", [ports], ["PORTS:OK"])
    irq = _ced_irq_records(seed, contracts, instance_of, set(blocks)) if seed else None
    for stage, proc, _ in found["stages"]:
        if stage != "connect" or proc == "ipiasm::check_undriven_clocks":
            continue
        if proc == "ipiasm::attach_noc_port_clocks":
            d.call("connect", "noc_port_clocks", proc, [], ["NOC_CLK:"])
        elif proc == "ipiasm::check_irq_paths" and irq and not irq[1]:
            # Registered once (a retry must repair wiring), then traced; the final gate re-traces it.
            d.call("connect", "irq_contracts", "ipiasm::set_irq_contracts", [irq[0]], ["IRQ_CONTRACTS:SET"])
            d.call("connect", "irq_paths", proc, [irq[0]], ["IRQ_PATHS:OK"])
        else:
            if proc == "ipiasm::check_irq_paths" and irq:
                d.todo.append(f"[connect] interrupts the CED reference does not wire: {irq[1]}; add them, then "
                              "set_irq_contracts and check_irq_paths with every DesignSpec interrupt")
            d.todo.append(f"[connect] add {proc} (required: ipcfg::help {proc.split('::')[-1]})")
    if ("connect", "ipiasm::check_undriven_clocks") in needed:
        d.call("connect", "connect_undriven", "ipiasm::check_undriven_clocks", [], ["UNDRIVEN_CLK:OK"])

    # qos
    if noc_roles:
        d.todo.append("[qos] route and size every NoC connection: set_qos per NoC slave interface and "
                      "destination (bandwidths from the spec, else a recorded assumption)")
    pairs, unpaired = [], []
    for master, target, masters, targets in found["reach"]:
        found_pairs = [(space, f"/{cell.strip('/')}/*") for cell in targets for m in masters
                       for space in (seed.spaces(m, cell) if seed and targets != ["DRAM"] else [])]
        if found_pairs and all(any(t == f"/{cell.strip('/')}/*" for _, t in found_pairs) for cell in targets):
            pairs += [item for pair in found_pairs for item in pair]
            continue
        unpaired.append(master)
        d.todo.append(f"[qos] check_required_reachability pair for {master} -> {target}: an address space "
                      f"of {','.join(masters) or '?'} and target {','.join(targets)} "
                      "(every space a glob matches must reach)")
    if pairs:
        d.call("qos", "reachability", "ipiasm::check_required_reachability", [pairs], ["REACH:OK"])
    if unpaired:
        d.todo.append("[qos] add those pairs to one ipiasm::check_required_reachability op (a flat list)")
    if ("qos", "ipiasm::check_noc_pre_address") in needed:
        d.call("qos", "noc_pre", "ipiasm::check_noc_pre_address", [], ["NOC_PRE:OK"])
    if ("qos", "ipiasm::check_memory_subsystem") in needed:
        d.todo.append("[qos] add ipiasm::check_memory_subsystem <memory controllers wanted>")

    # address and validate
    d.call("address", "addr_auto", "ipiasm::auto_address", [], ["SUCCESS:"])
    if seed:
        _ced_window_scopes(d, seed, contracts, instance_of)
    if noc_roles:
        d.call("address", "noc_post", "ipiasm::check_noc_post_address", [], ["NOC_POST:OK"])
        d.call("address", "noc_legal", "ipiasm::check_noc_legality", [], ["NOCLEGAL:"])
    # The dispatcher fails OVERMAP:NA unless the op expects it, and a soft-processor
    # design has no processing system to overmap.
    if ps_roles:
        d.call("address", "overmap", "ipiasm::check_overmap", [], ["OVERMAP:OK"])
    ps = _ref(ps_roles[0], "instance") if ps_roles else ""
    d.call("validate", "bundle", "ipiasm::validate_bundle", [0, ps, 0, 1 if noc_roles else 0], ["BUNDLE:OK"])
    for proc, _ in found["after_validation"]:
        d.call("validate", "after_" + proc.split("::")[-1], proc, [], None)
    irqs = ("PL interrupts the spec routes to the processing system" if ps
            else "0 interrupts (that count is processing-system capacity; an interrupt controller's "
                 "inputs are checked by check_irq_paths)")
    d.todo.append(f"[validate] validate_bundle arguments: memory controllers, {irqs} "
                  "(ipcfg::help validate_bundle); declare_open for pins left open on purpose")

    skeleton = requirements.coverage_skeleton(found["cover"])["parameter_coverage"]
    clock_checked = any(op.get("proc") == "ipiasm::register_clock_contracts" for op in d.stages["clocks"])
    for pointer, entry in covered.items():
        if pointer in skeleton and (clock_checked or "after_check_clock_contracts" not in entry.get("checks", [])):
            skeleton[pointer] = entry
    blank = sum(1 for entry in skeleton.values() if not entry.get("reason"))
    if blank:
        d.todo.append(f"[coverage] {blank} parameter_coverage entries need a reason and checks "
                      "(or disposition external with an owner)")
    template = {"schema_version": 1, "name": name,
                "stages": [{"name": s, "operations": ops, "checkpoint": True} for s, ops in d.stages.items() if ops],
                "parameter_coverage": skeleton}
    # Listed now so the plan is written for delivery, not repaired after a full build.
    d.todo.extend(f"[deliver] {gap}" for gap in compiler.final_gate_gaps(spec, template))
    return template, d.todo


def _ced_irq_records(seed: CedSeed, contracts: dict, instance_of: dict, spec_blocks: set) -> tuple[list, list]:
    """(check_irq_paths records, DesignSpec interrupts the CED does not wire).

    One labelled {sink source <block>.<output>} record per DesignSpec interrupt output whose
    destination is a DesignSpec block, and an OPEN record for each controller input a removed
    CED cell drove: check_irq_paths refuses a floating input the table does not declare."""
    records, missing, sinks = [], [], set()
    for item in contracts.get("interrupts") or []:
        if not isinstance(item, dict) or item.get("required") is False or item.get("destination") not in spec_blocks:
            continue
        source, destination = instance_of.get(str(item.get("source"))), instance_of.get(str(item["destination"]))
        for output in item.get("outputs") or []:
            label = f"{item.get('source')}.{output}"
            pin = seed.irq.get(f"{source}/{output}") if source and destination else None
            if not pin:
                missing.append(label)
                continue
            sink = seed.irq_sink(pin, destination)
            sinks.add(sink)
            records.append([sink, f"{source}/{output}", label])
    destinations = sorted({instance_of.get(str(item.get("destination"))) or "" for item in
                           contracts.get("interrupts") or [] if isinstance(item, dict)} - {""})
    controller = next((cell for cell in destinations if seed.catalog_of(cell) == "axi_intc"), "")
    for source, pin in sorted(seed.irq.items()):
        if source.split("/")[0] in seed.remove:
            sink = seed.irq_sink(pin, controller)
            if sink not in sinks:
                sinks.add(sink)
                records.append([sink, "OPEN"])
    return records, missing


def _ced_window_scopes(d: Draft, seed: CedSeed, contracts: dict, instance_of: dict) -> None:
    """check_window_scope for each DesignSpec fixed address window the CED maps.

    The slave segment is /<cell>/<interface>/<block>, the block taken from the CED's mapped
    segment name SEG_<cell>_<block>; the allowed spaces are the window's masters' spaces that
    the CED maps to that cell."""
    for window in contracts.get("fixed_address_windows") or []:
        if not isinstance(window, dict) or window.get("required") is False:
            continue
        target = instance_of.get(str(window.get("target") or window.get("block")))
        maps = [r for r in seed.maps if target and str(r.get("slave", "")).strip("/") == target]
        prefix = f"SEG_{target}_"
        names = sorted({seg.rsplit("/", 1)[-1][len(prefix):] for seg in seed.segments
                        if seg.rsplit("/", 1)[-1].startswith(prefix)})
        masters = {instance_of.get(str(m)) for m in window.get("masters") or []}
        spaces = sorted({"/" + str(r["master"]).strip("/") + "/" + str(r["space"]) for r in maps
                         if str(r.get("master", "")).strip("/") in masters})
        label = window.get("name") or target
        if not maps or len(names) != 1 or not spaces:
            d.todo.append(f"[address] check_window_scope for window {label}: the CED reference does not map it "
                          "from its masters (add the window's slave segment and spaces by hand)")
            continue
        slave = f"/{target}/{maps[0].get('slave_intf')}/{names[0]}"
        if compiler._address(maps[0].get("offset")) != compiler._address(window.get("offset")):
            # assign_bd_address -offset moves a segment the CED already mapped (2026.2).
            d.call("address", f"place_{target}", "ipiasm::enforce_window_scope",
                   [str(label), slave, spaces, window.get("offset"), window.get("range")], ["WINDOW_SCOPE:"])
        d.call("address", f"win_{target}", "ipiasm::check_window_scope",
               [slave, spaces, window.get("offset"), window.get("range")], ["WINDOW_SCOPE:"])


def compiler_gaps(spec: dict, intent: dict, snapshot: dict, template: dict) -> list[str]:
    """What the compiler still refuses in the draft, one line per check."""
    gaps = []
    try:
        plan = binder.bind(spec, intent, snapshot, json.loads(json.dumps(template)))
    except (binder.BindError, ValueError) as error:
        return [f"bind: {str(error).splitlines()[0][:200]}"]
    try:
        compiler.compile_plan(plan)
    except compiler.PlanError as error:
        gaps.append(f"compile: {str(error)[:200]}")
    for check in (compiler.check_stage_gates, compiler.check_reachability_coverage,
                  compiler.check_boundary_coverage, compiler.check_parameter_coverage):
        try:
            check(spec, plan)
        except compiler.PlanError as error:
            gaps.append(f"{check.__name__}: {str(error)[:200]}")
    # What deliver's final gate will refuse after the whole build (about 5 min on Versal).
    gaps += [f"final_gate: {gap[:300]}" for gap in compiler.final_gate_gaps(spec, plan)]
    return gaps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n".join(__doc__.splitlines()[2:]))
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--intent", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--fragments", nargs="*", type=Path, default=[])
    parser.add_argument("--ced-fit", type=Path, help="ced_fit.json; a STRUCTURAL fit seeds the plan with its CED")
    parser.add_argument("--clock-fingerprint", type=Path, help="clock_fingerprint.tcldict from ipiasm::clock_fingerprint")
    parser.add_argument("--out", type=Path, required=True, help="assembly_plan.template.json to write")
    parser.add_argument("--todo", type=Path, help="TODO list (default: <out without .json>.todo.md)")
    options = parser.parse_args(argv)
    try:
        spec, intent, snapshot = (json.loads(p.read_text(encoding="utf-8"))
                                  for p in (options.spec, options.intent, options.snapshot))
        fragments = [json.loads(p.read_text(encoding="utf-8")) for p in options.fragments]
        ced = None
        if options.ced_fit:
            fit = json.loads(options.ced_fit.read_text(encoding="utf-8"))
            reference = Path(fit.get("reference") or options.ced_fit.with_name("ced_reference.json"))
            # EXACT and CONFIG_ONLY fits are delivered with deliver_ced, never planned.
            if fit.get("verdict") == "STRUCTURAL" and reference.is_file():
                ced = [json.loads(line) for line in reference.read_text(encoding="utf-8").splitlines() if line.strip()]
        fingerprint = str(options.clock_fingerprint.resolve()) if options.clock_fingerprint else None
        template, todo = draft(spec, intent, snapshot, fragments, ced, fingerprint)
        gaps = compiler_gaps(spec, intent, snapshot, template)
    except (OSError, ValueError, requirements.RequirementError) as error:
        print(f"DRAFT_PLAN_FAIL:{str(error).splitlines()[0][:300]}")
        return 1
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(json.dumps(template, indent=1) + "\n", encoding="utf-8")
    todo_path = options.todo or options.out.with_name(options.out.name.replace(".json", "") + ".todo.md")
    lines = ["# What the draft plan still needs", "", *(f"- {item}" for item in todo), "",
             "## What the compiler still refuses (fix, then recompile with --spec)", "",
             *(f"- {gap}" for gap in gaps or ["nothing"])]
    todo_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ops = sum(len(stage["operations"]) for stage in template["stages"])
    for item in todo[:12]:
        print("TODO " + item)
    if len(todo) > 12:
        print(f"TODO ... {len(todo) - 12} more in {todo_path}")
    for gap in gaps[:4]:
        print("GAP " + gap)
    print(f"DRAFT_PLAN:OK ops={ops} todo={len(todo)} gaps={len(gaps)} template={options.out} todo_file={todo_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
