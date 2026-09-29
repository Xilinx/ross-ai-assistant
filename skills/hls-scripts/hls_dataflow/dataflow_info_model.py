#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DataflowInfo:
    dataflow_module_name: str
    process_infos: ProcessInfos
    channel_infos: ChannelInfos


@dataclass
class ProcessInfo:
    process_name: str
    cosim_category: str
    cosim_stalling_time: str
    fifo_empty: str
    fifo_full: str
    cosim_stall_no_start: str
    cosim_stall_no_continue: str
    cosim_avg_ii: str
    cosim_max_ii: str
    cosim_min_ii: str
    cosim_avg_latency: str
    cosim_max_latency: str
    cosim_min_latency: str


@dataclass
class ProcessInfos:
    TITLES = ProcessInfo(
        process_name="Process Name",
        cosim_category="Cosim Category",
        cosim_stalling_time="Cosim Stalling Time",
        fifo_empty="FIFO Empty",
        fifo_full="FIFO Full",
        cosim_stall_no_start="Cosim Stall No Start",
        cosim_stall_no_continue="Cosim Stall No Continue",
        cosim_avg_ii="Cosim Avg II",
        cosim_max_ii="Cosim Max II",
        cosim_min_ii="Cosim Min II",
        cosim_avg_latency="Cosim Avg Latency",
        cosim_max_latency="Cosim Max Latency",
        cosim_min_latency="Cosim Min Latency",
    )
    rows: list[ProcessInfo]


@dataclass
class ChannelInfo:
    channel_name: str
    cosim_category: str
    fifo_empty: str
    fifo_full: str
    cosim_max_depth: str
    depth: str
    worst_estimated_depth: str
    channel_type: str
    sub_type: str
    bit_width: str
    producer: str
    consumer: str
    bram: str
    uram: str
    words: str
    banks: str


@dataclass
class ChannelInfos:
    TITLES = ChannelInfo(
        channel_name="Channel Name",
        cosim_category="Cosim Category",
        fifo_empty="FIFO Empty",
        fifo_full="FIFO Full",
        cosim_max_depth="Cosim Max Depth",
        depth="Depth",
        worst_estimated_depth="Worst Estimated Depth",
        channel_type="Channel Type",
        sub_type="Sub-Type",
        bit_width="Bit Width",
        producer="Producer",
        consumer="Consumer",
        bram="Bram",
        uram="Uram",
        words="Words",
        banks="Banks",
    )
    rows: list[ChannelInfo]
