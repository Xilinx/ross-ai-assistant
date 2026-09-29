#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CdfgNodeInfo:
    id: str
    rtl_name: str
    bitwidth: str


@dataclass
class PipeProcessInfo:
    name: str
    ssdmobj_id: str


@dataclass
class PipeChannelInfo:
    name: str
    ssdmobj_id: str
    ctype: str
    depth: str
    bitwidth: str
    suggested_type: str
    suggested_depth: str
    sources: list[str]
    sinks: list[str]
    bram: str
    uram: str
    storage: str


@dataclass
class AdbInfo:
    cdfg_nodes: list[CdfgNodeInfo]
    pipe_processes: list[PipeProcessInfo]
    pipe_channels: list[PipeChannelInfo]
