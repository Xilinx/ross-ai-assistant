#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

import os
import sys

from pathlib import Path
from typing import Callable, Optional

from hls_common.file_path_util import join_and_normalize, is_absolute

from .component_basic_info_model import ComponentBasicInfo
from hls_common.vitis_comp_getter import (
    find_hls_cfg_file,
    get_component_name,
    get_vitis_comp,
)
from hls_common.file_util import read_file, path_exists
from hls_common.cfg_parser import (
    get_cfg_dict,
    get_hls_top,
    get_hls_include_paths,
    get_hls_source_file_paths,
    get_hls_testbench_file_paths,
    get_hls_relative_roots,
)


def get_component_basic_info(component_location: str) -> Optional[ComponentBasicInfo]:
    vitis_comp = get_vitis_comp(component_location)
    if not vitis_comp:
        return None
    hls_cfg_file = find_hls_cfg_file(component_location, vitis_comp)
    cfg_dict = get_cfg_dict(read_file(hls_cfg_file)) if hls_cfg_file else None
    relative_roots = (
        [
            component_location if relative_root == "cwd" else relative_root
            for relative_root in get_hls_relative_roots(cfg_dict)
        ]
        if cfg_dict
        else []
    )
    if hls_cfg_file:
        relative_roots = [
            (
                str(Path(hls_cfg_file).parent)
                if relative_root == "file"
                else relative_root
            )
            for relative_root in relative_roots
        ]
    return ComponentBasicInfo(
        component_name=get_component_name(vitis_comp),
        source_files=_get_path_values(
            cfg_dict, relative_roots, component_location, get_hls_source_file_paths
        ),
        testbench_files=_get_path_values(
            cfg_dict, relative_roots, component_location, get_hls_testbench_file_paths
        ),
        include_paths=_deduplicate(
            [
                *_get_path_values(
                    cfg_dict, relative_roots, component_location, get_hls_include_paths
                ),
                *_get_default_hls_include_paths(),
            ]
        ),
        top_function=get_hls_top(cfg_dict) if cfg_dict else "",
    )


def _get_default_hls_include_paths() -> list[str]:
    root = os.getenv("XILINX_VITIS")
    return (
        [
            join_and_normalize(root, "include"),
            join_and_normalize(root, "include", "etc"),
            join_and_normalize(
                root,
                "win64" if sys.platform.startswith("win") else "lnx64",
                "tools",
                "auto_cc",
                "include",
            ),
        ]
        if root and root.strip()
        else []
    )


def _get_path_values(
    cfg_dict: Optional[dict[str, list[str]]],
    roots: list[str],
    component_location: str,
    func: Callable[[dict[str, list[str]]], list[str]],
) -> list[str]:
    return (
        [_to_absolute_path(path, roots, component_location) for path in func(cfg_dict)]
        if cfg_dict
        else []
    )


def _to_absolute_path(path: str, roots: list[str], component_location: str) -> str:
    for root in roots:
        abs_path = join_and_normalize(root, path)
        if path_exists(abs_path):
            return abs_path
    return path if is_absolute(path) else join_and_normalize(component_location, path)


def _deduplicate(strs: list[str]) -> list[str]:
    return list(dict.fromkeys(strs))
