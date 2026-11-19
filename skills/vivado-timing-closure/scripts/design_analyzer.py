#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
design_analyzer.py -- the "Classification Agent": categorize what is wrong with a run.

Standalone and stdlib-only: no third-party packages, no network access.

Runs four independent checkers over one run's parsed reports:
  * Partition  -- failing paths crossing SLR boundaries, SLR utilization imbalance
  * Congestion -- placer/router congestion windows above threshold
  * Clocking   -- inherent clock skew on failing paths
  * Timing     -- always-on; severity tracks the worst slack

Each returns detected / severity / confidence / evidence / summary. That detail
is what the arbitration step and the final report reason over, so it is always
emitted rather than reduced to a bare category name.

Usage:
    design_analyzer.py --run-dir <dir> [--stage postroute_physopt]
    design_analyzer.py --parsed run.json          # reuse report_parser.py output

Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report_parser  # noqa: E402


# ===========================================================================
# Partition checker
# ===========================================================================

# Primitives treated as Macros: hard blocks that can't be replicated or retimed,
# so an SLR crossing into one is much harder to fix than a flop-to-flop crossing.
MACRO_PRIMITIVES = {
    "RAMB36E5", "RAMB36E5_INT", "RAMB36E2", "RAMB18E5", "RAMB18E2",
    "RAMB36", "RAMB18",
    "DSP58", "DSP48E5", "DSP48E2", "DSPCPLX", "DSPFP32",
    "DSP_OUTPUT58", "DSP_ALU", "DSP_A_B_DATA", "DSP_C_DATA",
    "DSP_MULTIPLIER", "DSP_M_DATA", "DSP_PREADD_DATA", "DSP_PREADD",
    "GT", "GTHE4_CHANNEL", "GTYE4_CHANNEL", "GTM_CHANNEL",
    "GTYP_CHANNEL", "GTM_DUAL",
    "DCMAC", "MRMAC", "HBM", "ILKN",
    "URAM288", "URAM288E5", "URAM",
    "PCIE40E4", "PCIE50", "PCIE",
}

SLR_IMBALANCE_THRESHOLD = 30.0   # max% - min% above this is "imbalanced"
CONGESTION_LEVEL_THRESHOLD = 5   # level >= 5 is problematic
INHERENT_SKEW_THRESHOLD = -0.500  # ns; skew worse than this is a clocking problem


def _is_macro(primitive):
    """True when a primitive name denotes a hard block (BRAM/DSP/GT/URAM/...)."""
    if not primitive:
        return False
    base = primitive.split('/')[0]
    if base in MACRO_PRIMITIVES:
        return True
    for macro in MACRO_PRIMITIVES:
        if base.startswith(macro):
            return True
    return False


def _classify_path_impact(path):
    """Classify one SLR-crossing path as CRITICAL / HIGH / LOW."""
    start_is_macro = _is_macro(path.get('start_primitive', ''))
    end_is_macro = _is_macro(path.get('end_primitive', ''))

    # Crossing into or out of a Macro is the hardest case to fix.
    if start_is_macro or end_is_macro:
        return "CRITICAL"
    # Flop -> Flop with combinational logic in between.
    if path.get('logic_levels', 0) > 0:
        return "HIGH"
    # Flop -> Flop with no logic: properly pipelined, least concerning.
    return "LOW"


def _slr_imbalance(slr_util):
    """Shared SLR-utilization imbalance calculation (partition + congestion)."""
    imbalance = {"detected": False, "slice_util_per_slr": {}, "max_diff": 0.0}
    if not slr_util:
        return imbalance
    values = list(slr_util.values())
    if not values:
        return imbalance
    diff = max(values) - min(values)
    imbalance["slice_util_per_slr"] = slr_util
    imbalance["max_diff"] = round(diff, 2)
    imbalance["detected"] = diff > SLR_IMBALANCE_THRESHOLD
    return imbalance


def check_partition(parsed_data):
    """Detect partition problems: failing paths that cross SLR boundaries."""
    result = {
        "category": "Partition",
        "detected": False,
        "severity": "NONE",
        "confidence": 0.0,
        "evidence": [],
        "slr_imbalance": {"detected": False, "slice_util_per_slr": {}, "max_diff": 0.0},
        "summary": "",
    }

    paths = parsed_data.get("paths", [])
    num_slrs = parsed_data.get("num_slrs", 0)

    # Partition rules are meaningless on a single-SLR device.
    if num_slrs == 0:
        result["summary"] = "Single-SLR device -- partition analysis not applicable."
        return result

    failing_slr_paths = []
    for path in paths:
        slack = path.get('slack')
        if path.get('slr_crossings', 0) > 0 and slack is not None and slack < 0:
            failing_slr_paths.append({
                "path_num": path.get('path_num', 0),
                "slack": slack,
                "slr_crossings": path.get('slr_crossings', 0),
                "logic_levels": path.get('logic_levels', 0),
                "start_primitive": path.get('start_primitive', ''),
                "end_primitive": path.get('end_primitive', ''),
                "net_delay_pct": path.get('net_delay_pct', 0),
                "high_fanout": path.get('high_fanout', 0),
                "impact": _classify_path_impact(path),
            })

    if failing_slr_paths:
        result["detected"] = True
        result["evidence"] = failing_slr_paths
        total_paths = len(paths) if paths else 10
        result["confidence"] = len(failing_slr_paths) / float(total_paths)

        impacts = [e["impact"] for e in failing_slr_paths]
        if "CRITICAL" in impacts:
            result["severity"] = "CRITICAL"
        elif "HIGH" in impacts:
            result["severity"] = "HIGH"
        else:
            result["severity"] = "LOW"

    result["slr_imbalance"] = _slr_imbalance(parsed_data.get("slr_utilization", {}))

    # --- summary ---
    if not result["detected"]:
        if result["slr_imbalance"]["detected"]:
            util_str = ", ".join(
                "%s: %.1f%%" % (k, v)
                for k, v in result["slr_imbalance"]["slice_util_per_slr"].items())
            result["summary"] = (
                "No SLR crossing failures, but SLR imbalance detected (%s)." % util_str)
        else:
            result["summary"] = "No partition issues detected."
        return result

    macro_paths = [p for p in failing_slr_paths if p["impact"] == "CRITICAL"]
    logic_paths = [p for p in failing_slr_paths if p["impact"] == "HIGH"]

    parts = ["%d/%d critical paths cross SLR (severity: %s)." % (
        len(failing_slr_paths), len(paths) or 10, result["severity"])]
    if macro_paths:
        parts.append("%d paths involve Macros (BRAM/DSP/etc)." % len(macro_paths))
    if logic_paths:
        parts.append("%d paths have logic levels > 0 crossing SLR." % len(logic_paths))
    if result["slr_imbalance"]["detected"]:
        slr_data = result["slr_imbalance"]["slice_util_per_slr"]
        max_slr = max(slr_data, key=slr_data.get)
        min_slr = min(slr_data, key=slr_data.get)
        parts.append("SLR imbalance: %s at %.1f%% vs %s at %.1f%%." % (
            max_slr, slr_data[max_slr], min_slr, slr_data[min_slr]))

    result["summary"] = " ".join(parts)
    return result


# ===========================================================================
# Congestion checker
# ===========================================================================
def check_congestion(parsed_data):
    """Detect routing congestion from placer/router congestion windows."""
    result = {
        "category": "Congestion",
        "detected": False,
        "severity": "NONE",
        "confidence": 0.0,
        "max_level": 0,
        "congestion_windows": [],
        "possible_cause": "",
        "slr_imbalance": {"detected": False, "slice_util_per_slr": {}, "max_diff": 0.0},
        "summary": "",
    }

    all_windows = (parsed_data.get("congestion_placer", []) +
                   parsed_data.get("congestion_router", []))

    significant = [w for w in all_windows
                   if w.get("level", 0) >= CONGESTION_LEVEL_THRESHOLD]

    if not significant:
        result["summary"] = "No congestion issues detected (all levels < 5)."
        return result

    result["detected"] = True
    result["congestion_windows"] = significant
    max_level = max(w["level"] for w in significant)
    result["max_level"] = max_level

    if max_level >= 8:
        result["severity"] = "CRITICAL"
    elif max_level >= 6:
        result["severity"] = "HIGH"
    else:
        result["severity"] = "LOW"

    # More windows => more likely a systemic problem than a local hotspot.
    num_windows = len(significant)
    if num_windows >= 5:
        result["confidence"] = 0.9
    elif num_windows >= 3:
        result["confidence"] = 0.7
    else:
        result["confidence"] = 0.5

    slr_util = parsed_data.get("slr_utilization", {})
    result["slr_imbalance"] = _slr_imbalance(slr_util)
    if result["slr_imbalance"]["detected"]:
        result["possible_cause"] = "slr_imbalance"
    elif slr_util and any(v > 75.0 for v in slr_util.values()):
        result["possible_cause"] = "high_utilization"
    else:
        result["possible_cause"] = "unknown"

    # --- summary ---
    long_windows = [w for w in significant if w.get("type") == "Long"]
    parts = ["Congestion detected: max level %d, %d window(s)." % (
        max_level, num_windows)]
    if long_windows:
        parts.append("%d Long-range congestion window(s)." % len(long_windows))

    if result["possible_cause"] == "slr_imbalance":
        slr_data = result["slr_imbalance"]["slice_util_per_slr"]
        max_slr = max(slr_data, key=slr_data.get)
        parts.append("SLR imbalance may be causing congestion (%s at %.1f%%)." % (
            max_slr, slr_data[max_slr]))
    elif result["possible_cause"] == "high_utilization":
        parts.append("High utilization in one or more SLRs may be contributing.")

    result["summary"] = " ".join(parts)
    return result


# ===========================================================================
# Clocking checker
# ===========================================================================
def check_clocking(parsed_data):
    """
    Detect clocking problems via inherent clock skew.

    inherent skew = Clock Skew - CLK_MOD Skew. When that is very negative the
    clock topology itself is at fault -- placement/optimization can't recover it.
    """
    result = {
        "category": "Clocking",
        "detected": False,
        "severity": "NONE",
        "confidence": 0.0,
        "evidence": [],
        "max_inherent_skew": 0.0,
        "affected_paths": 0,
        "clock_pairs": [],
        "summary": "",
    }

    paths = parsed_data.get("paths", [])
    if not paths:
        result["summary"] = "No path data available for clocking analysis."
        return result

    affected = []
    clock_pairs_seen = set()

    for path in paths:
        slack = path.get('slack')
        if slack is None or slack >= 0:
            continue  # only failing paths matter

        clock_skew = path.get('clock_skew')
        if clock_skew is None:
            continue

        clk_mod_skew = path.get('clk_mod_skew')
        if clk_mod_skew is not None:
            inherent_skew = clock_skew - clk_mod_skew
        else:
            inherent_skew = clock_skew

        if inherent_skew < INHERENT_SKEW_THRESHOLD:
            src_clk = path.get('start_clock', 'unknown')
            dst_clk = path.get('end_clock', 'unknown')
            clock_pairs_seen.add("%s -> %s" % (src_clk, dst_clk))
            affected.append({
                "path_num": path.get('path_num', 0),
                "slack": slack,
                "clock_skew": clock_skew,
                "clk_mod_skew": clk_mod_skew,
                "inherent_skew": inherent_skew,
                "start_clock": src_clk,
                "end_clock": dst_clk,
                "requirement": path.get('requirement'),
            })

    if not affected:
        result["summary"] = "No clocking issues detected (all inherent skew > -400ps)."
        return result

    result["detected"] = True
    result["evidence"] = affected
    result["affected_paths"] = len(affected)
    result["clock_pairs"] = sorted(clock_pairs_seen)

    worst_skew = min(p["inherent_skew"] for p in affected)
    result["max_inherent_skew"] = worst_skew
    result["severity"] = "CRITICAL" if worst_skew < -0.600 else "HIGH"

    total_failing = len([p for p in paths
                         if p.get('slack') is not None and p['slack'] < 0])
    pct_affected = len(affected) / float(max(total_failing, 1))
    if pct_affected >= 0.5:
        result["confidence"] = 0.9
    elif pct_affected >= 0.3:
        result["confidence"] = 0.7
    else:
        result["confidence"] = 0.5

    pairs_str = ", ".join(result["clock_pairs"][:3])
    if len(result["clock_pairs"]) > 3:
        pairs_str += " (+%d more)" % (len(result["clock_pairs"]) - 3)

    result["summary"] = (
        "%s: %d paths with inherent clock skew < -400ps (worst: %.0fps). "
        "Clock pairs: %s" % (
            result["severity"], len(affected), worst_skew * 1000, pairs_str))
    return result


# ===========================================================================
# Timing checker
# ===========================================================================
def check_timing(parsed_data):
    """
    Always-on category. Timing closure is the whole point, so this category is
    unconditionally active; only its severity varies with the worst slack.
    """
    result = {
        "category": "Timing",
        "detected": True,
        "severity": "LOW",
        "confidence": 1.0,
        "evidence": [],
        "summary": "Timing category always-on",
    }

    worst_slack = None
    for path in parsed_data.get("paths", []):
        slack = path.get('slack')
        if slack is not None and (worst_slack is None or slack < worst_slack):
            worst_slack = slack

    if worst_slack is not None and worst_slack < 0:
        if worst_slack < -1.0:
            result["severity"] = "CRITICAL"
        elif worst_slack < -0.5:
            result["severity"] = "HIGH"
        elif worst_slack < -0.2:
            result["severity"] = "MEDIUM"
        result["summary"] = (
            "Timing category always-on (worst slack=%.3f ns)" % worst_slack)
        result["worst_slack"] = worst_slack

    return result


# ===========================================================================
# Orchestrator
# ===========================================================================
CHECKERS = [check_partition, check_congestion, check_clocking, check_timing]

SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2.5, "LOW": 2, "NONE": 1}


def analyze(parsed_data):
    """Run every checker and rank the detected problems by severity."""
    results = [checker(parsed_data) for checker in CHECKERS]

    detected = [r for r in results if r.get("detected")]
    detected_sorted = sorted(
        detected,
        key=lambda r: SEVERITY_ORDER.get(r.get("severity", "NONE"), 0),
        reverse=True,
    )
    primary = detected_sorted[0] if detected_sorted else None

    if not detected:
        summary = "No design problems detected in analyzed categories."
    else:
        names = [r["category"] for r in detected_sorted]
        summary = "Detected problems: %s. Primary: %s (%s)." % (
            ", ".join(names), primary["category"], primary["severity"])

    by_cat = dict((r["category"], r) for r in results)
    partition = by_cat.get("Partition", {})
    congestion = by_cat.get("Congestion", {})

    # SLR imbalance is reported by both the partition and congestion checkers;
    # either flagging it counts (mirrors how Stage 2 built its condition dict).
    slr_imbalance = bool(
        partition.get("slr_imbalance", {}).get("detected") or
        congestion.get("slr_imbalance", {}).get("detected"))

    # Boolean conditions consumed by skill_resolver.py to match the
    # `condition:` blocks in each skill's directives.yaml.
    conditions = {
        "partition_detected": bool(partition.get("detected")),
        "congestion_detected": bool(congestion.get("detected")),
        "clocking_detected": bool(by_cat.get("Clocking", {}).get("detected")),
        "timing_detected": bool(by_cat.get("Timing", {}).get("detected")),
        "slr_imbalance": slr_imbalance,
    }

    # Numeric metrics matching the `trigger:` blocks in skills_config.yaml.
    paths = parsed_data.get("paths", [])
    worst_slack = None
    for p in paths:
        s = p.get("slack")
        if s is not None and (worst_slack is None or s < worst_slack):
            worst_slack = s

    slr_util = parsed_data.get("slr_utilization", {}) or {}
    metrics = {
        "congestion_level": congestion.get("max_level", 0),
        "top_paths_slr_crossing": len(partition.get("evidence", [])),
        "clock_skew_dominant": bool(by_cat.get("Clocking", {}).get("detected")),
        "wns": worst_slack,
        "slr_imbalance_diff": partition.get("slr_imbalance", {}).get("max_diff", 0.0),
        "max_slr_utilization": max(slr_util.values()) if slr_util else 0.0,
    }

    return {
        "categories": results,
        "detected_problems": detected_sorted,
        "primary_problem": primary,
        "summary": summary,
        "conditions": conditions,
        "metrics": metrics,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Classify a run's timing-closure problems by category.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--run-dir", help="Run directory to analyze (parses reports).")
    src.add_argument("--parsed",
                     help="report_parser.py JSON for one run (reuses its "
                          "'analysis' object instead of re-reading reports). "
                          "Use '-' for stdin.")
    parser.add_argument("--stage", default="postroute_physopt",
                        help="Report stage prefix (default: postroute_physopt).")
    parser.add_argument("-o", "--output", help="Write JSON here instead of stdout.")
    args = parser.parse_args()

    if args.run_dir:
        run_dir = os.path.abspath(args.run_dir)
        stage = args.stage
        parsed_data = report_parser.parse_analysis(run_dir, stage)
    else:
        if args.parsed == "-":
            record = json.load(sys.stdin)
        else:
            with open(args.parsed, 'r', encoding='utf-8') as f:
                record = json.load(f)
        if isinstance(record, list):
            parser.error("--parsed expects a single run object, not an array")
        parsed_data = record.get("analysis")
        if parsed_data is None:
            parser.error("--parsed JSON has no 'analysis' object; "
                         "was it produced by report_parser.py?")
        run_dir = record.get("run_dir", "")
        stage = record.get("stage", args.stage)

    result = analyze(parsed_data)
    result["run_dir"] = run_dir
    result["stage"] = stage

    if not parsed_data.get("paths"):
        result["warning"] = (
            "no path data parsed from %s_design_analysis.rpt -- classification is "
            "based on congestion/utilization only" % stage)

    text = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text + "\n")
        print(result["summary"], file=sys.stderr)
    else:
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
