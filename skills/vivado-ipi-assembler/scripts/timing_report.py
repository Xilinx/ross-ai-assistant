# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Show where a build's time went, from the timing trace the library records in Vivado.

Usage: timing_report.py TRACE [TRACE ...] [--top N] [--budget BUDGET.json] [--format md|json]

TRACE is a trace log (.jsonl) or a directory of them: the library writes one per Vivado
process to <vivado-working-dir>/.ipiasm/trace/. A TRACE.py.jsonl beside a log (written when
IPIASM_TRACE_PYTHON=1) adds profiled Python functions. BUDGET.json maps stage to minutes.

For the whole build and for each build-flow stage it reports:
  wall       first to last event
  library    time inside ipcfg::/ipiasm:: calls (outermost calls only)
  python     time in Python run by the library (part of library)
  native     Vivado commands the agent ran directly (validate_bd_design, source of its own
             stage scripts, write_hw_platform, ...), outside the library
  outside    wall minus everything above: Vivado idle between commands, i.e. the agent
             reasoning and running other tools, plus quick untraced commands
Busy time is the union of the events' intervals, so nested calls are never counted twice.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

MARKERS = {"start", "stage", "stop"}

# The skill's build flow (SKILL.md "Minimum successful path", ipiasm::api). Raw ipiasm::stage
# labels are mapped onto these by prefix; anything else is shown as given.
STAGES = [
    ("spec", "0 SPEC"), ("plan", "0a PLAN"), ("board", "0b BOARD"), ("cell", "1 CELLS"),
    ("inventory", "1b INVENTORY"), ("clock", "2 CLOCKS"), ("connect", "3 CONNECT"),
    ("external", "3 CONNECT"), ("visibility", "3 CONNECT"), ("qos", "3b QOS"), ("noc", "3b QOS"),
    ("address", "4 ADDRESS"), ("validate", "5 VALIDATE"), ("final", "5 VALIDATE"),
    ("deliver", "6 DELIVER"), ("compile", "7 COMPILE"), ("synth", "7 COMPILE"),
]
PRE = "0/0a SPEC+PLAN (before the first build stage)"


def stage_name(label):
    """The skill's stage name for a raw ipiasm::stage label."""
    raw = str(label).lower()
    if raw in {"pre", "init", ""}:
        return PRE
    for prefix, name in STAGES:
        if raw.startswith(prefix):
            return name
    return f"other: {label}"


def load(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_all(paths):
    """Records and profiles from trace files and directories, in time order, with stage
    labels mapped to the skill's stage names."""
    records, profiles = [], []
    for path in paths:
        files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        for file in files:
            if file.name.endswith(".py.jsonl"):
                profiles += load(file)
                continue
            records += load(file)
            sibling = file.with_suffix(".py.jsonl")
            if not path.is_dir() and sibling.is_file():
                profiles += load(sibling)
    records.sort(key=lambda r: r["ts"])
    for record in records:
        record["stage"] = stage_name(record.get("stage", "pre"))
    return records, profiles


def stage_windows(records):
    """[(stage, start_ms, end_ms)] from the marker records, in order; the last stage runs
    to the last event."""
    marks = [r for r in records if r.get("kind") in MARKERS]
    windows = [(a["stage"], a["ts"], b["ts"]) for a, b in zip(marks, marks[1:])]
    if marks and records and records[-1]["ts"] > marks[-1]["ts"]:
        windows.append((marks[-1]["stage"], marks[-1]["ts"], records[-1]["ts"]))
    return windows


def stage_at(windows, ts):
    for stage, start, end in windows:
        if start <= ts <= end:
            return stage
    return windows[-1][0] if windows else "?"


def _interval(record):
    """(start_ms, end_ms) of an event: ts is written when the call returns."""
    return record["ts"] - record["incl_us"] / 1000.0, float(record["ts"])


def _union_ms(intervals, low=None, high=None):
    total, current = 0.0, None
    for start, end in sorted(intervals):
        if low is not None:
            start, end = max(start, low), min(end, high)
            if end <= start:
                continue
        if current is None or start > current[1]:
            if current:
                total += current[1] - current[0]
            current = [start, end]
        else:
            current[1] = max(current[1], end)
    if current:
        total += current[1] - current[0]
    return total


def _events(records):
    library = [r for r in records if r.get("kind") in {"tcl", "python"} and r.get("depth", 0) == 0]
    native = [r for r in records if r.get("kind") == "native"]
    python = [r for r in records if r.get("kind") == "python"]
    return library, native, python


def summarize(records, low=None, high=None):
    """Wall, library, python, native, busy and outside milliseconds for a time window."""
    if not records:
        return {"wall_ms": 0, "library_ms": 0, "python_ms": 0, "native_ms": 0, "busy_ms": 0, "outside_ms": 0}
    library, native, python = _events(records)
    if low is None:
        low = min([records[0]["ts"]] + [_interval(r)[0] for r in library + native])
        high = records[-1]["ts"]
    lib_ms = _union_ms([_interval(r) for r in library], low, high)
    py_ms = _union_ms([_interval(r) for r in python], low, high)
    native_ms = _union_ms([_interval(r) for r in native], low, high)
    busy_ms = _union_ms([_interval(r) for r in library + native], low, high)
    wall_ms = max(0.0, high - low)
    return {"wall_ms": round(wall_ms), "library_ms": round(lib_ms), "python_ms": round(py_ms),
            "native_ms": round(native_ms), "busy_ms": round(busy_ms),
            "outside_ms": round(max(0.0, wall_ms - busy_ms))}


def aggregate(records, profiles=()):
    windows = stage_windows(records)
    stages = {}

    def bucket(name):
        return stages.setdefault(name, {"wall_ms": 0, "busy_us": 0, "summary": None, "calls": defaultdict(
            lambda: {"calls": 0, "incl_us": 0, "excl_us": 0, "max_us": 0}), "python": defaultdict(
            lambda: {"calls": 0, "excl_us": 0, "incl_us": 0})})

    for stage, start, end in windows:
        bucket(stage)["wall_ms"] += end - start
    for record in records:
        if record.get("kind") in MARKERS:
            continue
        name = record["name"]
        if record.get("kind") == "native":
            name = f"native {name}" + (f" {record['script']}" if record.get("script") else "")
        row = bucket(record["stage"])["calls"][name]
        row["calls"] += 1
        row["incl_us"] += record["incl_us"]
        row["excl_us"] += record["excl_us"]
        row["max_us"] = max(row["max_us"], record["incl_us"])
        bucket(record["stage"])["busy_us"] += record["excl_us"]
    for profile in profiles:
        target = bucket(stage_at(windows, profile["ts"]))["python"]
        for fn in profile["top"]:
            row = target[f"{profile['script']} {fn['fn']}"]
            row["calls"] += fn["calls"]
            row["excl_us"] += fn["excl_us"]
            row["incl_us"] += fn["incl_us"]
    totals = defaultdict(lambda: {"wall_ms": 0, "library_ms": 0, "python_ms": 0, "native_ms": 0,
                                  "busy_ms": 0, "outside_ms": 0})
    for stage, start, end in windows:
        for key, value in summarize(records, start, end).items():
            totals[stage][key] += value
    for stage in stages:
        stages[stage]["summary"] = dict(totals[stage]) if stage in totals else None
    order = list(dict.fromkeys(stage for stage, _, _ in windows))
    order += [stage for stage in stages if stage not in order]
    return {stage: stages[stage] for stage in order}


def _min(ms):
    return f"{ms / 60000:.1f}"


def render_markdown(agg, top, budget=None, overall=None):
    lines = []
    if overall:
        lines += ["# Build timing", "",
                  "| | min | share |", "|---|---:|---:|"]
        wall = overall["wall_ms"] or 1
        for key, label in (("wall_ms", "wall"), ("library_ms", "library (ipcfg/ipiasm)"),
                           ("python_ms", "  of which Python"), ("native_ms", "native Vivado commands (agent)"),
                           ("outside_ms", "outside Vivado (agent, other tools)")):
            lines.append(f"| {label} | {_min(overall[key])} | {100 * overall[key] / wall:.0f}% |")
        lines += ["", "| stage | wall min | library | native | outside |", "|---|---:|---:|---:|---:|"]
        for stage, data in agg.items():
            s = data["summary"]
            if s:
                lines.append(f"| {stage} | {_min(s['wall_ms'])} | {_min(s['library_ms'])} | "
                             f"{_min(s['native_ms'])} | {_min(s['outside_ms'])} |")
        lines.append("")
    for stage, data in agg.items():
        wall = data["wall_ms"] / 60000
        s = data["summary"] or {}
        head = f"## {stage}   wall {wall:.1f} min"
        if s:
            head += (f"   library {_min(s['library_ms'])}   native {_min(s['native_ms'])}"
                     f"   outside {_min(s['outside_ms'])}")
        if budget and stage in budget:
            head += f"   budget {budget[stage]} min   delta {wall - budget[stage]:+.1f}"
        lines += [head, "", "| proc / command | calls | excl s | incl s | max ms |", "|---|---:|---:|---:|---:|"]
        ranked = sorted(data["calls"].items(), key=lambda item: item[1]["excl_us"], reverse=True)
        for name, row in ranked[:top]:
            lines.append(f"| `{name}` | {row['calls']} | {row['excl_us'] / 1e6:.2f} | "
                         f"{row['incl_us'] / 1e6:.2f} | {row['max_us'] / 1e3:.0f} |")
        if data["python"]:
            lines += ["", "| python function (profiled) | calls | excl s | incl s |", "|---|---:|---:|---:|"]
            ranked = sorted(data["python"].items(), key=lambda item: item[1]["excl_us"], reverse=True)
            for name, row in ranked[:top]:
                lines.append(f"| `{name}` | {row['calls']} | {row['excl_us'] / 1e6:.2f} | {row['incl_us'] / 1e6:.2f} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace", type=Path, nargs="+", help="trace .jsonl files or directories of them")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--format", choices=("md", "json"), default="md")
    args = parser.parse_args(argv)
    records, profiles = load_all(args.trace)
    if not records:
        print("TIMING_FAIL:no trace records in " + " ".join(map(str, args.trace)))
        return 1
    agg = aggregate(records, profiles)
    overall = summarize(records)
    if args.format == "json":
        print(json.dumps({"overall": overall, "stages": agg}, indent=2))
    else:
        budget = json.loads(args.budget.read_text()) if args.budget else None
        print(render_markdown(agg, args.top, budget, overall))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
