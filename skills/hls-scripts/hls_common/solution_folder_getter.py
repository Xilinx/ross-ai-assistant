#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import Optional

from .file_path_util import join_and_normalize
from .file_util import read_file
from .vitis_comp_getter import (
    find_hls_cfg_file,
    get_vitis_comp,
    get_work_dir,
    is_hls_component,
)

from .cfg_parser import get_cfg_dict, get_hls_top


def get_solution_folder(component_location: str) -> Optional[str]:
    work_dir = _get_hls_work_dir_path(component_location)
    return join_and_normalize(work_dir, "hls") if work_dir else None


def _get_hls_work_dir_path(component_location: str) -> Optional[str]:
    vitis_comp = get_vitis_comp(component_location)
    if not vitis_comp:
        return None
    if not is_hls_component(vitis_comp):
        return None
    work_dir = get_work_dir(vitis_comp)
    if work_dir:
        return join_and_normalize(component_location, work_dir)
    hls_cfg = find_hls_cfg_file(component_location, vitis_comp)
    if hls_cfg:
        top = get_hls_top(get_cfg_dict(read_file(hls_cfg)))
        if top:
            return join_and_normalize(component_location, top)
    return None
