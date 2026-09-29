#!/usr/bin/env python3
#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
"""
qor_log.py  —  extract key QoR metrics from synthesis report and optionally append a CSV row.

Usage:
    python3 qor_log.py --report REPORT_CONTENT [--label LABEL] [--csv FILE]

    --report REPORT   Synthesis report content (string)
    --label  LABEL    A short tag for this iteration (default: timestamp)
    --csv    FILE     CSV file to append a row to (default: qor_history.csv)

Always prints a human-readable summary to stdout.
When --csv is given (or qor_history.csv already exists), appends a CSV row.

Columns tracked:
  label, timestamp, top_module, lat_max_cyc, lat_max_ns, interval,
  slack, bram, dsp, ff, lut, uram, pipelined
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_CSV = Path("qor_history.csv")

FIELDNAMES = [
    "label", "timestamp", "top_module",
    "lat_max_cyc", "lat_max_ns", "interval", "slack",
    "bram", "dsp", "ff", "lut", "uram", "pipelined",
]


def parse_resource(value):
    """Parse resource value like '224 (12%)' or '-' to integer."""
    if not value or value.strip() == '-':
        return -1
    m = re.match(r'(\d+)', value.strip())
    return int(m.group(1)) if m else -1


def parse_number(value):
    """Parse numeric value or '-' to appropriate type."""
    if not value or value.strip() == '-':
        return -1
    try:
        if '.' in value:
            return float(value.strip())
        return int(value.strip())
    except ValueError:
        return -1


def parse_synthesis_report(report_content):
    """
    Parse synthesis report content and extract top-level QoR metrics.
    Returns a dict with extracted metrics.
    """
    result = {
        "top_module": "?",
        "lat_max_cyc": -1,
        "lat_max_ns": -1.0,
        "interval": -1,
        "slack": -1.0,
        "bram": -1,
        "dsp": -1,
        "ff": -1,
        "lut": -1,
        "uram": -1,
        "pipelined": "?",
    }

    lines = report_content.strip().split('\n')
    
    # Find the table rows (lines starting with |)
    for line in lines:
        line = line.strip()
        if not line.startswith('|') or line.startswith('+'):
            continue
        # Skip header rows
        if 'Modules' in line or 'Issue' in line or '& Loops' in line:
            continue
        
        # Split by | and clean up
        cols = [c.strip() for c in line.split('|')]
        cols = [c for c in cols if c]  # Remove empty strings
        
        if len(cols) < 14:
            continue
        
        name = cols[0].strip()
        is_top = name.startswith('+') and '*' in name  # Top module has + prefix and * suffix
        
        if not is_top:
            continue
        
        # Clean name
        clean_name = name.lstrip('+ ').rstrip('*').strip()
        
        # Parse columns based on table structure:
        # Modules & Loops | Issue Type | Violation Type | Iteration Latency | Interval | Trip Count | Pipelined | Latency(cycles) | Latency(ns) | Slack | BRAM | DSP | FF | LUT | URAM
        try:
            interval = cols[4] if len(cols) > 4 else '-'
            pipelined = cols[6] if len(cols) > 6 else '-'
            lat_cyc = cols[7] if len(cols) > 7 else '-'
            lat_ns = cols[8] if len(cols) > 8 else '-'
            slack = cols[9] if len(cols) > 9 else '-'
            bram = cols[10] if len(cols) > 10 else '-'
            dsp = cols[11] if len(cols) > 11 else '-'
            ff = cols[12] if len(cols) > 12 else '-'
            lut = cols[13] if len(cols) > 13 else '-'
            uram = cols[14] if len(cols) > 14 else '-'
        except IndexError:
            continue
        
        result["top_module"] = clean_name
        result["lat_max_cyc"] = parse_number(lat_cyc)
        result["lat_max_ns"] = parse_number(lat_ns)
        result["interval"] = parse_number(interval)
        result["slack"] = parse_number(slack)
        result["bram"] = parse_resource(bram)
        result["dsp"] = parse_resource(dsp)
        result["ff"] = parse_resource(ff)
        result["lut"] = parse_resource(lut)
        result["uram"] = parse_resource(uram)
        result["pipelined"] = pipelined.strip()
        break  # Found top module, stop
    
    return result


def main():
    ap = argparse.ArgumentParser(description="Log synthesis QoR metrics to CSV")
    ap.add_argument("--report", required=True, help="Synthesis report content (string)")
    ap.add_argument("--label", default=None, help="Short label for this iteration")
    ap.add_argument("--csv", default=None, help="CSV file to append to")
    args = ap.parse_args()

    label = args.label or datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(args.csv) if args.csv else DEFAULT_CSV

    # Parse synthesis report
    metrics = parse_synthesis_report(args.report)

    row = {
        "label":          label,
        "timestamp":      datetime.now().isoformat(timespec="seconds"),
        "top_module":     metrics["top_module"],
        "lat_max_cyc":    metrics["lat_max_cyc"],
        "lat_max_ns":     f"{metrics['lat_max_ns']:.3f}" if metrics["lat_max_ns"] >= 0 else "?",
        "interval":       metrics["interval"],
        "slack":          f"{metrics['slack']:.2f}" if metrics["slack"] >= 0 else "?",
        "bram":           metrics["bram"],
        "dsp":            metrics["dsp"],
        "ff":             metrics["ff"],
        "lut":            metrics["lut"],
        "uram":           metrics["uram"],
        "pipelined":      metrics["pipelined"],
    }

    # Human-readable summary
    lat_ms = metrics["lat_max_ns"] / 1e6 if metrics["lat_max_ns"] >= 0 else 0
    print(
        f"[{label}]  {metrics['top_module']}  lat={metrics['lat_max_cyc']} cyc ({lat_ms:.3f} ms)\n"
        f"  interval: {metrics['interval']}  slack: {metrics['slack']}\n"
        f"  pipelined: {metrics['pipelined']}\n"
        f"  resources:  DSP={metrics['dsp']} FF={metrics['ff']} LUT={metrics['lut']} BRAM={metrics['bram']} URAM={metrics['uram']}"
    )

    # CSV append
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        w.writerow(row)
    print(f"  → appended to {csv_path}")

    return row


if __name__ == "__main__":
    main()
