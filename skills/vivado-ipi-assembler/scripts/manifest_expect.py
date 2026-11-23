#!/usr/bin/env python3
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Convert a build manifest into the Tcl digest consumed by reconcile_spec.

The accepted manifest section is either the document root or ``reconcile``:

    cells: mapping of cell name to required count
    domains: [{name, mhz, reset, wizard}, ...]
    edges: [[source_cell, destination_cell], ...]
    windows: [{name, size, count}, ...]
    out_of_scope: [cell_prefix, ...]

The aliases documented in the normalization functions below are accepted for
compatibility with DesignSpec-derived manifest producers. Output is data, not
Tcl code, and every field is restricted to a safe Tcl-list token.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    """The manifest cannot be represented by the reconciliation contract."""


SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_./:+@-]+$")


def token(value: Any, path: str) -> str:
    if value is None or value == "":
        return "{}"
    text = str(value)
    if not SAFE_TOKEN.fullmatch(text):
        raise ManifestError(f"{path}: unsafe or whitespace-containing token {text!r}")
    return text


def required_token(value: Any, path: str) -> str:
    if value is None or value == "":
        raise ManifestError(f"{path}: required non-empty token")
    return token(value, path)


def positive_count(value: Any, path: str) -> int:
    if isinstance(value, bool):
        raise ManifestError(f"{path}: count must be a positive integer")
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"{path}: count must be a positive integer") from exc
    if count < 1 or str(count) != str(value):
        raise ManifestError(f"{path}: count must be a positive integer")
    return count


def normalize_cells(raw: Any) -> list[str]:
    result: list[str] = []
    if isinstance(raw, dict):
        items = sorted(raw.items())
    elif isinstance(raw, list):
        items = []
        for index, item in enumerate(raw):
            if isinstance(item, str):
                items.append((item, 1))
            elif isinstance(item, dict):
                name = item.get("name", item.get("cell"))
                items.append((name, item.get("count", item.get("instances", 1))))
            else:
                raise ManifestError(f"cells[{index}]: expected string or object")
    else:
        raise ManifestError("cells: expected object or list")
    for index, (name, count) in enumerate(items):
        result.extend(
            (
                required_token(name, f"cells[{index}].name"),
                str(positive_count(count, f"cells[{index}].count")),
            )
        )
    return result


def normalize_domains(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ManifestError("domains: expected list")
    result: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ManifestError(f"domains[{index}]: expected object")
        result.extend(
            (
                required_token(item.get("name"), f"domains[{index}].name"),
                required_token(
                    item.get("mhz", item.get("clock_mhz")),
                    f"domains[{index}].mhz",
                ),
                token(
                    item.get("reset", item.get("reset_cell", "")),
                    f"domains[{index}].reset",
                ),
                token(
                    item.get("wizard", item.get("clock_wizard", "")),
                    f"domains[{index}].wizard",
                ),
            )
        )
    return result


def normalize_edges(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ManifestError("edges: expected list")
    result: list[str] = []
    for index, item in enumerate(raw):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            source, destination = item
        elif isinstance(item, dict):
            source = item.get("source", item.get("from"))
            destination = item.get(
                "destination", item.get("target", item.get("to"))
            )
        else:
            raise ManifestError(f"edges[{index}]: expected pair or object")
        result.extend(
            (
                required_token(source, f"edges[{index}].source"),
                required_token(destination, f"edges[{index}].destination"),
            )
        )
    return result


def normalize_windows(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ManifestError("windows: expected list")
    result: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ManifestError(f"windows[{index}]: expected object")
        result.extend(
            (
                required_token(item.get("name"), f"windows[{index}].name"),
                required_token(
                    item.get("size", item.get("range")),
                    f"windows[{index}].size",
                ),
                str(
                    positive_count(
                        item.get("count", item.get("instances", 1)),
                        f"windows[{index}].count",
                    )
                ),
            )
        )
    return result


def normalize_oos(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        raise ManifestError("out_of_scope: expected list")
    result: list[str] = []
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            item = item.get("name", item.get("cell"))
        result.append(required_token(item, f"out_of_scope[{index}]"))
    return result


def digest(manifest: Any) -> dict[str, list[str]]:
    if not isinstance(manifest, dict):
        raise ManifestError("manifest root must be an object")
    section = manifest.get("reconcile", manifest)
    if not isinstance(section, dict):
        raise ManifestError("reconcile must be an object")
    return {
        "cells": normalize_cells(section.get("cells", {})),
        "domains": normalize_domains(
            section.get("domains", section.get("clock_domains", []))
        ),
        "edges": normalize_edges(
            section.get("edges", section.get("required_edges", []))
        ),
        "windows": normalize_windows(
            section.get("windows", section.get("address_windows", []))
        ),
        "oos": normalize_oos(
            section.get(
                "out_of_scope",
                section.get("out_of_scope_blocks", section.get("oos", [])),
            )
        ),
    }


def emit_tcl_digest(values: dict[str, list[str]]) -> str:
    return "\n".join(
        f"{name} {{{' '.join(values[name])}}}"
        for name in ("cells", "domains", "edges", "windows", "oos")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text())
        print(emit_tcl_digest(digest(manifest)))
    except (OSError, json.JSONDecodeError, ManifestError) as exc:
        print(f"MANIFEST_EXPECT_FAIL:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
