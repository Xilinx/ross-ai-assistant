#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations
from typing import Optional


from hls_common.file_path_util import cosim_rpt_file_path
from hls_common.file_util import read_file
from hls_design.design_model import Instance, LoopInstance, TopInstance
from hls_process_channel_cosim.process_channel_cosim_info_getter import (
    get_loop_cosim_infos,
    get_process_cosim_infos,
)
from hls_process_channel_cosim.process_channel_cosim_info_model import (
    CosimLoopInfo,
    CosimProcessInfo,
)

from .report_info_model import (
    IILatencyInfo,
    PerformanceAndResourceEstimates,
    ReportInfo,
    RowInfo,
)
from hls_dataflow.dataflow_info_getter import get_dataflow_infos
from hls_design.design_hierarchy_getter import get_top_instance, get_instance_path


def get_report_info(solution_folder: str) -> ReportInfo:
    return ReportInfo(
        performance_and_resource_estimates=PerformanceAndResourceEstimates(
            rows=_get_top_rows(solution_folder)
        ),
        dataflow_infos=get_dataflow_infos(solution_folder),
    )


def _get_top_rows(solution_folder: str) -> list[RowInfo]:
    top_instance = get_top_instance(solution_folder)
    return (
        [
            _parse_instance(
                top_instance,
                top_instance,
                get_process_cosim_infos(solution_folder),
                get_loop_cosim_infos(solution_folder),
                _get_total_execution_time(solution_folder, top_instance.module.name),
            )
        ]
        if top_instance
        else []
    )


def _parse_instance(
    instance: TopInstance | Instance | LoopInstance,
    top_instance: TopInstance,
    process_infos: list[CosimProcessInfo],
    loop_infos: list[CosimLoopInfo],
    total_execution_time: str,
) -> RowInfo:
    data = _to_ii_latency_info(
        _find_loop_info(instance, top_instance, loop_infos)
        if isinstance(instance, LoopInstance)
        else _find_process_info(instance, top_instance, process_infos)
    )
    return RowInfo(
        module_or_loop=(
            instance.loop.name
            if isinstance(instance, LoopInstance)
            else instance.module.name
        ),
        avg_ii=data.avg_ii,
        max_ii=data.max_ii,
        min_ii=data.min_ii,
        avg_latency=data.avg_latency,
        max_latency=data.max_latency,
        min_latency=data.min_latency,
        total_execution_time=(
            total_execution_time if isinstance(instance, TopInstance) else ""
        ),
        child_rows=[
            _parse_instance(
                child, top_instance, process_infos, loop_infos, total_execution_time
            )
            for child in instance.child_base_instances
        ],
    )


def _to_ii_latency_info(
    info: Optional[CosimProcessInfo | CosimLoopInfo],
) -> IILatencyInfo:
    return (
        IILatencyInfo(
            avg_ii=info.avg_ii,
            max_ii=info.max_ii,
            min_ii=info.min_ii,
            avg_latency=info.avg_latency,
            max_latency=info.max_latency,
            min_latency=info.min_latency,
        )
        if isinstance(info, CosimProcessInfo)
        else (
            IILatencyInfo(
                avg_ii=info.loop_avg_ii,
                max_ii=info.loop_max_ii,
                min_ii=info.loop_min_ii,
                avg_latency=info.loop_avg_latency,
                max_latency=info.loop_max_latency,
                min_latency=info.loop_min_latency,
            )
            if isinstance(info, CosimLoopInfo)
            else IILatencyInfo(
                avg_ii="",
                max_ii="",
                min_ii="",
                avg_latency="",
                max_latency="",
                min_latency="",
            )
        )
    )


def _get_total_execution_time(solution_folder: str, top_module_name: str) -> str:
    for line in read_file(cosim_rpt_file_path(solution_folder, top_module_name)).splitlines():
        if "Verilog" in line or "VHDL" in line:
            datas = [data.strip() for data in line.split("|")]
            if len(datas) > 9:
                total_execution_time = datas[9]
                if total_execution_time != "NA" and len(total_execution_time) != 0:
                    return total_execution_time
    return ""


def _find_process_info(
    instance: TopInstance | Instance,
    top_instance: TopInstance,
    process_infos: list[CosimProcessInfo],
) -> Optional[CosimProcessInfo]:
    instance_path = get_instance_path(instance, top_instance)
    inst_name = (
        "AESL_inst_" + instance.rtl_prefix.strip() + instance.module.name
        if isinstance(instance, TopInstance)
        else instance.inst_name
    )
    return next(
        (
            process_info
            for process_info in process_infos
            if (
                process_info.instance_path == instance_path
                and process_info.rtl_name == inst_name
            )
        ),
        None,
    )


def _find_loop_info(
    instance: LoopInstance,
    top_instance: TopInstance,
    loop_infos: list[CosimLoopInfo],
) -> Optional[CosimLoopInfo]:
    instance_path = get_instance_path(instance, top_instance)
    return next(
        (
            loop_info
            for loop_info in loop_infos
            if (
                loop_info.instance_path == instance_path
                and loop_info.loop_name == instance.loop.name
            )
        ),
        None,
    )
