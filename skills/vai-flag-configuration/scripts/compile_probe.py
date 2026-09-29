#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""Compile or get_capability probe with structured, user-facing failure reports.

Usage:
  python3 compile_probe.py <model.onnx> --vitisai-config vitisai_config.json \\
    --mode capability|compile [--cache-dir DIR] [-o probe.json]

Fast mode (`capability`): FlexML ``get_capability_v4`` when the shipped
``flexml`` Python package (native ``pyflexmlcompile``) is importable — typical in
the official Vitis AI Docker image and product virtualenv. Falls back to full ORT
compile with an explicit ``fallback`` note in ``probe.json`` when ``flexml`` is
missing (e.g. a skill-only venv with just ``onnx``).

Complete mode (`compile`): ORT VitisAI EP compile via sibling ``compile.py``
(full partition + AIE compile).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

SUMMARY_GOPS_RE = re.compile(
    r"GOPs (?:supported|offloaded) by VAIML:\s*([\d.]+)\s*\(([\d.]+)%\)"
)
THRESHOLD_RE = re.compile(
    r"Number of subgraphs below (\d+)% GOPs threshold \(fall back to CPU\):\s*(\d+)"
)
ERROR_LINE_RE = re.compile(r"^(ERROR|FATAL|error:|\[error\])", re.I)


def _load_compile_module() -> Any:
    here = Path(__file__).resolve().parent
    candidate = (
        here.parent.parent / "vai-custom-op-implementation" / "scripts" / "compile.py"
    )
    if not candidate.is_file():
        raise FileNotFoundError(
            "compile.py not found at ../../vai-custom-op-implementation/scripts/compile.py"
        )
    spec = importlib.util.spec_from_file_location("compile_mod", candidate)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tail_errors(path: Path, limit: int = 12) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(errors="replace").splitlines()
    hits = [ln.strip() for ln in lines if ERROR_LINE_RE.search(ln.strip())]
    return hits[-limit:]


def _read_summaries(cache_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("preliminary-vaiml-pass-summary.txt", "final-vaiml-pass-summary.txt"):
        p = cache_dir / "cache" / name
        if not p.is_file():
            p = cache_dir / name
        if not p.is_file():
            continue
        text = p.read_text(errors="replace")
        out[name] = text
        m = SUMMARY_GOPS_RE.search(text)
        if m:
            out[f"{name}_gops"] = float(m.group(1))
            out[f"{name}_gops_pct"] = float(m.group(2))
        t = THRESHOLD_RE.search(text)
        if t:
            out["threshold_gops_percent_seen"] = int(t.group(1))
            out["subgraphs_below_threshold"] = int(t.group(2))
    return out


def _read_get_capability_json(cache_dir: Path) -> dict[str, Any] | None:
    for p in sorted(cache_dir.rglob("get_capability_v5.json")):
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _unsupported_from_cache(cache_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for p in sorted(cache_dir.rglob("aie_unsupported_original_ops_with_reasons.json")):
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, list):
            for item in data[:20]:
                if isinstance(item, dict):
                    rows.append(
                        {
                            "op": str(item.get("op_type") or item.get("name") or ""),
                            "reason": str(
                                item.get("reason") or item.get("Message") or ""
                            ),
                        }
                    )
    return rows


def build_failure_report(
    *,
    mode: str,
    cache_dir: Path,
    exception: str | None,
    component_errors: list[str],
) -> dict[str, Any]:
    summaries = _read_summaries(cache_dir)
    cap = _read_get_capability_json(cache_dir)
    unsupported = _unsupported_from_cache(cache_dir)
    compile_log = cache_dir / "compile.log"
    log_errors = _tail_errors(compile_log)

    evidence: list[dict[str, str]] = []
    if exception:
        evidence.append({"source": "exception", "line": exception[:500]})
    for line in component_errors:
        evidence.append({"source": "component_scan", "line": line})
    for line in log_errors:
        evidence.append({"source": str(compile_log), "line": line})

    user_parts: list[str] = []
    actionable: list[str] = []

    if summaries.get("subgraphs_below_threshold"):
        user_parts.append(
            f"{summaries['subgraphs_below_threshold']} partition(s) fell below the "
            f"{summaries.get('threshold_gops_percent_seen', '?')}% GOPs threshold and "
            "stayed on CPU — not necessarily unsupported ops."
        )
        actionable.append(
            "Lower `threshold_gops_percent` in vaiml_config or inspect "
            "preliminary-vaiml-pass-summary.txt for the per-partition GOP split."
        )

    if unsupported:
        top = unsupported[0]
        user_parts.append(
            f"Unsupported on NPU: {top['op'] or 'unknown op'} — "
            f"{top['reason'][:200] or 'no reason string'}."
        )
        actionable.append(
            "Map the op/reason through offload_gap routes (fe/be/mllib/user-config)."
        )

    if component_errors:
        user_parts.append(
            "AIE compiler reported errors after the top level printed success — "
            "the subgraph likely fell back to CPU."
        )
        actionable.append("Open AIECompiler.log / aiecompiler-flexml.log under cache.")

    if exception and not user_parts:
        user_parts.append(f"Compile raised: {exception[:300]}")

    if mode == "capability" and cap is not None:
        user_parts.append("get_capability JSON is present — fast probe completed.")

    status = "success"
    if exception or component_errors:
        status = "failed"
    elif unsupported and mode == "capability":
        status = "capability_blocked"

    return {
        "status": status,
        "mode": mode,
        "user_message": " ".join(user_parts) if user_parts else "Compile finished.",
        "actionable": actionable,
        "evidence": evidence,
        "summaries": {
            k: v
            for k, v in summaries.items()
            if not k.endswith(".txt") and k != "preliminary-vaiml-pass-summary.txt"
        },
        "unsupported_ops_sample": unsupported[:5],
        "has_get_capability_json": cap is not None,
    }


def _vaiml_config_for_flexml_api(vaiml_config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *vaiml_config* with ``device`` normalized for flexml API.

    The Vitis AI EP accepts hyphenated IDs (``ve2-xc2ve3858``). The flexml native
    ``get_capability`` entry point expects flexml device tokens (``ve2``, ``stx``,
    …). ORT/VAIP may normalize EP strings on the compile path; this helper does
    the same for the fast capability path only.
    """
    out = dict(vaiml_config)
    device = str(out.get("device", "")).strip()
    if device.startswith("ve2-"):
        out["device"] = "ve2"
    elif device.lower() in {"t50", "t20"}:
        out["device"] = "ve2"
    return out


def _serialize_capability_result(result: Any) -> dict[str, Any]:
    unsupported = getattr(result, "unsupported_nodes", None)
    incompatible = getattr(result, "incompatible_map", None)
    if unsupported is not None:
        return {
            "unsupported_nodes": sorted(str(x) for x in unsupported),
            "incompatible_map": {
                str(k): [str(v) for v in vals]
                for k, vals in (incompatible or {}).items()
            },
        }
    if isinstance(result, (list, tuple, set)):
        return {"unsupported_nodes": sorted(str(x) for x in result)}
    return {"raw": str(result)}


def run_capability_flexml(model: Path, config: Path, cache_dir: Path) -> dict[str, Any]:
    import importlib

    try:
        dynamic = importlib.import_module("flexml.experimental.ext.dynamic")
    except ImportError:
        return {"available": False}

    import_dynamic = getattr(dynamic, "import_dynamic")

    flexml_dynamic_lib = import_dynamic()
    onnx_bytes = model.read_bytes()
    vaiml_config: dict[str, Any] = {}
    passes = json.loads(config.read_text()).get("passes", [])
    for p in passes:
        if p.get("plugin") == "vaip-pass_vaiml_partition":
            vaiml_config = p.get("vaiml_config", {})
            break

    cache_dir.mkdir(parents=True, exist_ok=True)
    flexml_config = _vaiml_config_for_flexml_api(vaiml_config)
    unsupported = flexml_dynamic_lib.get_capability_v4(
        flexml_dynamic_lib.context(),
        onnx_protobuf=onnx_bytes,
        onnx_external_data_dir=str(model.parent),
        output_dir=str(cache_dir),
        logging_level="info",
        config=json.dumps(flexml_config),
    )
    return {
        "available": True,
        "flexml_device": flexml_config.get("device"),
        "capability": _serialize_capability_result(unsupported),
    }


def run_compile(model: Path, config: Path, cache_dir: Path, keep_cache: bool) -> None:
    mod = _load_compile_module()
    mod.compile_vitisai(
        model_path=model,
        vitisai_config=config,
        cache_dir=cache_dir,
        keep_cache=keep_cache,
        analyze=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model", type=Path)
    ap.add_argument("--vitisai-config", type=Path, required=True)
    ap.add_argument(
        "--mode",
        choices=("capability", "compile"),
        default="compile",
        help="capability=get_capability FE probe; compile=full ORT compile",
    )
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--keep-cache", action="store_true")
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()

    cache_dir = args.cache_dir or Path(args.model.stem)
    result: dict[str, Any] = {
        "model": str(args.model.resolve()),
        "config": str(args.vitisai_config.resolve()),
        "cache_dir": str(cache_dir.resolve()),
        "mode": args.mode,
    }

    exc: str | None = None
    component_errors: list[str] = []
    try:
        if args.mode == "capability":
            cap = run_capability_flexml(args.model, args.vitisai_config, cache_dir)
            result["flexml_lite"] = cap
            if not cap.get("available"):
                result[
                    "fallback"
                ] = "flexml Python package not importable — running full ORT compile instead"
                run_compile(args.model, args.vitisai_config, cache_dir, args.keep_cache)
            else:
                result["status"] = "ok"
        else:
            run_compile(args.model, args.vitisai_config, cache_dir, args.keep_cache)
            result["status"] = "ok"
    except Exception as e:  # noqa: BLE001
        exc = f"{type(e).__name__}: {e}"
        result["status"] = "error"

    try:
        mod = _load_compile_module()
        component_errors = mod.scan_errors(cache_dir)
    except Exception:  # noqa: BLE001
        pass

    result["failure_report"] = build_failure_report(
        mode=args.mode,
        cache_dir=cache_dir,
        exception=exc,
        component_errors=component_errors,
    )
    if component_errors and result.get("status") == "ok":
        result["status"] = "error"
        result["failure_report"]["status"] = "failed"

    text = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(text + "\n")
    else:
        print(text)

    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
