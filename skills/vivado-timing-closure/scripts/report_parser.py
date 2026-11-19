#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
report_parser.py -- Parse the Vivado reports in a run directory into one JSON blob.

Standalone and stdlib-only: no third-party packages, no network access.

Extracts, from one run directory:
  * WNS / WHS / TNS / THS and failing-endpoint counts from the timing summary
  * top failing path groups and SLR-crossing counts from the timing report
  * the setup path-characteristics table, congestion windows and per-SLR
    utilization from the design-analysis and utilization reports
  * QoR-suggestion counts by category, and the .rqs file path

The emitted JSON is the shared contract consumed by design_analyzer.py (via the
"analysis" sub-object) and decision_engine.py (via the top-level wns/whs/tns).

Usage:
    # one run
    report_parser.py --run-dir <dir> [--stage postroute_physopt] [--run-id S1_explore]

    # many runs at once -> JSON array (repeatable, --run-id paired by position)
    report_parser.py --run-dir a --run-id S1_explore --run-dir b --run-id S1_auto_1

    # discover every run under a root -> JSON array
    report_parser.py --scan-root <output_dir>/stage1

Requires Python 3.6+.
"""

from __future__ import print_function

import argparse
import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Report filenames written by the generated run.tcl, keyed by stage prefix.
# Keep in sync with tcl_gen.py's report block.
# ---------------------------------------------------------------------------
def report_paths(run_dir, stage):
    """Return the expected report file paths for a given stage prefix."""
    return {
        "timing_summary": os.path.join(run_dir, "%s_timing_summary.rpt" % stage),
        "timing_max": os.path.join(run_dir, "%s_timing_max.rpt" % stage),
        "timing_min": os.path.join(run_dir, "%s_timing_min.rpt" % stage),
        "design_analysis": os.path.join(run_dir, "%s_design_analysis.rpt" % stage),
        "utilization": os.path.join(run_dir, "%s_utilization.rpt" % stage),
        "clock_interaction": os.path.join(run_dir, "%s_clock_interaction.rpt" % stage),
        "qor_assessment": os.path.join(run_dir, "%s_qor_assessment.rpt" % stage),
        "rqs": os.path.join(run_dir, "%s_rqs.rpt" % stage),
        "checkpoint": os.path.join(run_dir, "%s.dcp" % stage),
    }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def read_report(path):
    """Read an entire report file; return None if missing/unreadable."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except (IOError, OSError):
        return None


def parse_float(value):
    """Safely parse a float from a string."""
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


def parse_int(value):
    """Safely parse an int from a string."""
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return None


# ===========================================================================
# Timing report parsing
# ===========================================================================
def parse_timing_summary(report_path):
    """
    Parse report_timing_summary output (table format).

    Extracts the 'Design Timing Summary' row:
      WNS(ns) TNS(ns) TNS_Failing TNS_Total WHS(ns) THS(ns) THS_Failing THS_Total
    Returns: {wns, whs, tns, ths, num_failing_setup, num_failing_hold}
    """
    result = {
        "wns": None,
        "whs": None,
        "tns": None,
        "ths": None,
        "num_failing_setup": 0,
        "num_failing_hold": 0,
    }

    try:
        with open(report_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except (IOError, OSError):
        return result

    # Find the Design Timing Summary header row (first table with WNS and WHS).
    # Must NOT be a per-clock / inter-clock table.
    header_idx = -1
    for i in range(len(lines)):
        line = lines[i]
        if 'WNS(ns)' in line and 'WHS(ns)' in line:
            if 'Clock' not in line and 'From Clock' not in line:
                header_idx = i
                break

    if header_idx < 0:
        return result

    # Data row is 2 lines after header (header, separator, data)
    data_idx = header_idx + 2
    if data_idx >= len(lines):
        return result

    data_line = lines[data_idx].strip()
    if not data_line:
        return result

    parts = data_line.split()
    if len(parts) < 8:
        return result

    for key, idx, caster in (
        ("wns", 0, float),
        ("tns", 1, float),
        ("num_failing_setup", 2, int),
        ("whs", 4, float),
        ("ths", 5, float),
        ("num_failing_hold", 6, int),
    ):
        try:
            result[key] = caster(parts[idx])
        except (ValueError, IndexError):
            pass

    return result


def _parse_single_path(block):
    """Parse a single timing path block out of a report_timing dump."""
    path = {}

    match = re.search(r'Slack\s*\((?:VIOLATED|MET)\)\s*:\s*([-\d.]+)ns', block)
    if not match:
        return None
    path['slack'] = float(match.group(1))

    match = re.search(r'Source:\s*(\S+)', block)
    if match:
        path['source'] = match.group(1)

    match = re.search(r'Destination:\s*(\S+)', block)
    if match:
        path['destination'] = match.group(1)

    match = re.search(r'Path Group:\s*(\S+)', block)
    if match:
        path['path_group'] = match.group(1)

    match = re.search(r'Requirement:\s*([-\d.]+)ns', block)
    if match:
        path['requirement'] = float(match.group(1))

    clocks = re.findall(r'clocked by\s+(\S+)', block)
    if len(clocks) >= 2:
        path['src_clk'] = clocks[0]
        path['dst_clk'] = clocks[1]
    elif len(clocks) == 1:
        path['src_clk'] = clocks[0]
        path['dst_clk'] = clocks[0]

    path['slr_crossing'] = bool(re.search(r'SLR\s*Crossing|LAGUNA', block))

    match = re.search(r'Data Path Delay:\s*([-\d.]+)ns', block)
    if match:
        path['data_path_delay'] = float(match.group(1))

    match = re.search(
        r'logic\s+([-\d.]+)ns\s*\(([\d.]+)%\)\s*route\s+([-\d.]+)ns\s*\(([\d.]+)%\)',
        block)
    if match:
        path['logic_delay'] = float(match.group(1))
        path['logic_pct'] = float(match.group(2))
        path['route_delay'] = float(match.group(3))
        path['route_pct'] = float(match.group(4))

    return path


def parse_timing_paths(report_path):
    """
    Parse a report_timing -max_paths dump.
    Returns {paths: [...], path_groups: [{name, wns, src_clk, dst_clk, requirement}]}.
    """
    result = {"path_groups": [], "paths": []}

    content = read_report(report_path)
    if content is None:
        return result

    for block in re.split(r'-{40,}', content):
        path_info = _parse_single_path(block)
        if path_info:
            result['paths'].append(path_info)

    groups = {}
    for path in result['paths']:
        pg = path.get('path_group', 'unknown')
        if pg not in groups:
            groups[pg] = {
                "name": pg,
                "wns": path.get('slack'),
                "src_clk": path.get('src_clk'),
                "dst_clk": path.get('dst_clk'),
                "requirement": path.get('requirement'),
                "path_count": 0,
            }
        groups[pg]['path_count'] += 1
        if path.get('slack') is not None:
            if groups[pg]['wns'] is None or path['slack'] < groups[pg]['wns']:
                groups[pg]['wns'] = path['slack']

    result['path_groups'] = list(groups.values())
    return result


def get_top_failing_groups(report_path, n=2):
    """Top N failing path groups with clock info and requirements."""
    data = parse_timing_paths(report_path)
    failing = [g for g in data['path_groups'] if (g.get('wns') or 0) < 0]
    failing.sort(key=lambda g: g.get('wns') or 0)
    return failing[:n]


def count_slr_crossing_paths(report_path, n_top=10):
    """How many of the top N worst paths cross an SLR boundary."""
    data = parse_timing_paths(report_path)
    top_paths = sorted(data['paths'], key=lambda p: p.get('slack') or 0)[:n_top]
    return sum(1 for p in top_paths if p.get('slr_crossing', False))


# ===========================================================================
# Design-analysis + utilization parsing
# This is the structured input every category checker consumes.
# ===========================================================================
def _parse_pipe_columns(line):
    """Split a pipe-delimited table line into stripped column values."""
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|'):
        line = line[:-1]
    return [col.strip() for col in line.split('|')]


def _extract_path_info(col_map):
    """Extract structured path info from a header->value column map."""
    path_info = {}

    paths_val = col_map.get('Paths', '')
    m = re.search(r'#(\d+)', paths_val)
    path_info['path_num'] = int(m.group(1)) if m else 0

    # Float-valued columns
    for key, col in (
        ('slack', 'Slack'),
        ('requirement', 'Requirement'),
        ('clock_skew', 'Clock Skew'),
        ('clk_mod_skew', 'CLK_MOD Skew'),
        ('clock_uncertainty', 'Clock Uncertainty'),
    ):
        try:
            path_info[key] = float(col_map.get(col, '').strip())
        except (ValueError, TypeError):
            path_info[key] = None

    # Int-valued columns (default 0)
    for key, col in (
        ('logic_levels', 'Logic Levels'),
        ('slr_crossings', 'SLR Crossings'),
        ('ram_crossings', 'RAM Crossings'),
        ('dsp_crossings', 'DSP Crossings'),
        ('high_fanout', 'High Fanout'),
    ):
        try:
            path_info[key] = int(col_map.get(col, '').strip())
        except (ValueError, TypeError):
            path_info[key] = 0

    # Net Delay is a percentage like "62%"
    nd_str = col_map.get('Net Delay', '').strip()
    m = re.search(r'(\d+)%', nd_str)
    path_info['net_delay_pct'] = int(m.group(1)) if m else 0

    # String columns
    path_info['start_primitive'] = col_map.get('Start Point Pin Primitive', '').strip()
    path_info['end_primitive'] = col_map.get('End Point Pin Primitive', '').strip()
    path_info['logical_path'] = col_map.get('Logical Path', '').strip()
    path_info['start_clock'] = col_map.get('Start Point Clock', '').strip()
    path_info['end_clock'] = col_map.get('End Point Clock', '').strip()
    path_info['clock_relationship'] = col_map.get('Clock Relationship', '').strip()

    return path_info


def _parse_path_table(content):
    """
    Parse the Setup Path Characteristics table from design_analysis.rpt.

    Both the header and the individual path rows can wrap across several
    physical lines, so rows are accumulated until the next "| Path #" or a
    "+---" table separator.
    """
    paths = []
    lines = content.split('\n')

    header_start = None
    for i, line in enumerate(lines):
        if '|' in line and 'Paths' in line and 'Requirement' in line:
            header_start = i
            break

    if header_start is None:
        return paths

    # Join header continuation lines
    header_joined = lines[header_start]
    for j in range(header_start + 1, min(header_start + 10, len(lines))):
        l = lines[j]
        if l.startswith('+') or re.match(r'\|\s*Path\s*#', l):
            break
        if '|' in l:
            header_joined += ' ' + l
        else:
            break

    # Collect multi-line path rows
    path_blocks = []
    current_block = None
    in_table = False
    for line in lines:
        if re.match(r'\|\s*Path\s*#\d+', line):
            if current_block is not None:
                path_blocks.append(current_block)
            current_block = line
            in_table = True
        elif in_table and current_block is not None:
            if line.startswith('+'):
                path_blocks.append(current_block)
                current_block = None
            elif '|' in line and not re.match(r'\|\s*Path\s*#', line):
                current_block += ' ' + line
            else:
                path_blocks.append(current_block)
                current_block = None
                in_table = False
    if current_block is not None:
        path_blocks.append(current_block)

    if not path_blocks:
        return paths

    columns = _parse_pipe_columns(header_joined)

    for path_line in path_blocks:
        path_data = _parse_pipe_columns(path_line)
        if len(path_data) < len(columns):
            continue
        col_map = {}
        for idx, col_name in enumerate(columns):
            if idx < len(path_data):
                col_map[col_name.strip()] = path_data[idx].strip()
        path_info = _extract_path_info(col_map)
        if path_info:
            paths.append(path_info)

    return paths


def _parse_congestion_section(content, section_type):
    """
    Parse congestion windows from the Placer or Router section of
    design_analysis.rpt. Returns [{direction, type, level, tile1, tile2, section}].
    """
    windows = []

    if section_type == "Placer":
        section_header = "Placer Final Level Congestion"
    else:
        section_header = "Router Initial Congestion"

    # Use the LAST occurrence -- the first is the table-of-contents reference.
    idx = content.rfind(section_header)
    if idx == -1:
        return windows

    section_content = content[idx:]
    next_section = re.search(r'\n\d+\.\s+[A-Z]', section_content[100:])
    if next_section:
        section_content = section_content[:100 + next_section.start()]

    if "No congestion windows" in section_content:
        return windows

    # | Direction | Type | Level | (tile1,tile2) | ...
    pattern = re.compile(
        r'\|\s*(\w+)\s*\|\s*(\w+)\s*\|\s*(\d+)\s*\|\s*\(([^,]+),\s*([^)]+)\)'
    )

    for match in pattern.finditer(section_content):
        direction = match.group(1)
        if direction in ("Direction",):  # header row
            continue
        windows.append({
            "direction": direction,
            "type": match.group(2),
            "level": int(match.group(3)),
            "tile1": match.group(4).strip(),
            "tile2": match.group(5).strip(),
            "section": section_type,
        })

    return windows


def _parse_slr_utilization(filepath):
    """
    Parse the 'SLR CLB Logic and Dedicated Block Utilization' section.
    Returns ({"SLR0": <slice%>, ...}, num_slrs). Empty/0 on single-SLR devices.
    """
    slr_util = {}

    content = read_report(filepath)
    if content is None:
        return slr_util, 0

    # Last occurrence is the real data (first is the table of contents).
    last_pos = content.rfind("SLR CLB Logic and Dedicated Block Utilization")
    if last_pos == -1:
        return slr_util, 0

    section_content = content[last_pos:]

    header_match = re.search(r'\|\s*Site Type\s*\|(.+)\|', section_content)
    if not header_match:
        return slr_util, 0

    header_parts = [p.strip() for p in header_match.group(1).split('|')]

    slr_pct_cols = []
    for i, part in enumerate(header_parts):
        m = re.match(r'SLR(\d+)\s*%', part)
        if m:
            slr_pct_cols.append((i, "SLR%s" % m.group(1)))

    num_slrs = len(slr_pct_cols)
    if num_slrs == 0:
        return slr_util, 0

    # The SLICE row (not SLICEL/SLICEM) holds:
    #   Site Type, SLR0_count..SLRn_count, SLR0%..SLRn%
    for line in section_content.split('\n'):
        if re.match(r'\|\s*SLICE\s+', line):
            parts = [p.strip() for p in line.split('|')]
            parts = [p for p in parts if p]
            pct_start_idx = 1 + num_slrs
            for idx, (_, slr_name) in enumerate(slr_pct_cols):
                val_idx = pct_start_idx + idx
                if val_idx < len(parts):
                    try:
                        slr_util[slr_name] = float(parts[val_idx])
                    except ValueError:
                        pass
            break

    return slr_util, num_slrs


def parse_analysis(run_dir, stage):
    """
    Build the structured "parsed_data" dict the category checkers consume.

    Returns:
        {paths, congestion_placer, congestion_router, slr_utilization, num_slrs}
    """
    result = {
        "paths": [],
        "congestion_placer": [],
        "congestion_router": [],
        "slr_utilization": {},
        "num_slrs": 0,
    }

    paths_map = report_paths(run_dir, stage)

    da_content = read_report(paths_map["design_analysis"])
    if da_content is not None:
        result["paths"] = _parse_path_table(da_content)
        result["congestion_placer"] = _parse_congestion_section(da_content, "Placer")
        result["congestion_router"] = _parse_congestion_section(da_content, "Router")

    if os.path.isfile(paths_map["utilization"]):
        slr_util, num_slrs = _parse_slr_utilization(paths_map["utilization"])
        result["slr_utilization"] = slr_util
        result["num_slrs"] = num_slrs

    return result


def congestion_summary(analysis):
    """
    Summarize congestion levels from already-parsed windows.

    Derived from the already-parsed windows rather than re-parsing
    design_analysis.rpt with a second regex -- same source report, one pass.
    """
    placer = analysis.get("congestion_placer", [])
    router = analysis.get("congestion_router", [])

    max_placer = max([w["level"] for w in placer], default=0)
    max_router = max([w["level"] for w in router], default=0)

    return {
        "max_placer_level": max_placer,
        "max_router_level": max_router,
        "max_level": max(max_placer, max_router),
        "num_windows": len(placer) + len(router),
        "is_congested": max_placer >= 5 or max_router >= 5,
    }


# ===========================================================================
# QoR-suggestion counts
# ===========================================================================
RQS_CATEGORIES = {
    "RQS_CONG": "CONG",
    "RQS_TIMING": "TIMING",
    "RQS_CLOCK": "CLOCK",
    "RQS_UTIL": "UTIL",
    "RQS_NETLIST": "NETLIST",
}

# ===========================================================================
# QoR assessment score
# ===========================================================================
def parse_rqa(rqa_rpt_path):
    """
    The report_qor_assessment score (1-5) and its per-category scores:
    {"score": 3, "categories": {"Timing": 4.0, "Congestion": 3.0, ...}}, or
    {"score": None, "categories": {}} when the report is missing or unreadable.
    """
    result = {"score": None, "categories": {}}
    content = read_report(rqa_rpt_path)
    if not content:
        return result
    m = re.search(r'\|\s*QoR Assessment Score\s*\|\s*([1-5])\b', content)
    if m:
        result["score"] = int(m.group(1))
    # Category rows: "| Timing   |   |   |   |   |   4.0 |   |" -- a name with no
    # leading "*" or indent, and a value in the Score column.
    for line in content.splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) != 7 or not line.startswith('| ') or line.startswith('|  '):
            continue
        try:
            result["categories"][cells[0]] = float(cells[5])
        except ValueError:
            pass
    return result


# The "Generated At" value in the report for each stage prefix.
RQS_STAGE_FILTER = {
    "postplace_physopt": "postplace_phys_opt",
    "postroute_physopt": "postroute_phys_opt",
}


def parse_rqs_counts(rqs_rpt_path, filter_stage=None):
    """
    Parse the RQS report summary table, counting suggestions by category.

    filter_stage: only count suggestions whose "Generated At" matches
                  (e.g. "postroute_phys_opt"). None counts all.
    """
    counts = dict((cat, 0) for cat in RQS_CATEGORIES.values())

    if not os.path.isfile(rqs_rpt_path):
        return counts

    try:
        with open(rqs_rpt_path, 'r', errors='replace') as f:
            in_summary = False
            found_table_header = False
            for line in f:
                if '1. QoR Suggestions Report Summary' in line and not line.startswith(' '):
                    in_summary = True
                    found_table_header = False
                    continue

                if not in_summary:
                    continue

                if line.startswith('+---'):
                    found_table_header = True
                    continue

                if not found_table_header:
                    continue

                # Stop at the next numbered section or the end of the table
                if re.match(r'^\d+\.\s+QoR Suggestions\s+-\s+', line):
                    break
                if line.startswith('+') and '---' in line:
                    break

                if '|' not in line:
                    continue

                parts = [p.strip() for p in line.split('|')]
                if len(parts) < 5:
                    continue

                name = parts[1] if len(parts) > 1 else ""
                generated_at = parts[4] if len(parts) > 4 else ""

                # Skip the header row and multi-line continuation rows
                if not name or name == "Name" or not name.startswith("RQS_"):
                    continue

                if filter_stage and generated_at != filter_stage:
                    continue

                for prefix, category in RQS_CATEGORIES.items():
                    if name.startswith(prefix):
                        counts[category] += 1
                        break
    except (IOError, OSError):
        pass

    return counts


def find_rqs_file(run_dir, stage):
    """
    Locate the machine-readable .rqs file written by write_qor_suggestions,
    preferring the requested stage and falling back to the other one.
    """
    order = [stage]
    for other in ("postroute_physopt", "postplace_physopt"):
        if other not in order:
            order.append(other)

    for st in order:
        candidate = os.path.join(run_dir, "%s_rqs" % st, "%s.rqs" % st)
        if os.path.isfile(candidate):
            return candidate
    return ""


# ===========================================================================
# Design-level checks (upstream framework, 2026-10-08): report_methodology,
# check_timing and report_route_status
# ===========================================================================
def parse_methodology_summary(rpt_path):
    """
    The REPORT SUMMARY table of report_methodology -file: one row per rule.
    The count column is "Violations" before 2026.2 and "Checks" from it on.
    Returns {"rules": [{rule, severity, description, count}], "by_severity": {}}
    or None when the report is missing.
    """
    content = read_report(rpt_path)
    if not content:
        return None
    # The table of contents names both sections too: cut at the details heading
    # (the one underlined with dashes).
    summary = re.split(r"\n2\. REPORT DETAILS\s*\n-+", content)[0]
    rules = []
    for line in summary.splitlines():
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) != 4 or not line.lstrip().startswith("|") or cols[0] == "Rule":
            continue
        count = parse_int(cols[3])
        if count is None:
            continue
        rules.append({"rule": cols[0], "severity": cols[1], "description": cols[2],
                      "count": count})
    by_sev = {}
    for r in rules:
        by_sev[r["severity"]] = by_sev.get(r["severity"], 0) + r["count"]
    return {"rules": rules, "by_severity": by_sev}


_CHECK_TIMING_RE = re.compile(r"^\s*\d+\.\s+checking\s+(\w+)\s+\((\d+)\)\s*$")


def parse_check_timing(rpt_path):
    """{check name: count} from check_timing's table of contents, or None."""
    content = read_report(rpt_path)
    if not content:
        return None
    out = {}
    for line in content.splitlines():
        m = _CHECK_TIMING_RE.match(line)
        if m and m.group(1) not in out:
            out[m.group(1)] = int(m.group(2))
    return out


_ROUTE_STATUS_RE = re.compile(r"^\s*#\s+of\s+(.+?)\.{2,}\s*:\s*(\d+)")


def parse_route_status(rpt_path):
    """{"nets with routing errors": n, ...} from report_route_status, or None."""
    content = read_report(rpt_path)
    if not content:
        return None
    out = {}
    for line in content.splitlines():
        m = _ROUTE_STATUS_RE.match(line)
        if m:
            out[m.group(1).strip()] = int(m.group(2))
    return out


# ===========================================================================
# Top-level per-run parse
# ===========================================================================
def parse_run(run_dir, stage="postroute_physopt", run_id=None, flow_name=None,
              methodology=None):
    """Parse every available report for one run directory into a single dict."""
    run_dir = os.path.abspath(run_dir)
    paths_map = report_paths(run_dir, stage)

    timing = parse_timing_summary(paths_map["timing_summary"])
    analysis = parse_analysis(run_dir, stage)

    top_groups = []
    slr_crossing_top10 = 0
    if os.path.isfile(paths_map["timing_max"]):
        top_groups = get_top_failing_groups(paths_map["timing_max"], n=3)
        slr_crossing_top10 = count_slr_crossing_paths(paths_map["timing_max"], n_top=10)

    rqs_counts = parse_rqs_counts(
        paths_map["rqs"], filter_stage=RQS_STAGE_FILTER.get(stage))

    rqa = parse_rqa(paths_map["qor_assessment"])

    reports_found = dict(
        (name, os.path.isfile(path)) for name, path in paths_map.items())

    # A run is only usable for a decision once its timing summary parsed.
    complete = timing.get("wns") is not None

    return {
        "run_id": run_id or os.path.basename(run_dir),
        "flow_name": flow_name or "",
        "methodology": methodology or "",
        "run_dir": run_dir,
        "stage": stage,
        "complete": complete,
        # Flat metrics -- what decision_engine.py ranks on
        "wns": timing.get("wns"),
        "whs": timing.get("whs"),
        "tns": timing.get("tns"),
        "ths": timing.get("ths"),
        "num_failing_setup": timing.get("num_failing_setup"),
        "num_failing_hold": timing.get("num_failing_hold"),
        # Supporting detail
        "top_failing_groups": top_groups,
        "slr_crossing_top10": slr_crossing_top10,
        "congestion": congestion_summary(analysis),
        "rqs_counts": rqs_counts,
        "rqs_total": sum(rqs_counts.values()),
        "rqs_file": find_rqs_file(run_dir, stage),
        "rqa_score": rqa["score"],
        "rqa_categories": rqa["categories"],
        "checkpoint": paths_map["checkpoint"] if os.path.isfile(
            paths_map["checkpoint"]) else "",
        "reports_found": reports_found,
        # Structured input for design_analyzer.py's checkers
        "analysis": analysis,
    }


def discover_runs(scan_root, stage):
    """Find immediate subdirectories of scan_root that look like completed runs."""
    found = []
    if not os.path.isdir(scan_root):
        return found
    for name in sorted(os.listdir(scan_root)):
        candidate = os.path.join(scan_root, name)
        if not os.path.isdir(candidate):
            continue
        summary = os.path.join(candidate, "%s_timing_summary.rpt" % stage)
        if os.path.isfile(summary):
            found.append(candidate)
    return found


def main():
    parser = argparse.ArgumentParser(
        description="Parse Vivado run reports into JSON.")
    parser.add_argument("--run-dir", action="append", default=[],
                        help="Run directory to parse (repeatable).")
    parser.add_argument("--run-id", action="append", default=[],
                        help="Run ID for the corresponding --run-dir (repeatable, "
                             "paired by position; defaults to the directory name).")
    parser.add_argument("--flow-name", action="append", default=[],
                        help="Flow name for the corresponding --run-dir (repeatable).")
    parser.add_argument("--methodology", action="append", default=[],
                        help="Methodology label for the corresponding --run-dir "
                             "(repeatable).")
    parser.add_argument("--scan-root",
                        help="Parse every immediate subdirectory of this root that "
                             "contains a <stage>_timing_summary.rpt.")
    parser.add_argument("--stage", default="postroute_physopt",
                        help="Report stage prefix (default: postroute_physopt).")
    parser.add_argument("--array", action="store_true",
                        help="Always emit a JSON array, even for a single run.")
    parser.add_argument("-o", "--output", help="Write JSON here instead of stdout.")
    args = parser.parse_args()

    run_dirs = list(args.run_dir)
    run_ids = list(args.run_id)
    flow_names = list(args.flow_name)
    methodologies = list(args.methodology)

    if args.scan_root:
        discovered = discover_runs(args.scan_root, args.stage)
        run_dirs.extend(discovered)

    if not run_dirs:
        parser.error("no runs to parse: pass --run-dir and/or --scan-root")

    results = []
    for i, run_dir in enumerate(run_dirs):
        if not os.path.isdir(run_dir):
            print("WARNING: not a directory, skipping: %s" % run_dir,
                  file=sys.stderr)
            continue
        results.append(parse_run(
            run_dir,
            stage=args.stage,
            run_id=run_ids[i] if i < len(run_ids) else None,
            flow_name=flow_names[i] if i < len(flow_names) else None,
            methodology=methodologies[i] if i < len(methodologies) else None,
        ))

    payload = results if (args.array or len(results) != 1) else results[0]
    text = json.dumps(payload, indent=2)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text + "\n")
        print("Wrote %d run(s) to %s" % (len(results), args.output), file=sys.stderr)
    else:
        print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
