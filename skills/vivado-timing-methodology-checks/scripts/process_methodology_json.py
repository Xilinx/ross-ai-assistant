#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Build an ordered, lookup-friendly methodology index.

Usage:
  python3 process_methodology_json.py <methodology.json> <references_dir> <output.json>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

SEVERITY_RANK = {
    "error": 0,
    "critical warning": 1,
    "warning": 2,
    "info": 3,
    # Unspecified severities are placed after the requested priority levels.
    "advisory": 4,
}

TIMING_RE = re.compile(r"^TIMING-\d+$", re.IGNORECASE)


def severity_rank(severity: str) -> int:
    return SEVERITY_RANK.get((severity or "").strip().lower(), 99)


def as_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def normalize_rule_entry(row: list[Any], headers: list[str], refs_dir: Path) -> dict[str, Any]:
    mapped = dict(zip(headers, row))
    rule = str(mapped.get("rule", "")).strip()
    severity = str(mapped.get("severity", "")).strip()
    description = str(mapped.get("description", "")).strip()
    count = as_int(mapped.get("count", 0))
    details = mapped.get("details", [])
    if not isinstance(details, list):
        details = [str(details)]

    ref_path = refs_dir / f"{rule}.md"
    is_timing = bool(TIMING_RE.match(rule))

    return {
        "rule": rule,
        "severity": severity,
        "severity_rank": severity_rank(severity),
        "description": description,
        "count": count,
        "is_timing_id": is_timing,
        "supported": is_timing,
        "has_reference_file": ref_path.exists(),
        "reference_file": str(ref_path) if ref_path.exists() else None,
        "details_count": len(details),
        "details_preview": details[:5],
    }


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "Usage: python3 process_methodology_json.py "
            "<methodology.json> <references_dir> <output.json>",
            file=sys.stderr,
        )
        return 2

    input_json = Path(sys.argv[1])
    refs_dir = Path(sys.argv[2])
    output_json = Path(sys.argv[3])

    if not input_json.exists():
        print(f"Error: input file not found: {input_json}", file=sys.stderr)
        return 1

    with input_json.open("r", encoding="utf-8") as f:
        data = json.load(f)

    summary = data.get("tables", {}).get("Report Summary", {})
    headers = [str(h).strip().lower() for h in summary.get("headers", [])]
    rows = summary.get("data", [])

    entries = [normalize_rule_entry(row, headers, refs_dir) for row in rows if isinstance(row, list)]

    ordered = sorted(
        entries,
        key=lambda x: (
            x["severity_rank"],
            0 if x["is_timing_id"] else 1,
            x["rule"],
        ),
    )

    lookup_by_rule = {entry["rule"]: entry for entry in ordered}

    # Only TIMING-* IDs are resolved by the skill; everything else is report-only.
    timing_work_list = [e for e in ordered if e["is_timing_id"]]
    unsupported_rules = [e for e in ordered if not e["is_timing_id"]]

    output = {
        "source_file": str(input_json),
        "report_header": data.get("report_header", {}),
        "severity_priority": ["Error", "Critical Warning", "Warning", "Info"],
        "ordered_rules": ordered,
        "timing_work_list": timing_work_list,
        "unsupported_rules": unsupported_rules,
        "lookup_by_rule": lookup_by_rule,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    with output_json.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote ordered methodology index: {output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
