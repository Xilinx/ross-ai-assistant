#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Classify each source file in a compile order report as IP or user-RTL.

Parses the output of `report_compile_order -file compile_order.rpt` and produces a
JSON map that downstream steps use to decide whether a violating object comes from
an IP (whose constraints are usually owned by the IP) or from user RTL.

Usage:
  python3 parse_compile_order.py <compile_order.rpt> <output.json>

The parser is format-tolerant: it scans every line for tokens that look like source
file paths (by extension), so it works with both the plain numbered listing and the
bordered-table variants of the report.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

# Source/IP file extensions we recognize in the compile order report.
SOURCE_EXT = (
    "v", "sv", "svh", "vh", "vhd", "vhdl", "vho", "vo",
    "xci", "xcix", "bd", "edn", "edf", "edif", "ngc", "dcp",
)

# Extensions that are always IP by nature.
IP_EXT = {"xci", "xcix", "bd", "edn", "edf", "edif", "ngc", "dcp"}

# Path fragments that indicate generated/packaged IP output products.
IP_PATH_MARKERS = (
    "/ip/",
    "/ipshared/",
    ".gen/",
    ".ip_user_files/",
    "/bd/",
)

PATH_RE = re.compile(
    r"[^\s|,;]+\.(?:" + "|".join(SOURCE_EXT) + r")\b",
    re.IGNORECASE,
)

IP_NAME_RE = re.compile(r"/ip/([^/]+)/", re.IGNORECASE)


def language_of(ext: str) -> str:
    ext = ext.lower()
    if ext in {"vhd", "vhdl", "vho"}:
        return "VHDL"
    if ext in {"v", "vh", "vo"}:
        return "Verilog"
    if ext in {"sv", "svh"}:
        return "SystemVerilog"
    if ext in {"xci", "xcix"}:
        return "IP"
    if ext == "bd":
        return "BlockDesign"
    return "Netlist"


def classify(path: str) -> dict[str, Any]:
    ext = path.rsplit(".", 1)[-1].lower()
    lower = path.lower()

    is_ip = False
    reason = "user RTL"

    if ext in IP_EXT:
        is_ip = True
        reason = f"IP/netlist source (.{ext})"
    else:
        for marker in IP_PATH_MARKERS:
            if marker in lower:
                is_ip = True
                reason = f"generated IP output product (path contains '{marker}')"
                break

    ip_match = IP_NAME_RE.search(path)
    if ip_match:
        ip_name = ip_match.group(1)
        is_ip = True
    elif ext in {"xci", "xcix", "bd"}:
        ip_name = Path(path).stem
    else:
        ip_name = None

    return {
        "path": path,
        "file": Path(path).name,
        "language": language_of(ext),
        "is_ip": is_ip,
        "ip_name": ip_name,
        "reason": reason,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: python3 parse_compile_order.py <compile_order.rpt> <output.json>",
            file=sys.stderr,
        )
        return 2

    input_rpt = Path(sys.argv[1])
    output_json = Path(sys.argv[2])

    if not input_rpt.exists():
        print(f"Error: input file not found: {input_rpt}", file=sys.stderr)
        return 1

    seen: dict[str, dict[str, Any]] = {}
    with input_rpt.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            for match in PATH_RE.finditer(line):
                path = match.group(0).strip().strip("'\"")
                if path not in seen:
                    seen[path] = classify(path)

    # The compile-order report lists each source twice: once as a bare "File Name"
    # and once as a "Full Path Name". Drop bare-filename tokens when the same
    # basename is also present with a directory component, so we don't double-count.
    basenames_with_dir = {
        e["file"] for e in seen.values() if "/" in e["path"]
    }
    files = [
        e
        for e in seen.values()
        if "/" in e["path"] or e["file"] not in basenames_with_dir
    ]
    files.sort(key=lambda e: e["path"].lower())

    ip_files = [e for e in files if e["is_ip"]]
    rtl_files = [e for e in files if not e["is_ip"]]

    output = {
        "source_file": str(input_rpt),
        "summary": {
            "total_files": len(files),
            "ip_files": len(ip_files),
            "user_rtl_files": len(rtl_files),
        },
        "files": files,
        "is_ip_by_file": {e["file"]: e["is_ip"] for e in files},
        "is_ip_by_path": {e["path"]: e["is_ip"] for e in files},
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    with output_json.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(
        f"Wrote IP classification map: {output_json} "
        f"({len(ip_files)} IP, {len(rtl_files)} user RTL, {len(files)} total)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
