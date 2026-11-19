#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
summary_report.py -- the final verdict a human reads.

Standalone and stdlib-only: no third-party packages, no network access.

Reads the JSON produced by the other scripts and renders the report a user
actually reads. Answers three questions:
  1. Did timing close, and by what margin (WNS/WHS/TNS)?
  2. If it closed -- which run, and which technique closed it?
  3. If it didn't -- what was tried, and which problem categories were detected?

The overall winner and the run ranking use decision_engine.py's own rules
(hold-met pool first, then WNS, then TNS), so the report can never disagree
with the stage verdicts.

Usage:
    summary_report.py --design my_design \
        --stage1-runs stage1_runs.json --stage1-best stage1_best.json \
        [--stage2-runs stage2_runs.json] [--stage2-best stage2_best.json] \
        [--stage3-runs stage3_runs.json] [--stage3-best stage3_best.json] \
        [--classification classification.json] [--plan stage2_plan.json] \
        [--state state.json] [--verification verification.json] [--json]

Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from decision_engine import (floor_verdict, pick_best_run, rank_runs, timing_met,  # noqa: E402
                             load_config, load_selection, why_not)
from report_parser import parse_timing_summary, report_paths  # noqa: E402

WIDTH = 78
RULE = "=" * WIDTH


def load(path):
    """Load a JSON file, returning None when the path is absent."""
    if not path:
        return None
    if not os.path.isfile(path):
        print("WARNING: no such file, ignoring: %s" % path, file=sys.stderr)
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def fmt_ns(value):
    """Format a slack value in ns with an explicit sign, or n/a."""
    if value is None:
        return "n/a"
    return "%+.3f ns" % value


def technique_for(run_id, runs, plan):
    """
    Best-effort human label for what a run actually did: prefer the methodology
    recorded on the parsed run, fall back to the planner's record.
    """
    for r in runs or []:
        if r.get("run_id") == run_id and r.get("methodology"):
            return r["methodology"]
    if plan:
        for r in (plan.get("runs", []) + plan.get("fixed_runs", [])):
            if r.get("run_name") == run_id or ("S2_" + r.get("run_name", "")) == run_id:
                return r.get("methodology", "")
    return ""


def _as_candidate(best, stage):
    """A stage's decision_engine output, shaped like a run for pick_best_run."""
    return {"run_id": best.get("best_run_id", ""), "wns": best.get("wns"),
            "whs": best.get("whs"), "tns": best.get("tns"),
            "best_run_dir": best.get("best_run_dir", ""), "stage": stage,
            "num_failing_setup": best.get("num_failing_setup"), "rqa_score": best.get("rqa_score")}


def llm_usage(output_dir):
    """
    The model calls' tokens and cost, from <output_dir>/token_usage.jsonl (the
    engine's headless judge writes one line per call; see the engine's
    token_usage.py), and the decisions answered without such a call, from
    decisions/*.json. None when there is neither.
    """
    kinds = ("input", "output", "cache_read", "cache_write")
    t = dict.fromkeys(kinds, 0)
    t.update({"calls": 0, "calls_with_usage": 0, "cost_usd": None})
    bases = set()
    try:
        with open(os.path.join(output_dir, "token_usage.jsonl")) as fh:
            lines = fh.readlines()
    except (IOError, OSError):
        lines = []
    for line in lines:
        try:
            call = json.loads(line)
        except ValueError:
            continue
        t["calls"] += 1
        usage = (call or {}).get("usage") or {}
        if not usage:
            continue
        t["calls_with_usage"] += 1
        for k in kinds:
            t[k] += int(usage.get(k) or 0)
        if usage.get("cost_usd") is not None:
            t["cost_usd"] = round((t["cost_usd"] or 0.0) + float(usage["cost_usd"]), 6)
        if usage.get("cost_basis"):
            bases.add(str(usage["cost_basis"]))
    answered = {}
    for path in glob.glob(os.path.join(output_dir, "decisions", "*.json")):
        rec = load(path) or {}
        who = rec.get("answered_by")
        if who:
            answered[who] = answered.get(who, 0) + 1
    if not t["calls"] and not answered:
        return None
    t["cost_basis"] = ",".join(sorted(bases)) or None
    t["decisions_by_answerer"] = answered
    return t


# The winner's timing after each step (upstream PR #742's per-stage table).
STAGE_ROWS = (("Place", "postplace"), ("Place phys_opt", "postplace_physopt"),
              ("Route", "postroute"), ("Route phys_opt", "postroute_physopt"))


def stage_timing(run_dir):
    """[{stage, label, wns, tns, whs, ths}] for each step whose timing summary exists."""
    rows = []
    for label, stage in STAGE_ROWS:
        path = report_paths(run_dir, stage)["timing_summary"]
        if os.path.isfile(path):
            t = parse_timing_summary(path)
            rows.append(dict(stage=stage, label=label,
                             **{k: t.get(k) for k in ("wns", "tns", "whs", "ths")}))
    return rows


ERROR_LINE = re.compile(r"^ERROR:.*$", re.MULTILINE)
LOG_CHUNK = 1024 * 1024


def vivado_errors(run_dir, max_lines=3):
    """
    The ERROR: lines of a run's vivado.log (upstream PR #742): the earliest
    (usually the cause) and the last (what stopped the run), at most max_lines.
    Only the log's first and last MB are read; it can be tens of MB. A session
    run's log is .vivado-ai/vivado_*.log.
    """
    rd = run_dir or ""
    logs = [os.path.join(rd, n) for n in ("vivado.log", "vivado_console.log")]
    logs += sorted(glob.glob(os.path.join(rd, ".vivado-ai", "vivado*.log")))
    for path in logs:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                head = fh.read(LOG_CHUNK).decode("utf-8", "replace")
                fh.seek(0, 2)
                size = fh.tell()
                tail = ""
                if size > LOG_CHUNK:
                    fh.seek(max(LOG_CHUNK, size - LOG_CHUNK))
                    tail = fh.read(LOG_CHUNK).decode("utf-8", "replace")
        except (IOError, OSError):
            continue
        found = []
        for chunk in (head, tail):
            for m in ERROR_LINE.finditer(chunk):
                line = m.group(0).strip()
                if line not in found:
                    found.append(line)
        if not found:
            continue
        if len(found) > max_lines:
            found = found[:max_lines - 1] + [found[-1]]
        return found
    return []


def _tok(n):
    n = int(n or 0)
    return "%.1fM" % (n / 1e6) if n >= 1000000 else "%.1fk" % (n / 1e3) if n >= 1000 else str(n)


def build(args):
    stages = []
    for label, runs_path, best_path in (
            ("Stage 1", args.stage1_runs, args.stage1_best),
            ("Stage 2", args.stage2_runs, args.stage2_best),
            ("Stage 3", args.stage3_runs, args.stage3_best),
            ("Planner", args.planner_runs, args.planner_best)):
        stages.append((label, load(runs_path) or [], load(best_path)))
    classification = load(args.classification)
    plan = load(args.plan)
    state = load(args.state) or {}
    verification = load(args.verification)

    all_runs = []
    for _, runs, _ in stages:
        all_runs.extend(runs)

    candidates = [_as_candidate(b, label) for label, _, b in stages
                  if b and b.get("wns") is not None]
    selection = load_selection(load_config(args.config))
    overall = pick_best_run(candidates, selection=selection) or None
    # Every run, not only each stage's pick: under closability a stage's own
    # pick may not be its best-WNS run.
    by_wns = pick_best_run([dict(r, stage=label) for label, runs, _ in stages
                            for r in runs]) or None

    lines = [RULE, "TIMING CLOSURE SUMMARY -- %s" % args.design, RULE]
    for label, runs, best in stages:
        if runs or best:
            lines.append("%s runs completed : %d" % (
                label, len([r for r in runs if r.get("wns") is not None])))
    lines.append("Total runs             : %d" % len(all_runs))
    lines.append("")

    is_closed = timing_met(overall) if overall else False
    run_id = (overall or {}).get("run_id", "?")
    technique = technique_for(run_id, all_runs, plan)

    if is_closed:
        lines.append("VERDICT: TIMING CLOSED")
        lines.append("  Winning run      : %s (%s)" % (run_id, overall["stage"]))
        if technique:
            lines.append("  What closed it   : %s" % technique)
        lines.append("  WNS (postroute)  : %s" % fmt_ns(overall.get("wns")))
        lines.append("  WHS (postroute)  : %s" % fmt_ns(overall.get("whs")))
        lines.append("  TNS (postroute)  : %s" % fmt_ns(overall.get("tns")))
    else:
        lines.append("VERDICT: TIMING NOT CLOSED")
        if overall:
            lines.append("  Closest run      : %s (%s)" % (run_id, overall["stage"]))
            if technique:
                lines.append("  Closest attempt  : %s" % technique)
            lines.append("  Best WNS so far  : %s" % fmt_ns(overall.get("wns")))
            lines.append("  Best WHS so far  : %s" % fmt_ns(overall.get("whs")))
            lines.append("  Best TNS so far  : %s" % fmt_ns(overall.get("tns")))
        else:
            lines.append("  No run produced a parsable timing summary.")
    if selection["mode"] != "wns" and overall:
        lines.append("  Chosen by        : selection.mode %s" % selection["mode"])
        if by_wns and by_wns.get("run_id") != overall.get("run_id"):
            lines.append("  Best WNS instead : %s (%s) WNS %s -- %s" % (
                by_wns["run_id"], by_wns["stage"], fmt_ns(by_wns.get("wns")),
                why_not(overall, by_wns, selection)))

    # Did each later stage earn its runtime? decision_engine's floor already
    # applies stage2_min_improvement_ps, so reuse its verdict.
    floors = [(label, (best or {}).get("floor")) for label, _, best in stages[1:]]
    if any(f for _, f in floors):
        lines.append("")
    for label, floor in floors:
        if floor:
            lines.append("%s vs previous best : %+.3f ns (%s; threshold %.3f ns)" % (
                label, floor["delta_ns"], floor_verdict(floor),
                floor.get("min_improvement_ns", 0.0)))

    by_stage = stage_timing(overall["best_run_dir"]) if overall and overall.get(
        "best_run_dir") else []
    if by_stage:
        lines.append("")
        lines.append("%s after each step:" % run_id)
        lines.append("  %-16s %11s %13s %11s %13s" % ("Step", "WNS", "TNS", "WHS", "THS"))
        for r in by_stage:
            lines.append("  %-16s %11s %13s %11s %13s" % (
                r["label"], fmt_ns(r["wns"]), fmt_ns(r["tns"]), fmt_ns(r["whs"]),
                fmt_ns(r["ths"])))

    ranked = rank_runs(all_runs, selection)
    if ranked:
        lines.append("")
        lines.append("All runs, best first (%s):" % (
            "hold-met, then WNS, then TNS" if selection["mode"] == "wns" else
            "hold-met, then WNS band, QoR assessment, TNS band, WNS, TNS"))
        for r in ranked:
            label = technique_for(r.get("run_id"), all_runs, plan) or r.get(
                "flow_name", "")
            hold = "" if (r.get("whs") is not None and r["whs"] >= 0) else " [hold]"
            lines.append("  %-9s  %-24s %s%s" % (
                fmt_ns(r.get("wns")), r.get("run_id", "?"), label[:34], hold))

    incomplete = [r.get("run_id", "?") for r in all_runs if r.get("wns") is None]
    run_errors = {}
    if incomplete:
        lines.append("")
        lines.append("Runs with no parsable timing summary (crashed, killed, or "
                     "still running):")
        for r in all_runs:
            if r.get("wns") is not None:
                continue
            lines.append("  - %s" % r.get("run_id", "?"))
            errs = vivado_errors(r.get("run_dir"))
            if errs:
                run_errors[r.get("run_id", "?")] = errs
            for e in errs:
                lines.append("      %s" % e[:WIDTH - 6])

    removed = state.get("checkpoints_pruned")
    if removed and removed.get("count"):
        lines.append("")
        lines.append("Checkpoints: kept the winner's (%s); removed %d from runs that could "
                     "no longer win (%.1f GB, output.keep_dcps: winner)."
                     % (removed.get("kept"), removed.get("count", 0),
                        removed.get("bytes", 0) / 1e9))

    cancelled = state.get("cancelled_runs") or []
    if cancelled:
        lines.append("")
        lines.append("Runs cancelled after %s met timing (guardrails.on_timing_closed):"
                     % (state.get("closed_by") or {}).get("run_id", "a run"))
        for c in cancelled:
            lines.append("  - %s" % c.get("run_id"))

    not_started = state.get("stage1_not_started") or []
    if not_started:
        lines.append("")
        lines.append("Stage 1 strategies not launched (stopped after first close):")
        for rid in not_started:
            lines.append("  - %s" % rid)

    skipped = state.get("stage1_skipped") or []
    if skipped:
        lines.append("")
        lines.append("Stage 1 strategies not run (no placer sub-directives on %s; "
                     "they would place as their twin):" % skipped[0].get("architecture", "?"))
        for s in skipped:
            lines.append("  - %s = %s" % (s.get("run_id", "?"), s.get("same_as", "?")))

    duplicates = state.get("stage1_duplicates") or []
    if duplicates:
        lines.append("")
        lines.append("Stage 1 strategies stopped after placement (identical placement to "
                     "an earlier strategy):")
        for d in duplicates:
            lines.append("  - %s = %s" % (d.get("run_id", "?"), d.get("duplicate_of", "?")))

    if not is_closed and classification:
        detected = classification.get("detected_problems", [])
        if detected:
            lines.append("")
            lines.append("Problem categories detected by the checkers:")
            for c in detected:
                lines.append("  - %s (severity: %s, confidence: %.2f)" % (
                    c.get("category", "?"), c.get("severity", "?"),
                    c.get("confidence", 0.0)))
                for chunk in _wrap(c.get("summary", ""), WIDTH - 6):
                    lines.append("      %s" % chunk)

    skipped = []
    if plan and plan.get("skipped"):
        for s in plan["skipped"]:
            who = "/".join(x for x in (s.get("category"), s.get("sub_problem")) if x)
            skipped.append((who, s.get("reason", "")))
    for key in ("stage2_skipped", "stage3_skipped", "planner_skipped"):
        for s in state.get(key) or []:
            skipped.append((s.get("run_name", "?"), s.get("reason", "")))
    if skipped:
        lines.append("")
        lines.append("Candidates not launched:")
        for who, why in skipped:
            lines.append("  - %s" % who)
            for chunk in _wrap(why, WIDTH - 8):
                lines.append("      %s" % chunk)

    retries = state.get("retries") or []
    if retries:
        lines.append("")
        lines.append("Retried runs (infrastructure failures, one retry each):")
        for r in retries:
            lines.append("  - %s: %s" % (r.get("run_dir", "?"), r.get("failure_class", "?")))

    if verification is not None:
        lines.append("")
        if verification.get("verified"):
            lines.append("Verification: PASSED (winner re-parsed from its own reports)")
        else:
            lines.append("Verification: FAILED")
            for issue in verification.get("verify_issues", []):
                lines.append("  - %s" % issue)
        for warning in verification.get("warnings", []):
            lines.append("  warning: %s" % warning)

    baseline = state.get("stage1_baseline")
    if baseline and baseline.get("wns") is not None:
        lines.append("")
        lines.append("Plain-Vivado reference (%s, not ranked): WNS %s  WHS %s  TNS %s" % (
            baseline.get("run_id"), fmt_ns(baseline.get("wns")),
            fmt_ns(baseline.get("whs")), fmt_ns(baseline.get("tns"))))

    pb = state.get("path_b") or {}
    pb_flows = dict((f, v) for f, v in (pb.get("flows") or {}).items()
                    if v.get("state") not in ("no_partition",))
    if pb_flows or pb.get("s2r2"):
        lines.append("")
        lines.append("Path B (R2: Stage 1 flows rerun with their own SLR pblock XDC):")
        for flow, v in sorted(pb_flows.items()):
            lines.append("  %-16s %-13s %s" % (flow, v.get("state"), v.get("why", "")[:44]))
        s2r2 = pb.get("s2r2") or {}
        if s2r2:
            lines.append("  Stage 2 on R2 (%s): %s" % (
                s2r2.get("from"), ", ".join(s2r2.get("rows") or []) or s2r2.get("why", "")))

    checks = state.get("design_checks") or {}
    meth = checks.get("methodology")
    if meth or checks.get("check_timing"):
        lines.append("")
        lines.append("Design checks (input design, before implementation):")
        if meth:
            lines.append("  report_methodology: %s" % (", ".join(
                "%s %d" % kv for kv in sorted(meth.get("by_severity", {}).items()))
                or "no findings"))
            for r in meth.get("top_rules", [])[:5]:
                lines.append("    %-10s %-16s x%-5d %s" % (
                    r["rule"], r["severity"], r["count"], r["description"][:40]))
            lines.append("    full report: %s" % meth.get("report"))
        if checks.get("check_timing"):
            lines.append("  check_timing: %s" % ", ".join(
                "%s %d" % kv for kv in sorted(checks["check_timing"].items())))

    run_dir = os.path.dirname(os.path.abspath(args.state)) if args.state else None
    # The run's live page, when the engine started one (tc web writes <output_dir>/web.json).
    web_file = os.path.join(run_dir, "web.json") if run_dir else ""
    web_url = (load(web_file) or {}).get("web_url") if os.path.isfile(web_file) else None
    if web_url:
        lines.append("")
        lines.append("Live page (open in a browser): %s" % web_url)

    usage = llm_usage(run_dir) if run_dir else None
    if usage:
        lines.append("")
        lines.append("Model calls (the engine's headless judge):")
        if usage["calls"]:
            cost = "-" if usage["cost_usd"] is None else "$%.2f" % usage["cost_usd"]
            if usage["cost_basis"] == "unknown" and usage["cost_usd"] is not None:
                cost += " (CLI estimate, not an invoice)"
            lines.append("  %d call(s), %s input / %s cache-read / %s cache-write / %s output "
                         "tokens, %s" % (usage["calls"], _tok(usage["input"]),
                                         _tok(usage["cache_read"]), _tok(usage["cache_write"]),
                                         _tok(usage["output"]), cost))
            if usage["calls"] > usage["calls_with_usage"]:
                lines.append("  %d call(s) reported no usage (another CLI, or a timeout)"
                             % (usage["calls"] - usage["calls_with_usage"]))
        else:
            lines.append("  none")
        if usage["decisions_by_answerer"]:
            lines.append("  decisions answered by: %s" % ", ".join(
                "%s %d" % kv for kv in sorted(usage["decisions_by_answerer"].items())))
            if usage["decisions_by_answerer"].get("ide_agent"):
                lines.append("  (an editor agent's tokens are its own client's and are not "
                             "counted here)")

    if not is_closed:
        lines.append("")
        lines.append("NEXT STEP:")
        lines.append("  Automated closure stops here. The closest result and the")
        lines.append("  detected categories above are the handoff point for manual work.")

    if overall and overall.get("best_run_dir"):
        lines.append("")
        lines.append("Winning run directory : %s" % overall["best_run_dir"])

    lines.append(RULE)

    payload = {
        "design": args.design,
        "verdict": "CLOSED" if is_closed else "NOT_CLOSED",
        "best_run_id": run_id,
        "best_stage": (overall or {}).get("stage", ""),
        "technique": technique,
        "wns": (overall or {}).get("wns"),
        "whs": (overall or {}).get("whs"),
        "tns": (overall or {}).get("tns"),
        "selection_mode": selection["mode"],
        "best_run_dir": (overall or {}).get("best_run_dir", ""),
        "stage1_runs": len(stages[0][1]),
        "stage2_runs": len(stages[1][1]),
        "stage3_runs": len(stages[2][1]),
        "planner_runs": len(stages[3][1]),
        "incomplete_runs": incomplete,
        "incomplete_run_errors": run_errors,
        "best_run_by_stage": by_stage,
        "not_started_runs": not_started,
        "cancelled_runs": [c.get("run_id") for c in state.get("cancelled_runs") or []],
        "closed_by": (state.get("closed_by") or {}).get("run_id"),
        "duplicate_runs": duplicates,
        "retries": retries,
        "skipped": [{"name": w, "reason": r} for w, r in skipped],
        "detected_categories": [
            c.get("category") for c in (classification or {}).get(
                "detected_problems", [])],
        "stage3_required": not is_closed,
    }
    if state.get("stage1_baseline"):
        payload["baseline"] = state["stage1_baseline"]
    if state.get("design_checks"):
        payload["design_checks"] = state["design_checks"]
    if state.get("path_b"):
        payload["path_b"] = {"flows": state["path_b"].get("flows"),
                             "s2r2": state["path_b"].get("s2r2")}
    if usage:
        payload["token_usage"] = usage
    if web_url:
        payload["web_url"] = web_url
    if verification is not None:
        payload["verified"] = bool(verification.get("verified"))
        payload["verify_issues"] = verification.get("verify_issues", [])

    return "\n".join(lines), payload


def _wrap(text, width):
    """Minimal greedy wrapper (textwrap would do, but keep the deps at zero)."""
    words, out, cur = (text or "").split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        out.append(cur)
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Render the final timing-closure verdict.")
    parser.add_argument("--design", default="design", help="Design name for the header.")
    parser.add_argument("--stage1-runs", help="report_parser.py array for Stage 1.")
    parser.add_argument("--stage1-best", help="decision_engine.py output for Stage 1.")
    parser.add_argument("--stage2-runs", help="report_parser.py array for Stage 2.")
    parser.add_argument("--stage2-best", help="decision_engine.py output for Stage 2.")
    parser.add_argument("--stage3-runs", help="report_parser.py array for Stage 3.")
    parser.add_argument("--stage3-best", help="decision_engine.py output for Stage 3.")
    parser.add_argument("--planner-runs", help="report_parser.py array for planner runs.")
    parser.add_argument("--planner-best", help="decision_engine.py output for planner runs.")
    parser.add_argument("--classification", help="design_analyzer.py output.")
    parser.add_argument("--plan", help="skill_resolver.py output.")
    parser.add_argument("--state", help="orchestrate.py state.json (skips, retries).")
    parser.add_argument("--verification", help="orchestrate.py finalize verification JSON.")
    parser.add_argument("--config", help="agent_config.yaml, for selection.mode.")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON instead of the text report.")
    parser.add_argument("-o", "--output", help="Write here instead of stdout.")
    args = parser.parse_args()

    text, payload = build(args)
    out = json.dumps(payload, indent=2) if args.json else text

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(out + "\n")
        print("Wrote %s (%s)" % (args.output, payload["verdict"]), file=sys.stderr)
    else:
        print(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
