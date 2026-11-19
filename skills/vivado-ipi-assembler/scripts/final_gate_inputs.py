#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Derive ipiasm::final_contract_gate's arguments from an assembly plan and its DesignSpec.

Everything comes from what the plan already checks and what the spec already
states, so a plan-built design reaches the final gate without hand-written glue:

- windows: each plan `ipiasm::check_window_scope` op, named after the DesignSpec
  fixed address window with the same offset and range (and, when several share them,
  the same masters, block and target interface);
- bundle: the arguments of the plan's last `ipiasm::validate_bundle` op;
- dram_masters: the address spaces allowed into every window that a
  `memory_reachability` contract requires and that a `memory_controller` block owns
  (on-chip memories such as a BRAM mailbox are not DRAM), plus the patterns of each plan
  `ipiasm::check_required_dram_masters` op (a spec with no fixed window on its memory);
- reachability: the pairs of the plan's last `ipiasm::check_required_reachability` op;
- visibility: the {slave segment, allowed spaces} of each plan `ipiasm::check_visibility`
  op, which the gate reruns and records as visibility_policy evidence (a plan op's own
  result is not gate evidence, so without this a plan could not prove that category).

Prints a Tcl dict. Fails, naming the window, when a plan window matches no spec
window or more than one.
"""

import argparse
import json
from pathlib import Path
import re
import sys

SIMPLE = re.compile(r"^[A-Za-z0-9_./:*#@+-]+$")


def tcl(value):
    """Quote a JSON value as one Tcl word."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple)):
        return "{" + " ".join(tcl(item) for item in value) + "}"
    text = str(value)
    if SIMPLE.match(text):
        return text
    if "\\" not in text and text.count("{") == text.count("}") and "}{" not in text:
        return "{" + text + "}"
    return '"' + re.sub(r'([\\"$\[\]{}])', r"\\\1", text) + '"'


def number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def operations(plan):
    for stage in plan.get("stages") or []:
        for operation in stage.get("operations") or []:
            if isinstance(operation, dict) and operation.get("op", "call") == "call":
                yield operation


def narrow(matches, slave, spaces):
    """Spec windows of one offset and range that this plan window is.

    Several masters can each map one memory at the same offset in their own address spaces
    (two traffic generators at 0x0 on one LPDDR controller); offset and range then name every
    such window. Keep, while more than one is left and the test leaves some, the windows whose
    masters own the plan's address spaces, then those whose block and target interface the
    slave segment names."""
    cells = {str(space).strip("/").split("/")[0] for space in spaces}
    parts = [part for part in str(slave).strip("/").split("/") if part]
    tests = (
        lambda window: bool(cells & {str(master).strip("/") for master in window.get("masters") or []}),
        lambda window: bool(parts) and parts[0] == str(window.get("block", "")),
        lambda window: "." in str(window.get("target", "")) and any(
            part.endswith(str(window["target"]).split(".", 1)[1]) for part in parts[1:]),
    )
    for test in tests:
        if len(matches) <= 1:
            break
        kept = [window for window in matches if test(window)]
        if kept:
            matches = kept
    return matches


def derive(plan, spec):
    contracts = spec.get("functional_contracts") or {}
    spec_windows = [window for window in contracts.get("fixed_address_windows") or []
                    if isinstance(window, dict) and window.get("required") is not False]
    memories = {item.get("memory") for item in contracts.get("memory_reachability") or []
                if isinstance(item, dict) and item.get("required") is not False}
    controllers = {block.get("name") for block in spec.get("blocks") or []
                   if isinstance(block, dict) and block.get("workload_type") == "memory_controller"}
    windows, dram_masters, errors, visibility = [], [], [], []
    bundle, reachability = None, None
    for operation in operations(plan):
        proc, args = operation.get("proc"), operation.get("args") or []
        if proc == "ipiasm::check_window_scope" and len(args) >= 4:
            slave, spaces, offset, size = args[:4]
            matches = narrow([window for window in spec_windows
                              if number(window.get("offset", -1)) == number(offset)
                              and number(window.get("range", -1)) == number(size)], slave, spaces)
            if len(matches) != 1:
                names = ", ".join(window.get("name", "?") for window in matches) or "none"
                errors.append(f"{operation.get('id')} ({slave} {offset}/{size}) matches {names}")
                continue
            window = matches[0]
            windows.append([window.get("name"), slave, spaces, offset, size])
            if window.get("target") in memories and window.get("block") in controllers:
                dram_masters.extend(space for space in spaces if space not in dram_masters)
        elif proc == "ipiasm::validate_bundle":
            bundle = args
        elif proc == "ipiasm::check_required_reachability" and args:
            reachability = args[0]
        elif proc == "ipiasm::check_visibility" and args:
            visibility.append([args[0], args[1] if len(args) > 1 else []])
        elif proc == "ipiasm::check_required_dram_masters" and args and isinstance(args[0], list):
            dram_masters.extend(space for space in args[0] if space not in dram_masters)
    if errors:
        raise ValueError("plan window(s) not matched to exactly one DesignSpec window by offset, range "
                         "and masters: "
                         + "; ".join(errors))
    return {
        "windows": windows,
        "bundle": bundle if bundle is not None else [0, "", 0, 1, ""],
        "dram_masters": dram_masters,
        "reachability": reachability or [],
        "visibility": visibility,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    try:
        inputs = derive(plan, spec)
    except ValueError as error:
        print(f"FINAL_INPUTS_FAIL:{error}")
        return 1
    print(" ".join(f"{key} {tcl(value)}" for key, value in inputs.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
