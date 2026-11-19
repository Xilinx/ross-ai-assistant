#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Print what compile_assembly_plan.py --spec will demand of this design, before you write the template.

Run it once after binding_snapshot.json exists (and after export_proven, so proven
leaves are known). One call lists, from the DesignSpec, the binding intent and the
snapshot:

  ROLE <role> instance=<cell> vlnv=<vlnv> spec_block=<block>
      the cell each role binds to (${bind.blocks.<role>.instance})
  STAGE <stage> needs <proc> (<why>)
      a check that stage must call, after the work it checks (STAGE_GATE_FAIL otherwise)
  AFTER_VALIDATION <proc> (<why>)
      a check that belongs in a stage after qos, after ipiasm::validate_bundle
  BOUNDARY <block>.<interface> port=<name>[|<name>...]
      a top_level interface; one of these names goes into check_required_ports
  REACH <master>-><target> masters=<cells> targets=<cells|DRAM>
      a required contract that needs one check_required_reachability pair
  REQUIRED_ROLE <role>
      a row the plan.md `| role | cell |` table needs (with --roles-from)
  COVER <pointer> = <json value>
      a parameter leaf parameter_coverage must map; proven leaves are left to proven_to_plan.py

With --coverage-out, writes {"parameter_coverage": {...}} holding every COVER leaf
with its exact source value, "disposition": "hardware", an empty reason and no checks:
fill reason and checks (or make it external with an owner) and merge it into the template.

Last line: PLAN_REQ:OK roles=<n> stage_checks=<n> boundary=<n> reach=<n> cover=<n> proven=<n>
or PLAN_REQ_FAIL:<reason> (exit 1).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bind_design_spec as binder  # noqa: E402
import compile_assembly_plan as compiler  # noqa: E402
import proven_to_plan as proven  # noqa: E402


class RequirementError(Exception):
    pass


def _load(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RequirementError(f"cannot read {what} {path}: {error}") from error


def parameter_leaves(spec: dict) -> dict[str, Any]:
    """Every pointer parameter_coverage must key, with its source value."""
    leaves: dict[str, Any] = {}

    def visit(value: Any, pointer: str) -> None:
        if isinstance(value, dict) and value:
            for key, child in value.items():
                visit(child, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list) and value:
            for index, child in enumerate(value):
                visit(child, f"{pointer}/{index}")
        else:
            leaves[pointer] = value

    for index, block in enumerate(spec.get("blocks") or []):
        for key, value in (block.get("parameters") or {}).items():
            visit(value, f"/blocks/{index}/parameters/" + key.replace("~", "~0").replace("/", "~1"))
    return leaves


def proven_leaves(spec: dict, fragments: list[dict]) -> set[str]:
    """Leaves an export_proven fragment's spec_pointers cover; proven_to_plan.py maps these."""
    covered: set[str] = set()
    for fragment in fragments:
        for cell in fragment.get("cells") or []:
            if not cell.get("requested"):
                continue
            for pointer in cell.get("spec_pointers") or []:
                try:
                    covered.update(proven._parameter_leaves(spec, pointer))
                except proven.ProvenError as error:
                    raise RequirementError(f"role {cell.get('role')!r}: {error}") from error
    return covered


def stage_checks(spec: dict, resolved: dict, board_part: str | None) -> tuple[list, list]:
    """(stage, proc, why) rows the compiler's STAGE_CHECKS table asks of this design."""
    ip = {str(block.get("vlnv", "")).split(":")[2] for block in resolved["blocks"].values()
          if str(block.get("vlnv", "")).count(":") >= 2}
    contracts = spec.get("functional_contracts") or {}
    noc = any(name.startswith("axi_noc") for name in ip)
    design = {
        "ip": ip,
        "board": bool(board_part),
        "clock_reset": bool(contracts.get("clock_reset")),
        "interrupts": bool(contracts.get("interrupts")),
        "reachability": bool(contracts.get("control_reachability") or contracts.get("memory_reachability")),
        "mipi": "mipi_csi2_rx_subsystem" in ip,
        "isp": any(name.startswith("visp") for name in ip),
        "noc": noc,
        "ps": bool(ip & compiler.PROCESSING_SYSTEMS),
        # Known only once the plan calls attach_board_memory; a bound NoC on a board may.
        "memory": bool(board_part) and noc,
    }
    rows = []
    for stage in compiler.STAGE_ORDER:
        for row_stage, check, why, needed in compiler.STAGE_CHECKS:
            if row_stage != stage or not needed(design):
                continue
            if not needed({**design, "memory": False}):
                why += " (only if the plan calls ipiasm::attach_board_memory)"
            rows.append((stage, compiler._full_proc(check), why))
    after = []
    if design["clock_reset"]:
        after = [(proc, "reads values Vivado sets only on validation") for proc in sorted(compiler.AFTER_VALIDATION)]
    return rows, after


def boundary_ports(spec: dict) -> list[tuple[str, list[str]]]:
    """Each top_level interface and the port names check_required_ports may list for it."""
    blocks = {block.get("name") for block in spec.get("blocks") or [] if isinstance(block, dict)}
    connections = [item for item in (spec.get("functional_contracts") or {}).get("connections") or []
                   if isinstance(item, dict) and item.get("required", True)]
    rows = []
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
            rows.append((endpoint, sorted(names)))
    return rows


def reachability_pairs(spec: dict, resolved: dict) -> list[tuple[str, str, list[str], list[str]]]:
    """Required reachability contracts a plan cell implements, with the cells a pair may name."""
    contracts = spec.get("functional_contracts") or {}
    wanted = [(item.get("master"), item.get("memory"), "memory") for item in contracts.get("memory_reachability") or []
              if isinstance(item, dict) and item.get("required", True)]
    wanted += [(item.get("master"), item.get("target"), "control") for item in contracts.get("control_reachability")
               or [] if isinstance(item, dict) and item.get("required", True)]
    cells: dict[str, set] = {}
    noc_blocks = set()
    for block in resolved["blocks"].values():
        name = block.get("spec_block") or block.get("instance")
        cells.setdefault(name, set()).add(block.get("instance"))
        if str(block.get("vlnv", "")).count(":") >= 2 and block["vlnv"].split(":")[2].startswith("axi_noc"):
            noc_blocks.add(name)
    groups = (spec.get("global_constraints") or {}).get("reference_groups") or {}

    def blocks_of(name, seen=()):
        if name in groups and name not in seen:
            return {block for member in groups[name] or [] for block in blocks_of(member, seen + (name,))}
        return {str(name or "").split(".", 1)[0]}

    rows = []
    for master, target, kind in wanted:
        block = str(target or "").split(".", 1)[0]
        implemented = cells.get(block, set())
        if not implemented:
            continue
        masters = sorted({cell for name in blocks_of(master) for cell in cells.get(name, set())})
        targets = ["DRAM"] if kind == "memory" and block in noc_blocks else sorted(implemented)
        rows.append((str(master), str(target), masters, targets))
    return rows


def requirements(spec: dict, intent: dict, snapshot: dict, roles_tcl: str | None = None,
                 fragments: list[dict] | None = None) -> dict[str, Any]:
    try:
        resolved = binder.resolve_bindings(spec, intent, snapshot)
    except binder.BindError as error:
        raise RequirementError(str(error).replace("\n", " ")) from error
    board_part = (snapshot.get("environment") or {}).get("board_part")
    stages, after = stage_checks(spec, resolved, board_part)
    leaves = parameter_leaves(spec)
    done = proven_leaves(spec, fragments or []) & leaves.keys()
    try:
        roles = compiler.required_roles_from_tcl(roles_tcl) if roles_tcl is not None else []
    except compiler.PlanError as error:
        raise RequirementError(str(error)) from error
    return {
        "roles": {role: block for role, block in sorted(resolved["blocks"].items())},
        "stages": stages,
        "after_validation": after,
        "boundary": boundary_ports(spec),
        "reach": reachability_pairs(spec, resolved),
        "required_roles": roles,
        "cover": {pointer: value for pointer, value in leaves.items() if pointer not in done},
        "proven": sorted(done),
    }


def coverage_skeleton(cover: dict[str, Any]) -> dict[str, Any]:
    return {"parameter_coverage": {pointer: {"value": value, "disposition": "hardware", "reason": "",
                                             "checks": []} for pointer, value in cover.items()}}


def report(found: dict[str, Any]) -> str:
    lines = [f"ROLE {role} instance={block['instance']} vlnv={block['vlnv']} spec_block={block['spec_block']}"
             for role, block in found["roles"].items()]
    lines += [f"STAGE {stage} needs {proc} ({why})" for stage, proc, why in found["stages"]]
    lines += [f"AFTER_VALIDATION {proc} ({why})" for proc, why in found["after_validation"]]
    lines += [f"BOUNDARY {endpoint} port={'|'.join(names)}" for endpoint, names in found["boundary"]]
    lines += [f"REACH {master}->{target} masters={','.join(masters) or '-'} targets={','.join(targets)}"
              for master, target, masters, targets in found["reach"]]
    lines += [f"REQUIRED_ROLE {role}" for role in found["required_roles"]]
    lines += [f"COVER {pointer} = {json.dumps(value)}" for pointer, value in found["cover"].items()]
    lines.append(f"PLAN_REQ:OK roles={len(found['roles'])} stage_checks={len(found['stages'])} "
                 f"boundary={len(found['boundary'])} reach={len(found['reach'])} "
                 f"cover={len(found['cover'])} proven={len(found['proven'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n".join(__doc__.splitlines()[2:]))
    parser.add_argument("--spec", type=Path, required=True, help="design_spec.json")
    parser.add_argument("--intent", type=Path, required=True, help="binding_intent.json")
    parser.add_argument("--snapshot", type=Path, required=True, help="binding_snapshot.json")
    parser.add_argument("--roles-from", type=Path, help="Tcl script with ::addrmap::required_roles")
    parser.add_argument("--fragments", nargs="*", type=Path, default=[], help="proven/*.json from export_proven")
    parser.add_argument("--coverage-out", type=Path, help="write the parameter_coverage skeleton here")
    options = parser.parse_args(argv)
    try:
        spec = _load(options.spec, "design spec")
        intent = _load(options.intent, "binding intent")
        snapshot = _load(options.snapshot, "binding snapshot")
        fragments = [_load(path, "proven fragment") for path in options.fragments]
        roles_tcl = options.roles_from.read_text(encoding="utf-8") if options.roles_from else None
        found = requirements(spec, intent, snapshot, roles_tcl, fragments)
    except (RequirementError, OSError) as error:
        print(f"PLAN_REQ_FAIL:{error}")
        return 1
    if options.coverage_out:
        options.coverage_out.write_text(json.dumps(coverage_skeleton(found["cover"]), indent=2) + "\n",
                                        encoding="utf-8")
    print(report(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
