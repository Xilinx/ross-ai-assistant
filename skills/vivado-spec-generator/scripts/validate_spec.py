#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Validate a DesignSpec's structure and implementation-independent contracts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


CONTRACT_LISTS = (
    "connections",
    "control_reachability",
    "interrupts",
    "memory_reachability",
    "fixed_address_windows",
    "visibility_policy",
    "clock_reset",
    "software",
)
ACCESS = {"read", "write", "read_write"}
POLARITY = {"active_low", "active_high", "interface_defined"}
HEX_OR_SIZE = re.compile(r"^(?:0x[0-9a-f]+|[1-9][0-9]*[kmgt]?)$", re.IGNORECASE)
CAMERA_PARAMETER_ALIASES = {
    "line_rate_mbps_per_lane": "line_rate_mbps",
    "line_rate_mb_s_per_lane": "line_rate_mbps",
    "active_lane_filtering": "active_lane_filter",
    "user_data_type_filtering": "user_data_type_filter",
    "isp_bridge_enabled": "isp_bridge",
    "isp_bridge_support": "isp_bridge",
    "dphy_register_interface_enabled": "dphy_register_interface",
    "d_phy_register_interface": "dphy_register_interface",
    "dphy_reg_if": "dphy_register_interface",
}
PS_VISIBILITY_ALIASES = {
    "platform_management_controller": "processing_system.pmc",
    "platform_processing_unit": "processing_system.ppu",
    "apu_system_unit": "processing_system.asu",
    "ps_mmi_peripheral_masters": "processing_system.mmi_peripheral_masters",
}


def _nonempty(item: dict[str, Any], field: str, path: str, errors: list[str]) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}.{field}: required non-empty string")
        return ""
    return value.strip()


def _is_application_master(master: object, groups=None, seen=()) -> bool:
    """Match explicit application actors, expanding declared reference groups."""
    name = str(master)
    if groups and name in groups:
        if name in seen:
            raise ValueError(f"cyclic master group '{name}'")
        members = groups[name]
        if not isinstance(members, list) or not members or not all(
            isinstance(member, str) and member.strip() for member in members
        ):
            raise ValueError(f"master group '{name}' must be a non-empty list of names")
        matches = [_is_application_master(member, groups, (*seen, name)) for member in members]
        return any(matches)
    return any(re.fullmatch(
        r"(?:apu|application(?:_cores)?|linux_apu|cortexa78|a78)"
        r"(?:_?\d+|\[[0-9:]+\])?(?:_(?:control|memory)_master)?",
        component,
    ) for component in name.lower().split("."))


def canonicalize(spec: object) -> list[str]:
    """Apply only lossless, schema-level alias migrations in place."""
    if not isinstance(spec, dict):
        return []
    changes: list[str] = []
    for block in spec.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        parameters = block.get("parameters")
        if not isinstance(parameters, dict):
            continue
        for alias, canonical in CAMERA_PARAMETER_ALIASES.items():
            if alias not in parameters or canonical in parameters:
                continue
            parameters[canonical] = parameters.pop(alias)
            changes.append(
                f"blocks.{block.get('name', '?')}.parameters.{alias}->{canonical}"
            )
    contracts = spec.get("functional_contracts")
    if not isinstance(contracts, dict):
        return changes
    for index, item in enumerate(contracts.get("visibility_policy") or []):
        if not isinstance(item, dict) or not isinstance(item.get("masters"), list):
            continue
        migrated = []
        for master in item["masters"]:
            replacement = PS_VISIBILITY_ALIASES.get(str(master).lower(), master)
            migrated.append(replacement)
            if replacement != master:
                changes.append(
                    f"visibility_policy[{index}].masters.{master}->{replacement}"
                )
        item["masters"] = migrated
    return changes


def validate(spec: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["root: expected JSON object"]

    for field in ("name", "target_device"):
        _nonempty(spec, field, "root", errors)
    board_part = spec.get("board_part")
    if board_part is not None and (
        not isinstance(board_part, str) or board_part.count(":") < 3
    ):
        errors.append("root.board_part: expected exact board VLNV")

    blocks = spec.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append("root.blocks: expected at least one functional block")
        blocks = []
    names: list[str] = []
    for index, block in enumerate(blocks):
        path = f"root.blocks[{index}]"
        if not isinstance(block, dict):
            errors.append(f"{path}: expected object")
            continue
        name = _nonempty(block, "name", path, errors)
        _nonempty(block, "workload_type", path, errors)
        if name:
            names.append(name)
        interfaces = block.get("interfaces", [])
        if not isinstance(interfaces, list):
            errors.append(f"{path}.interfaces: expected list")
            continue
        for j, interface in enumerate(interfaces):
            ipath = f"{path}.interfaces[{j}]"
            if not isinstance(interface, dict):
                errors.append(f"{ipath}: expected object")
                continue
            _nonempty(interface, "name", ipath, errors)
            _nonempty(interface, "protocol", ipath, errors)
            if interface.get("direction") not in {"in", "out", "bidirectional"}:
                errors.append(f"{ipath}.direction: expected in/out/bidirectional")
            if interface.get("scope", "internal") not in {
                "internal", "top_level", "external"
            }:
                errors.append(f"{ipath}.scope: expected internal/top_level/external")
    if len(names) != len(set(names)):
        errors.append("root.blocks: block names must be unique")

    by_name = {
        block.get("name"): block
        for block in blocks
        if isinstance(block, dict)
        and isinstance(block.get("name"), str)
        and block.get("name")
    }
    for name, block in by_name.items():
        text = json.dumps(block, sort_keys=True).lower()
        parameters = block.get("parameters") or {}
        if "mipi" in text or "camera_receiver" in name.lower():
            for alias, canonical in CAMERA_PARAMETER_ALIASES.items():
                if alias in parameters:
                    errors.append(
                        f"root.blocks[{name}].parameters.{alias}: use canonical "
                        f"'{canonical}'"
                    )

    ps_blocks = [
        block for name, block in by_name.items()
        if "processing_system" in name.lower() or block.get("workload_type") == "custom_ps"
    ]
    pcie_functions = [
        name for name in by_name
        if "pcie" in name.lower() and "processing_system" not in name.lower()
    ]
    if any("pcie" in json.dumps(block).lower() for block in ps_blocks) and not pcie_functions:
        errors.append(
            "root.blocks: preserve the integrated PCIe function as a separate "
            "semantic pcie_endpoint block"
        )

    unresolved = (spec.get("global_constraints") or {}).get(
        "unresolved_requirements", []
    )
    if not isinstance(unresolved, list) or not all(
        isinstance(item, str) and item.strip() for item in unresolved
    ):
        errors.append(
            "root.global_constraints.unresolved_requirements: expected list of "
            "non-empty strings"
        )

    contracts = spec.get("functional_contracts")
    if not isinstance(contracts, dict):
        errors.append("root.functional_contracts: expected object")
        contracts = {}
    for key in CONTRACT_LISTS:
        if not isinstance(contracts.get(key), list):
            errors.append(f"root.functional_contracts.{key}: expected list")

    for index, item in enumerate(contracts.get("connections") or []):
        path = f"functional_contracts.connections[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected object")
            continue
        _nonempty(item, "source", path, errors)
        _nonempty(item, "destination", path, errors)
        _nonempty(item, "protocol", path, errors)

    for index, item in enumerate(contracts.get("control_reachability") or []):
        path = f"functional_contracts.control_reachability[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected object")
            continue
        _nonempty(item, "master", path, errors)
        _nonempty(item, "target", path, errors)
        if item.get("access", "read_write") not in ACCESS:
            errors.append(f"{path}.access: expected read/write/read_write")

    for index, item in enumerate(contracts.get("interrupts") or []):
        path = f"functional_contracts.interrupts[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected object")
            continue
        _nonempty(item, "source", path, errors)
        _nonempty(item, "destination", path, errors)
        outputs = item.get("outputs")
        if not isinstance(outputs, list) or not outputs or not all(
            isinstance(output, str) and output.strip() for output in outputs
        ):
            errors.append(f"{path}.outputs: expected non-empty string list")

    for index, item in enumerate(contracts.get("memory_reachability") or []):
        path = f"functional_contracts.memory_reachability[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected object")
            continue
        _nonempty(item, "master", path, errors)
        _nonempty(item, "memory", path, errors)
        if item.get("access") not in ACCESS:
            errors.append(f"{path}.access: expected read/write/read_write")
        bandwidth = item.get("minimum_bandwidth_mb_s")
        if bandwidth is not None and (
            not isinstance(bandwidth, int) or bandwidth <= 0
        ):
            errors.append(f"{path}.minimum_bandwidth_mb_s: expected positive integer")

    for index, item in enumerate(contracts.get("fixed_address_windows") or []):
        path = f"functional_contracts.fixed_address_windows[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected object")
            continue
        for field in ("name", "target", "reason"):
            _nonempty(item, field, path, errors)
        if "block" in item and item["block"] not in names:
            errors.append(f"{path}.block: expected the name of a declared block")
        masters = item.get("masters")
        if not isinstance(masters, list) or not masters:
            errors.append(f"{path}.masters: expected non-empty list")
        for field in ("offset", "range"):
            value = item.get(field)
            if not isinstance(value, str) or not HEX_OR_SIZE.fullmatch(value):
                errors.append(f"{path}.{field}: expected hex or binary-size string")

    for index, item in enumerate(contracts.get("visibility_policy") or []):
        path = f"functional_contracts.visibility_policy[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected object")
            continue
        if item.get("action") not in {"allow", "deny"}:
            errors.append(f"{path}.action: expected allow/deny")
        for field in ("masters", "targets"):
            if not isinstance(item.get(field), list) or not item[field]:
                errors.append(f"{path}.{field}: expected non-empty list")
        for master in item.get("masters") or []:
            canonical = PS_VISIBILITY_ALIASES.get(str(master).lower())
            if canonical:
                errors.append(
                    f"{path}.masters: use canonical '{canonical}' instead of "
                    f"'{master}'"
                )

    for index, item in enumerate(contracts.get("clock_reset") or []):
        path = f"functional_contracts.clock_reset[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected object")
            continue
        _nonempty(item, "target", path, errors)
        if item.get("reset_polarity", "interface_defined") not in POLARITY:
            errors.append(f"{path}.reset_polarity: invalid polarity")
        if item.get("required", True) and item.get("synchronized") is not True:
            errors.append(f"{path}.synchronized: required resets must be synchronized")

    for index, item in enumerate(contracts.get("software") or []):
        path = f"functional_contracts.software[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path}: expected object")
            continue
        os_name = _nonempty(item, "os", path, errors)
        visible = item.get("visible_devices")
        if os_name.lower() == "linux" and (
            not isinstance(visible, list) or not visible
        ):
            errors.append(f"{path}.visible_devices: Linux requires visible devices")

    linux_visible = {
        str(device).split(".", 1)[0]
        for item in contracts.get("software") or []
        if isinstance(item, dict) and str(item.get("os", "")).lower() == "linux"
        for device in item.get("visible_devices") or []
    }
    groups = (spec.get("global_constraints") or {}).get("reference_groups", {})
    if not isinstance(groups, dict):
        errors.append("root.global_constraints.reference_groups: expected name-to-members object")
        groups = {}

    def is_application_master(master):
        try:
            return _is_application_master(master, groups)
        except ValueError as error:
            errors.append(f"root.global_constraints.reference_groups: {error}")
            return False

    if linux_visible:
        for index, item in enumerate(contracts.get("control_reachability") or []):
            if not isinstance(item, dict):
                continue
            if not is_application_master(item.get("master", "")):
                continue
            target = str(item.get("target", "")).split(".", 1)[0]
            if target not in by_name:
                continue
            target_block = by_name[target]
            if target_block.get("workload_type") in {"ml_inference", "custom_aie"}:
                continue
            if target not in linux_visible:
                errors.append(
                    f"functional_contracts.control_reachability[{index}].target: "
                    f"Linux-controlled '{target}' must appear in software.visible_devices"
                )

    # Inverse of the check above: a target explicitly denied to the application
    # cores cannot also be advertised to the OS that runs on them. Without this,
    # a spec can deny apu->camera_i2c and still list camera_i2c as Linux-visible.
    for index, item in enumerate(contracts.get("visibility_policy") or []):
        if not isinstance(item, dict) or item.get("action") != "deny":
            continue
        application_masters = [is_application_master(master) for master in item.get("masters") or []]
        if not any(application_masters):
            continue
        for target in item.get("targets") or []:
            name = str(target).split(".", 1)[0]
            if name in linux_visible:
                errors.append(
                    f"functional_contracts.visibility_policy[{index}].targets: "
                    f"'{name}' is denied to the application cores but listed in "
                    f"software.visible_devices for Linux"
                )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument(
        "--canonicalize-out",
        type=Path,
        help="write a copy with only lossless canonical alias migrations applied",
    )
    args = parser.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text())
    except (OSError, ValueError) as exc:
        print(f"SPEC: FAILED\n  - {exc}")
        return 1
    if args.canonicalize_out:
        changes = canonicalize(spec)
        args.canonicalize_out.parent.mkdir(parents=True, exist_ok=True)
        args.canonicalize_out.write_text(json.dumps(spec, indent=2) + "\n")
        print(f"SPEC_CANONICALIZED:{len(changes)}")
        for change in changes:
            print(f"  - {change}")
    errors = validate(spec)
    if errors:
        print("SPEC: FAILED")
        for error in errors:
            print(f"  - {error}")
        return 1
    unresolved = (spec.get("global_constraints") or {}).get(
        "unresolved_requirements", []
    )
    if unresolved:
        print("SPEC: FAILED\nResolve these functional questions before continuing:")
        for item in unresolved:
            print(f"  - {item}")
        return 1
    contracts = spec["functional_contracts"]
    count = sum(len(contracts[key]) for key in CONTRACT_LISTS)
    print(f"SPEC:SCHEMA_OK blocks={len(spec['blocks'])} contracts={count}; run audit_spec.py for the final result")
    return 0


if __name__ == "__main__":
    sys.exit(main())
