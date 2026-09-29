#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""Board timing from the AI Analyzer SDK — do not parse raw timer JSON by hand."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize_timer_json  # noqa: E402

EXEC_US = "Execution Time.\u03bcs"
BOARD_RE = re.compile(r"\|\s*Inference\s*\|\s*([0-9.]+)\s*ms/batch", re.IGNORECASE)
DUR_RE = re.compile(r"([\d.]+)\s*(ns|us|ms|s)")


def parse_dur_ms(raw: str) -> float:
    m = DUR_RE.match(str(raw).strip())
    if not m:
        return float(raw)
    v, u = float(m.group(1)), m.group(2)
    if u == "ns":
        return v / 1e6
    if u == "us":
        return v / 1e3
    if u == "s":
        return v * 1e3
    return v


def ms_to_usec(ms: float) -> float:
    return round(float(ms) * 1000.0, 3)


def vaip_partition_medians(work_dir: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for path in sorted(work_dir.glob("record_timer_vaip_vaiml_par_*.json")):
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            continue
        durs = [parse_dur_ms(e["duration"]) for e in data if "duration" in e]
        if durs:
            par = path.stem.replace("record_timer_vaip_", "")
            out[par] = statistics.median(durs)
    return out


def board_ms_from_log(work_dir: Path) -> float | None:
    log = work_dir / "board.log"
    if not log.is_file():
        return None
    hits = [
        float(m.group(1)) for m in BOARD_RE.finditer(log.read_text(errors="replace"))
    ]
    return hits[0] if len(hits) == 1 else (min(hits) if hits else None)


def fetch_timing_frame(work_dir: Path) -> pd.DataFrame:
    from dlanalyzer.sdk.data_access.timing import (  # type: ignore[import-not-found]
        get_timing_dataframe_batch,
    )

    response = get_timing_dataframe_batch([str(work_dir)], 1)
    if not getattr(response, "success", False) or not getattr(response, "data", None):
        return pd.DataFrame()
    frames = [
        item.data
        for item in response.data.results
        if item.data is not None and not item.data.empty
    ]
    if not frames:
        return pd.DataFrame()
    out: pd.DataFrame = pd.concat(frames, ignore_index=True)
    return out


def metrics_from_frame(df: pd.DataFrame) -> dict[str, object]:
    """Per-inference timing from one SDK frame (see skill for the five traps)."""
    if df is None or df.empty or EXEC_US not in df.columns:
        return {
            "inference_ms": None,
            "npu_layer_ms": None,
            "pm_load_ms": None,
            "n_npu_layers": 0,
        }

    us = pd.to_numeric(df[EXEC_US], errors="coerce")
    name = df["Name"].astype(str).str.strip()
    is_inference = name.str.fullmatch(r"Inference \d+")
    is_partition = name.str.startswith("vaiml_par_")
    is_pm_load = name.str.match(r"PM Load\b")
    on_npu = df["timeline"].astype(str).str.startswith("vaiml_par_")

    inference_us = us[is_inference].dropna()

    def per_inference(mask: pd.Series) -> pd.Series:
        return us[mask].groupby(df.loc[mask, "inference_id"]).sum()

    layer_mask = on_npu & ~is_partition & ~is_inference & ~is_pm_load
    pm_mask = on_npu & is_pm_load
    layer_us = per_inference(layer_mask)
    pm_us = per_inference(pm_mask)

    out: dict[str, object] = {
        "inference_ms": None,
        "npu_layer_ms": None,
        "n_inferences": int(inference_us.size),
        "n_npu_layers": int(df.loc[layer_mask, "Name"].nunique()),
        "n_pm_loads": int(df.loc[pm_mask, "Name"].nunique()),
        "pm_load_ms": float(pm_us.mean()) / 1000.0 if pm_us.size else 0.0,
    }
    if inference_us.size:
        out["inference_ms"] = float(inference_us.mean()) / 1000.0
    if layer_us.size:
        out["npu_layer_ms"] = float(layer_us.mean()) / 1000.0
    npu_layer = out.get("npu_layer_ms")
    pm = out.get("pm_load_ms")
    npu = (float(npu_layer) if isinstance(npu_layer, (int, float)) else 0.0) + (
        float(pm) if isinstance(pm, (int, float)) else 0.0
    )
    out["npu_device_total_ms"] = round(float(npu), 3)
    return out


def cpu_host_rows(df: pd.DataFrame, top_n: int = 12) -> list[dict[str, object]]:
    """Top CPU-timeline rows by mean time — host bottlenecks including input quant."""
    if df is None or df.empty or EXEC_US not in df.columns:
        return []
    us = pd.to_numeric(df[EXEC_US], errors="coerce")
    on_cpu = df["timeline"].astype(str).eq("CPU")
    name = df["Name"].astype(str).str.strip()
    skip = name.str.fullmatch(r"Inference \d+") | name.str.startswith("vaiml_par_")
    mask = on_cpu & ~skip
    if not mask.any():
        return []
    frame = pd.DataFrame({"Name": name[mask], "us": us[mask]})
    grouped = frame.groupby("Name", as_index=False).agg(us=("us", "mean"))
    top = grouped.sort_values(by="us", ascending=False).head(top_n)
    total = float(top["us"].sum()) or 1.0
    return [
        {
            "name": row["Name"],
            "mean_ms": round(float(row["us"]) / 1000.0, 3),
            "share_pct": round(float(row["us"]) / total * 100.0, 1),
        }
        for _, row in top.iterrows()
    ]


def measure(work_dir: Path, shadow_root: Path | None = None) -> dict[str, object]:
    readable = normalize_timer_json.readable_work_dir(work_dir, shadow_root)
    out: dict[str, object] = {
        "work_dir": str(work_dir),
        "timer_normalized": readable != work_dir,
        "vaip_median_ms": vaip_partition_medians(work_dir),
        "board_ms": board_ms_from_log(work_dir),
    }
    try:
        df = fetch_timing_frame(readable)
        out["sdk_status"] = "OK" if not df.empty else "EMPTY"
        metrics = metrics_from_frame(df)
        out.update(metrics)
        out["cpu_host_rows"] = cpu_host_rows(df)
        # E2E is the SDK `Inference N` row. The VAIP medians and the CPU
        # `vaiml_par_*` wrapper row time the EP call only, so they miss the host
        # work around it and must stay cross-checks.
        e2e = metrics.get("inference_ms")
        npu = metrics.get("npu_device_total_ms")
        if isinstance(e2e, (int, float)) and isinstance(npu, (int, float)):
            e2e_ms = round(float(e2e), 3)
            out["board_e2e_ms"] = e2e_ms
            out["board_e2e_usec"] = ms_to_usec(e2e_ms)
            out["cpu_only_ms"] = round(e2e_ms - float(npu), 3)
    except Exception as exc:  # noqa: BLE001
        out["sdk_status"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("work_dir", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    ap.add_argument("--shadow-root", type=Path, default=None)
    args = ap.parse_args()
    result = measure(args.work_dir.resolve(), args.shadow_root)
    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text + "\n")
    else:
        print(text)
    return 0 if result.get("sdk_status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
