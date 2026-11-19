#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Validate a structured assembly plan and compile it to guarded Tcl.

The plan cannot contain raw Tcl. Every operation is a small whitelisted adapter
or a call to an explicitly allowed ipiasm/ipcfg procedure.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ALLOWED_PROCS = {
    "ipiasm::set_clock_contracts",
    "ipiasm::register_clock_contracts",
    "ipiasm::check_clock_contracts",
    "ipiasm::check_feasibility",
    "ipiasm::stage",
    "ipiasm::policy",
    "ipiasm::register_board_repo",
    "ipiasm::require_board_preset",
    "ipiasm::attach_board_memory",
    "ipiasm::check_memory_regions",
    "ipiasm::region_selectors",
    "ipiasm::wire_mem_noc_sysclk",
    "ipiasm::check_mem_noc_sysclk",
    "ipiasm::instantiate_ced",
    "ipiasm::remove_cell",
    "ipiasm::create_cell",
    "ipiasm::create_module_ref",
    "ipiasm::instantiate_n",
    "ipiasm::gate_inventory",
    "ipiasm::check_bram_backing",
    "ipiasm::check_cell_config",
    "ipiasm::check_source_files",
    "ipiasm::check_interface_routes",
    "ipiasm::check_ps_fabric_irqs",
    "ipiasm::set_irq_contracts",
    "ipiasm::check_irq_contracts",
    "ipiasm::check_irq_paths",
    "ipiasm::record_evidence",
    "ipiasm::wire_clk_rst_auto",
    "ipiasm::build_clocks",
    "ipiasm::fanout_domain",
    "ipiasm::ensure_reset_domain",
    "ipiasm::check_interface_clock_domains",
    "ipiasm::set_interface_contracts",
    "ipiasm::preflight_interface_contracts",
    "ipiasm::preflight_connect",
    "ipiasm::check_mipi_video_clocks",
    "ipiasm::connect_board_intf",
    "ipiasm::externalize_oos",
    "ipiasm::connect_intf",
    "ipiasm::disconnect_intf_edge",
    "ipiasm::connect_net",
    "ipiasm::declare_open",
    "ipiasm::connect_axi_auto",
    "ipiasm::build_control_fanout",
    "ipiasm::add_noc_si",
    "ipiasm::attach_noc_port_clocks",
    "ipiasm::attach_aie_config",
    "ipiasm::check_aie_config",
    "ipiasm::externalize_interface",
    "ipiasm::create_port",
    "ipiasm::name_port",
    "ipiasm::check_wrapper_boundary",
    "ipiasm::check_ps_noc_masters",
    "ipiasm::connect_external_axi",
    "ipiasm::sync_intf_freq_hz",
    "ipiasm::apply_qos_plan",
    "ipiasm::set_qos",
    "ipiasm::set_unused_qos_routes",
    "ipiasm::check_noc_pre_address",
    "ipiasm::place_named_windows",
    "ipiasm::auto_address",
    "ipiasm::restrict_master",
    "ipiasm::restrict_address_space",
    "ipiasm::restrict_window_to_spaces",
    "ipiasm::check_window_scope",
    "ipiasm::check_noc_post_address",
    "ipiasm::check_overmap",
    "ipiasm::check_required_dram_masters",
    "ipiasm::validate_bundle",
    "ipiasm::check_board_hooks",
    "ipiasm::check_camera_isp_format",
    "ipiasm::check_camera_line_buffer",
    "ipiasm::check_camera_geometry",
    "ipiasm::check_isp_resources",
    "ipiasm::checkpoint",
    "ipcfg::require",
    "ipcfg::apply_dict",
    "ipcfg::verify_intent",
    "ipcfg::audit_requirements",
    "ipcfg::clock_placement_status",
    "ipcfg::board_bind",
    "ipcfg::run_automation",
    "ipcfg::preset_drift",
    "ipiasm::check_required_reachability",
    "ipiasm::enforce_window_scope",
    "ipiasm::check_visibility",
    "ipiasm::check_required_dram_masters",
    "ipiasm::check_undriven_clocks",
    "ipiasm::check_required_ports",
    "ipiasm::check_noc_legality",
    "ipiasm::check_memory_subsystem",
}
OBJECT_GETTERS = {
    "cell": "get_bd_cells",
    "intf_pin": "get_bd_intf_pins",
    "pin": "get_bd_pins",
    "intf_port": "get_bd_intf_ports",
    "port": "get_bd_ports",
}
DEFAULT_EXPECT = (
    "BRAM:OK", "CELL_CONFIG:OK", "SOURCE_FILES:OK", "INTERFACE_ROUTES:OK",
    "CLOCK_CONTRACTS:SET", "CLOCK_CONTRACTS:OK",
    "FEASIBILITY:OK",
    "SUCCESS:", "SKIP:", "STAGE:", "READY:", "INVENTORY:OK",
    "IRQ_CAPACITY:OK", "MEM_SYSCLK:OK", "AIE:OK", "EXTERNAL:OK",
    "EXT_AXI:OK", "QOS:", "NOC_PRE:OK", "NOC_POST:OK", "NOC_CLK:", "PRESET:OK",
    "NAMED_WINDOWS:", "WINDOW_SCOPE:", "RESTRICT:OK", "OVERMAP:OK",
    "FORMAT:OK", "LINEBUF:OK", "GEOMETRY:OK", "ISP_RESOURCES:OK", "MEM_REGION:OK", "REGION_SELECT:OK",
    "CLOCK_PLACEMENT:OK", "BOARD_REPO:OK", "IRQ_CONTRACTS:SET", "IRQ_PATHS:OK",
    "EVIDENCE:OK", "AUTOMATION:OK", "BOARD:bound", "REACH:OK", "VISIBILITY:OK", "DRAM_REACH:OK",
    "UNDRIVEN_CLK:OK", "PORTS:OK", "NOCLEGAL:OK", "MEM:OK", "REQ:pinned+applied", "REQ:OK",
    "FREQ_SYNC:OK", "RESET_SYNC:WIRED", "CLOCK_DOMAIN:",
    "INTF_CONTRACTS:SET", "INTF_PREFLIGHT:", "PREFLIGHT:",
    "VIDEOCLK:", "DRAM_REACH:", "BUNDLE:OK", "CHECKPOINT:", "BD:OK", "PROPERTY:OK",
    "HOOKS:", "IRQ_CONTRACTS:", "PS_NOC:", "BOUNDARY:", "AUTO:", "MANUAL:", "PORT:OK", "PORT_NAME:OK",
    "POLICY:", "QOS_UNUSED:SET", "OPEN:OK", "CED:OK",
)
SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")


class PlanError(ValueError):
    pass


def _brace(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return "{}"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key, item in value.items():
            parts.extend((_brace(str(key)), _brace(item)))
        return "[list " + " ".join(parts) + "]"
    if isinstance(value, list):
        return "[list " + " ".join(_brace(item) for item in value) + "]"
    text = str(value)
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"[encoding convertfrom utf-8 [binary decode base64 {{{encoded}}}]]"


def _validate_expect(prefixes: object, op_id: str) -> list[str]:
    if not isinstance(prefixes, list) or not prefixes:
        raise PlanError(f"{op_id}.expect must be a non-empty status list")
    for prefix in prefixes:
        if (not isinstance(prefix, str)
                or not re.fullmatch(r"[A-Z][A-Z0-9_]*:(?:[A-Za-z0-9_.=+-]+)?", prefix)
                or re.search(r"(?:^|[_:])(FAIL|ERROR|PENDING|PARTIAL)(?:$|[_:])", prefix)):
            raise PlanError(f"{op_id}.expect has invalid status {prefix!r}")
    return prefixes


def _tcl_words(text: str) -> list[str]:
    """Top-level words of a Tcl list; enough for proc argument lists."""
    words, depth, current = [], 0, ""
    for char in text:
        if char == "{":
            depth += 1
            if depth == 1:
                continue
        elif char == "}":
            depth -= 1
            if depth == 0:
                words.append(current)
                current = ""
                continue
        if depth == 0 and char.isspace():
            if current:
                words.append(current)
                current = ""
            continue
        current += char
    if current:
        words.append(current)
    return words


def _signatures() -> dict[str, tuple[int, int]]:
    """(minimum, maximum; -1 = variadic) argument counts from the installed helper libraries."""
    here = Path(__file__).resolve().parent
    libraries = [*(here.parent / "lib").glob("*.tcl"),
                 *(here.parents[1] / "vivado-ip-configurator" / "lib").rglob("*.tcl")]
    found: dict[str, tuple[int, int]] = {}
    for library in libraries:
        for match in re.finditer(r"^proc\s+(?:::)?((?:ipiasm|ipcfg)::\w+)\s+", library.read_text(errors="replace"), re.M):
            text, start = match.string, match.end()
            if text[start] == "{":
                depth, end = 0, start
                for end in range(start, len(text)):
                    depth += {"{": 1, "}": -1}.get(text[end], 0)
                    if depth == 0:
                        break
                formals = [_tcl_words(word) or [word] for word in _tcl_words(text[start + 1:end])]
            else:
                formals = [[text[start:].split()[0]]]
            required = max((i + 1 for i, formal in enumerate(formals) if len(formal) == 1 and formal[0] != "args"),
                           default=0)
            variadic = bool(formals) and formals[-1] == ["args"]
            found[match.group(1)] = (required, -1 if variadic else len(formals))
    return found


SIGNATURES = _signatures()


def _compile_call(operation: dict[str, Any]) -> list[str]:
    proc = operation.get("proc")
    if not isinstance(proc, str) or proc not in ALLOWED_PROCS:
        raise PlanError(f"procedure {proc!r} is not allowed (allowed list: compile_assembly_plan.py --list-ops)")
    args = operation.get("args", [])
    if not isinstance(args, list):
        raise PlanError("call.args must be a list")
    if proc in SIGNATURES:
        minimum, maximum = SIGNATURES[proc]
        if len(args) < minimum or (maximum >= 0 and len(args) > maximum):
            expected = f"{minimum}..{maximum}" if maximum >= 0 else f"at least {minimum}"
            raise PlanError(f"{operation.get('id', proc)}: {proc} got {len(args)} argument(s), takes {expected} "
                            f"(call shape: ipcfg::help {proc})")
    flat_pairs = {"ipiasm::check_required_reachability": "space, target, space, target",
                  "ipiasm::set_unused_qos_routes": "route, reason, route, reason"}
    if proc in flat_pairs and args and isinstance(args[0], list):
        # Tcl reads the list two items at a time, so a list of pairs would pair each
        # {space target} with the next one and check nothing the plan meant.
        if any(not isinstance(item, str) for item in args[0]) or len(args[0]) % 2:
            raise PlanError(f"{operation.get('id', proc)}: {proc} takes one flat list "
                            f"[{flat_pairs[proc]}, ...] of strings; got a list of pairs or an odd count. "
                            "Flatten it")
    command = f"{proc} " + " ".join(_brace(arg) for arg in args)
    return [f"set __result [{command}]"]


def _compile_set_property(operation: dict[str, Any]) -> list[str]:
    obj = operation.get("object")
    properties = operation.get("properties")
    if not isinstance(obj, dict) or obj.get("kind") not in OBJECT_GETTERS:
        raise PlanError("set_property.object requires kind and path")
    if not isinstance(properties, dict) or not properties:
        raise PlanError("set_property.properties must be a non-empty object")
    for name in properties:
        if not str(name).startswith("CONFIG."):
            raise PlanError(f"only CONFIG.* properties are allowed, got {name!r}")
    getter = OBJECT_GETTERS[obj["kind"]]
    path = obj.get("path")
    if not isinstance(path, str) or not path:
        raise PlanError("set_property.object.path is required")
    allow_board = operation.get("allow_board", False)
    if allow_board is not False:
        reason = operation.get("reason")
        if allow_board is not True or obj["kind"] != "cell" or not isinstance(reason, str) or not reason.strip():
            raise PlanError("set_property.allow_board must be true, on a cell, with a non-empty reason")
    if obj["kind"] == "cell":
        hooks = {k: v for k, v in properties.items() if str(k).endswith("_BOARD_INTERFACE")}
        if hooks:
            # A board-interface write is a board bind: record what the preset then owns.
            others = {k: v for k, v in properties.items() if k not in hooks}
            reason = operation.get("reason") or "board preset"
            return [
                f"set __applied [ipcfg::board_bind {_brace(path)} {_brace(hooks)} {_brace(others)} {_brace(reason)}]",
                f'set __result [expr {{[string match BOARD:bound* $__applied] ? {_brace("PROPERTY:OK " + path)} : $__applied}}]',
            ]
        # Cell configuration goes through the board-ownership guard and attempt budget.
        return [
            f"set __applied [ipcfg::apply_dict {_brace(path)} {_brace(properties)} {_brace(allow_board)}]",
            f'set __result [expr {{[string match SUCCESS:* $__applied] ? {_brace("PROPERTY:OK " + path)} : $__applied}}]',
        ]
    return [
        f"set __object [{getter} -quiet {_brace(path)}]",
        f'if {{$__object eq ""}} {{error {_brace("PLAN_FAIL:no " + obj["kind"] + " " + path)}}}',
        f"set_property -dict {_brace(properties)} $__object",
        f'set __result {_brace("PROPERTY:OK " + path)}',
    ]


def _compile_create_bd(operation: dict[str, Any], verify_existing: bool = False) -> list[str]:
    name = operation.get("name")
    if not isinstance(name, str) or not SAFE_ID.fullmatch(name):
        raise PlanError("create_bd_design.name must be a safe identifier")
    if verify_existing:
        return [
            f'if {{[current_bd_design] ne {_brace(name)}}} {{error "PLAN_ENV_FAIL:expected open BD {name}"}}',
            f'set __result "BD:OK {name} already open"',
        ]
    # The delivery flow opens the BD before begin_design, so the plan often finds an empty BD of
    # its own name already open; creating it again fails on the taken name. Adopt it only when
    # empty: anything in it was not built by this plan. -quiet keeps a missing BD from logging
    # the native ERROR the operation's message capture would fail on.
    return [
        f"if {{![catch {{current_bd_design -quiet}} __open] && $__open eq {_brace(name)}",
        "        && ![llength [get_bd_cells -quiet]] && ![llength [get_bd_ports -quiet]]",
        "        && ![llength [get_bd_intf_ports -quiet]]} {",
        f'    set __result "BD:OK {name} (already open and empty)"',
        f'}} elseif {{[llength [get_files -quiet */{name}.bd]]}} {{',
        # Agents reran the whole plan after a late stage failed, lost the stage record to this
        # error and rebuilt the project; the stage rerun was the cheap repair.
        f'    error "PLAN_ENV_FAIL:block design {name} already exists and holds a build: rerun only the'
        " stages ipiasm::plan_execution_status <plan> reports as incomplete, with ipiasm::assemble_from_plan"
        ' <plan> <stage>; to rebuild from nothing, remove the BD from the project first"',
        "} else {",
        f"    create_bd_design {_brace(name)}",
        f'    set __result "BD:OK {name}"',
        "}",
    ]


def _compile_automation(operation: dict[str, Any]) -> list[str]:
    rule = operation.get("rule")
    target = operation.get("target")
    kind = operation.get("target_kind", "cell")
    config = operation.get("config", {})
    if not isinstance(rule, str) or not rule.startswith("xilinx.com:bd_rule:"):
        raise PlanError("apply_automation.rule must be a Xilinx BD rule")
    if kind not in OBJECT_GETTERS or not isinstance(target, str):
        raise PlanError("apply_automation target_kind/path is invalid")
    if not isinstance(config, dict):
        raise PlanError("apply_automation.config must be an object")
    getter = OBJECT_GETTERS[kind]
    if kind == "cell":
        # Checks the options against the rule and the board preset, within the attempt budget.
        return [f"set __result [ipcfg::run_automation {_brace(target)} {_brace(rule)} {_brace(config)}]"]
    return [
        f"set __object [{getter} -quiet {_brace(target)}]",
        f'if {{$__object eq ""}} {{error {_brace("PLAN_FAIL:no automation target " + target)}}}',
        f"apply_bd_automation -rule {_brace(rule)} -config {_brace(config)} $__object",
        f'set __result {_brace("AUTOMATION:OK " + rule + " " + target)}',
    ]


def _is_region_dicts(regions: object) -> bool:
    return (isinstance(regions, list) and bool(regions)
            and all(isinstance(r, dict) and "base" in r and "size" in r for r in regions))


def _region_count(args: list[Any], op_id: str) -> int:
    regions = args[0] if args else None
    if _is_region_dicts(regions):
        return len(regions)
    if isinstance(regions, dict) and regions:
        return len(regions)
    if isinstance(regions, list) and regions and len(regions) % 2 == 0:
        return len(regions) // 2
    raise PlanError(f"{op_id}: check_memory_regions needs the DesignSpec regions as a "
                    "non-empty {name size ...} list or {name base size} objects")


def _check_board_hooks_order(stages: list[dict[str, Any]]) -> None:
    ordered = [op for entry in stages for op in entry["operations"]]
    creators = {"ipiasm::create_cell", "ipiasm::instantiate_n", "ipiasm::create_module_ref", "ipiasm::instantiate_ced"}
    first_cell = next((i for i, op in enumerate(ordered) if op.get("proc") in creators), None)
    for i, op in enumerate(ordered):
        if op.get("proc") == "ipiasm::check_board_hooks" and (first_cell is None or i < first_cell):
            raise PlanError(f"{op.get('id', 'check_board_hooks')}: ipiasm::check_board_hooks inspects cells for "
                            "board hooks, so it must come after the cells are created (it returns HOOKS:none on "
                            "an empty design); move it after the cell stage")


def _check_reads_after_create(stages: list[dict[str, Any]]) -> None:
    # A readback of a cell the plan creates later reads a cell that does not exist yet, so
    # the stage halts on its own ordering. Cells the plan never creates are not judged here.
    ordered = [op for entry in stages for op in entry["operations"]]
    created = {}
    for i, op in enumerate(ordered):
        args = op.get("args") or []
        if op.get("proc") == "ipiasm::create_cell" and len(args) > 1:
            created.setdefault(str(args[1]).strip("/"), i)
    for i, op in enumerate(ordered):
        args = op.get("args") or []
        if op.get("proc") in {"ipiasm::check_cell_config", "ipcfg::preset_drift"} and args:
            cell = str(args[0]).strip("/")
            if created.get(cell, -1) > i:
                raise PlanError(f"{op.get('id', op.get('proc'))}: {op.get('proc')} reads {cell} before "
                                f"{ordered[created[cell]].get('id', 'create_cell')} creates it; move the readback "
                                "after the create")


def _leaf_strings(node: object):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _leaf_strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _leaf_strings(value)


def _check_ports_after_create(stages: list[dict[str, Any]]) -> None:
    # An operation naming a top-level port the plan creates later finds no such port, so the
    # stage halts on its own ordering. Ports the plan never creates are not judged here.
    ordered = [op for entry in stages for op in entry["operations"]]
    created = {}
    for i, op in enumerate(ordered):
        args = op.get("args") or []
        if op.get("proc") == "ipiasm::create_port" and args:
            created.setdefault(str(args[0]).strip("/"), i)
    for i, op in enumerate(ordered):
        for leaf in _leaf_strings(op.get("args") or []):
            port = leaf.lstrip("/")
            if "/" not in port and created.get(port, -1) > i:
                raise PlanError(f"{op.get('id', op.get('proc'))}: uses top-level port {leaf} before "
                                f"{ordered[created[port]].get('id', 'create_port')} creates it; move the "
                                "create_port before its first use")


def _check_stage_sequence(stages: list[dict[str, Any]]) -> None:
    # Stages run in plan order and each needs every earlier one complete. The canonical
    # stages are run by name, so a stage with another name before the last canonical one
    # is never run and blocks every stage after it. Extra stages go after the canonical ones.
    names = [entry["name"] for entry in stages]
    canonical = [i for i, name in enumerate(names) if name in STAGE_ORDER]
    if not canonical:
        return
    blocking = [name for i, name in enumerate(names) if name not in STAGE_ORDER and i < canonical[-1]]
    if blocking:
        raise PlanError(f"STAGE_SEQUENCE_FAIL:stage(s) {', '.join(blocking)} sit before {names[canonical[-1]]}; "
                        f"only {', '.join(STAGE_ORDER)} are run by name, so a stage between them is never run "
                        "and blocks the stages after it. Fold its operations into the canonical stage they "
                        "belong to, or move the stage after the last canonical one")


def _check_bram_before_inventory(stages: list[dict[str, Any]]) -> None:
    # gate_inventory requires every BRAM controller backed by a connected memory, so the
    # controller-to-memory connection belongs before the first inventory gate.
    ordered = [op for entry in stages for op in entry["operations"]]
    gate = next((i for i, op in enumerate(ordered) if op.get("proc") == "ipiasm::gate_inventory"), None)
    if gate is None:
        return
    for i, op in enumerate(ordered[gate + 1:], gate + 1):
        if op.get("proc") in {"ipiasm::connect_intf", "ipiasm::connect_net"} and any(
                "BRAM_PORT" in str(arg) for arg in (op.get("args") or [])[:2]):
            raise PlanError(f"{op.get('id', op.get('proc'))}: connects a BRAM port after "
                            f"{ordered[gate].get('id', 'gate_inventory')}, which requires every BRAM controller "
                            "backed by connected memory; move the connection before the inventory gate")


def _check_memory_plan(stages: list[dict[str, Any]]) -> None:
    # DDR windows exist only after a NoC input is routed to an MC port, and each
    # region beyond the IP default needs a selector in the same board bind.
    ordered = [(entry["name"], op) for entry in stages for op in entry["operations"]]
    procs = [op.get("proc") if op.get("op") == "call" else None for _, op in ordered]
    attaches = [i for i, proc in enumerate(procs) if proc == "ipiasm::attach_board_memory"]
    if not attaches:
        return
    routes = [i for i, proc in enumerate(procs)
              if proc in {"ipiasm::add_noc_si", "ipiasm::set_qos", "ipiasm::apply_qos_plan"}]
    after = max(attaches + routes[:1])
    checks = [i for i, proc in enumerate(procs) if proc == "ipiasm::check_memory_regions" and i > after]
    if not checks:
        anchor = ordered[after][1].get("id", f"operation {after}")
        present = [ordered[i][1].get("id", str(i)) for i, proc in enumerate(procs) if proc == "ipiasm::check_memory_regions"]
        where = f" (found only before it: {', '.join(present)})" if present else ""
        raise PlanError("attach_board_memory needs a later ipiasm::check_memory_regions with the "
                        "DesignSpec memory regions, after the first NoC route to a memory controller: "
                        f"place it after operation '{anchor}'{where}")
    selectors = 0
    for i in attaches:
        args = ordered[i][1].get("args", [])
        regions = args[2] if len(args) > 2 else []
        if _is_region_dicts(regions):
            pairs = len(regions)
        else:
            pairs = len(regions) if isinstance(regions, dict) else len(regions) // 2 if isinstance(regions, list) else 0
        selectors += max(1, pairs)
    wanted = max(_region_count(ordered[i][1].get("args", []), ordered[i][1].get("id", "check")) for i in checks)
    if selectors < wanted:
        raise PlanError(f"check_memory_regions expects {wanted} region(s) but attach_board_memory selects "
                        f"{selectors}; pass the DesignSpec regions ({{name, base, size}} objects) as "
                        f"attach_board_memory's third argument so it picks each MC_CHAN_REGION selector")


NUMBER_TEXT = re.compile(r"\s*(0[xX][0-9a-fA-F]+|[0-9]+)\s*")


def _number_text(value: object) -> int | None:
    """The integer a string spells, in hex or decimal ("0x80000008000", "4096"), else None."""
    return int(value, 0) if isinstance(value, str) and NUMBER_TEXT.fullmatch(value) else None


def _scalar_forms(value: object) -> set:
    """The string forms one scalar can take once JSON and Tcl have both touched it."""
    if isinstance(value, bool):
        return {"true" if value else "false", "1" if value else "0"}
    if isinstance(value, float) and value.is_integer():
        return {str(value), str(int(value))}
    # An integer and the hex or decimal text that spells it are the same value.
    number = value if isinstance(value, int) else _number_text(value)
    if number is not None:
        return {str(number), hex(number)}
    return {str(value).strip().lower()}


def _asserted_values(node: object, found: set) -> None:
    for child in (node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()):
        _asserted_values(child, found)
    if not isinstance(node, (dict, list)) and node is not None:
        found |= _scalar_forms(node)


def _registration_args(proc: str, all_operations: list) -> list:
    """Args of the registrations that a registration-backed readback verifies.

    `ipiasm::check_clock_contracts` and its siblings take no arguments of their
    own: the values they verify were handed to the paired `ipiasm::set_*`
    earlier in the plan, so that is where the parameter's value has to appear.
    """
    setter = proc.replace("::check_", "::set_", 1)
    return [operation.get("args", []) for operation in all_operations
            if operation.get("op") == "call" and _registers_as(operation.get("proc")) == setter]


def _names_config_keys(node: object) -> bool:
    for child in (node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()):
        if _names_config_keys(child):
            return True
    return isinstance(node, dict) and any(str(key).upper().startswith("CONFIG.") for key in node)


def _readback_carries_values(operation: dict, all_operations: list) -> bool:
    """Whether this readback is the kind that names the values it verifies.

    A cell-config readback carries a CONFIG dict and a registration-backed
    contract check carries its paired `ipiasm::set_*` arguments, so a value
    absent from either really is unasserted. A structural readback - one that
    verifies a port exists or a connection was made - never names a parameter's
    value, and demanding one there would reject correct plans.
    """
    return bool(_registration_args(operation.get("proc", ""), all_operations)
                or _names_config_keys(operation.get("args", [])))


def _asserts_value(operation: dict, value: object, all_operations: list) -> bool:
    """Whether a readback operation actually expects this parameter's value.

    A check that never names the value cannot have verified it, however
    well-formed the plan is - the parameter reaches hardware unasserted and the
    readback still reports OK.
    """
    found = set()
    _asserted_values(operation.get("args", []), found)
    for args in _registration_args(operation.get("proc", ""), all_operations):
        _asserted_values(args, found)
    return bool(_scalar_forms(value) & found)


def _carries_pairs(operation: dict, pairs: dict) -> bool:
    """Whether one CONFIG dict in this readback's arguments expects every key/value pair."""
    def dicts(node):
        if isinstance(node, dict):
            yield node
        for child in (node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()):
            yield from dicts(child)

    return any(all(key in found and _scalar_forms(found[key]) & _scalar_forms(value) for key, value in pairs.items())
               for found in dicts(operation.get("args", [])))


def check_parameter_coverage(spec: dict, plan: dict) -> None:
    required = {}

    def visit(value, pointer):
        if isinstance(value, dict) and value:
            for key, child in value.items():
                visit(child, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list) and value:
            for index, child in enumerate(value):
                visit(child, pointer + "/" + str(index))
        else:
            required[pointer] = value

    for index, block in enumerate(spec["blocks"]):
        for key, value in block.get("parameters", {}).items():
            visit(value, f"/blocks/{index}/parameters/" + key.replace("~", "~0").replace("/", "~1"))
    coverage = plan.get("parameter_coverage", {})
    if not isinstance(coverage, dict):
        raise PlanError("parameter_coverage must be an object keyed by DesignSpec JSON pointers")
    missing = sorted(required.keys() - coverage.keys())
    extra = sorted(coverage.keys() - required.keys())
    if missing or extra:
        raise PlanError(f"PARAMETER_COVERAGE_FAIL:missing={missing} unknown={extra}")
    all_operations = [operation for stage in plan["stages"] for operation in stage["operations"]]
    operations = {operation["id"]: operation for operation in all_operations if "id" in operation}

    def problem(pointer, value):
        """Why this leaf's coverage is not acceptable, or None."""
        entry = coverage[pointer]
        if not isinstance(entry, dict) or "value" not in entry or entry["value"] != value:
            return f"{pointer} missing or stale source value"
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            return f"{pointer} requires mapping rationale"
        if entry.get("disposition") == "external":
            if not isinstance(entry.get("owner"), str) or not entry["owner"].strip():
                return f"{pointer} requires external owner"
            return None
        checks = entry.get("checks")
        if entry.get("disposition") != "hardware" or not isinstance(checks, list) or not checks:
            return f"{pointer} requires hardware checks or external ownership"
        for check in checks:
            operation = operations.get(check) if isinstance(check, str) else None
            if (not operation or operation.get("op") != "call"
                    or not operation.get("proc", "").startswith("ipiasm::check_")
                    or operation["proc"] not in ALLOWED_PROCS):
                return f"{pointer} invalid readback operation {check!r}"
        # A value whose hardware form differs - a tile index held as CONFIG.C_TILE0_ENABLE true,
        # a nominal 74.25 MHz realized as 74251394 Hz - names that form, and a check must carry it.
        if "asserted_as" in entry:
            form = entry["asserted_as"]
            if isinstance(form, dict) and form and all(str(key).upper().startswith("CONFIG.") for key in form):
                if not any(_carries_pairs(operations[check], form) for check in checks):
                    return f"{pointer} asserted_as {form} is not carried by any of {checks}"
            elif isinstance(form, (str, int, float)) and not isinstance(form, bool):
                if not any(_asserts_value(operations[check], form, all_operations) for check in checks):
                    return f"{pointer} asserted_as {form!r} is never expected by {checks}"
            else:
                return f"{pointer} asserted_as must be a scalar or a non-empty CONFIG.* key/value object"
            return None
        # A number written as text ("0x80000008000") is held to the same rule as a number.
        if (not isinstance(value, (int, float)) or isinstance(value, bool)) and _number_text(value) is None:
            return None
        naming = [operations[check] for check in checks
                  if _readback_carries_values(operations[check], all_operations)]
        if naming and not any(_asserts_value(operation, value, all_operations) for operation in naming):
            return f"{pointer} value {value!r} is never expected by {checks}"
        return None

    # Report every problem at once: one repair pass should fix them all.
    problems = [found for found in (problem(pointer, value) for pointer, value in required.items()) if found]
    if problems:
        hint = ("; the readback must carry the parameter's own value - for a cell parameter the CONFIG key "
                "that holds it, for a registration-backed readback such as ipiasm::check_clock_contracts the "
                "paired ipiasm::set_* call, or the hardware form named in asserted_as - or the value reaches "
                "hardware unasserted and the check still reports OK"
                if any("is never expected by" in found for found in problems) else "")
        raise PlanError("PARAMETER_COVERAGE_FAIL:" + "; ".join(problems) + hint)


PROVEN_FILE = "proven_ip_config.json"


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def check_proven_provenance(plan: dict, plan_dir: Path) -> None:
    """Hold every proven role to the exact Stage 2 values embedded from proven_ip_config.json.

    A plan built next to a proven_ip_config.json must reference it; each proven
    role's create/configure/readback operations must carry provenance whose
    sha256 matches the proven cell and whose dict is the proven `requested`
    verbatim; and no un-provenanced create_cell may bind a proven role.
    """
    reference = plan.get("proven_ip_config")
    if reference is None:
        if (plan_dir / PROVEN_FILE).exists():
            raise PlanError(f"PROVEN_CONFIG_FAIL:{PROVEN_FILE} exists but the plan does not reference it; "
                            "run proven_to_plan.py on the template")
        return
    if (not isinstance(reference, dict) or not isinstance(reference.get("path"), str)
            or not isinstance(reference.get("cells"), list)):
        raise PlanError("PROVEN_CONFIG_FAIL:proven_ip_config must be {path, sha256, cells}; "
                        "run proven_to_plan.py on the template")
    cells = {}
    for cell in reference["cells"]:
        if not isinstance(cell, dict) or not isinstance(cell.get("role"), str) or cell["role"] in cells:
            raise PlanError(f"PROVEN_CONFIG_FAIL:malformed or duplicate embedded proven cell {cell!r}")
        cells[cell["role"]] = cell
    # The plan is self-contained; the merged file, when it travels with the plan, must agree.
    path = Path(reference["path"])
    path = path if path.is_absolute() else plan_dir / path
    if path.exists():
        try:
            proven = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PlanError(f"PROVEN_CONFIG_FAIL:cannot read {path}: {exc}") from exc
        if _canonical_sha256(proven) != reference.get("sha256"):
            raise PlanError(f"PROVEN_CONFIG_FAIL:{path} changed since proven_to_plan.py wrote the plan")
        on_file = {}
        for cell in proven.get("cells", []):
            body = {key: value for key, value in cell.items() if key != "sha256"}
            if _canonical_sha256(body) != cell.get("sha256"):
                raise PlanError(f"PROVEN_CONFIG_FAIL:proven cell {cell.get('role')!r} was edited after merge")
            on_file[cell.get("role")] = {key: cell[key] for key in cells.get(cell.get("role"), {})
                                         if key in cell}
        if on_file != cells:
            raise PlanError(f"PROVEN_CONFIG_FAIL:plan's embedded proven cells disagree with {path}")
    seen: dict[str, set] = {role: set() for role in cells}
    for entry in plan.get("stages", []):
        for operation in entry.get("operations", []):
            if not isinstance(operation, dict) or operation.get("op") != "call":
                continue
            proc, args, op_id = operation.get("proc"), operation.get("args", []), operation.get("id")
            record = operation.get("provenance")
            if record is None:
                if proc == "ipiasm::create_cell" and args:
                    for role in cells:
                        if str(args[0]).startswith(f"${{bind.blocks.{role}."):
                            raise PlanError(f"PROVEN_CONFIG_FAIL:{op_id} creates proven role {role} without "
                                            "provenance; take its cell from proven_to_plan.py")
                continue
            role = record.get("role") if isinstance(record, dict) else None
            if role not in cells:
                raise PlanError(f"PROVEN_CONFIG_FAIL:{op_id} cites unknown proven role {role!r}")
            cell = cells[role]
            if record.get("sha256") != cell["sha256"]:
                raise PlanError(f"PROVEN_CONFIG_FAIL:{op_id} cites a different proof of {role}")
            if proc in {"ipcfg::require", "ipiasm::check_cell_config"}:
                if len(args) < 2 or args[1] != cell.get("requested", {}):
                    raise PlanError(f"PROVEN_CONFIG_FAIL:{op_id} does not apply the proven values of {role} "
                                    "verbatim; to change a value, re-prove it in Stage 2")
            seen[role].add(proc)
    for role, cell in cells.items():
        needed = set()
        if cell.get("requested"):
            needed.add("ipiasm::check_cell_config")
        if cell.get("apply_via") == "create_cell":
            needed.add("ipiasm::create_cell")
            if cell.get("requested"):
                needed.add("ipcfg::require")
        missing = sorted(needed - seen[role])
        if missing:
            raise PlanError(f"PROVEN_CONFIG_FAIL:proven role {role} has no provenanced {', '.join(missing)}")


def required_roles_from_tcl(text: str) -> list[str]:
    """Read the role list an addressing script's ::addrmap::required_roles returns.

    The seed list is the `set result {...}` inside required_roles; every second
    word of each `target_roles` triple (logical role suffix) follows, in order.
    """
    body = re.search(r"proc\s+::addrmap::required_roles\s*\{\}\s*\{(.*?)\n\}", text, re.S)
    seed = re.search(r"set\s+result\s+\{([^}]*)\}", body.group(1)) if body else None
    table = re.search(r"variable\s+target_roles\s+\{(.*?)\n\s*\}", text, re.S)
    if not seed or not table:
        raise PlanError("ROLE_TABLE_FAIL:no ::addrmap::required_roles / target_roles in the roles script")
    roles = seed.group(1).split()
    words = [line.split() for line in table.group(1).splitlines() if line.split()
             and not line.lstrip().startswith("#")]
    for triple in words:
        if len(triple) >= 2 and triple[1] not in roles:
            roles.append(triple[1])
    return roles


def created_cells(plan: dict) -> set[str]:
    """Instance names the plan creates, as a static reader of the plan sees them."""
    created: set[str] = set()
    for entry in plan.get("stages", []):
        for op in entry.get("operations", []):
            args = op.get("args", [])
            if op.get("op") != "call":
                continue
            if op.get("proc") in ("ipiasm::create_cell", "ipiasm::create_module_ref") and len(args) >= 2:
                created.add(str(args[1]))
            elif op.get("proc") == "ipiasm::instantiate_n" and len(args) >= 3 and str(args[2]).isdigit():
                created.update(f"{args[1]}_{index}" for index in range(int(args[2])))
    return created


def check_role_table(prose: str, roles: list[str], plan: dict) -> None:
    """Every required role has one `| role | \\`cell\\` |` row naming a distinct created cell."""
    created = created_cells(plan)
    mapping: dict[str, str] = {}
    problems: list[str] = []
    for role in roles:
        named_cells = []
        for line in prose.splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0].strip("`") == role:
                named = re.findall(r"`([^`]+)`", cells[1]) or cells[1].split()[:1]
                named_cells.extend(named[:1])
        hit = next((cell for cell in named_cells if cell in created), None)
        if hit:
            mapping[role] = hit
        elif named_cells:
            problems.append(f"{role}->{named_cells[0]} is not a cell the plan creates")
        else:
            problems.append(f"{role} has no '| {role} | `cell` |' row")
    shared = sorted({cell for cell in mapping.values() if list(mapping.values()).count(cell) > 1})
    for cell in shared:
        problems.append(f"{cell} is mapped by several roles ({' '.join(r for r, c in mapping.items() if c == cell)})")
    if problems:
        raise PlanError("ROLE_TABLE_FAIL:" + "; ".join(problems)
                        + f" (created cells: {' '.join(sorted(created))})")


def created_ip(plan: dict) -> set[str]:
    """Catalog IP names (the third VLNV field) the plan creates, placeholders resolved."""
    blocks = (plan.get("resolved_bindings") or {}).get("blocks") or {}
    names: set[str] = set()
    for entry in plan.get("stages", []):
        for op in entry.get("operations", []):
            args = op.get("args") or []
            if op.get("op") != "call" or op.get("proc") not in ("ipiasm::create_cell", "ipiasm::instantiate_n") \
                    or not args:
                continue
            vlnv = str(args[0])
            bound = re.fullmatch(r"\$\{bind\.blocks\.([^.}]+)\.vlnv(?:_unversioned)?\}", vlnv)
            if bound:
                vlnv = str((blocks.get(bound[1]) or {}).get("vlnv", ""))
            if vlnv.count(":") >= 2:
                names.add(vlnv.split(":")[2])
    return names


# A plan stage proves its own result, so it carries the checks its design needs, and only
# those: a check is required when the plan creates the IP it inspects or the DesignSpec
# states the contract it proves. A design without that IP or contract is never asked for it.
# Each row: plan stage, check, why it is needed, and whether this design needs it. A check
# without a namespace is an ipiasm:: helper.
STAGE_CHECKS = (
    ("board", "require_board_preset", "the plan targets a board", lambda d: d["board"]),
    ("board", "ipcfg::preset_drift", "the plan targets a board and creates a processing system, which the"
     " board preset configures: create it, apply the preset, then prove it here", lambda d: d["board"] and d["ps"]),
    ("board", "attach_board_memory", "the plan attaches board memory to a NoC; the board owns that memory",
     lambda d: d["memory"]),
    ("cells", "gate_inventory", "the plan creates cells", lambda d: d["ip"]),
    ("clocks", "set_clock_contracts", "the DesignSpec has clock_reset contracts", lambda d: d["clock_reset"]),
    ("clocks", "check_interface_clock_domains", "the DesignSpec has clock_reset contracts",
     lambda d: d["clock_reset"]),
    ("clocks", "check_undriven_clocks", "the plan creates cells", lambda d: d["ip"]),
    ("clocks", "check_mipi_video_clocks", "the plan creates a MIPI CSI-2 receiver", lambda d: d["mipi"]),
    ("clocks", "check_mem_noc_sysclk", "the plan attaches board memory to a NoC", lambda d: d["memory"]),
    ("connect", "check_irq_paths", "the DesignSpec has interrupt contracts", lambda d: d["interrupts"]),
    ("qos", "check_required_reachability", "the DesignSpec has reachability contracts; NoC routes are written"
     " with their QoS in this stage, so paths through a NoC exist only from here", lambda d: d["reachability"]),
    ("connect", "check_camera_isp_format", "the plan creates a MIPI CSI-2 receiver and an ISP",
     lambda d: d["mipi"] and d["isp"]),
    ("connect", "attach_noc_port_clocks", "the plan creates a NoC, whose port aclks follow the peers"
     " this stage wires", lambda d: d["noc"]),
    ("connect", "check_undriven_clocks", "the plan creates cells", lambda d: d["ip"]),
    ("qos", "check_noc_pre_address", "the plan creates a NoC", lambda d: d["noc"]),
    ("qos", "check_memory_subsystem", "the plan attaches board memory to a NoC", lambda d: d["memory"]),
)

# The canonical stage order a plan's checks are placed against. A check placed before the
# stage that can satisfy it halts the plan on work that has not happened yet.
STAGE_ORDER = ("board", "cells", "clocks", "connect", "qos")

# Checks that read values Vivado sets only when the design validates (propagated clock
# frequencies, reset polarity). The build stages run before the design can validate, so these
# belong in a later stage, after that stage's validation.
AFTER_VALIDATION = {"ipiasm::check_clock_contracts"}
VALIDATIONS = {"ipiasm::validate", "ipiasm::validate_bundle"}

# Catalog names of the processing systems a board preset configures.
PROCESSING_SYSTEMS = {"ps_wizard", "versal_cips", "zynq_ultra_ps_e", "processing_system7"}


def _full_proc(check: str) -> str:
    return check if "::" in check else f"ipiasm::{check}"


# Helpers that end in the registration they are named for: register_clock_contracts fills
# the measured fields from the live design and then calls set_clock_contracts itself.
REGISTRATION_ALIASES = {"ipiasm::register_clock_contracts": "ipiasm::set_clock_contracts"}


def _registers_as(proc: object) -> object:
    return REGISTRATION_ALIASES.get(proc, proc)


def check_stage_gates(spec: dict, plan: dict) -> None:
    """Every check this design needs is in the plan stage it belongs to (STAGE_CHECKS).

    Only stages the plan has are checked: a partial plan without a clocks stage is not
    asked for clock checks, and a stage with another name is the author's own. A check is
    also refused in a canonical stage that runs before its own: a copy placed early fails
    the stage on work a later stage has not done yet, and no later success can undo it."""
    ip = created_ip(plan)
    contracts = spec.get("functional_contracts") or {}
    calls = {entry.get("name"): {_registers_as(op.get("proc")) for op in entry.get("operations", [])
                                 if op.get("op") == "call"}
             for entry in plan.get("stages", [])}
    design = {
        "ip": ip,
        "board": bool((plan.get("binding_context") or {}).get("board_part")),
        "clock_reset": bool(contracts.get("clock_reset")),
        "interrupts": bool(contracts.get("interrupts")),
        "reachability": bool(contracts.get("control_reachability") or contracts.get("memory_reachability")),
        "mipi": "mipi_csi2_rx_subsystem" in ip,
        "isp": any(name.startswith("visp") for name in ip),
        "noc": any(name.startswith("axi_noc") for name in ip),
        "ps": bool(ip & PROCESSING_SYSTEMS),
        "memory": any("ipiasm::attach_board_memory" in procs for procs in calls.values()),
    }
    missing = []
    for stage, check, reason, needed in STAGE_CHECKS:
        if stage in calls and needed(design) and _full_proc(check) not in calls[stage]:
            missing.append(f"{stage} lacks {_full_proc(check)} ({reason})")
    # Earliest stage each check belongs to; a copy before it is judging work not yet done.
    earliest = {}
    for stage, check, _reason, _needed in STAGE_CHECKS:
        rank = STAGE_ORDER.index(stage)
        if rank < earliest.get(_full_proc(check), len(STAGE_ORDER)):
            earliest[_full_proc(check)] = rank
    early = []
    for stage, procs in calls.items():
        if stage not in STAGE_ORDER:
            continue
        for proc in sorted(procs):
            if proc in earliest and STAGE_ORDER.index(stage) < earliest[proc]:
                early.append(f"{stage} runs {proc} before {STAGE_ORDER[earliest[proc]]},"
                             f" the stage that does the work it checks")
    for entry in plan.get("stages", []):
        validated = False
        for op in entry.get("operations", []):
            proc = op.get("proc") if op.get("op") == "call" else None
            if proc in VALIDATIONS:
                validated = True
            elif proc in AFTER_VALIDATION and (entry.get("name") in STAGE_ORDER or not validated):
                early.append(f"{entry.get('name')} runs {proc} before the design validates; it reads values"
                             " Vivado sets only on validation, so run it after ipiasm::validate_bundle in a"
                             " stage after qos")
    if missing or early:
        raise PlanError("STAGE_GATE_FAIL:" + "; ".join(missing + early)
                        + ". Add each check to that stage, after the work it checks, and remove copies from"
                        " earlier stages, so every stage proves its own result and nothing else")


def check_reachability_coverage(spec: dict, plan: dict) -> None:
    """Every required reachability contract has a pair in the plan's reachability checks.

    A pair covers a contract when its address space belongs to one of the master's cells and
    its target is one of the target block's cells (each matched on its first path element,
    as a glob), or DRAM when a NoC implements the target block. Masters are resolved through
    the spec's reference groups. A side no plan cell implements, such as a host outside the
    design, is not this plan's to check. A reachability check with pairs missing still
    reports REACH:OK, so the gap would otherwise surface only in hardware.
    """
    import fnmatch

    contracts = spec.get("functional_contracts") or {}
    wanted = [(item.get("master"), item.get("memory"), "memory") for item in contracts.get("memory_reachability") or []
              if isinstance(item, dict) and item.get("required", True)]
    wanted += [(item.get("master"), item.get("target"), "control") for item in contracts.get("control_reachability") or []
               if isinstance(item, dict) and item.get("required", True)]
    if not wanted:
        return
    cells: dict[str, set] = {}
    noc_blocks = set()
    for block in ((plan.get("resolved_bindings") or {}).get("blocks") or {}).values():
        name = block.get("spec_block") or block.get("instance")
        cells.setdefault(name, set()).add(block.get("instance"))
        if str(block.get("vlnv", "")).split(":")[2:3] and block["vlnv"].split(":")[2].startswith("axi_noc"):
            noc_blocks.add(name)
    groups = (spec.get("global_constraints") or {}).get("reference_groups") or {}

    def blocks_of(name, seen=()):
        if name in groups and name not in seen:
            return {block for member in groups[name] or [] for block in blocks_of(member, seen + (name,))}
        return {str(name or "").split(".", 1)[0]}

    pairs = []
    for entry in plan.get("stages", []):
        for op in entry.get("operations", []):
            if op.get("op") == "call" and op.get("proc") == "ipiasm::check_required_reachability":
                for record in op.get("args") or []:
                    flat = [str(item) for item in (record if isinstance(record, list) else [record])]
                    pairs += list(zip(flat[0::2], flat[1::2]))

    def names(glob, instances):
        # A Vivado path is absolute (/ps/cpu0); its cell is the first element after the root.
        return any(fnmatch.fnmatchcase(instance, glob.lstrip("/").split("/", 1)[0]) for instance in instances)

    missing = []
    for master, target, kind in wanted:
        block = str(target or "").split(".", 1)[0]
        implemented = cells.get(block, set())
        dram = kind == "memory" and block in noc_blocks
        if not implemented:
            continue
        masters = {cell for name in blocks_of(master) for cell in cells.get(name, set())}
        covered = any((not masters or names(space, masters))
                      and ((dram and goal == "DRAM") or names(goal, implemented))
                      for space, goal in pairs)
        if not covered:
            missing.append(f"{master}->{target}")
    if missing:
        raise PlanError(f"REACHABILITY_FAIL:{len(missing)} required reachability contract(s) have no pair in any "
                        f"ipiasm::check_required_reachability: {', '.join(missing)}. Add a "
                        "{<master address-space glob> <target segment glob | DRAM>} pair for each")


def check_boundary_coverage(spec: dict, plan: dict) -> None:
    """Every `top_level` spec interface is a port the plan's check_required_ports reads back.

    A `top_level` interface is part of the design boundary, so the block design must
    expose it as a connected port. The port takes the interface's name, or the bare
    name at the other end of a required connection that uses the interface. Without
    this, a boundary port the plan never creates is invisible to every gate: nothing
    fails, and the wrapper simply lacks the port.
    """
    blocks = {block.get("name") for block in spec.get("blocks") or [] if isinstance(block, dict)}
    connections = [item for item in (spec.get("functional_contracts") or {}).get("connections") or []
                   if isinstance(item, dict) and item.get("required", True)]
    listed = set()
    for entry in plan.get("stages", []):
        for op in entry.get("operations", []):
            if op.get("op") == "call" and op.get("proc") == "ipiasm::check_required_ports":
                for port in (op.get("args") or [[]])[0] or []:
                    if isinstance(port, dict) and port.get("name"):
                        listed.add(str(port["name"]))
    missing = []
    for block in spec.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for interface in block.get("interfaces") or []:
            if not isinstance(interface, dict) or interface.get("scope") != "top_level":
                continue
            endpoint = f"{block.get('name')}.{interface.get('name')}"
            names = {str(interface.get("name"))}
            for item in connections:
                ends = [str(item.get("source", "")), str(item.get("destination", ""))]
                if endpoint in ends:
                    other = ends[1 - ends.index(endpoint)]
                    if "." not in other and other not in blocks:
                        names.add(other)
            if not names & listed:
                missing.append(f"{endpoint} (port {' or '.join(sorted(names))})")
    if missing:
        raise PlanError(f"BOUNDARY_FAIL:{len(missing)} top_level DesignSpec interface(s) are not read back by any "
                        f"ipiasm::check_required_ports: {', '.join(missing)}. Create each port, wire it to "
                        "its block, and list it in check_required_ports. When the RTL top names it differently, "
                        "record the RTL name(s) with scripts/boundary_names.py add, then rebind")


def _words(value: object) -> list[str]:
    """A plan argument that may be a JSON list or a Tcl word list, as plain words."""
    if isinstance(value, list):
        return [str(item) for item in value]
    return str(value or "").split()


def _address(value: object) -> int | None:
    """An address or size written as a JSON integer or as hex/decimal text, else None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return _number_text(value)


def final_gate_gaps(spec: dict, plan: dict) -> list[str]:
    """What ipiasm::deliver's final gate will refuse that this plan does not provide.

    ipiasm::deliver runs final_contract_gate_from_plan, which takes its window checks
    from the plan (final_gate_inputs.py), requires pinned values (ipcfg::audit_requirements
    must report REQ:OK, and reports REQ:empty when nothing was ever pinned), and maps every
    DesignSpec contract to passing evidence (contract_coverage.py). A plan that lacks one of
    these still builds and validates, so without this report the gap surfaces only after the
    whole build, at delivery. Each gap can also be closed outside the plan (an ipcfg::require
    in a repair script, ipiasm::record_evidence of a supplied checker), so the compiler
    reports gaps instead of refusing the plan. Returns one line per gap, empty when none.
    """
    contracts = spec.get("functional_contracts") or {}
    blocks = {block.get("name") for block in spec.get("blocks") or [] if isinstance(block, dict)}
    calls = [op for entry in plan.get("stages", []) for op in entry.get("operations", [])
             if isinstance(op, dict) and op.get("op") == "call"]
    procs = {op.get("proc") for op in calls}
    evidenced = set()
    for op in calls:
        args = op.get("args") or []
        if op.get("proc") == "ipiasm::record_evidence" and len(args) >= 2:
            evidenced.update(_words(args[1]))
    gaps = []
    if "ipcfg::require" not in procs:
        gaps.append("pins: no ipcfg::require operation; the final gate requires REQ:OK from "
                    "ipcfg::audit_requirements and reports REQ:empty when nothing was pinned. Pin each "
                    "CONFIG value the plan sets or the DesignSpec requires with ipcfg::require <cell> <dict> "
                    "<reason> (cells or clocks stage) instead of a set_property operation")
    if "interrupts" not in evidenced:
        labels, readable = set(), True
        for op in calls:
            if op.get("proc") != "ipiasm::set_irq_contracts":
                continue
            table = (op.get("args") or [[]])[0]
            if not isinstance(table, list):
                readable = False
                continue
            for record in table:
                if isinstance(record, list) and len(record) == 3:
                    labels.add(str(record[2]))
        # Same obligations as contract_coverage.py: an interrupt delivered outside the
        # design (to a host the spec does not model as a block) is deferred, not checked.
        wanted = [f"{item.get('source')}.{output}" for item in contracts.get("interrupts") or []
                  if isinstance(item, dict) and item.get("required") is not False
                  and str(item.get("destination", "")).split(".")[0] in blocks
                  for output in item.get("outputs") or []]
        missing = [label for label in wanted if label not in labels]
        if readable and missing:
            gaps.append(f"interrupts: {len(missing)} DesignSpec interrupt output(s) have no labelled "
                        f"ipiasm::set_irq_contracts record: {', '.join(missing)}. Register one "
                        "{<sink pin> <source pin> <source>.<output>} record per output in the connect stage, "
                        "then check them with ipiasm::check_irq_paths on the same table")
    if "fixed_address_windows" not in evidenced:
        scoped = set()
        for op in calls:
            args = op.get("args") or []
            if op.get("proc") == "ipiasm::check_window_scope" and len(args) >= 4:
                offset, size = _address(args[2]), _address(args[3])
                if offset is not None and size is not None:
                    scoped.add((offset, size))
        missing = []
        for window in contracts.get("fixed_address_windows") or []:
            if not isinstance(window, dict) or window.get("required") is False:
                continue
            place = (_address(window.get("offset")), _address(window.get("range")))
            if None not in place and place not in scoped:
                missing.append(f"{window.get('name')} ({window.get('offset')}/{window.get('range')})")
        if missing:
            gaps.append(f"windows: {len(missing)} DesignSpec fixed address window(s) have no "
                        f"ipiasm::check_window_scope with the same offset and range: {', '.join(missing)}. "
                        "Add one per window in the address stage, after auto_address: "
                        "{<slave segment> {<allowed address spaces>} <offset> <range>}")
    rules = [item for item in contracts.get("visibility_policy") or []
             if isinstance(item, dict) and item.get("required") is not False]
    if rules and "visibility_policy" not in evidenced and "ipiasm::check_visibility" not in procs:
        gaps.append(f"visibility: {len(rules)} DesignSpec visibility_policy rule(s) and no "
                    "ipiasm::check_visibility operation; the final gate needs VISIBILITY:OK and reruns "
                    "the plan's check_visibility ops to get it. Add one per governed target's slave "
                    "segment in the address stage, after auto_address: {<slave segment> "
                    "{<allowed address spaces>}}")
    if "memory_reachability" not in evidenced and "ipiasm::check_required_dram_masters" not in procs:
        # final_gate_inputs.py derives DRAM masters from windows on memory_controller blocks;
        # a spec without such a window leaves the gate none to check (DRAM_REACH:NA).
        controllers = {block.get("name") for block in spec.get("blocks") or []
                       if isinstance(block, dict) and block.get("workload_type") == "memory_controller"}
        memories = {item.get("memory") for item in contracts.get("memory_reachability") or []
                    if isinstance(item, dict) and item.get("required") is not False}
        windowed = {window.get("target") for window in contracts.get("fixed_address_windows") or []
                    if isinstance(window, dict) and window.get("required") is not False
                    and window.get("block") in controllers
                    and "ipiasm::check_window_scope" in procs}
        unchecked = sorted(str(memory) for memory in memories - windowed)
        if unchecked:
            gaps.append(f"memory: {len(unchecked)} DesignSpec memory_reachability target(s) have no "
                        f"window check on a memory_controller block: {', '.join(unchecked)}. Add "
                        "ipiasm::check_required_dram_masters {<master address spaces>} in the address "
                        "stage, after auto_address (the final gate reruns it for DRAM_REACH:OK)")
    return gaps


def compile_plan(plan: object, stage: str | None = None, dispatch: bool = False) -> str:
    if not isinstance(plan, dict):
        raise PlanError("plan root must be an object")
    if plan.get("schema_version") != 1:
        raise PlanError("schema_version must be 1")
    stages = plan.get("stages")
    if not isinstance(stages, list) or not stages:
        raise PlanError("stages must be a non-empty list")

    lines = []
    context = plan.get("binding_context", {})
    if not isinstance(context, dict):
        raise PlanError("binding_context must be an object")
    if context:
        if not isinstance(context, dict):
            raise PlanError("binding_context must be an object")
        expected_version = str(context.get("vivado_version", ""))
        expected_part = str(context.get("part", ""))
        expected_board = str(context.get("board_part", ""))
        lines += [
            "set __actual_version {}",
            "set __actual_part {}",
            "set __actual_board {}",
        ]
        if expected_version:
            lines += [
                "set __actual_version [version -short]",
                f"if {{![string match {_brace(expected_version + '*')} $__actual_version]}} {{",
                f'  error {_brace("PLAN_ENV_FAIL:Vivado mismatch, expected " + expected_version)}',
                "}",
            ]
        if expected_part:
            lines += [
                "set __actual_part [get_property -quiet PART [current_project]]",
                f"if {{$__actual_part ne {_brace(expected_part)}}} {{",
                f'  error {_brace("PLAN_ENV_FAIL:part mismatch, expected " + expected_part)}',
                "}",
            ]
        if expected_board:
            # Unset is allowed: the board stage sets it, and the board gate blocks cells until then.
            lines += [
                # The executor accepts exactly this board being set during the run (from unset).
                f"set __expected_board {_brace(expected_board)}",
                "set __actual_board [get_property -quiet BOARD_PART [current_project]]",
                f"if {{$__actual_board ne {{}} && $__actual_board ne {_brace(expected_board)}}} {{",
                f'  error {_brace("PLAN_ENV_FAIL:board mismatch, expected " + expected_board)}',
                "}",
            ]
        capabilities = context.get("capabilities", {})
        if not isinstance(capabilities, dict):
            raise PlanError("binding_context.capabilities must be an object")
        for capability, required in capabilities.items():
            if str(required).lower() not in {"1", "true", "yes"}:
                continue
            if not SAFE_ID.fullmatch(str(capability)):
                raise PlanError(f"invalid capability name {capability!r}")
            lines += [
                f"if {{![ipcfg::has_native {_brace(capability)}]}} {{",
                f'  error "PLAN_ENV_FAIL:required capability {capability} is unavailable"',
                "}",
            ]
    environment_script = "\n".join(lines)
    compiled_stages = []
    stage_names = set()
    operation_ids = set()
    for stage_index, entry in enumerate(stages):
        if not isinstance(entry, dict):
            raise PlanError(f"stage {stage_index} must be an object")
        stage_name = entry.get("name")
        operations = entry.get("operations")
        if not isinstance(stage_name, str) or not SAFE_ID.fullmatch(stage_name):
            raise PlanError(f"stage {stage_index} has invalid name")
        if stage_name in stage_names:
            raise PlanError(f"duplicate stage name {stage_name}")
        stage_names.add(stage_name)
        if not isinstance(operations, list) or not operations:
            raise PlanError(f"stage {stage_name} operations must be a non-empty list")
        if not isinstance(entry.get("checkpoint", False), bool):
            raise PlanError(f"stage {stage_name} checkpoint must be boolean")
        compiled_operations = []
        for op_index, operation in enumerate(operations):
            if not isinstance(operation, dict):
                raise PlanError(f"{stage_name}[{op_index}] must be an object")
            op = operation.get("op")
            op_id = operation.get("id", f"{stage_name}_{op_index}")
            if not isinstance(op_id, str) or not SAFE_ID.fullmatch(op_id):
                raise PlanError(f"{stage_name}[{op_index}] has invalid id")
            if op_id in operation_ids:
                raise PlanError(f"duplicate operation id {op_id}")
            operation_ids.add(op_id)
            if "tcl" in operation or "command" in operation:
                raise PlanError(f"{op_id}: raw Tcl is forbidden")
            if op == "call":
                body = _compile_call(operation)
            elif op == "set_property":
                body = _compile_set_property(operation)
            elif op == "create_bd_design":
                body = _compile_create_bd(operation, verify_existing=stage == stage_name)
            elif op == "apply_automation":
                body = _compile_automation(operation)
            else:
                raise PlanError(f"{op_id}: unsupported op {op!r}")
            prefixes = _validate_expect(operation.get("expect", list(DEFAULT_EXPECT)), op_id)
            compiled_operation = {"id": op_id, "body": "\n".join(body),
                                  "expect": prefixes, "op": op}
            if dispatch and op == "create_bd_design":
                compiled_operation["selected_body"] = "\n".join(_compile_create_bd(operation, verify_existing=True))
            if op == "call":
                compiled_operation["call"] = [operation["proc"], operation.get("args", [])]
            compiled_operations.append(compiled_operation)
        compiled_stages.append({"name": stage_name, "operations": compiled_operations,
                                "checkpoint": entry.get("checkpoint", False)})
    if stage is not None and stage not in stage_names:
        raise PlanError(f"unknown stage {stage!r}")
    _check_memory_plan(stages)
    _check_board_hooks_order(stages)
    _check_reads_after_create(stages)
    _check_ports_after_create(stages)
    _check_stage_sequence(stages)
    _check_bram_before_inventory(stages)
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"),
                                      allow_nan=False).encode()).hexdigest()
    selected = [entry for entry in compiled_stages if stage is None or entry["name"] == stage]
    names = [entry["name"] for entry in compiled_stages]
    if dispatch:
        return ("# Generated by compile_assembly_plan.py; do not edit.\n"
                + f"return [ipiasm::_dispatch_plan {_brace(digest)} {_brace(names)} "
                + f"{_brace(compiled_stages)} {_brace(environment_script)}]\n")
    return ("# Generated by compile_assembly_plan.py; do not edit.\n"
            + "# Procedures: " + " ".join(sorted({op.get("proc", "")
                for entry in stages for op in entry["operations"] if op.get("proc")})) + "\n"
            + f"return [ipiasm::_execute_plan {_brace(digest)} {_brace(names)} "
            + f"{_brace(stage or '')} {_brace(selected)} {_brace(environment_script)}]\n")


OPERATION_SHAPES = {
    "call": '{"id": "x", "op": "call", "proc": "<allowed procedure>", "args": [...], "expect": ["PREFIX:"]}',
    "set_property": '{"id": "x", "op": "set_property", "object": {"kind": "cell|intf_pin|pin|intf_port|port", "path": "/cell"},'
                    ' "properties": {"CONFIG.K": "v"}, "allow_board": false, "reason": "..."}  -> PROPERTY:OK',
    "create_bd_design": '{"id": "x", "op": "create_bd_design", "name": "design_1"}  -> BD:OK',
    "apply_automation": '{"id": "x", "op": "apply_automation", "rule": "xilinx.com:bd_rule:<rule>", "target": "/cell",'
                        ' "target_kind": "cell", "config": {...}}  -> AUTOMATION:OK',
}


def list_operations() -> str:
    lines = ['plan: {"schema_version": 1, "name": "...", "stages": [{"name": "<stage>", "operations": [...], "checkpoint": true}]}',
             "operation types (no raw Tcl):"]
    lines += [f"  {name:17} {shape}" for name, shape in OPERATION_SHAPES.items()]
    lines.append(f"allowed call procedures ({len(ALLOWED_PROCS)}; call shape and results: ipcfg::help <name> in Vivado):")
    lines += ["  " + " ".join(sorted(ALLOWED_PROCS))]
    lines.append("default expect prefixes when an operation gives none:")
    lines += ["  " + " ".join(DEFAULT_EXPECT)]
    lines.append("placeholders: ${bind.blocks.<role>.vlnv|vlnv_unversioned|instance|interfaces.<n>|pins.<n>|properties.<n>}"
                 " ${env.vivado_version|part|board_part}")
    return "\n".join(lines)


# Plumbing IP is wiring, not a spec block, so the binding probe need not create it.
PLUMBING_IP = {"xlconstant", "ilconstant", "xlconcat", "ilconcat", "xlslice", "ilslice", "util_vector_logic",
               "ilvector_logic", "util_reduced_logic", "ilreduced_logic", "proc_sys_reset", "smartconnect",
               "axi_interconnect", "axi_register_slice", "axis_register_slice", "axi_clock_converter",
               "axi_dwidth_converter", "axi_protocol_converter"}


def check_live_binding(plan: dict, plan_dir: Path) -> None:
    """Refuse catalog IP that the live binding probe never created.

    Every non-plumbing catalog VLNV a plan creates must appear as a
    resolved_vlnv in the binding_snapshot.json beside the plan, or be the
    VLNV of a passing clock-search record. A helper cell (a BRAM behind a
    controller, say) is bound by adding its own binding_intent role whose
    spec_block is the block it serves and rerunning the probe, not by writing
    it into the plan.
    """
    snapshot_path = plan_dir / "binding_snapshot.json"
    if not snapshot_path.exists():
        return
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    resolved = {str(entry.get("resolved_vlnv", "")).rsplit(":", 1)[0]
                for entry in snapshot.get("catalog", {}).values() if isinstance(entry, dict)}
    searched = set()
    for path in plan_dir.rglob("*.tcldict"):
        match = re.match(r"schema_version \d+ data \{status PASS vlnv (\S+)", path.read_text(errors="replace"))
        if match:
            searched.add(match[1].split(":")[2] if match[1].count(":") >= 2 else match[1])
    missing = {}
    for stage in plan.get("stages", []):
        for op in stage.get("operations", []):
            args = op.get("args") or []
            if (op.get("op") != "call" or op.get("proc") not in ("ipiasm::create_cell", "ipiasm::instantiate_n")
                    or not args or not isinstance(args[0], str) or "${" in args[0] or args[0].count(":") < 2):
                continue
            vlnv = args[0].rsplit(":", 1)[0] if args[0].count(":") == 3 else args[0]
            name = vlnv.split(":")[2]
            if vlnv not in resolved and name not in PLUMBING_IP and name not in searched:
                missing.setdefault(vlnv, []).append(str(op.get("id")))
    if missing:
        detail = "; ".join(f"{vlnv} (ops {', '.join(ids)})" for vlnv, ids in sorted(missing.items()))
        raise PlanError(f"BINDING_FAIL:not created by the live binding probe: {detail}. Add each as a "
                        "binding_intent role (a helper names the spec_block it serves), rerun the probe, and rebind")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--list-ops" in argv:
        print(list_operations())
        return 0
    parser = argparse.ArgumentParser(description="Compile assembly_plan.json to Tcl. --list-ops prints the plan format.")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stage")
    parser.add_argument("--dispatch", action="store_true")
    parser.add_argument("--spec", type=Path, help="Require complete parameter coverage against this DesignSpec, "
                                                  "and every check the design needs in its stage")
    parser.add_argument("--roles-from", type=Path,
                        help="Addressing script whose ::addrmap::required_roles every role-table row must cover")
    parser.add_argument("--role-table", type=Path,
                        help="Markdown with '| role | `cell` |' rows (default: plan.md beside the plan)")
    args = parser.parse_args(argv)
    try:
        source = args.plan.read_bytes()
        plan = json.loads(source)
        tcl = compile_plan(plan, stage=args.stage, dispatch=args.dispatch)
        check_proven_provenance(plan, args.plan.resolve().parent)
        check_live_binding(plan, args.plan.resolve().parent)
        if args.roles_from or args.role_table:
            if not args.roles_from:
                raise PlanError("ROLE_TABLE_FAIL:--role-table needs --roles-from <addressing script>")
            table = args.role_table or args.plan.parent / "plan.md"
            check_role_table(table.read_text(errors="replace"),
                             required_roles_from_tcl(args.roles_from.read_text(errors="replace")), plan)
        spec_path = args.spec or args.plan.parent / "design_spec.json"
        spec_digest = plan.get("binding_context", {}).get("design_spec_sha256")
        gaps: list[str] = []
        if args.spec or spec_path.exists() or spec_digest:
            if not spec_path.exists():
                # A recorded digest is a claim that this plan is bound to one
                # exact DesignSpec. Without that spec the claim cannot be
                # checked, and emitting Tcl anyway would hide the drift the
                # digest exists to catch. Refuse, and say which file is missing.
                raise PlanError(f"PARAMETER_COVERAGE_FAIL:no DesignSpec at {spec_path}")
            spec_source = spec_path.read_bytes()
            if spec_digest and hashlib.sha256(spec_source).hexdigest() != spec_digest:
                raise PlanError("PARAMETER_COVERAGE_FAIL:DesignSpec differs from the bound plan")
            check_parameter_coverage(json.loads(spec_source), plan)
            check_stage_gates(json.loads(spec_source), plan)
            check_reachability_coverage(json.loads(spec_source), plan)
            check_boundary_coverage(json.loads(spec_source), plan)
            gaps = final_gate_gaps(json.loads(spec_source), plan)
    except (OSError, ValueError, KeyError, TypeError, PlanError) as exc:
        print(f"ASSEMBLY_PLAN_FAIL:{exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source).hexdigest()
    selector = "*" if args.dispatch else (args.stage or "")
    args.out.write_text(f"# plan-source-sha256: {digest}\n# selected-stage: {selector}\n" + tcl)
    for gap in gaps:
        print(f"FINAL_GATE_GAP:{gap}")
    # The OK line stays last and keeps its fields; the gap count is appended so a reader
    # of the tail alone still sees that delivery would refuse this plan as written.
    print(f"ASSEMBLY_PLAN:OK stages={len(plan['stages'])} tcl={args.out}"
          + (f" final_gate_gaps={len(gaps)}" if gaps else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
