#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Capture immutable source snapshots and offline regression measurements."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--test", action="append", default=[])
    parser.add_argument("--load-probe", action="store_true")
    parser.add_argument("--libraries", type=Path)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be positive")
    if not args.test and not args.load_probe:
        parser.error("select --test or --load-probe")
    root = Path(__file__).resolve().parents[4]
    args.out.mkdir(parents=True, exist_ok=False)
    sources = {}
    for directory in (root / ".claude/skills", root / "inputs/rave2_board"):
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix not in {".tcl", ".py", ".md", ".xml", ".json"}:
                continue
            if any(part in {"__pycache__", "cache", ".ipcfg"} for part in path.parts):
                continue
            sources[path.relative_to(root).as_posix()] = path
    for name in ("lab.ipynb", "outputs/design_spec.json", "outputs/assembly_plan.json"):
        if (root / name).is_file():
            sources[name] = root / name
    hashes = {}
    for name, path in sources.items():
        destination = args.out / "sources" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    libraries = (args.libraries or root / ".claude/skills").resolve()
    probe_sources = [libraries / name / "lib" / filename for name, filename in (
        ("vivado-ip-configurator", "ipcfg/ipcfg.tcl"), ("vivado-ipi-assembler", "ipiasm.tcl"))]
    metadata = {
        "label": args.label, "source_sha256": hashes,
        "python": sys.version, "python_executable": sys.executable,
        "platform": platform.platform(), "load_average": os.getloadavg(),
        "scope": "offline Tcl mocks; NOT live Vivado or agent performance",
        "vivado_runtime": None, "agent_model": None,
        "worktree": subprocess.run(["git", "status", "--short"], cwd=root,
                                   text=True, capture_output=True, check=True).stdout,
        "tests": args.test,
        "probe_library_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in probe_sources},
    }
    (args.out / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    results = []
    for trial in range(1, args.trials + 1):
        started = time.perf_counter()
        if args.load_probe:
            script = "\n".join(f"source {{{path}}}" for path in probe_sources * 2)
            script = "if {[catch {\n" + script + "\n} detail]} {puts stderr $detail; exit 1}\n"
            result = subprocess.run(["tclsh"], input=script, text=True, capture_output=True)
        else:
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "-v", *args.test],
                cwd=root / ".claude/skills/vivado-ipi-assembler/tests/unit",
                text=True, capture_output=True,
            )
        elapsed = time.perf_counter() - started
        (args.out / f"trial-{trial}.log").write_text(result.stdout + result.stderr)
        record = {"label": args.label, "trial": trial, "elapsed_s": elapsed,
                  "exit_code": result.returncode,
                  "output_bytes": len((result.stdout + result.stderr).encode()),
                  "live_writes": None, "tool_calls": None, "agent_elapsed_s": None}
        results.append(record)
    (args.out / "results.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in results))
    with (args.out / "results.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    durations = [record["elapsed_s"] for record in results]
    summary = {
        "label": args.label, "trials": args.trials,
        "passed": sum(record["exit_code"] == 0 for record in results),
        "elapsed_median_s": statistics.median(durations),
        "elapsed_min_s": min(durations), "elapsed_max_s": max(durations),
        "output_median_bytes": statistics.median(record["output_bytes"] for record in results),
        "scope": metadata["scope"],
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return int(any(record["exit_code"] for record in results))


if __name__ == "__main__":
    raise SystemExit(main())