#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Replay Stage 2's proven IP configurations into the Stage 4 assembly plan.

Stage 2 writes one `ipcfg::export_proven` fragment per scratch session
(`proven/g<k>.json`). This script merges them into one hash-pinned
`proven_ip_config.json`, checks every cell against `binding_snapshot.json`,
and rewrites the cells stage of `assembly_plan.template.json` so each proven
role is created, configured and read back with exactly the values that were
proven - nothing re-derived from the spec by hand. Every emitted operation
carries a `provenance` record that `compile_assembly_plan.py` verifies.

Prints PROVEN_PLAN:OK or PROVEN_PLAN_FAIL:<reason> (exit 1).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

FRAGMENT_SCHEMA = "ipcfg.proven/1"
MERGED_SCHEMA = "ipiasm.proven_ip_config/1"
EMBEDDED_KEYS = ("role", "apply_via", "requested", "sha256")
APPLY_VIA = {"create_cell", "ps_wizard", "board_preset"}
# Procs that configure or read back one cell; a proven role's copies are replaced.
CELL_PROCS = {"ipcfg::require", "ipcfg::apply_dict", "ipiasm::check_cell_config"}


class ProvenError(Exception):
    pass


def canonical_sha256(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProvenError(f"cannot read {what} {path}: {error}") from error


def merge(fragment_paths: list[Path], snapshot_path: Path) -> dict[str, Any]:
    """Merge export_proven fragments, refusing anything the snapshot did not bind."""
    if not fragment_paths:
        raise ProvenError("no proven fragments given")
    snapshot = _load(snapshot_path, "binding snapshot")
    environment = snapshot.get("environment", {})
    catalog = snapshot.get("catalog", {})
    cells: dict[str, dict[str, Any]] = {}
    fragments = []
    for path in fragment_paths:
        fragment = _load(path, "proven fragment")
        if fragment.get("schema") != FRAGMENT_SCHEMA:
            raise ProvenError(f"{path}: schema is not {FRAGMENT_SCHEMA}")
        for key in ("part", "board_part"):
            if fragment.get(key) != environment.get(key):
                raise ProvenError(f"{path}: {key} {fragment.get(key)!r} was proven on a different target than "
                                  f"the binding snapshot's {environment.get(key)!r}")
        fragments.append({"path": str(path.resolve()), "sha256": file_sha256(path)})
        for cell in fragment.get("cells", []):
            role = cell.get("role")
            if role in cells:
                raise ProvenError(f"role {role!r} is proven twice ({path}); each role belongs to one group")
            cells[role] = _checked_cell(cell, catalog.get(role), path)
    for role, cell in cells.items():
        cell["sha256"] = canonical_sha256(cell)
    return {
        "schema": MERGED_SCHEMA,
        "part": environment.get("part"),
        "board_part": environment.get("board_part"),
        "binding_snapshot_sha256": file_sha256(snapshot_path),
        "fragments": fragments,
        "cells": [cells[role] for role in sorted(cells)],
    }


def _checked_cell(cell: dict[str, Any], binding: dict[str, Any] | None, source: Path) -> dict[str, Any]:
    role = cell.get("role")
    if binding is None:
        raise ProvenError(f"role {role!r} ({source}) is not in the binding snapshot catalog")
    if cell.get("vlnv") != binding.get("resolved_vlnv"):
        raise ProvenError(f"role {role!r}: proven VLNV {cell.get('vlnv')!r} is not the bound "
                          f"{binding.get('resolved_vlnv')!r}")
    if cell.get("apply_via") not in APPLY_VIA:
        raise ProvenError(f"role {role!r}: apply_via must be one of {sorted(APPLY_VIA)}")
    requested = cell.get("requested", {})
    if not isinstance(requested, dict):
        raise ProvenError(f"role {role!r}: requested must be an object")
    known = set(binding.get("config_properties", []))
    unknown = sorted(key for key in requested if key not in known)
    if unknown:
        raise ProvenError(f"role {role!r}: requested keys {unknown} are not CONFIG properties of the bound IP")
    # Only a create_cell dict is written by the plan; a wizard- or preset-owned cell's dict is
    # read back, and reading back a board-owned value (its bound board interface) overrides nothing.
    overrides = cell.get("board_override", {})
    owned = sorted(key for key in requested
                   if cell.get("apply_via") == "create_cell"
                   and key in set(cell.get("board_owned", [])) and not str(overrides.get(key, "")).strip())
    if owned:
        raise ProvenError(f"role {role!r}: requested keys {owned} are board-owned and carry no board_override "
                          f"reason; re-prove them with ipcfg::require <cell> <dict> <reason> {{}} {{spec ...}}")
    evidence = cell.get("evidence", [])
    if not evidence:
        raise ProvenError(f"role {role!r}: no capture evidence")
    hashes = {}
    for item in evidence:
        path = Path(item)
        if not path.is_absolute():
            path = source.parent / path
        if not path.is_file():
            raise ProvenError(f"role {role!r}: evidence not found: {item}")
        hashes[item] = file_sha256(path)
    checked = copy.deepcopy(cell)
    checked["evidence_sha256"] = hashes
    return checked


def _role_of(operation: dict[str, Any], roles: dict[str, dict[str, Any]]) -> str | None:
    """The proven role a cells-stage operation creates, configures or reads back."""
    if operation.get("op") != "call":
        return None
    args = operation.get("args", [])
    proc = operation.get("proc")
    for role, cell in roles.items():
        names = {role, cell.get("instance"), f"${{bind.blocks.{role}.instance}}"}
        if proc == "ipiasm::create_cell" and len(args) >= 2 and (
                args[1] in names or str(args[0]).startswith(f"${{bind.blocks.{role}.")):
            return role
        if proc in CELL_PROCS and args and args[0] in names:
            return role
    return None


def _proven_operations(cell: dict[str, Any], source: str) -> tuple[list[dict[str, Any]], str | None]:
    role = cell["role"]
    provenance = {"source": source, "role": role, "sha256": cell["sha256"]}
    instance = f"${{bind.blocks.{role}.instance}}"
    requested = cell.get("requested", {})
    operations = []
    if cell["apply_via"] == "create_cell":
        operations.append({"id": f"proven_create_{role}", "op": "call", "proc": "ipiasm::create_cell",
                           "args": [f"${{bind.blocks.{role}.vlnv_unversioned}}", instance, {}],
                           "expect": ["SUCCESS:"]})
        if requested:
            args = [instance, requested, f"proven in Stage 2 ({role})"]
            overrides = [f"{key}: {cell['board_override'][key]}" for key in requested
                         if key in cell.get("board_override", {})]
            if overrides:
                args += [{}, "; ".join(overrides)]
            # ipcfg::require reports success as REQ:pinned+applied, not SUCCESS:.
            operations.append({"id": f"proven_configure_{role}", "op": "call", "proc": "ipcfg::require",
                               "args": args, "expect": ["REQ:pinned+applied"]})
    check = None
    if requested:
        check = f"proven_check_{role}"
        operations.append({"id": check, "op": "call", "proc": "ipiasm::check_cell_config",
                           "args": [instance, requested], "expect": ["CELL_CONFIG:OK"]})
    for operation in operations:
        operation["provenance"] = dict(provenance)
    return operations, check


def _parameter_leaves(spec: dict[str, Any], pointer: str) -> dict[str, Any]:
    """Leaf pointers under `pointer` that parameter_coverage has to account for."""
    node: Any = spec
    for token in [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]:
        try:
            node = node[int(token)] if isinstance(node, list) else node[token]
        except (KeyError, IndexError, ValueError, TypeError) as error:
            raise ProvenError(f"spec pointer {pointer} does not resolve in the DesignSpec") from error
    leaves: dict[str, Any] = {}

    def visit(value: Any, at: str) -> None:
        if isinstance(value, dict) and value:
            for key, child in value.items():
                visit(child, at + "/" + key.replace("~", "~0").replace("/", "~1"))
        elif isinstance(value, list) and value:
            for index, child in enumerate(value):
                visit(child, f"{at}/{index}")
        else:
            leaves[at] = value

    visit(node, pointer)
    return {at: value for at, value in leaves.items()
            if len(at.split("/")) > 4 and at.split("/")[1] == "blocks" and at.split("/")[3] == "parameters"}


def apply_to_template(template: dict[str, Any], merged: dict[str, Any], source: str,
                      spec: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = copy.deepcopy(template)
    stages = [stage for stage in plan.get("stages", []) if stage.get("name") == "cells"]
    if len(stages) != 1:
        raise ProvenError("the template needs exactly one stage named 'cells'")
    stage = stages[0]
    roles = {cell["role"]: cell for cell in merged["cells"]}
    replaced: dict[str, str | None] = {}
    emitted: dict[str, tuple[list[dict[str, Any]], str | None]] = {
        role: _proven_operations(cell, source) for role, cell in roles.items()}
    kept, placed = [], set()
    for operation in stage.get("operations", []):
        role = _role_of(operation, roles)
        if role is None:
            kept.append(operation)
            continue
        cell = roles[role]
        # A wizard- or preset-owned cell is created and configured by the board stage;
        # only its readback is replaced here.
        if cell["apply_via"] != "create_cell" and operation.get("proc") != "ipiasm::check_cell_config":
            kept.append(operation)
            continue
        # Nothing proven to read back (an empty requested dict): the author's readback stays,
        # or the parameters it covers would be left with no check at all.
        if operation.get("proc") == "ipiasm::check_cell_config" and emitted[role][1] is None:
            kept.append(operation)
            continue
        if "id" in operation:
            replaced[operation["id"]] = emitted[role][1]
        if role not in placed:
            kept.extend(emitted[role][0])
            placed.add(role)
    head = next((index + 1 for index, operation in enumerate(kept)
                 if operation.get("proc") == "ipiasm::stage"), 0)
    for role in sorted(roles.keys() - placed):
        kept[head:head] = emitted[role][0]
        head += len(emitted[role][0])
    stage["operations"] = kept

    coverage = plan.setdefault("parameter_coverage", {})
    for entry in coverage.values():
        if isinstance(entry, dict) and isinstance(entry.get("checks"), list):
            remapped = []
            for check in entry["checks"]:
                check = replaced.get(check, check) if check in replaced else check
                if check and check not in remapped:
                    remapped.append(check)
            entry["checks"] = remapped
    if spec is not None:
        for role, cell in roles.items():
            check = emitted[role][1]
            if check is None:
                continue
            for pointer in cell.get("spec_pointers", []):
                for leaf, value in _parameter_leaves(spec, pointer).items():
                    entry = coverage.get(leaf)
                    if entry is None:
                        coverage[leaf] = {"value": value, "reason": f"proven in Stage 2 by role {role}",
                                          "disposition": "hardware", "checks": [check]}
                    elif isinstance(entry, dict) and entry.get("disposition") == "hardware":
                        checks = entry.setdefault("checks", [])
                        if check not in checks:
                            checks.append(check)
    # The plan carries what the compiler checks, so it stays valid without the merged
    # file next to it (a grader or reviewer may copy the plan alone).
    plan["proven_ip_config"] = {"path": source, "sha256": canonical_sha256(merged),
                                "cells": [embedded_cell(cell) for cell in merged["cells"]]}
    return plan


def embedded_cell(cell: dict) -> dict:
    return {key: cell[key] for key in EMBEDDED_KEYS if key in cell}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fragments", nargs="+", type=Path, required=True,
                        help="ipcfg::export_proven outputs, one per Stage 2 group")
    parser.add_argument("--snapshot", type=Path, required=True, help="binding_snapshot.json")
    parser.add_argument("--template", type=Path, required=True, help="assembly_plan.template.json")
    parser.add_argument("--merged", type=Path, required=True, help="proven_ip_config.json to write")
    parser.add_argument("--spec", type=Path, help="design_spec.json; wires spec_pointers into parameter_coverage")
    parser.add_argument("--out", type=Path, help="template output (default: update --template in place)")
    options = parser.parse_args(argv)
    try:
        merged = merge(options.fragments, options.snapshot)
        template = _load(options.template, "assembly plan template")
        spec = _load(options.spec, "design spec") if options.spec else None
        out = options.out or options.template
        # The plan names the merged file relative to itself, so the pair can move together.
        try:
            source = str(options.merged.resolve().relative_to(out.resolve().parent))
        except ValueError:
            source = str(options.merged.resolve())
        plan = apply_to_template(template, merged, source, spec)
    except ProvenError as error:
        print(f"PROVEN_PLAN_FAIL:{error}")
        return 1
    options.merged.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(f"PROVEN_PLAN:OK roles={len(merged['cells'])} merged={options.merged} template={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
