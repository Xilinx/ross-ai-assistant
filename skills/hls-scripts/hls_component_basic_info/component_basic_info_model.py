#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ComponentBasicInfo:
    component_name: str
    source_files: list[str]
    testbench_files: list[str]
    include_paths: list[str]
    top_function: str
