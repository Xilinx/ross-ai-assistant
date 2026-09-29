#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BaseInstance:
    child_base_instances: list[Instance | LoopInstance]
    module: Module


@dataclass
class AbstractInstance(BaseInstance):
    child_instances: list[Instance]


@dataclass
class TopInstance(AbstractInstance):
    rtl_prefix: str


@dataclass
class Instance(AbstractInstance):
    inst_name: str


@dataclass
class LoopInstance(BaseInstance):
    loop: Loop


@dataclass
class Module:
    name: str
    is_timing_violation: bool
    performance_estimates: PerformanceEstimates
    area_estimates: AreaEstimates


@dataclass
class PerformanceEstimates:
    summary_of_timing_analysis: SummaryOfTimingAnalysis
    summary_of_overall_latency: SummaryOfOverallLatency
    summary_of_loop_latency: SummaryOfLoopLatency


@dataclass
class SummaryOfTimingAnalysis:
    target_clock_period: str
    clock_uncertainty: str
    estimated_clock_period: str


@dataclass
class SummaryOfOverallLatency:
    best_case_latency: str
    average_case_latency: str
    worst_case_latency: str
    best_case_real_time_latency: str
    average_case_real_time_latency: str
    worst_case_real_time_latency: str
    best_pipeline_initiation_interval: str
    worst_pipeline_initiation_interval: str
    pipeline_initiation_interval: str
    pipeline_type: str


@dataclass
class SummaryOfLoopLatency:
    loops: list[Loop]


@dataclass
class Loop:
    name: str
    best_trip_count: str
    worst_trip_count: str
    trip_count: str
    best_latency: str
    worst_latency: str
    latency: str
    best_absolute_time_latency: str
    worst_absolute_time_latency: str
    absolute_time_latency: str
    best_pipeline_ii: str
    worst_pipeline_ii: str
    pipeline_ii: str
    best_pipeline_depth: str
    worst_pipeline_depth: str
    pipeline_depth: str
    pipeline_type: str
    target_ti: str
    real_ti: str
    ti_met: str
    is_ii_violation: bool
    child_loops: list[Loop]
    child_instances: list[str]


@dataclass
class AreaEstimates:
    resources: Resources


@dataclass
class Resources:
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
