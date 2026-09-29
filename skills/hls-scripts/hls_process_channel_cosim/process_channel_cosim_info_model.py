#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CosimProcessInfo:
    instance_path: str
    rtl_name: str
    stalling_time: str
    avg_ii: str
    max_ii: str
    min_ii: str
    avg_latency: str
    max_latency: str
    min_latency: str
    stall_no_start: str
    stall_no_continue: str
    read_block_time: str
    write_block_time: str


@dataclass
class CosimChannelInfo:
    instance_path: str
    rtl_name: str
    category: str
    read_block_time: str
    write_block_time: str
    cosim_max_depth: str


@dataclass
class CosimLoopInfo:
    instance_path: str
    loop_name: str
    loop_avg_latency: str
    loop_max_latency: str
    loop_min_latency: str
    loop_avg_ii: str
    loop_max_ii: str
    loop_min_ii: str
    iter_avg_latency: str
    iter_max_latency: str
    iter_min_latency: str
    iter_avg_ii: str
    iter_max_ii: str
    iter_min_ii: str


@dataclass
class CosimCategory:
    NONE = "0"
    WRITE_BLOCK = "1"
    READ_BLOCK = "2"
    READ_BLOCK_WRITE_BLOCK = "3"
