#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import Optional

from .design_info_model import (
    DesignInfo,
    InstanceInfo,
    LoopInfo,
    ModuleInfo,
    TopInstanceInfo,
)
from .design_model import (
    AreaEstimates,
    Instance,
    Loop,
    LoopInstance,
    Module,
    PerformanceEstimates,
    Resources,
    SummaryOfLoopLatency,
    SummaryOfOverallLatency,
    SummaryOfTimingAnalysis,
    TopInstance,
)


def to_design(design_info: DesignInfo) -> Optional[TopInstance]:
    return (
        _parse_top_instance_info(
            design_info.top_instance,
            [_parse_module_info(mod) for mod in design_info.modules],
        )
        if design_info.top_instance
        else None
    )


def _parse_top_instance_info(
    instance_info: TopInstanceInfo,
    modules: list[Module],
) -> Optional[TopInstance]:
    module = _get_module(modules, instance_info.module_name)
    if module:
        child_instances = _parse_child_instances(instance_info.child_instances, modules)
        return TopInstance(
            module=module,
            child_instances=child_instances,
            child_base_instances=_parse_child_base_instances(module, child_instances),
            rtl_prefix=instance_info.rtl_prefix,
        )
    return None


def _parse_instance_info(
    instance_info: InstanceInfo,
    modules: list[Module],
) -> Optional[Instance]:
    module = _get_module(modules, instance_info.module_name)
    if module:
        child_instances = _parse_child_instances(instance_info.child_instances, modules)
        return Instance(
            module=module,
            child_instances=child_instances,
            child_base_instances=_parse_child_base_instances(module, child_instances),
            inst_name=instance_info.inst_name,
        )
    return None


def _parse_child_instances(
    child_instances: list[InstanceInfo],
    modules: list[Module],
) -> list[Instance]:
    return [
        child_instance
        for child_instance in [
            _parse_instance_info(child_instance, modules)
            for child_instance in child_instances
        ]
        if (child_instance is not None)
    ]


def _parse_child_base_instances(
    module: Module, child_instances: list[Instance]
) -> list[Instance | LoopInstance]:
    loops = module.performance_estimates.summary_of_loop_latency.loops
    instance_names = _get_instance_names(loops)
    return [
        *[
            child_instance
            for child_instance in child_instances
            if child_instance.inst_name not in instance_names
        ],
        *[_parse_loop(loop, module, child_instances) for loop in loops],
    ]


def _parse_loop(
    loop: Loop, module: Module, child_instances: list[Instance]
) -> LoopInstance:
    return LoopInstance(
        loop=loop,
        module=module,
        child_base_instances=[
            *[
                child_instance
                for child_instance in child_instances
                if child_instance.inst_name in loop.child_instances
            ],
            *[
                _parse_loop(child_loop, module, child_instances)
                for child_loop in loop.child_loops
            ],
        ],
    )


def _get_instance_names(loops: list[Loop]) -> list[str]:
    return [
        *[child_instance for loop in loops for child_instance in loop.child_instances],
        *[
            instance_name
            for loop in loops
            for instance_name in _get_instance_names(loop.child_loops)
        ],
    ]


def _get_module(modules: list[Module], module_name: str) -> Optional[Module]:
    return next((m for m in modules if m.name == module_name), None)


def _parse_module_info(module_info: ModuleInfo) -> Module:
    performance_estimates = module_info.performance_estimates
    timing_analysis = performance_estimates.summary_of_timing_analysis
    overall_latency = performance_estimates.summary_of_overall_latency
    initiation_intervals = _parse_min_max(overall_latency.pipeline_initiation_interval)
    resources = module_info.area_estimates.resources
    return Module(
        name=module_info.name,
        is_timing_violation=module_info.is_timing_violation,
        performance_estimates=PerformanceEstimates(
            summary_of_timing_analysis=SummaryOfTimingAnalysis(
                target_clock_period=timing_analysis.target_clock_period,
                clock_uncertainty=timing_analysis.clock_uncertainty,
                estimated_clock_period=timing_analysis.estimated_clock_period,
            ),
            summary_of_overall_latency=SummaryOfOverallLatency(
                best_case_latency=overall_latency.best_case_latency,
                average_case_latency=overall_latency.average_case_latency,
                worst_case_latency=overall_latency.worst_case_latency,
                best_case_real_time_latency=overall_latency.best_case_real_time_latency,
                average_case_real_time_latency=overall_latency.average_case_real_time_latency,
                worst_case_real_time_latency=overall_latency.worst_case_real_time_latency,
                best_pipeline_initiation_interval=initiation_intervals[0],
                worst_pipeline_initiation_interval=initiation_intervals[1],
                pipeline_initiation_interval=overall_latency.pipeline_initiation_interval,
                pipeline_type=overall_latency.pipeline_type,
            ),
            summary_of_loop_latency=SummaryOfLoopLatency(
                loops=[
                    _parse_loop_info(loopInfo)
                    for loopInfo in performance_estimates.summary_of_loop_latency.loops
                ]
            ),
        ),
        area_estimates=AreaEstimates(
            resources=Resources(
                ff=resources.ff,
                avail_ff=resources.avail_ff,
                util_ff=resources.util_ff,
                lut=resources.lut,
                avail_lut=resources.avail_lut,
                util_lut=resources.util_lut,
                bram18k=resources.bram18k,
                avail_bram=resources.avail_bram,
                util_bram=resources.util_bram,
                dsp=resources.dsp,
                avail_dsp=resources.avail_dsp,
                util_dsp=resources.util_dsp,
                uram=resources.uram,
                avail_uram=resources.avail_uram,
                util_uram=resources.util_uram,
            )
        ),
    )


def _parse_loop_info(loop_info: LoopInfo) -> Loop:
    min_max_trip_count = _parse_min_max(loop_info.trip_count)
    min_max_latency = _parse_min_max(loop_info.latency)
    min_max_absolute_time_latency = _parse_min_max(loop_info.absolute_time_latency)
    min_max_pipeline_ii = _parse_min_max(loop_info.pipeline_ii)
    min_max_pipeline_depth = _parse_min_max(loop_info.pipeline_depth)
    return Loop(
        name=loop_info.name,
        best_trip_count=min_max_trip_count[0],
        worst_trip_count=min_max_trip_count[1],
        trip_count=loop_info.trip_count,
        best_latency=min_max_latency[0],
        worst_latency=min_max_latency[1],
        latency=loop_info.latency,
        best_absolute_time_latency=min_max_absolute_time_latency[0],
        worst_absolute_time_latency=min_max_absolute_time_latency[1],
        absolute_time_latency=loop_info.absolute_time_latency,
        best_pipeline_ii=min_max_pipeline_ii[0],
        worst_pipeline_ii=min_max_pipeline_ii[1],
        pipeline_ii=loop_info.pipeline_ii,
        best_pipeline_depth=min_max_pipeline_depth[0],
        worst_pipeline_depth=min_max_pipeline_depth[1],
        pipeline_depth=loop_info.pipeline_depth,
        pipeline_type=loop_info.pipeline_type,
        target_ti=loop_info.target_ti,
        real_ti=loop_info.real_ti,
        ti_met=loop_info.ti_met,
        is_ii_violation=loop_info.is_ii_violation,
        child_loops=[
            _parse_loop_info(child_loop) for child_loop in loop_info.child_loops
        ],
        child_instances=[
            child_instance for child_instance in loop_info.child_instances
        ],
    )


def _parse_min_max(value: str) -> list[str]:
    strs = value.split("~")
    str0 = strs[0].strip()
    return [str0, strs[1].strip() if len(strs) > 1 else str0]
