#!/usr/bin/env python3
# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""Make a newer runtime's timer file readable by the AI Analyzer SDK.

``record_timer_subgraph_cpu_ts.json`` is where the SDK finds a row's partition
executions. Runs up to CSR_55392 wrote it as one JSON object. Newer flexmlrt
builds write a **list** holding the same recording twice, once under ``schema``
2.0 and once under 3.0.

dlanalyzer 1.8.7+ ``MLProfilerEngine`` already accepts that list and selects
schema ``"2.0"`` (same as ``PREFERRED_SCHEMA`` here). Older SDKs still treat a
list as having no partition executions. Independently, SDK report discovery
uses ``os.walk`` and does not follow directory symlinks (``report_store.py``).

The shadow is still required: a real directory tree whose payloads are file
symlinks, with this one timer rewritten as a single object. That lets
discovery recurse into compile artifacts without copying a large cache, and
keeps older SDKs from seeing a list. It is done in scratch, never in the run
tree.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import cast

TIMER_NAME = "record_timer_subgraph_cpu_ts.json"
PREFERRED_SCHEMA = "2.0"


def _executions(session: dict[str, object]) -> list[object]:
    value = session.get("execution")
    return value if isinstance(value, list) else []


def select_session(payload: object) -> dict[str, object]:
    if isinstance(payload, dict):
        return cast(dict[str, object], payload)
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{TIMER_NAME} holds no usable session")
    usable = [s for s in payload if _executions(s)] or list(payload)
    for schema in (PREFERRED_SCHEMA, "3.0"):
        for session in usable:
            if session.get("schema") == schema:
                return cast(dict[str, object], session)
    return cast(dict[str, object], usable[0])


def _symlink_file(src: str, dst: str) -> str:
    Path(dst).symlink_to(Path(src).resolve())
    return dst


def _selected_cache_dirs(session: dict[str, object], work_dir: Path) -> set[Path]:
    contexts = session.get("context_init")
    if not isinstance(contexts, list):
        return set()
    root = work_dir.resolve()
    selected: set[Path] = set()
    for context in contexts:
        if not isinstance(context, dict):
            continue
        raw_path = context.get("subgraph_path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        subgraph = Path(raw_path)
        if not subgraph.is_absolute():
            subgraph = work_dir / subgraph
        if not subgraph.name.startswith("vaiml_par_"):
            continue
        cache = subgraph.parent.resolve()
        try:
            cache.relative_to(root)
        except ValueError:
            continue
        if cache.is_dir():
            selected.add(cache)
    return selected


def readable_work_dir(work_dir: Path, shadow_root: Path | None = None) -> Path:
    """Return a directory the SDK can read, shadowing a list timer when needed.

    A list payload is still shadowed even when this SDK can parse it: the
    shadow is a file-symlink tree so ``os.walk`` can see reports under the
    compile cache (directory symlinks are not followed), and older SDKs still
    need a single-object timer.
    """
    timer = work_dir / TIMER_NAME
    if not timer.is_file():
        return work_dir
    payload = json.loads(timer.read_text())
    if isinstance(payload, dict):
        return work_dir
    session = select_session(payload)
    shadow = shadow_root or work_dir.parent / ".ai_analyzer_timer_shadow"
    shadow.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(work_dir.resolve()).encode()).hexdigest()[:16]
    out = shadow / key
    if out.exists():
        shutil.rmtree(out)
    resolved_shadow = shadow.resolve()
    if resolved_shadow == work_dir.resolve():
        raise ValueError("shadow root must not be the timing work directory")
    selected_caches = _selected_cache_dirs(session, work_dir)

    def ignore_shadow(directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if (Path(directory) / name).resolve() == resolved_shadow
            or (
                selected_caches
                and name == "cache"
                and (Path(directory) / name).resolve() not in selected_caches
            )
        }

    shutil.copytree(
        work_dir,
        out,
        copy_function=_symlink_file,
        ignore=ignore_shadow,
    )
    normalized_timer = out / TIMER_NAME
    normalized_timer.unlink()
    normalized_timer.write_text(json.dumps(session, indent=2))
    return out
