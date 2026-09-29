#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

import shlex
from typing import Optional


def get_cfg_dict(cfg_content: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    current_section: str | None = None
    for raw_line in cfg_content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        key, value = (s.strip() for s in line.split("=", 1))
        full_key = f"{current_section}.{key}" if current_section else key
        result.setdefault(full_key, []).append(value)
    return result


def _get_i_values_from_cflags(cflags: str) -> list[str]:
    try:
        tokens = shlex.split(cflags)
    except ValueError:
        return []
    minus_i = "-I"
    minus_i_len = len(minus_i)
    return [
        value
        for value in [
            (
                tokens[index]
                if index > 0 and tokens[index - 1] == minus_i
                else (
                    tokens[index][minus_i_len:]
                    if tokens[index].startswith(minus_i) and tokens[index] != minus_i
                    else None
                )
            )
            for index in range(len(tokens))
        ]
        if value
    ]


def get_hls_include_paths(cfg_dict: dict[str, list[str]]) -> list[str]:
    return _deduplicate(
        [
            include_path
            for cflags in [
                *_get_values(
                    cfg_dict, "hls.syn.cflags", "hls.syn.csimflags", "hls.tb.cflags"
                ),
                *[
                    file_cflags[file_cflags.find(",") + 1 :]
                    for file_cflags in _get_values(
                        cfg_dict,
                        "hls.syn.file_cflags",
                        "hls.syn.file_csimflags",
                        "hls.tb.file_cflags",
                    )
                    if "," in file_cflags
                ],
            ]
            for include_path in _get_i_values_from_cflags(cflags)
        ]
    )


def get_hls_source_file_paths(cfg_dict: dict[str, list[str]]) -> list[str]:
    return _deduplicate(
        [*_get_hls_syn_file(cfg_dict), *_get_hls_syn_blackbox_file(cfg_dict)]
    )


def get_hls_testbench_file_paths(cfg_dict: dict[str, list[str]]) -> list[str]:
    return _deduplicate(_get_hls_tb_file(cfg_dict))


def get_hls_top(cfg_dict: dict[str, list[str]]) -> str:
    return _get_hls_syn_top(cfg_dict) or ""


def get_hls_relative_roots(cfg_dict: dict[str, list[str]]) -> list[str]:
    relative_roots = _deduplicate(
        [
            root.strip()
            for roots in _get_hls_relative_roots(cfg_dict)
            for root in roots.split(";")
            if root.strip()
        ]
    )
    return (
        (relative_roots if "cwd" in relative_roots else [*relative_roots, "cwd"])
        if relative_roots
        else ["file", "cwd"]
    )


def _get_hls_relative_roots(cfg_dict: dict[str, list[str]]) -> list[str]:
    return _get_values(cfg_dict, "hls.relative_roots")


def _get_hls_syn_top(cfg_dict: dict[str, list[str]]) -> Optional[str]:
    return _get_first_value(cfg_dict, "hls.syn.top")


def _get_hls_syn_file(cfg_dict: dict[str, list[str]]) -> list[str]:
    return _get_values(cfg_dict, "hls.syn.file")


def _get_hls_syn_blackbox_file(cfg_dict: dict[str, list[str]]) -> list[str]:
    return _get_values(cfg_dict, "hls.syn.blackbox.file")


def _get_hls_tb_file(cfg_dict: dict[str, list[str]]) -> list[str]:
    return _get_values(cfg_dict, "hls.tb.file")


def _get_first_value(cfg_dict: dict[str, list[str]], key: str) -> Optional[str]:
    return next(iter(cfg_dict.get(key, [])), None)


def _get_values(cfg_dict: dict[str, list[str]], *keys: str) -> list[str]:
    return [value for key in keys for value in cfg_dict.get(key, [])]


def _deduplicate(strs: list[str]) -> list[str]:
    return list(dict.fromkeys(strs))
