#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Debug-readiness check for a migrated Unified workspace (stdlib only).

For every component that has `_ide/launch.json`, verify that each file the launch
configurations reference (ELF, PDI, bitstream, FSBL, psu_init/ps7_init tcl) exists.

Usage (Python bundled with Vitis): vitis -s check_launch.py <unified_workspace> [component ...]
Exit 0 when every requested component has a launch.json and no referenced file is missing.
"""
import json
import os
import sys

if sys.version_info < (3, 10):
    sys.exit("Run with the Python bundled with Vitis: vitis -s check_launch.py ...")

PATH_KEYS = {"file", "executable"}


def _paths(node, trail=""):
    """Yield (json_trail, value) for every non-empty file-path field."""
    if isinstance(node, dict):
        for key, val in node.items():
            here = f"{trail}.{key}" if trail else key
            if isinstance(val, str) and val and (key.endswith("File") or key in PATH_KEYS):
                yield here, val
            else:
                yield from _paths(val, here)
    elif isinstance(node, list):
        for i, val in enumerate(node):
            yield from _paths(val, f"{trail}[{i}]")


def launchable_host_apps(ws: str) -> list[str]:
    """Every HOST app: the IDE creates a launch configuration for each, Linux included."""
    out: list[str] = []
    for name in sorted(os.listdir(ws)):
        cj = os.path.join(ws, name, "vitis-comp.json")
        if not os.path.isfile(cj):
            continue
        try:
            with open(cj, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("type") != "HOST":
            continue
        out.append(name)
    return out


def components_to_check(ws: str) -> list[str]:
    """Union of dirs with _ide/launch.json and HOST apps that should have one."""
    have_launch = {d for d in os.listdir(ws)
                   if os.path.isfile(os.path.join(ws, d, "_ide", "launch.json"))}
    return sorted(have_launch | set(launchable_host_apps(ws)))


def check_component(ws: str, comp: str) -> list[str]:
    """Return a list of problems for one component (empty list means debug-ready)."""
    launch = os.path.join(ws, comp, "_ide", "launch.json")
    if not os.path.isfile(launch):
        return [f"{comp}: no _ide/launch.json"]
    try:
        with open(launch, encoding="utf-8") as fh:
            raw = fh.read()
        if not raw.strip():
            return [f"{comp}: launch.json is empty or not valid JSON ({launch})"]
        configs = json.loads(raw).get("configurations", [])
    except json.JSONDecodeError:
        return [f"{comp}: launch.json is empty or not valid JSON ({launch})"]
    if not configs:
        return [f"{comp}: launch.json has no configurations"]
    problems = []
    for cfg in configs:
        for trail, raw_path in _paths(cfg):
            path = raw_path.replace("${workspaceFolder}", ws)
            if not os.path.isabs(path):
                path = os.path.join(os.path.dirname(launch), path)
            if not os.path.exists(path):
                problems.append(f"{comp}: [{cfg.get('name')}] {trail} -> missing {raw_path}")
    return problems


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    ws = os.path.abspath(argv[0])
    if argv[1:]:
        comps = argv[1:]
    else:
        comps = components_to_check(ws)
        if not comps:
            print(f"PASS: no migrated application to check in {ws}")
            return 0
    problems = [p for c in comps for p in check_component(ws, c)]
    for p in problems:
        print(f"FAIL: {p}")
    print(f"{'FAIL' if problems else 'PASS'}: checked {len(comps)} component(s): {', '.join(comps)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
