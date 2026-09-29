#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class InstanceInfo:
    module_name: str
    inst_name: str
    child_instances: list[InstanceInfo]


@dataclass
class TopInstanceInfo:
    module_name: str
    rtl_prefix: str
    child_instances: list[InstanceInfo]


@dataclass
class SummaryOfTimingAnalysisInfo:
    target_clock_period: str
    clock_uncertainty: str
    estimated_clock_period: str


@dataclass
class SummaryOfOverallLatencyInfo:
    best_case_latency: str
    average_case_latency: str
    worst_case_latency: str
    best_case_real_time_latency: str
    average_case_real_time_latency: str
    worst_case_real_time_latency: str
    pipeline_initiation_interval: str
    pipeline_type: str


@dataclass
class LoopInfo:
    name: str
    trip_count: str
    latency: str
    absolute_time_latency: str
    pipeline_ii: str
    pipeline_depth: str
    pipeline_type: str
    target_ti: str
    real_ti: str
    ti_met: str
    is_ii_violation: bool
    child_loops: list[LoopInfo]
    child_instances: list[str]


@dataclass
class SummaryOfLoopLatencyInfo:
    loops: list[LoopInfo]


@dataclass
class PerformanceEstimatesInfo:
    summary_of_timing_analysis: SummaryOfTimingAnalysisInfo
    summary_of_overall_latency: SummaryOfOverallLatencyInfo
    summary_of_loop_latency: SummaryOfLoopLatencyInfo


@dataclass
class ResourcesInfo:
    ff: str
    avail_ff: str
    util_ff: str
    lut: str
    avail_lut: str
    util_lut: str
    bram18k: str
    avail_bram: str
    util_bram: str
    dsp: str
    avail_dsp: str
    util_dsp: str
    uram: str
    avail_uram: str
    util_uram: str


@dataclass
class AreaEstimatesInfo:
    resources: ResourcesInfo


@dataclass
class ModuleInfo:
    name: str
    is_timing_violation: bool
    performance_estimates: PerformanceEstimatesInfo
    area_estimates: AreaEstimatesInfo


@dataclass
class DesignInfo:
    top_instance: Optional[TopInstanceInfo]
    modules: list[ModuleInfo]
