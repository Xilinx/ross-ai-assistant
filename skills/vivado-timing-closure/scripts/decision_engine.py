#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
decision_engine.py -- "Compare and pick Best WNS" for a set of parsed runs.

Standalone and stdlib-only: no third-party packages, no network access.
Operates on a plain JSON array of run records (the output of report_parser.py).

Selection rules (selection.mode wns, the default; closability is described at
closability_key below):
  * Only runs with a parsed WNS are candidates.
  * Prefer runs that meet hold (WHS >= 0; an unparsed WHS does NOT count as
    met). Fall back to the full pool only if NO run meets hold.
  * Within the chosen pool, best (highest) WNS wins.
  * Tie-break on TNS (less negative is better).

"Timing closed" is WNS >= 0 AND WHS >= 0 -- one definition, used everywhere.

Usage:
    decision_engine.py --runs stage1_runs.json --stage S1
    decision_engine.py --runs stage2_runs.json --stage S2 --floor stage1_best.json
    decision_engine.py --runs stage1_runs.json --config config/agent_config.yaml

Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yaml_lite import load_yaml  # noqa: E402


# Defaults mirror config/agent_config.yaml so the script works without a config.
DEFAULT_OC_THRESHOLD_PS = -200
DEFAULT_MP_THRESHOLD_PS = -200
DEFAULT_MIN_IMPROVEMENT_PS = 5
# ...or, at equal WNS (within 1 ps), cut TNS by at least this much.
DEFAULT_MIN_TNS_IMPROVEMENT_PCT = 20.0


def timing_met(run):
    """Timing is closed when both setup and hold are met."""
    wns = run.get("wns")
    whs = run.get("whs")
    if wns is None or whs is None:
        return False
    return wns >= 0 and whs >= 0


def hold_met(run):
    """WHS parsed and non-negative -- the same hold rule timing_met() uses."""
    whs = run.get("whs")
    return whs is not None and whs >= 0


# ---------------------------------------------------------------------------
# selection.mode closability (off by default: selection.mode wns)
# ---------------------------------------------------------------------------
# WNS alone ranks two runs 5 ps apart as decisively as two runs 500 ps apart.
# closability reads WNS and TNS together the way AMD's methodology does:
#   * WP480's quality bands -- WNS +-100 / -300 / -600 ps, TNS -10 / -100 /
#     -1000 ns -- so a run 20 ps better on WNS but with ten times the TNS no
#     longer wins;
#   * report_qor_assessment as a gate inside a WNS band, not a ranker: IDR takes
#     a run into Last Mile only at RQA >= 3 (UG906), and runs of one design mostly
#     share a score. Below the WNS band, so RQA 3 at -0.700 never beats RQA 2 at
#     -0.050; on our suite runs every 2-vs-3 split was the congestion score;
#   * then WNS, TNS and failing endpoints inside the bands.
# The research behind it and what is still unproven: LEARNINGS.md 2026-10-12.
SELECTION_MODES = ("wns", "closability")
DEFAULT_SELECTION = {
    "mode": "wns",
    "min_rqa": 3,
    "wns_bands_ps": [-100, -300, -600],
    "tns_bands_ns": [-10, -100, -1000],
}


def load_selection(cfg):
    """The `selection` section of an agent_config dict, over the defaults."""
    sel = dict(DEFAULT_SELECTION)
    for key, val in ((cfg or {}).get("selection") or {}).items():
        if val is not None:
            sel[key] = val
    sel["mode"] = str(sel["mode"]).strip().lower()
    if sel["mode"] not in SELECTION_MODES:
        sel["mode"] = "wns"
    return sel


def _band(value, edges):
    """0 for value >= edges[0], 1 for >= edges[1], ... len(edges) below the last."""
    for i, edge in enumerate(edges):
        if value >= edge:
            return i
    return len(edges)


def closability_key(run, sel=None):
    """Sort key, best first, for selection.mode closability."""
    sel = sel or DEFAULT_SELECTION
    tns = run.get("tns")
    tns = float('-inf') if tns is None else tns
    rqa = run.get("rqa_score")
    # An unknown score is not held against a run: older runs have no report.
    below_rqa = rqa is not None and rqa < int(sel.get("min_rqa", 3))
    fails = run.get("num_failing_setup")
    return (0 if hold_met(run) else 1,
            0 if timing_met(run) else 1,
            _band(run["wns"] * 1000.0, sel["wns_bands_ps"]),
            1 if below_rqa else 0,
            _band(tns, sel["tns_bands_ns"]),
            -run["wns"], -tns,
            float('inf') if fails is None else fails)


def _band_name(value, edges, unit):
    i = _band(value, edges)
    if i == 0:
        return ">= %g %s" % (edges[0], unit)
    if i == len(edges):
        return "< %g %s" % (edges[-1], unit)
    return "%g..%g %s" % (edges[i - 1], edges[i], unit)


def why_not(winner, other, sel=None):
    """One line: why closability ranked `winner` above `other`."""
    sel = sel or DEFAULT_SELECTION
    a, b = closability_key(winner, sel), closability_key(other, sel)
    wid, oid = winner.get("run_id", "?"), other.get("run_id", "?")
    if a[0] != b[0]:
        return "%s meets hold, %s does not" % (wid, oid)
    if a[1] != b[1]:
        return "%s closed timing" % wid
    if a[2] != b[2]:
        return "%s is in a better WNS band (%s vs %s)" % (
            wid, _band_name(winner["wns"] * 1000.0, sel["wns_bands_ps"], "ps"),
            _band_name(other["wns"] * 1000.0, sel["wns_bands_ps"], "ps"))
    if a[3] != b[3]:
        return "same WNS band, but %s has QoR assessment %s and %s only %s (gate %s)" % (
            wid, winner.get("rqa_score", "?"), oid, other.get("rqa_score"), sel.get("min_rqa", 3))
    if a[4] != b[4]:
        return ("same WNS band, but %s's TNS %.1f ns is in a better band than %s's %.1f ns (%s vs %s)"
                % (wid, winner.get("tns") or 0.0, oid, other.get("tns") or 0.0,
                   _band_name(-a[6], sel["tns_bands_ns"], "ns"),
                   _band_name(-b[6], sel["tns_bands_ns"], "ns")))
    return "same bands; %s is ahead on WNS, TNS or failing endpoints" % wid


def pick_best_run(runs, exclude=None, selection=None):
    """
    Pick the best run: by WNS, preferring runs that also meet hold (selection
    mode wns, the default), or by closability_key (selection mode closability).

    Returns the winning run dict, or {} when there are no usable candidates.
    """
    if selection and selection.get("mode") == "closability":
        ranked = rank_runs([r for r in runs if not any(
            ex in r.get("run_id", "") for ex in (exclude or []))], selection)
        return ranked[0] if ranked else {}
    exclude = exclude or []

    candidates = []
    for run in runs:
        if run.get("wns") is None:
            continue
        run_id = run.get("run_id", "")
        if exclude and any(ex in run_id for ex in exclude):
            continue
        candidates.append(run)

    if not candidates:
        return {}

    # Prefer the hold-met pool; fall back to everything only if none meet hold.
    met = [r for r in candidates if hold_met(r)]
    pool = met if met else candidates

    best = None
    for run in pool:
        if best is None:
            best = run
            continue
        if run["wns"] > best["wns"]:
            best = run
        elif run["wns"] == best["wns"]:
            tns = run.get("tns")
            best_tns = best.get("tns")
            tns = float('-inf') if tns is None else tns
            best_tns = float('-inf') if best_tns is None else best_tns
            if tns > best_tns:
                best = run

    return best or {}


def floor_verdict(floor):
    """'improved' / 'improved on TNS (-67%)' / 'no improvement' for a result['floor']."""
    if floor.get("improved_by") == "tns":
        return "improved on TNS (%+.0f%% at equal WNS)" % floor.get("tns_delta_pct", 0)
    return "improved" if floor.get("improved") else "no improvement"


def rank_runs(runs, selection=None):
    """Return runs sorted best-first, using the same ordering as pick_best_run."""
    scored = [r for r in runs if r.get("wns") is not None]
    if selection and selection.get("mode") == "closability":
        scored.sort(key=lambda r: closability_key(r, selection))
        return scored

    def sort_key(r):
        # hold-met first, then best WNS, then best TNS
        hold_ok = 0 if hold_met(r) else 1
        tns = r.get("tns")
        tns = float('-inf') if tns is None else tns
        return (hold_ok, -r["wns"], -tns)

    scored.sort(key=sort_key)
    return scored


def load_thresholds(config_path):
    """Read the Stage 2 trigger thresholds from agent_config.yaml, with defaults."""
    thresholds = {
        "oc_threshold_ps": DEFAULT_OC_THRESHOLD_PS,
        "mp_threshold_ps": DEFAULT_MP_THRESHOLD_PS,
        "min_improvement_ps": DEFAULT_MIN_IMPROVEMENT_PS,
        "min_tns_improvement_pct": DEFAULT_MIN_TNS_IMPROVEMENT_PCT,
        "oc_multipliers": [1.5, 2.0, 2.5, 3.0],
    }

    if not config_path or not os.path.isfile(config_path):
        return thresholds

    try:
        cfg = load_yaml(config_path)
    except Exception:
        return thresholds

    stage2 = cfg.get("stage2", {}) or {}
    oc = stage2.get("over_constrain", {}) or {}
    mp = stage2.get("multi_physopt", {}) or {}
    guard = cfg.get("guardrails", {}) or {}

    if oc.get("wns_threshold_ps") is not None:
        thresholds["oc_threshold_ps"] = oc["wns_threshold_ps"]
    if oc.get("wns_multipliers"):
        thresholds["oc_multipliers"] = oc["wns_multipliers"]
    if mp.get("wns_threshold_ps") is not None:
        thresholds["mp_threshold_ps"] = mp["wns_threshold_ps"]
    if guard.get("stage2_min_improvement_ps") is not None:
        thresholds["min_improvement_ps"] = guard["stage2_min_improvement_ps"]
    if guard.get("min_tns_improvement_pct") is not None:
        thresholds["min_tns_improvement_pct"] = float(guard["min_tns_improvement_pct"])

    return thresholds


def compute_triggers(best, thresholds):
    """
    Decide which fixed-shape Stage 2 branches are worth launching.

    Over-constrain and multi-physopt only make sense when the design is close:
    strictly between the (negative) threshold and 0.
    """
    wns = best.get("wns")
    if wns is None:
        return {
            "over_constrain": False,
            "multi_physopt": False,
            "reason": "no WNS parsed from the best run",
        }

    oc_thr = thresholds["oc_threshold_ps"] / 1000.0
    mp_thr = thresholds["mp_threshold_ps"] / 1000.0

    oc = oc_thr < wns < 0
    mp = mp_thr < wns < 0

    if wns >= 0:
        reason = "WNS >= 0: setup already met, no over-constrain needed"
    elif not oc:
        reason = ("WNS %.3f ns is outside (%.3f, 0) -- too far out for "
                  "over-constrain to help" % (wns, oc_thr))
    else:
        reason = "WNS %.3f ns is within (%.3f, 0)" % (wns, oc_thr)

    return {
        "over_constrain": oc,
        "multi_physopt": mp,
        "oc_multipliers": thresholds["oc_multipliers"] if oc else [],
        "reason": reason,
    }


def load_config(config_path):
    if not config_path or not os.path.isfile(config_path):
        return {}
    try:
        return load_yaml(config_path) or {}
    except Exception:
        return {}


def compare_rules(runs, best, selection, exclude=None):
    """
    Which rule chose, and what the other rule would have chosen. Recorded in
    every mode, so runs on the default rule still show where closability would
    have carried a different run forward -- the cases worth a `tc extend --from`.
    """
    mode = selection.get("mode", "wns")
    other_sel = dict(selection, mode="wns" if mode == "closability" else "closability")
    other = pick_best_run(runs, exclude=exclude, selection=other_sel)
    out = {"mode": mode, "other_mode": other_sel["mode"],
           "other_pick": other.get("run_id", ""), "agree": True}
    if best and other and other.get("run_id") != best.get("run_id"):
        out["agree"] = False
        out["other_wns"] = other.get("wns")
        out["other_tns"] = other.get("tns")
        win, lose = (other, best) if other_sel["mode"] == "closability" else (best, other)
        out["why"] = why_not(win, lose, selection if mode == "closability" else other_sel)
    return out


def load_runs(path):
    """Load a run array (or single run object) from a JSON file, or '-' for stdin."""
    if path == "-":
        data = json.load(sys.stdin)
    else:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    if isinstance(data, dict):
        return [data]
    return data


def main():
    parser = argparse.ArgumentParser(
        description="Pick the best run by WNS from parsed report_parser.py output.")
    parser.add_argument("--runs", required=True,
                        help="JSON file of run records from report_parser.py "
                             "(array or single object). Use '-' for stdin.")
    parser.add_argument("--stage", default="",
                        help="Label for this decision (e.g. S1, S2) -- echoed in output.")
    parser.add_argument("--floor",
                        help="JSON file holding the run to beat (e.g. the Stage 1 "
                             "winner). Enables the improvement check.")
    parser.add_argument("--config",
                        help="agent_config.yaml, for Stage 2 trigger thresholds.")
    parser.add_argument("--exclude", action="append", default=[],
                        help="Skip runs whose run_id contains this substring "
                             "(repeatable).")
    parser.add_argument("-o", "--output", help="Write JSON here instead of stdout.")
    args = parser.parse_args()

    runs = load_runs(args.runs)
    thresholds = load_thresholds(args.config)
    selection = load_selection(load_config(args.config))

    best = pick_best_run(runs, exclude=args.exclude, selection=selection)
    ranked = rank_runs(runs, selection)

    closed = timing_met(best) if best else False

    result = {
        "stage": args.stage,
        "num_runs": len(runs),
        "num_complete": len([r for r in runs if r.get("wns") is not None]),
        "verdict": "CLOSED" if closed else "NOT_CLOSED",
        "best_run_id": best.get("run_id", ""),
        "best_run_dir": best.get("run_dir", ""),
        "flow_name": best.get("flow_name", ""),
        "methodology": best.get("methodology", ""),
        "wns": best.get("wns"),
        "whs": best.get("whs"),
        "tns": best.get("tns"),
        "num_failing_setup": best.get("num_failing_setup"),
        "rqa_score": best.get("rqa_score"),
        "rqs_file": best.get("rqs_file", ""),
        # Post-route phys_opt suggestion counts per category (CONG, TIMING, ...):
        # skill_resolver's per-category RQS gate, as upstream's rqs_counts.
        "rqs_counts": best.get("rqs_counts") or {},
        "checkpoint": best.get("checkpoint", ""),
        "triggers": compute_triggers(best, thresholds),
        "selection": compare_rules(runs, best, selection, args.exclude),
        "ranking": [
            {
                "run_id": r.get("run_id", ""),
                "wns": r.get("wns"),
                "whs": r.get("whs"),
                "tns": r.get("tns"),
                "num_failing_setup": r.get("num_failing_setup"),
                "rqa_score": r.get("rqa_score"),
                "methodology": r.get("methodology", ""),
            }
            for r in ranked
        ],
    }

    # Runs that never produced a timing summary -- surfaced, not silently dropped.
    incomplete = [r.get("run_id", r.get("run_dir", "?"))
                  for r in runs if r.get("wns") is None]
    if incomplete:
        result["incomplete_runs"] = incomplete

    # Compare against a floor (the previous stage's winner).
    if args.floor:
        with open(args.floor, 'r', encoding='utf-8') as f:
            floor = json.load(f)
        floor_wns = floor.get("wns")
        min_impr = thresholds["min_improvement_ps"] / 1000.0
        if floor_wns is not None and best.get("wns") is not None:
            delta = best["wns"] - floor_wns
            # Equal WNS (within 1 ps) but a much smaller TNS is progress too: fewer
            # failing endpoints is a better start for whatever runs next.
            floor_tns, best_tns = floor.get("tns"), best.get("tns")
            tns_pct = None
            if floor_tns is not None and best_tns is not None and floor_tns < 0:
                tns_pct = 100.0 * (best_tns - floor_tns) / abs(floor_tns)
            tns_better = (tns_pct is not None and delta > -0.001 and
                          tns_pct >= thresholds["min_tns_improvement_pct"])
            improved_by = "wns" if delta > min_impr else ("tns" if tns_better else "")
            result["floor"] = {
                "run_id": floor.get("best_run_id") or floor.get("run_id", ""),
                "wns": floor_wns,
                "delta_ns": round(delta, 4),
                "tns": floor_tns,
                "tns_delta_pct": None if tns_pct is None else round(tns_pct, 1),
                "improved": bool(improved_by),
                "improved_by": improved_by,
                "min_improvement_ns": min_impr,
                "min_tns_improvement_pct": thresholds["min_tns_improvement_pct"],
            }
            # If the floor was actually better, report it as the overall winner.
            if delta < 0 or (delta == 0 and not tns_better):
                result["note"] = ("no run in this stage beat the floor "
                                  "(%.3f ns); the floor run remains best"
                                  % floor_wns)

    text = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text + "\n")
        print("Best: %s  WNS=%s  WHS=%s  -> %s" % (
            result["best_run_id"], result["wns"], result["whs"],
            result["verdict"]), file=sys.stderr)
    else:
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
