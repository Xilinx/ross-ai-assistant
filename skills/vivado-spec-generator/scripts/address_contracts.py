#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Generate DesignSpec address contracts from a supplied address-map CSV.

The CSV fixes masters, targets, offsets and ranges; this turns them into
fixed_address_windows, control_reachability, memory_reachability and the
reference_groups for each master, exactly as the audit matches them. The
caller supplies only the judgement the CSV cannot: which declared block owns
each target, which targets are memory, and which members make up each master
group. Run with --skeleton first to get that binding to fill in.

    address_contracts.py --csv map.csv --skeleton > binding.json
    address_contracts.py --csv map.csv --bind binding.json --out contracts.json

Merge the output into the spec: its four lists go under functional_contracts
and reference_groups under global_constraints. Prints ADDRESS_CONTRACTS:OK,
or ADDRESS_CONTRACTS_FAIL naming every unbound target and wrong group size.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path


def read_rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        rows = [{key.strip(): (value or "").strip() for key, value in row.items()}
                for row in csv.DictReader(handle)]
    missing = {"master", "target", "base", "size"} - set(rows[0] if rows else {})
    if missing:
        raise ValueError(f"CSV lacks columns {sorted(missing)}")
    return rows


def skeleton(rows):
    counts = {}
    for row in rows:
        counts.setdefault(row["master"], row.get("master_count") or "?")
    return {
        "blocks": {target: None for target in dict.fromkeys(row["target"] for row in rows)},
        "memory": [],
        "groups": {master: [f"<{count} member reference(s)>"] for master, count in counts.items()},
        "_help": ("blocks: declared block owning each target; a prefix key such as 'isp' binds every "
                  "'isp.*' target. memory: targets or prefixes that are memory (memory_reachability); "
                  "the rest become control_reachability. groups: each master's member references, "
                  "exactly master_count of them after expanding nested groups (a member may itself be a group "
                  "key, e.g. processing_system.pmc -> its four masters). Delete this _help key."),
    }


def owner(target, blocks):
    if target in blocks:
        return blocks[target]
    return blocks.get(target.split(".", 1)[0])


def is_memory(target, memory):
    return target in memory or target.split(".", 1)[0] in memory


def leaves(name, groups, seen=()):
    """Members of a group after expanding nested groups, as the audit counts them."""
    if name not in groups or name in seen:
        return {name}
    found = set()
    for member in groups[name]:
        found |= leaves(member, groups, seen + (name,))
    return found


def generate(rows, binding, source_name):
    blocks, memory, groups = binding.get("blocks", {}), set(binding.get("memory", [])), binding.get("groups", {})
    problems = []
    for target in dict.fromkeys(row["target"] for row in rows):
        if not owner(target, blocks):
            problems.append(f"target {target}: no owning block in 'blocks'")
    counts = {}
    for row in rows:
        counts.setdefault(row["master"], row.get("master_count"))
    for master, count in counts.items():
        members = groups.get(master)
        if not isinstance(members, list) or not members or any(not isinstance(m, str) or m.startswith("<")
                                                                 for m in members):
            problems.append(f"master {master}: 'groups' needs its member references")
        elif count and count.isdigit() and len(leaves(master, groups)) != int(count):
            found = sorted(leaves(master, groups))
            problems.append(f"master {master}: resolves to {len(found)} member(s) {found}, CSV master_count is {count}")
    if problems:
        return None, problems

    windows, control, reach = {}, {}, {}
    for row in rows:
        target, master = row["target"], row["master"]
        key = (target, row["base"], row["size"])
        if key not in windows:
            policy = row.get("policy")
            reason = f"Exact software contract from {source_name}" + (f" (policy {policy})" if policy else "")
            windows[key] = {"name": "win_" + re.sub(r"[^A-Za-z0-9]+", "_", target).strip("_"),
                            "target": target, "block": owner(target, blocks), "masters": [],
                            "offset": row["base"], "range": row["size"], "reason": reason}
        if master not in windows[key]["masters"]:
            windows[key]["masters"].append(master)
        if is_memory(target, memory):
            reach.setdefault((master, target), {"master": master, "memory": target,
                                                "access": "read_write", "required": True})
        else:
            block = owner(target, blocks)
            control.setdefault((master, block), {"master": master, "target": block,
                                                 "access": "read_write", "required": True})
    names = {}
    for window in windows.values():
        # Two windows on one target at different offsets need distinct names.
        count = names[window["name"]] = names.get(window["name"], 0) + 1
        if count > 1:
            window["name"] += f"_{count}"
    return {
        "fixed_address_windows": list(windows.values()),
        "control_reachability": list(control.values()),
        "memory_reachability": list(reach.values()),
        # Every group given, so nested groups a master expands through are defined too.
        "reference_groups": groups,
    }, []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", required=True, type=Path, help="supplied address-map CSV (master, target, base, size)")
    parser.add_argument("--skeleton", action="store_true", help="print the binding JSON to fill in, then stop")
    parser.add_argument("--bind", type=Path, help="filled binding JSON: blocks, memory, groups")
    parser.add_argument("--out", type=Path, help="write the contracts JSON here (default: stdout)")
    args = parser.parse_args(argv)
    rows = read_rows(args.csv)
    if args.skeleton:
        print(json.dumps(skeleton(rows), indent=2))
        return 0
    if not args.bind:
        parser.error("--bind is required unless --skeleton is given")
    contracts, problems = generate(rows, json.loads(args.bind.read_text(encoding="utf-8")), args.csv.name)
    if problems:
        print("ADDRESS_CONTRACTS_FAIL:\n  " + "\n  ".join(problems))
        return 1
    text = json.dumps(contracts, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    print(f"ADDRESS_CONTRACTS:OK windows={len(contracts['fixed_address_windows'])} "
          f"control={len(contracts['control_reachability'])} memory={len(contracts['memory_reachability'])} "
          f"groups={len(contracts['reference_groups'])}", file=sys.stderr if not args.out else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
