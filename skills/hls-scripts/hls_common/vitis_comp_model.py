#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Configuration:
    component_type: str
    config_files: list[str]
    work_dir: str


@dataclass
class VitisComp:
    name: str
    type: str
    configuration: Configuration
