#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Record a lab stage's execution time and gate evidence under outputs/.

    stage_report.py start  --stage 1 --out outputs
    ... do the stage ...
    stage_report.py finish --stage 1 --out outputs --audit outputs/audit_run2 \\
        --gate "SPEC:SCHEMA_OK=outputs/validate.log"

start stamps the first substantive operation. finish stamps the final gate, checks
each piece of evidence, and writes <out>/stage<N>/report.json and report.md with
start, end, execution seconds, every gate and the SHA-256 of every evidence file.
The status is done only when every gate is confirmed by its evidence: an --audit
directory needs report.json with status PASS and accepted true; a --gate TOKEN=FILE
needs TOKEN in FILE. Anything unconfirmed makes the stage failed and is listed.
Prints STAGE_REPORT:DONE or STAGE_REPORT:FAILED.
"""

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_audit(directory):
    report = Path(directory) / "report.json"
    if not report.is_file():
        return {"gate": "SPEC: OK", "evidence": str(report), "confirmed": False, "why": "no report.json"}
    data = json.loads(report.read_text(encoding="utf-8"))
    summary = data.get("summary") or {}
    ok = data.get("status") == "PASS" and summary.get("accepted") is True
    return {"gate": "SPEC: OK", "evidence": str(report), "sha256": sha256(report), "confirmed": ok,
            "spec_sha256": data.get("spec_sha256"),
            "why": "" if ok else f"status={data.get('status')} accepted={summary.get('accepted')}"}


def check_gate(spec):
    token, sep, path = spec.partition("=")
    if not sep or not token or not path:
        return {"gate": spec, "confirmed": False, "why": "expected TOKEN=FILE"}
    if not Path(path).is_file():
        return {"gate": token, "evidence": path, "confirmed": False, "why": "evidence file missing"}
    found = token in Path(path).read_text(encoding="utf-8", errors="replace")
    return {"gate": token, "evidence": path, "sha256": sha256(path), "confirmed": found,
            "why": "" if found else "token not in evidence"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("start", "finish"))
    parser.add_argument("--stage", required=True, help="stage number or name, e.g. 1")
    parser.add_argument("--out", required=True, type=Path, help="the run's outputs/ directory")
    parser.add_argument("--audit", action="append", default=[], help="spec audit directory whose report.json must PASS")
    parser.add_argument("--gate", action="append", default=[], help="TOKEN=FILE: TOKEN must appear in FILE")
    parser.add_argument("--status", choices=("done", "failed"), help="force failed with --blocker; done is derived")
    parser.add_argument("--blocker", default="", help="what stopped the stage, when it failed")
    parser.add_argument("--note", default="", help="one line for the report")
    args = parser.parse_args(argv)

    folder = args.out / f"stage{args.stage}"
    folder.mkdir(parents=True, exist_ok=True)
    timing = folder / "timing.json"
    if args.action == "start":
        if timing.exists():
            print(f"STAGE_REPORT_FAIL:stage {args.stage} already started ({timing}); one start per stage")
            return 1
        timing.write_text(json.dumps({"stage": args.stage, "start_utc": now().isoformat()}) + "\n")
        print(f"STAGE_REPORT:STARTED stage={args.stage}")
        return 0

    if not timing.exists():
        print(f"STAGE_REPORT_FAIL:stage {args.stage} has no start; run 'start' at its first substantive operation")
        return 1
    start = datetime.datetime.fromisoformat(json.loads(timing.read_text())["start_utc"])
    end = now()
    gates = [check_audit(d) for d in args.audit] + [check_gate(g) for g in args.gate]
    unconfirmed = [g for g in gates if not g["confirmed"]]
    if not gates:
        unconfirmed = [{"gate": "(none)", "why": "no --audit or --gate evidence given"}]
    status = "failed" if unconfirmed or args.status == "failed" else "done"
    report = {"stage": args.stage, "status": status, "start_utc": start.isoformat(), "end_utc": end.isoformat(),
              "execution_seconds": round((end - start).total_seconds(), 1),
              "timing_scope": "first substantive operation through the final required gate",
              "gates": gates, "blocker": args.blocker, "note": args.note}
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [f"# Stage {args.stage} report", "",
             f"Status: **{status}**. Execution {report['execution_seconds']} s "
             f"({report['start_utc']} to {report['end_utc']}), first substantive operation to final gate.", "",
             "| Gate | Evidence | Confirmed |", "|---|---|---|"]
    lines += [f"| `{g['gate']}` | `{g.get('evidence', '')}` | {'yes' if g['confirmed'] else 'NO: ' + g['why']} |"
              for g in gates]
    if args.blocker:
        lines += ["", f"Blocker: {args.blocker}"]
    if args.note:
        lines += ["", args.note]
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    word = "DONE" if status == "done" else "FAILED"
    detail = "" if status == "done" else " unconfirmed=" + ",".join(g["gate"] for g in unconfirmed)
    print(f"STAGE_REPORT:{word} stage={args.stage} seconds={report['execution_seconds']} "
          f"gates={len(gates)}{detail} report={folder / 'report.md'}")
    return 0 if status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
