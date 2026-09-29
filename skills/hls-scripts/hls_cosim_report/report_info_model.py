#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass

from hls_dataflow.dataflow_info_model import DataflowInfo


@dataclass
class ReportInfo:
    performance_and_resource_estimates: PerformanceAndResourceEstimates
    dataflow_infos: list[DataflowInfo]


@dataclass
class IILatencyInfo:
    avg_ii: str
    max_ii: str
    min_ii: str
    avg_latency: str
    max_latency: str
    min_latency: str


@dataclass
class RowInfo(IILatencyInfo):
    module_or_loop: str
    total_execution_time: str
    child_rows: list[RowInfo]


@dataclass
class PerformanceAndResourceEstimates:
    TITLES = RowInfo(
        module_or_loop="MODULES & LOOPS",
        avg_ii="Avg II",
        max_ii="Max II",
        min_ii="Min II",
        avg_latency="Avg Latency",
        max_latency="Max Latency",
        min_latency="Min Latency",
        total_execution_time="Total Execution Time",
        child_rows=[],
    )
    rows: list[RowInfo]
