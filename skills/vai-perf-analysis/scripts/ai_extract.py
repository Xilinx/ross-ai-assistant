#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""Extract performance data from a VAIML run directory using the AI Analyzer SDK.

Requires the product virtual environment to be sourced before running.

Usage:
    python3 ai_extract.py <run_dir> [--compact] [--query PAT] [--top N] [--human] [-o FILE]

Examples:
    # Human-readable full summary
    python3 ai_extract.py /path/to/run --compact --human

    # Top 20 slowest individual nodes
    python3 ai_extract.py /path/to/run --top 20 --human

    # Custom ops only
    python3 ai_extract.py /path/to/run --query "mydomain" --human

    # Human to terminal + JSON to file
    python3 ai_extract.py /path/to/run --compact --human -o /tmp/perf.json
"""

import argparse
import json
import os
import random
import sys
import traceback

# Remove the script's own directory from sys.path to prevent any local
# modules from shadowing real packages (e.g. pandas needs 'bottleneck').
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir in sys.path:
    sys.path.remove(_script_dir)


# ---------------------------------------------------------------------------
# SDK extraction
# ---------------------------------------------------------------------------

# Fields pulled off each PerformanceSummary, as (name, default). The name is
# both the output key and the suffix of the SDK getter (get_<name>()).
SUMMARY_FIELDS = [
    ("model_name", ""),
    ("inference_count", 0),
    ("batch_count", 0),
    ("clock_freq_mhz", 0),
    ("average_inference_time_usec", 0),
    ("min_inference_time_usec", 0),
    ("max_inference_time_usec", 0),
    ("average_npu_time_usec", 0),
    ("average_execution_provider_time_usec", 0),
    ("npu_running_percent", 0),
    ("total_gmacs", 0),
]


def extract_performance_data(run_dir):
    """Extract all available performance data from run_dir using dlanalyzer SDK."""
    from dlanalyzer import sdk

    result = {
        "run_dir": os.path.abspath(run_dir),
        "status": "ok",
        "errors": [],
    }

    # Start the SDK server on a random high port. Try two port ranges in case
    # the first is in use; surface a clear error if both fail so the caller
    # doesn't get a confusing downstream open_folder failure.
    sdk_started = False
    last_err = None
    for lo, hi in [(18200, 19200), (19201, 20200)]:
        try:
            sdk.open(port=random.randint(lo, hi))
            sdk_started = True
            break
        except Exception as e:
            last_err = e
    if not sdk_started:
        result["errors"].append(f"sdk.open failed in both port ranges: {last_err}")
        result["status"] = "error"
        return result

    try:
        resp = sdk.open_folder(run_dir)
        if not resp.success:
            result["errors"].append(f"open_folder failed: {resp.status_message}")
            result["status"] = "error"
            return result
    except Exception as e:
        result["errors"].append(f"open_folder failed: {e}")
        result["status"] = "error"
        return result

    # 1. Total execution time
    try:
        resp = sdk.get_total_execution_time(run_dir)
        if resp.success and resp.data is not None:
            result["total_execution_time_us"] = resp.data.total_seconds() * 1e6
        else:
            result["errors"].append(f"get_total_execution_time: {resp.status_message}")
    except Exception as e:
        result["errors"].append(f"get_total_execution_time: {e}")

    # 2. Performance metrics (summary + operators)
    try:
        # Renamed in dlanalyzer: get_performance_metrics -> get_performance_summary.
        # Same SdkResponse[PerformanceMetricsResult] (.summary / .operators).
        resp = sdk.get_performance_summary(run_dir)
        if resp.success and resp.data is not None:
            metrics = resp.data
            if hasattr(metrics, "summary") and metrics.summary is not None:
                recs = metrics.summary.to_dict(orient="records")
                for r in recs:
                    r.pop("report_id", None)
                result["performance_summary"] = recs
            if hasattr(metrics, "operators") and metrics.operators is not None:
                recs = metrics.operators.to_dict(orient="records")
                for r in recs:
                    r.pop("report_id", None)
                result["operator_metrics"] = recs
        else:
            result["errors"].append(f"get_performance_summary: {resp.status_message}")
    except Exception as e:
        result["errors"].append(f"get_performance_summary: {e}")

    # 3. Timing dataframe (per-layer/per-op timing)
    try:
        resp = sdk.get_timing_dataframe(run_dir)
        if resp.success and resp.data is not None:
            df = resp.data
            for col in [
                "Execution Time.us",
                "Start Time (us)",
                "End Time (us)",
                "Execution Time.Cycles",
                "Start Cycle",
                "End Cycle",
            ]:
                if col in df.columns:
                    df[col] = df[col].apply(
                        lambda x: float(x) if x and str(x).strip() else 0.0
                    )
            records = df.to_dict(orient="records")
            for r in records:
                r.pop("folder_path", None)
                r.pop("report_id", None)
            result["timing_data"] = records
        else:
            result["errors"].append(f"get_timing_dataframe: {resp.status_message}")
    except Exception as e:
        result["errors"].append(f"get_timing_dataframe: {e}")

    # 4. Per-model summary. The old sdk.get_summary_data(run_dir) is gone;
    #    the replacement is a *view accessor* that takes the response from
    #    get_performance_summary and returns PerformanceSummary objects whose
    #    fields are getter methods (get_average_npu_time_usec(), ...).
    try:
        resp = sdk.get_performance_summary(run_dir)
        if resp.success:
            summaries = sdk.get_performance_summaries(resp)

            def g(s, name, default):
                fn = getattr(s, f"get_{name}", None)
                if fn is None:
                    return default
                try:
                    v = fn()
                except Exception:
                    return default
                return default if v is None else v

            result["summary_data"] = [
                {f: g(s, f, d) for f, d in SUMMARY_FIELDS}
                for s in summaries.get_summaries()
            ]
        else:
            result["errors"].append(f"get_performance_summaries: {resp.status_message}")
    except Exception as e:
        result["errors"].append(f"get_performance_summaries: {e}")

    # Cleanup
    try:
        sdk.close_folder(run_dir)
    except Exception:
        pass
    try:
        sdk.close()
    except Exception:
        pass

    if not result["errors"]:
        del result["errors"]

    return result


# ---------------------------------------------------------------------------
# Output modes
# ---------------------------------------------------------------------------


def compact_output(data):
    """Compact summary without per-layer timing_data."""
    compact = {
        "run_dir": data.get("run_dir", ""),
        "status": data.get("status", ""),
        "total_execution_time_us": data.get("total_execution_time_us"),
    }
    if "errors" in data:
        compact["errors"] = data["errors"]
    if "performance_summary" in data:
        compact["performance_summary"] = data["performance_summary"]
    if "operator_metrics" in data:
        compact["operator_metrics"] = data["operator_metrics"]
    if "summary_data" in data:
        stripped = []
        for s in data["summary_data"]:
            stripped.append({k: v for k, v in s.items() if k != "npuOperatorBreakdown"})
        compact["summary_data"] = stripped
    if "timing_data" in data:
        td = data["timing_data"]
        npu = [t for t in td if t.get("timeline") != "CPU"]
        compact["timing_data_summary"] = {
            "total_entries": len(td),
            "npu_entries": len(npu),
            "cpu_entries": len(td) - len(npu),
        }
    return compact


def query_data(data, query_filter):
    """Filter timing_data entries by name/type pattern."""
    import re

    results = []
    for t in data.get("timing_data", []):
        name = str(t.get("Name", "") or "")
        op_type = str(t.get("Type", "") or "")
        try:
            hit = re.search(query_filter, name, re.IGNORECASE) or re.search(
                query_filter, op_type, re.IGNORECASE
            )
        except re.error:
            hit = (
                query_filter.lower() in name.lower()
                or query_filter.lower() in op_type.lower()
            )
        if hit:
            results.append(
                {
                    "timeline": t.get("timeline", ""),
                    "Name": name,
                    "Type": op_type,
                    "Execution Time.us": t.get("Execution Time.us", 0),
                    "Execution Time.Cycles": t.get("Execution Time.Cycles", 0),
                    "Start Time (us)": t.get("Start Time (us)", 0),
                    "End Time (us)": t.get("End Time (us)", 0),
                }
            )
    total_us = sum(float(r.get("Execution Time.us", 0) or 0) for r in results)
    return {
        "query": query_filter,
        "matches": len(results),
        "total_execution_time_us": total_us,
        "entries": results,
    }


def top_n_data(data, n, clock_mhz):
    """Return the top-N slowest individual NPU nodes by cycle count."""
    if n <= 0:
        raise ValueError(f"top_n_data: n must be > 0, got {n}")

    def _cycles(t):
        return float(t.get("Execution Time.Cycles", 0) or 0)

    npu = [t for t in data.get("timing_data", []) if t.get("timeline") != "CPU"]
    # Sort without mutating caller-owned dicts (us field is often 0 for NPU entries).
    npu_sorted = sorted(npu, key=_cycles, reverse=True)
    entries = []
    for t in npu_sorted[:n]:
        cycles = _cycles(t)
        entries.append(
            {
                "Name": str(t.get("Name", "") or ""),
                "Type": str(t.get("Type", "") or ""),
                "Execution Time.Cycles": cycles,
                "Approx Time.us": cycles / clock_mhz if clock_mhz > 0 else 0,
            }
        )
    total_cycles = sum(_cycles(t) for t in npu)
    top_cycles = sum(e["Execution Time.Cycles"] for e in entries)
    return {
        "top": n,
        "shown": len(entries),
        "total_npu_entries": len(npu),
        "top_cycles": top_cycles,
        "total_npu_cycles": total_cycles,
        "top_pct": (top_cycles / total_cycles * 100) if total_cycles > 0 else 0,
        "clock_mhz": clock_mhz,
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# Human-readable formatters
# ---------------------------------------------------------------------------


def _fmt_us(us):
    """Format microseconds into the most readable unit."""
    if us <= 0:
        return "0"
    if us < 1:
        return f"{us * 1000:.0f} ns"
    if us < 1000:
        return f"{us:.1f} us"
    if us < 1_000_000:
        return f"{us / 1000:.2f} ms"
    return f"{us / 1_000_000:.3f} s"


def format_human_report(data):
    """Format extracted data as a human-readable text report."""
    W = 78
    lines = []
    lines.append("=" * W)
    lines.append("  VAIML Performance Report (AI Analyzer)")
    lines.append("=" * W)
    lines.append(f"  Run directory: {data.get('run_dir', '?')}")

    total_us = data.get("total_execution_time_us", 0) or 0
    perf_summary = data.get("performance_summary", []) or []

    # Lead with NPU Compute -- the optimization target.
    # The host-side "Kernel Launch Window" (in the host profiling block below)
    # also includes DMA setup/teardown and runtime sync, so it is NOT the
    # number to optimize for kernel or custom-op work.
    for s in perf_summary:
        npu_us = s.get("average_npu_time_usec", 0) or 0
        inf_us = s.get("average_inference_time_usec", 0) or 0
        npu_pct = (npu_us / inf_us * 100) if inf_us > 0 else 0
        lines.append(
            f"  NPU Compute (target): {_fmt_us(npu_us)}"
            f" ({npu_pct:.1f}% of inference)"
        )
        inf_count = s.get("inference_count", 0) or 0
        per_inf_us = (total_us / inf_count) if inf_count else total_us
        lines.append(f"  Total inference time: {_fmt_us(per_inf_us)} (per inference)")
        lines.append(
            f"  Total time (all runs): {_fmt_us(total_us)}"
            f" across {inf_count} inferences"
        )
        lines.append(f"  Clock frequency:      {s.get('clock_freq_mhz', '?')} MHz")

    if not perf_summary:
        lines.append(f"  Total time (all runs): {_fmt_us(total_us)}")

    # Host profiling
    host_ops = [
        o
        for o in data.get("operator_metrics", [])
        if o.get("category") == "host_profiling"
    ]
    if host_ops:
        lines.append("")
        lines.append("--- Host Profiling Breakdown ---")
        lines.append(f"  {'Phase':<45} {'Time':>10} {'%':>8}")
        lines.append(f"  {'-' * 45} {'-' * 10} {'-' * 8}")
        for o in host_ops:
            display_name = o["name"]
            if display_name == "Kernel Execution":
                display_name = "Kernel Launch Window (host view)"
            lines.append(
                f"  {display_name:<45} {_fmt_us(o['value_usec']):>10}"
                f" {o.get('percent', ''):>8}"
            )

    # NPU operators
    npu_ops = [
        o for o in data.get("operator_metrics", []) if o.get("category") == "npu"
    ]
    if npu_ops:
        lines.append("")
        lines.append("--- NPU Operator Breakdown ---")
        lines.append(
            f"  {'Operator':<40} {'Count':>5} {'Total':>10}" f" {'%':>8} {'Avg/op':>10}"
        )
        lines.append(f"  {'-' * 40} {'-' * 5} {'-' * 10} {'-' * 8} {'-' * 10}")
        for o in npu_ops:
            name = o.get("name", "")
            count = o.get("count", 1) or 1
            value_us = o.get("value_usec", 0) or 0
            avg_us = value_us / count
            lines.append(
                f"  {name:<40} {count:>5} {_fmt_us(value_us):>10}"
                f" {o.get('percent', ''):>8}"
                f" {_fmt_us(avg_us):>10}"
            )

    # Model stats
    for s in data.get("summary_data", []):
        gmacs = s.get("totalGmacs", 0) or 0
        if gmacs > 0:
            lines.append("")
            lines.append("--- Model Stats ---")
            lines.append(f"  Total GMACs:        {gmacs:.3f}")
            lines.append(f"  Available GMACs:    {s.get('availableGmacs', 0):.1f}")
            lines.append(f"  GMACs/sec:          {s.get('gmacsPerSecond', 0):,.1f}")

    if data.get("errors"):
        lines.append("")
        lines.append("--- Warnings ---")
        for e in data["errors"]:
            lines.append(f"  ! {e}")

    lines.append("")
    lines.append("=" * W)
    return "\n".join(lines)


def format_human_query(query_result, clock_mhz=1800):
    """Format query results as human-readable text."""
    W = 78
    lines = []
    lines.append("=" * W)
    lines.append(f"  Query: \"{query_result['query']}\"")
    lines.append(f"  Matches: {query_result['matches']}")
    lines.append("=" * W)

    entries = query_result.get("entries", [])
    if not entries:
        lines.append("  No matching entries found.")
        lines.append("=" * W)
        return "\n".join(lines)

    lines.append(f"  {'Name':<35} {'Type':<30} {'Cycles':>10} {'~Time':>10}")
    lines.append(f"  {'-' * 35} {'-' * 30} {'-' * 10} {'-' * 10}")
    total_cycles = 0
    for e in entries:
        name = e.get("Name", "")
        if len(name) > 35:
            name = name[:32] + "..."
        op_type = e.get("Type", "")
        if len(op_type) > 30:
            op_type = op_type[:27] + "..."
        cycles = float(e.get("Execution Time.Cycles", 0) or 0)
        total_cycles += cycles
        approx_us = cycles / clock_mhz if clock_mhz > 0 else 0
        lines.append(
            f"  {name:<35} {op_type:<30} {cycles:>10,.0f}" f" {_fmt_us(approx_us):>10}"
        )

    total_approx_us = total_cycles / clock_mhz if clock_mhz > 0 else 0
    lines.append(f"  {'-' * 35} {'-' * 30} {'-' * 10} {'-' * 10}")
    lines.append(
        f"  {'TOTAL':<35} {'':<30} {total_cycles:>10,.0f}"
        f" {_fmt_us(total_approx_us):>10}"
    )
    lines.append("")
    lines.append(f"  (Times estimated from cycles at {clock_mhz} MHz)")
    lines.append("=" * W)
    return "\n".join(lines)


def format_human_top(top_result):
    """Format top-N results as human-readable text."""
    W = 78
    clock_mhz = top_result.get("clock_mhz", 1800)
    lines = []
    lines.append("=" * W)
    lines.append(
        f"  Top {top_result['shown']} slowest NPU nodes"
        f" (of {top_result['total_npu_entries']} total)"
    )
    lines.append("=" * W)

    entries = top_result.get("entries", [])
    if not entries:
        lines.append("  No NPU timing entries found.")
        lines.append("=" * W)
        return "\n".join(lines)

    lines.append(f"  {'#':>3} {'Name':<35} {'Type':<22} {'Cycles':>10} {'~Time':>10}")
    lines.append(f"  {'-' * 3} {'-' * 35} {'-' * 22} {'-' * 10} {'-' * 10}")
    for i, e in enumerate(entries, 1):
        name = e.get("Name", "")
        if len(name) > 35:
            name = name[:32] + "..."
        op_type = e.get("Type", "")
        if len(op_type) > 22:
            op_type = op_type[:19] + "..."
        cycles = e.get("Execution Time.Cycles", 0)
        approx_us = e.get("Approx Time.us", 0)
        lines.append(
            f"  {i:>3} {name:<35} {op_type:<22} {cycles:>10,.0f}"
            f" {_fmt_us(approx_us):>10}"
        )

    top_cycles = top_result.get("top_cycles", 0)
    total_cycles = top_result.get("total_npu_cycles", 0)
    top_us = top_cycles / clock_mhz if clock_mhz > 0 else 0
    total_us = total_cycles / clock_mhz if clock_mhz > 0 else 0
    lines.append(f"  {'-' * 3} {'-' * 35} {'-' * 22} {'-' * 10} {'-' * 10}")
    lines.append(
        f"  {'':>3} {'TOP TOTAL':<35} {'':<22} {top_cycles:>10,.0f}"
        f" {_fmt_us(top_us):>10}"
    )
    lines.append(
        f"  {'':>3} {'ALL NPU':<35} {'':<22} {total_cycles:>10,.0f}"
        f" {_fmt_us(total_us):>10}"
    )
    pct = top_result.get("top_pct", 0)
    lines.append(
        f"\n  Top {top_result['shown']} nodes account for"
        f" {pct:.1f}% of total NPU cycles"
    )
    lines.append(f"  (Times estimated from cycles at {clock_mhz} MHz)")
    lines.append("=" * W)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Extract VAIML performance data using AI Analyzer SDK",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  %(prog)s /path/to/run --compact --human          Full summary
  %(prog)s /path/to/run --top 20 --human           Top 20 slowest nodes
  %(prog)s /path/to/run --query "mydomain" --human  Custom ops only
  %(prog)s /path/to/run --query "Conv" --human      Convolution layers
  %(prog)s /path/to/run --compact --human -o p.json  Human + JSON""",
    )
    parser.add_argument("run_dir", help="Directory with profiling outputs")
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Write JSON to this file (human-readable still goes to stdout)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Compact summary (aggregated metrics, no per-layer data)",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Filter timing entries by name/type pattern (regex or substring)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        metavar="N",
        help="Show top N slowest individual NPU nodes (must be a positive integer)",
    )
    parser.add_argument(
        "--human",
        action="store_true",
        help="Human-readable output to stdout (combine with -o for JSON too)",
    )
    args = parser.parse_args()

    if args.top is not None and args.top <= 0:
        parser.error(f"--top must be a positive integer, got {args.top}")

    if not os.path.isdir(args.run_dir):
        print(f"Error: {args.run_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    try:
        data = extract_performance_data(args.run_dir)
    except ImportError as e:
        print(
            f"Error: dlanalyzer SDK not available ({e}). "
            "Enable the Ryzen AI virtual environment first.",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"Error extracting performance data: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    # Determine clock freq
    clock_mhz = 1800
    for s in data.get("performance_summary", []):
        if s.get("clock_freq_mhz"):
            clock_mhz = s["clock_freq_mhz"]
            break

    # Build output
    if args.top is not None:
        output = top_n_data(data, args.top, clock_mhz)
    elif args.query:
        output = query_data(data, args.query)
    elif args.compact:
        output = compact_output(data)
    else:
        output = data

    output_json = json.dumps(output, indent=2, default=str)

    if args.human:
        if args.top is not None:
            print(format_human_top(output))
        elif args.query:
            print(format_human_query(output, clock_mhz))
        else:
            print(format_human_report(output))
        if args.output:
            with open(args.output, "w") as f:
                f.write(output_json)
            print(f"\nJSON written to {args.output}", file=sys.stderr)
    elif args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
