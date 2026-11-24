#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Map every required DesignSpec contract to the readback evidence that proves it.

`derive` lists the obligations; `check` compares them with the final gate's
evidence and fails on any required contract no passing check covers. Contracts
a block design cannot prove (software behaviour, host-side interrupt delivery)
are reported as deferred, never silently dropped.
"""

import argparse
import json
from pathlib import Path
import re
import sys

NEEDS = {
    "connections": (r"PLAN_EXECUTION:OK", "an executed assembly plan (PLAN_EXECUTION:OK)"),
    "control_reachability": (r"(?<![A-Z_])REACH:OK", "ipiasm::check_required_reachability -> REACH:OK"),
    "memory_reachability": (r"DRAM_REACH:OK", "required_dram_masters -> DRAM_REACH:OK"),
    "visibility_policy": (r"VISIBILITY:OK", "ipiasm::check_visibility -> VISIBILITY:OK"),
    "clock_reset": (r"CLOCK_CONTRACTS:OK", "ipiasm::set_clock_contracts, then CLOCK_CONTRACTS:OK"),
}


def obligations(spec):
    """Return (required, deferred): lists of {id, category, needs, labels?, name?}."""
    contracts = spec.get("functional_contracts") or {}
    blocks = {block.get("name") for block in spec.get("blocks") or []}
    required, deferred = [], []
    for category, items in contracts.items():
        for index, item in enumerate(items if isinstance(items, list) else []):
            if isinstance(item, dict) and item.get("required") is False:
                continue
            ident = f"functional_contracts.{category}[{index}]"
            if category == "software":
                deferred.append({"id": ident, "category": category, "why": "software behaviour"})
            elif category == "interrupts":
                destination = str(item.get("destination", "")).split(".")[0]
                if destination not in blocks:
                    deferred.append({"id": ident, "category": category,
                                     "why": f"delivered outside the design to {destination or 'unknown'}"})
                    continue
                labels = [f"{item.get('source')}.{output}" for output in item.get("outputs") or []]
                required.append({"id": ident, "category": category, "labels": labels,
                                 "needs": "ipiasm::set_irq_contracts labels {" + " ".join(labels) + "} -> IRQ_PATHS:OK"})
            elif category == "fixed_address_windows":
                required.append({"id": ident, "category": category, "name": item.get("name", ""),
                                 "needs": f"a '{item.get('name', '')} WINDOW_SCOPE:OK' window spec or recorded address evidence"})
            elif category in NEEDS:
                required.append({"id": ident, "category": category, "needs": NEEDS[category][1]})
            else:
                required.append({"id": ident, "category": category,
                                 "needs": f"recorded evidence for {category} (ipiasm::record_evidence)"})
    for index, _ in enumerate(spec.get("clock_domains") or []):
        required.append({"id": f"clock_domains[{index}]", "category": "clock_reset",
                         "needs": NEEDS["clock_reset"][1]})
    return required, deferred


def recorded(evidence):
    categories = set()
    for line in evidence.splitlines():
        match = re.match(r"\s*EVIDENCE:(\S+) categories=\{([^}]*)\}", line)
        if match:
            categories.update(match.group(2).split())
    return categories


def covered(item, evidence, from_records):
    category = item["category"]
    if category in from_records:
        return True
    if category == "interrupts":
        labels = set()
        for match in re.finditer(r"IRQ_PATHS:OK \S+ covered=\{([^}]*)\}", evidence):
            labels.update(match.group(1).split())
        return bool(item["labels"]) and set(item["labels"]) <= labels
    if category == "fixed_address_windows":
        return bool(item["name"]) and re.search(
            r"(^|\s)" + re.escape(item["name"]) + r" WINDOW_SCOPE:OK", evidence, re.M) is not None
    if category in NEEDS:
        return re.search(NEEDS[category][0], evidence) is not None
    return False


def depth(item, from_records):
    """'each' when this contract has its own readback, else 'category'."""
    if item["category"] not in from_records and item["category"] in {"interrupts", "fixed_address_windows"}:
        return "each"
    return "category"


def check(spec, evidence):
    required, deferred = obligations(spec)
    from_records = recorded(evidence)
    missing = [item for item in required if not covered(item, evidence, from_records)]
    later = " ".join(item["id"] for item in deferred)
    each = sum(depth(item, from_records) == "each" for item in required)
    shared = {}
    for item in required:
        if depth(item, from_records) == "category":
            shared[item["category"]] = shared.get(item["category"], 0) + 1
    scope = (f"checked_each={each} checked_by_category={len(required) - each}"
             f" categories={{{' '.join(f'{name}({count})' for name, count in sorted(shared.items()))}}}")
    if missing:
        lines = [f"{item['id']}: needs {item['needs']}" for item in missing]
        return (f"COVERAGE_FAIL:{len(missing)}/{len(required)} required contract(s) have no passing readback"
                f" deferred={{{later}}}\n  " + "\n  ".join(lines))
    return (f"COVERAGE:OK obligations={len(required)} {scope} deferred={{{later}}}"
            " -- by-category contracts share one passing check per category, not one each")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    derive = commands.add_parser("derive")
    derive.add_argument("--spec", type=Path, required=True)
    verify = commands.add_parser("check")
    verify.add_argument("--spec", type=Path, required=True)
    verify.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if args.command == "derive":
        required, deferred = obligations(spec)
        print(json.dumps({"required": required, "deferred": deferred}, indent=1))
    else:
        print(check(spec, args.evidence.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
