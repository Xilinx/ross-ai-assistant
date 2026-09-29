#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional

from .design_info_model import (
    AreaEstimatesInfo,
    DesignInfo,
    LoopInfo,
    ResourcesInfo,
    SummaryOfLoopLatencyInfo,
    SummaryOfTimingAnalysisInfo,
    TopInstanceInfo,
    InstanceInfo,
    ModuleInfo,
    PerformanceEstimatesInfo,
    SummaryOfOverallLatencyInfo,
)
from hls_common.file_path_util import (
    find_design_file_path,
    global_setting_file_path,
)
from hls_common.file_util import read_file
from hls_common.xml_util import (
    xml_child_text,
    xml_find_child,
    xml_find_all,
    parse_xml,
    xml_text,
)


def get_design_info(solution_folder: str) -> DesignInfo:
    return _parse_design_info(
        find_design_file_path(solution_folder),
        global_setting_file_path(solution_folder),
    )


def _parse_design_info(
    design_file_path: str, global_setting_file_path: str
) -> DesignInfo:
    root = parse_xml(read_file(design_file_path))
    if root is None:
        return DesignInfo(top_instance=None, modules=[])
    top_mod = xml_find_child(xml_find_child(root, "RTLDesignHierarchy"), "TopModule")
    return DesignInfo(
        top_instance=(
            TopInstanceInfo(
                module_name=xml_child_text(top_mod, "ModuleName"),
                rtl_prefix=_get_rtl_prefix(global_setting_file_path),
                child_instances=[
                    _parse_instance(ins) for ins in _child_instances(top_mod)
                ],
            )
            if top_mod is not None
            else None
        ),
        modules=[
            _parse_module_info(module)
            for module in xml_find_all(
                xml_find_child(root, "ModuleInformation"), "Module"
            )
        ],
    )


def _parse_module_info(module: ET.Element) -> ModuleInfo:
    estimates = xml_find_child(module, "PerformanceEstimates")
    analysis = xml_find_child(estimates, "SummaryOfTimingAnalysis")
    latency = xml_find_child(estimates, "SummaryOfOverallLatency")
    resources = xml_find_child(xml_find_child(module, "AreaEstimates"), "Resources")
    summary_of_loop_latency = xml_find_child(estimates, "SummaryOfLoopLatency")
    summary_of_violations = xml_find_child(estimates, "SummaryOfViolations")
    summary_of_loop_violations = xml_find_child(
        summary_of_violations, "SummaryOfLoopViolations"
    )

    return ModuleInfo(
        name=xml_child_text(module, "Name"),
        is_timing_violation=(
            xml_child_text(summary_of_violations, "IssueType") == "Timing Violation"
        ),
        performance_estimates=PerformanceEstimatesInfo(
            summary_of_timing_analysis=SummaryOfTimingAnalysisInfo(
                target_clock_period=_vvalue(analysis, "TargetClockPeriod"),
                clock_uncertainty=_vvalue(analysis, "ClockUncertainty"),
                estimated_clock_period=_vvalue(analysis, "EstimatedClockPeriod"),
            ),
            summary_of_overall_latency=SummaryOfOverallLatencyInfo(
                best_case_latency=_vvalue(latency, "Best-caseLatency"),
                average_case_latency=_vvalue(latency, "Average-caseLatency"),
                worst_case_latency=_vvalue(latency, "Worst-caseLatency"),
                best_case_real_time_latency=_tvalue(
                    latency, "Best-caseRealTimeLatency"
                ),
                average_case_real_time_latency=_tvalue(
                    latency, "Average-caseRealTimeLatency"
                ),
                worst_case_real_time_latency=_tvalue(
                    latency, "Worst-caseRealTimeLatency"
                ),
                pipeline_initiation_interval=_vvalue(
                    latency, "PipelineInitiationInterval"
                ),
                pipeline_type=_pipeline_type(latency),
            ),
            summary_of_loop_latency=SummaryOfLoopLatencyInfo(
                loops=_parse_loops(summary_of_loop_latency, summary_of_loop_violations),
            ),
        ),
        area_estimates=AreaEstimatesInfo(
            resources=ResourcesInfo(
                ff=_vvalue(resources, "FF"),
                avail_ff=_vvalue(resources, "AVAIL_FF"),
                util_ff=_vvalue(resources, "UTIL_FF"),
                lut=_vvalue(resources, "LUT"),
                avail_lut=_vvalue(resources, "AVAIL_LUT"),
                util_lut=_vvalue(resources, "UTIL_LUT"),
                bram18k=_vvalue(resources, "BRAM_18K"),
                avail_bram=_vvalue(resources, "AVAIL_BRAM"),
                util_bram=_vvalue(resources, "UTIL_BRAM"),
                dsp=_vvalue(resources, "DSP"),
                avail_dsp=_vvalue(resources, "AVAIL_DSP"),
                util_dsp=_vvalue(resources, "UTIL_DSP"),
                uram=_vvalue(resources, "URAM"),
                avail_uram=_vvalue(resources, "AVAIL_URAM"),
                util_uram=_vvalue(resources, "UTIL_URAM"),
            )
        ),
    )


def _child_instances(instance_elem: ET.Element) -> list[ET.Element]:
    inst_list = xml_find_child(instance_elem, "InstancesList")
    return xml_find_all(inst_list, "Instance")


def _parse_instance(instance_elem: ET.Element) -> InstanceInfo:
    return InstanceInfo(
        module_name=xml_child_text(instance_elem, "ModuleName"),
        inst_name=xml_child_text(instance_elem, "InstName"),
        child_instances=[
            _parse_instance(ins) for ins in _child_instances(instance_elem)
        ],
    )


def _parse_loops(
    element: Optional[ET.Element], summary_of_loop_violations: Optional[ET.Element]
) -> list[LoopInfo]:
    loops: list[LoopInfo] = []
    for child_element in element or []:
        child_loops = _parse_loops(child_element, summary_of_loop_violations)
        if xml_find_child(child_element, "TripCount") is not None:
            loop_name = xml_child_text(child_element, "Name")
            loops.append(
                LoopInfo(
                    name=loop_name,
                    trip_count=_get_range_value(child_element, "TripCount"),
                    latency=_get_range_value(child_element, "Latency"),
                    absolute_time_latency=_transform_range_time(
                        _vvalue(child_element, "AbsoluteTimeLatency")
                    ),
                    pipeline_ii=_vvalue(child_element, "PipelineII"),
                    pipeline_depth=_vvalue(child_element, "PipelineDepth"),
                    pipeline_type=_pipeline_type(child_element),
                    target_ti=_vvalue(child_element, "TargetTI"),
                    real_ti=_vvalue(child_element, "RealTI"),
                    ti_met=_vvalue(child_element, "TIMet"),
                    is_ii_violation=(
                        xml_child_text(
                            _get_loop_violation(summary_of_loop_violations, loop_name),
                            "IssueType",
                        )
                        == "II Violation"
                    ),
                    child_loops=child_loops,
                    child_instances=[
                        xml_text(instance)
                        for instance in xml_find_all(
                            xml_find_child(child_element, "InstanceList"), "Instance"
                        )
                    ],
                )
            )
        else:
            loops.extend(child_loops)
    return loops


def _get_loop_violation(
    elem: Optional[ET.Element], loop_name: str
) -> Optional[ET.Element]:
    for child in elem or []:
        if (
            xml_child_text(child, "Name") == loop_name
            and xml_find_child(child, "IssueType") is not None
        ):
            return child
        violation = _get_loop_violation(child, loop_name)
        if violation is not None:
            return violation
    return None


def _pipeline_type(elem: Optional[ET.Element]) -> str:
    pipeline_type = _vvalue(elem, "PipelineType")
    return (
        "no" if not pipeline_type or pipeline_type.lower() == "none" else pipeline_type
    )


def _vvalue(elem: Optional[ET.Element], tag: str) -> str:
    value = xml_child_text(elem, tag)
    return "" if value == "undef" else value


def _tvalue(elem: Optional[ET.Element], tag: str) -> str:
    return _transform_time(_vvalue(elem, tag))


def _get_range_value(elem: Optional[ET.Element], tag: str) -> str:
    value = xml_find_child(elem, tag)
    range = xml_find_child(value, "range")
    if range is not None:
        min_value = _vvalue(range, "min")
        max_value = _vvalue(range, "max")
        return min_value + "~" + max_value if min_value or max_value else ""
    return _vvalue(elem, tag)


def _transform_range_time(value: str) -> str:
    min_max_values = _parse_min_max(value)
    return _transform_time(min_max_values[0]) + "~" + _transform_time(min_max_values[1])


def _parse_min_max(value: str) -> list[str]:
    strs = value.split("~")
    str0 = strs[0].strip()
    return [str0, strs[1].strip() if len(strs) > 1 else str0]


def _transform_time(s: str) -> str:
    parts = s.strip().split(" ")
    if len(parts) > 1:
        unit = parts[1]
        try:
            time = float(parts[0])
        except Exception:
            return s
        if unit == "sec":
            time *= 1_000_000_000
        elif unit == "ms":
            time *= 1_000_000
        elif unit == "us":
            time *= 1_000
        elif unit == "ns":
            pass
        elif unit == "pm":
            time /= 1_000
        return str(time).rstrip("0").rstrip(".") if "." in str(time) else str(time)
    return s


def _get_rtl_prefix(global_setting_file_path: str) -> str:
    lines = read_file(global_setting_file_path).split("\n")
    return _get_global_setting_value(lines, "RtlPrefix")


def _get_global_setting_value(global_setting_lines: list[str], key: str) -> str:
    prefix = f"set {key} "
    line_trim = next(
        (ln.strip() for ln in global_setting_lines if ln.strip().startswith(prefix)),
        None,
    )
    if line_trim:
        value = line_trim[len(prefix) :].strip()
        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1].strip()
        return value
    return ""


def get_dataflow_module_names(solution_folder: str) -> list[str]:
    d = get_design_info(solution_folder)
    return [
        m.name
        for m in d.modules
        if m.performance_estimates.summary_of_overall_latency.pipeline_type.strip().lower()
        == "dataflow"
    ]


def get_instance_path(design_info: DesignInfo, module_name: str) -> str:
    return _calc_instance_path(design_info.top_instance, "", module_name)


def _calc_instance_path(
    instance: Optional[TopInstanceInfo | InstanceInfo],
    instance_path: str,
    module_name: str,
) -> str:
    if instance:
        if instance.module_name == module_name:
            return instance_path
        else:
            for child_instance in instance.child_instances:
                child_instance_path = _calc_instance_path(
                    child_instance,
                    (
                        instance_path + "." + child_instance.inst_name
                        if instance_path
                        else child_instance.inst_name
                    ),
                    module_name,
                )
                if child_instance_path:
                    return child_instance_path
    return ""
