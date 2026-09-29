#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

"""Helpers for post-compile AIE artifact discovery and parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path

YAML_WIDEN_PREFIX = "INST_VCONV_fp32_bf16"  # InstructionMix key for the widen
CUSTOM_OP_NS = "custom_ops::"  # kernel_name prefix of a user-authored custom op


def find_lst_files(cache_dir: Path) -> list[Path]:
    return sorted(cache_dir.rglob("*.lst"))


def find_core_lsts(cache_dir: Path) -> list[Path]:
    """Per-core kernel disassembly (aie/<r>_<c>/Release/<r>_<c>.lst).

    Excludes analysis stubs (e.g. pm_reload_analysis0.lst) by requiring the stem
    to look like a core id.
    """
    core_re = re.compile(r"^\d+_\d+$")
    return sorted(p for p in cache_dir.rglob("*.lst") if core_re.match(p.stem))


def find_core_maps(cache_dir: Path) -> list[Path]:
    """Linked per-core .map files (aie/<r>_<c>/Release/<r>_<c>.map).

    Excludes analysis stubs like pm_reload_analysis0.map by requiring the stem
    to look like a core id (e.g. '0_0').
    """
    core_re = re.compile(r"^\d+_\d+$")
    return sorted(p for p in cache_dir.rglob("*.map") if core_re.match(p.stem))


def find_report(cache_dir: Path, name: str) -> Path | None:
    hits = sorted(cache_dir.rglob(name))
    return hits[0] if hits else None


def _load_remarks(path: Path) -> list[dict]:
    """Documents of one optimization-record YAML file.
    """
    import yaml  # optional: absent when the records were not requested

    # Use the fast C parser if present, else the Python one.
    Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    # Make our own loader so we do not change PyYAML globally.
    class _RemarkLoader(Loader):
        pass
    # Treat every YAML tag (!Passed / !Missed / !Analysis) as a normal dict.
    _RemarkLoader.add_multi_constructor(
        "", lambda ldr, suffix, node: ldr.construct_mapping(node, deep=True)
    )
    # Open the file and parse every YAML document in it.
    with path.open(errors="ignore") as fh:
        # Keep only dicts; skip anything else.
        return [d for d in yaml.load_all(fh, Loader=_RemarkLoader) if isinstance(d, dict)]


def remark_facts(cache_dir: Path) -> dict[str, dict]:
    """Scope facts per user-kernel function, from the optimization records.

    {name: {"blocks": [(offset, n_bytes, block)], "loops": {block},
            "mix": {block: {opcode_key: count}}, "widens": n}}
    keyed by mangled name, for functions in the custom_ops namespace only.
    Offsets are function-relative; the .lst label supplies the base address.
    'widens' is counted over the whole function, not per block, because an
    accumulator stays live across block boundaries -- only zero for the entire
    function proves no widen -> ALU -> narrow chain can exist in it.
    """
    facts: dict[str, dict] = {}
    for path in sorted(cache_dir.rglob("*.opt.yaml")):
        if "aie" not in path.parts:
            continue  # Work/pthread/*: x86sim host records, tens of MB, not AIE
        try:
            docs = _load_remarks(path)
        except Exception:  # noqa: BLE001 -- no PyYAML, or an unreadable record
            continue
        for doc in docs:
            fn = doc.get("Function")
            if not isinstance(fn, str) or "custom_ops" not in fn:
                continue
            args: dict = {}
            for arg in doc.get("Args") or []:
                if isinstance(arg, dict):
                    args.update(arg)
            entry = facts.setdefault(
                fn, {"blocks": [], "loops": set(), "mix": {}, "widens": 0}
            )
            if doc.get("Pass") == "aie-asm-printer" and "Offset" in args:
                entry["blocks"].append(
                    (int(args["Offset"]), int(args["ByteCount"]), args["BasicBlock"])
                )
            elif doc.get("Name") == "InstructionMix" and "BasicBlock" in args:
                entry["mix"][args["BasicBlock"]] = {
                    k: int(v) for k, v in args.items() if k.startswith("INST_")
                }
            elif args.get("Zero-Overhead-Loop") == "true":
                entry["loops"].add(args["BasicBlock"])
            elif doc.get("Name") == "schedule" and "Loop" in args:
                # 'bb.<n>.<block name>'
                entry["loops"].add(args["Loop"].split(".", 2)[-1])
    for entry in facts.values():
        entry["widens"] = sum(
            n
            for mix in entry["mix"].values()
            for key, n in mix.items()
            if key.startswith(YAML_WIDEN_PREFIX)
        )
    return facts


def custom_op_output_dtypes(cache_dir: Path) -> dict[str, str]:
    """{custom-op kernel function -> output tensor `elementType`}, from top.json.

    Reads each `custom_ops::` kernel function block's OUTPUT buffer port (the one
    with direction=="out" and bufferPort set) and returns its `elementType` (e.g.
    "int8"). This is role-based -- the port carries its own direction and dtype --
    so it is correct for weighted / multi-input kernels too, unlike positional
    parsing of the mangled template args. Returns {} if top.json is unavailable.

    top.json is the ADF graph description (aiecompiler/Work/temp/top.json), an
    elaboration artifact that may be absent (e.g. cleaned builds); tolerate that.
    """
    tops = [
        p
        for p in cache_dir.rglob("top.json")
        if "aiecompiler" in p.parts and "temp" in p.parts
    ]
    if not tops:
        return {}
    try:
        graph = json.loads(tops[0].read_text(errors="ignore"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for block in (graph.get("blockTypes") or {}).values():
        if not isinstance(block, dict) or block.get("type") != "function":
            continue
        fn = block.get("function", "")
        if not fn.startswith(CUSTOM_OP_NS):  # 'custom_ops::' only
            continue
        for port in (block.get("ports") or {}).values():
            if (
                isinstance(port, dict)
                and port.get("direction") == "out"
                and port.get("bufferPort")
                and port.get("elementType")
            ):
                out[fn] = port["elementType"]
                break
    return out


def max_report_value(report: Path | None, pattern: str) -> tuple[int, list[str]]:
    """Return (max_value, per_core_notes) for a 'label = N' pattern in a report."""
    if report is None:
        return 0, []
    rx = re.compile(pattern)
    core_rx = re.compile(r"^\s*Core\s+(\S+)")
    max_v = 0
    notes: list[str] = []
    cur_core = "?"
    for line in report.read_text(errors="ignore").splitlines():
        cm = core_rx.match(line)
        if cm:
            cur_core = cm.group(1)
            continue
        m = rx.search(line)
        if m:
            v = int(m.group(1))
            if v > max_v:
                max_v = v
            notes.append(f"core {cur_core}: {v} B")
    return max_v, notes


def walk_dicts(node: object, key: str):
    """Yield every dict that contains `key`, anywhere in the mladf report JSON."""
    if isinstance(node, dict):
        if key in node:
            yield node
        for value in node.values():
            yield from walk_dicts(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from walk_dicts(value, key)
