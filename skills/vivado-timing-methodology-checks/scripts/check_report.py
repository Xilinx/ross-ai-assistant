#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check that the resolution report content and the reports written from it are complete.

Usage (run from the project root; <skill_dir> is the directory containing SKILL.md):
  python3 <skill_dir>/scripts/check_report.py content [--dir ./methodology_results]
  python3 <skill_dir>/scripts/check_report.py md      [--dir ./methodology_results]
  python3 <skill_dir>/scripts/check_report.py html    [--dir ./methodology_results]

Exit 0 when complete. Exit 1 and print one line per problem when not. Fix every
problem and run the same command again.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

HEADER_KEYS = ["design", "part", "project", "run", "date", "skill_version"]
STATUSES = ("Resolved", "Waived", "User Decision Required", "New", "Unresolved", "Not Supported")
NOT_RESOLVED = ("User Decision Required", "New", "Unresolved", "Not Supported")
MD_HEADINGS = [
    "## 1. Summary",
    "## 2. Resolved Violations",
    "## 3. Waivers",
    "## 4. Remaining Actions",
    "## 5. Constraint File Diff",
    "## 6. Waiver File Diff",
]
HTML_MARKERS = [
    "Violation Summary",
    "Remaining Actions",
    "Constraint File Diff",
    "Waiver File Diff",
    "th { background: #4a4a4a",
    ".before {",
    ".after {",
    "display:flex",
]


def load_counts(path: Path, problems: list[str]) -> dict[str, int]:
    """Return {rule: count} from the Report Summary table of a report_methodology JSON."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{path.name} unreadable: {exc}")
        return {}
    table = data.get("tables", {}).get("Report Summary", {})
    headers = [str(h).strip().lower() for h in table.get("headers", [])]
    counts: dict[str, int] = {}
    for row in table.get("data", []):
        if not isinstance(row, list):
            continue
        mapped = dict(zip(headers, row))
        rule = str(mapped.get("rule", "")).strip()
        try:
            counts[rule] = int(str(mapped.get("count", 0)).strip())
        except ValueError:
            counts[rule] = 0
    return counts


def status_kind(status: str) -> str:
    for kind in STATUSES:
        if status.strip().startswith(kind):
            return kind
    return ""


def diff_counts(path: Path) -> tuple[int, int] | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return added, removed


def need(problems: list[str], cond: bool, message: str) -> None:
    if not cond:
        problems.append(message)


def check_content(root: Path, problems: list[str]) -> dict | None:
    path = root / "tmp" / "report_content.json"
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"tmp/report_content.json unreadable: {exc}")
        return None

    header = content.get("header", {})
    for key in HEADER_KEYS:
        need(problems, bool(str(header.get(key, "")).strip()), f"header.{key} is empty")

    before = load_counts(root / "methodology.json", problems)
    after = load_counts(root / "methodology_after_fix.json", problems)

    summary = content.get("summary", [])
    need(problems, bool(summary), "summary has no rows")
    seen = set()
    for row in summary:
        rid = str(row.get("id", "")).strip()
        seen.add(rid)
        need(problems, bool(str(row.get("severity", "")).strip()), f"summary {rid}: severity is empty")
        need(problems, status_kind(str(row.get("status", ""))) != "", f"summary {rid}: status is not one of {STATUSES}")
        if before:
            need(problems, row.get("count_before") == before.get(rid, 0),
                 f"summary {rid}: count_before {row.get('count_before')} != methodology.json {before.get(rid, 0)}")
        if after:
            need(problems, row.get("count_after") == after.get(rid, 0),
                 f"summary {rid}: count_after {row.get('count_after')} != methodology_after_fix.json {after.get(rid, 0)}")
    for rid in sorted(set(before) | set(after)):
        need(problems, rid in seen, f"summary is missing {rid}, which is in the before or after report")

    total = content.get("total", {})
    need(problems, total.get("count_before") == sum(r.get("count_before", 0) for r in summary),
         "total.count_before does not equal the sum of the summary rows")
    need(problems, total.get("count_after") == sum(r.get("count_after", 0) for r in summary),
         "total.count_after does not equal the sum of the summary rows")
    need(problems, bool(str(total.get("note", "")).strip()), "total.note is empty")

    resolved_ids = {r["id"] for r in summary if status_kind(str(r.get("status", ""))) == "Resolved"}
    waived_ids = {r["id"] for r in summary if status_kind(str(r.get("status", ""))) == "Waived"}
    open_ids = {r["id"] for r in summary if status_kind(str(r.get("status", ""))) in NOT_RESOLVED}

    covered = set()
    for res in content.get("resolutions", []):
        ids = res.get("check_ids", [])
        covered.update(ids)
        label = ",".join(ids) or "?"
        for key in ("instances", "root_cause", "analysis", "mode", "action", "before", "after", "diff_summary"):
            need(problems, bool(res.get(key)), f"resolution {label}: {key} is empty")
        need(problems, res.get("mode") in ("AUTO", "USER"), f"resolution {label}: mode must be AUTO or USER")
        need(problems, "case" in res, f"resolution {label}: case is missing (use null when the reference has a single resolution)")
    for rid in sorted(resolved_ids - covered):
        problems.append(f"{rid} is Resolved in the summary but has no entry in resolutions")

    waiver_ids = set()
    for wv in content.get("waivers", []):
        waiver_ids.add(wv.get("check_id"))
        for key in ("check_id", "justification", "objects", "before", "after"):
            need(problems, bool(wv.get(key)), f"waiver {wv.get('check_id', '?')}: {key} is empty")
    for rid in sorted(waived_ids - waiver_ids):
        problems.append(f"{rid} is Waived in the summary but has no entry in waivers")

    remaining = {r.get("id") for r in content.get("remaining_actions", [])}
    for rid in sorted(open_ids - remaining):
        problems.append(f"{rid} is not resolved but has no entry in remaining_actions")
    for row in content.get("remaining_actions", []):
        need(problems, bool(str(row.get("action", "")).strip()), f"remaining_actions {row.get('id', '?')}: action is empty")

    cd = content.get("constraint_diff", {})
    need(problems, (root / "constraints.xdc").exists(), "constraints.xdc does not exist")
    need(problems, (root / "constraints_after_fix.xdc").exists(), "constraints_after_fix.xdc does not exist")
    counts = diff_counts(root / "tmp" / "constraints.diff")
    need(problems, counts is not None, "tmp/constraints.diff does not exist")
    if counts is not None:
        need(problems, (cd.get("added"), cd.get("removed")) == counts,
             f"constraint_diff added/removed {cd.get('added')}/{cd.get('removed')} != constraints.diff {counts[0]}/{counts[1]}")
    need(problems, bool(str(cd.get("summary", "")).strip()), "constraint_diff.summary is empty")

    wd = content.get("waiver_diff", {})
    need(problems, "created" in wd, "waiver_diff.created is missing")
    after_exists = (root / "waivers_after_fix.rpt").exists()
    need(problems, bool(wd.get("created")) == (bool(content.get("waivers")) or after_exists),
         "waiver_diff.created disagrees with the waivers list and waivers_after_fix.rpt")
    if wd.get("created"):
        need(problems, after_exists, "waivers_after_fix.rpt does not exist")
        need(problems, (root / "waivers.rpt").exists(), "waivers.rpt does not exist")
        counts = diff_counts(root / "tmp" / "waivers.diff")
        need(problems, counts is not None, "tmp/waivers.diff does not exist")
        if counts is not None:
            need(problems, (wd.get("added"), wd.get("removed")) == counts,
                 f"waiver_diff added/removed {wd.get('added')}/{wd.get('removed')} != waivers.diff {counts[0]}/{counts[1]}")
        need(problems, bool(str(wd.get("summary", "")).strip()), "waiver_diff.summary is empty")
    return content


def file_lines(path: Path) -> list[str]:
    try:
        return [l.rstrip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except OSError:
        return []


def check_report(kind: str, root: Path, problems: list[str]) -> None:
    content = check_content(root, problems)
    if content is None:
        return
    report = root / f"methodology_resolution_report.{kind}"
    try:
        text = report.read_text(encoding="utf-8", errors="replace")
    except OSError:
        problems.append(f"{report.name} does not exist")
        return

    if kind == "md":
        for heading in MD_HEADINGS:
            need(problems, heading in text, f"{report.name} is missing the heading '{heading}'")
        haystack = text
        fence_count = text.count("```")
        need(problems, fence_count % 2 == 0, "unbalanced ``` fences in the markdown")
        bad_rows = [l for l in text.splitlines() if l.startswith("|") and "||" in l]
        need(problems, not bad_rows, f"double pipe || in a markdown table row: {bad_rows[:1]}")
    else:
        for marker in HTML_MARKERS:
            need(problems, marker in text, f"{report.name} is missing '{marker}'")
        haystack = html.unescape(re.sub(r"<[^>]+>", "\n", text))
        for row in content.get("summary", []):
            cls = {"Resolved": "resolved", "Waived": "waived", "User Decision Required": "user",
                   "New": "new", "Unresolved": "user", "Not Supported": "notsup"}.get(status_kind(str(row.get("status", ""))), "")
            pattern = rf"<td[^>]*>{re.escape(str(row.get('id')))}</td>[^\n]*<td class=['\"]{cls}['\"]"
            need(problems, bool(cls) and bool(re.search(pattern, text)),
                 f"{report.name}: no status cell with class '{cls}' for {row.get('id')}")

    for row in content.get("summary", []):
        need(problems, str(row.get("id")) in haystack, f"{report.name} does not mention {row.get('id')}")

    pairs = [("constraints.xdc", "constraints_after_fix.xdc")]
    if content.get("waiver_diff", {}).get("created"):
        pairs.append(("waivers.rpt", "waivers_after_fix.rpt"))
    for before_name, after_name in pairs:
        for name in (before_name, after_name):
            missing = [l for l in file_lines(root / name) if l not in haystack]
            need(problems, not missing,
                 f"{report.name} does not contain the whole of {name} ({len(missing)} lines missing, first: {missing[:1]})")
        need(problems, text.find(before_name) != -1 and text.find(after_name) != -1,
             f"{report.name} does not name both {before_name} and {after_name}")
        need(problems, text.find(before_name) < text.find(after_name),
             f"{report.name}: {before_name} must come before {after_name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["content", "md", "html"])
    parser.add_argument("--dir", default="./methodology_results")
    args = parser.parse_args()

    root = Path(args.dir)
    problems: list[str] = []
    if args.stage == "content":
        check_content(root, problems)
    else:
        check_report(args.stage, root, problems)

    if problems:
        print(f"check_report {args.stage}: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"check_report {args.stage}: complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
