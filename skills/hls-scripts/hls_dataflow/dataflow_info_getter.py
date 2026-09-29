#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations


from hls_process_channel_cosim.process_channel_cosim_info_getter import (
    get_channel_cosim_infos,
    get_process_cosim_infos,
    get_reconfig_file_lines,
)
from hls_process_channel_cosim.process_channel_cosim_info_model import (
    CosimChannelInfo,
    CosimProcessInfo,
)

from hls_adb.adb_info_model import AdbInfo, PipeChannelInfo, PipeProcessInfo
from hls_adb.adb_info_getter import get_adb_info

from hls_design.design_info_model import DesignInfo

from hls_design.design_info_getter import (
    get_dataflow_module_names,
    get_design_info,
    get_instance_path,
)

from .model_util import (
    get_bit_width,
    get_category_text,
    get_channel_subtype_text,
    get_channel_type_text,
    get_cosim_info,
    get_process_cosim_category,
    get_producer_or_consumer,
    get_reconfig_depth,
    get_words_and_banks,
    na,
    percent,
)
from .dataflow_info_model import (
    ChannelInfo,
    ChannelInfos,
    DataflowInfo,
    ProcessInfo,
    ProcessInfos,
)


def get_dataflow_infos(solution_folder: str) -> list[DataflowInfo]:
    modules_names = get_dataflow_module_names(solution_folder)
    design_info = get_design_info(solution_folder)
    process_infos = get_process_cosim_infos(solution_folder)
    channel_infos = get_channel_cosim_infos(solution_folder)
    reconfig_lines = get_reconfig_file_lines(solution_folder)
    return [
        _get_dataflow_info(
            module_name,
            design_info,
            process_infos,
            channel_infos,
            reconfig_lines,
            solution_folder,
        )
        for module_name in modules_names
    ]


def _get_dataflow_info(
    module_name: str,
    design_info: DesignInfo,
    process_infos: list[CosimProcessInfo],
    channel_infos: list[CosimChannelInfo],
    reconfig_lines: list[str],
    solution_folder: str,
) -> DataflowInfo:
    adb_info = get_adb_info(solution_folder, module_name)
    instance_path = get_instance_path(design_info, module_name)
    return DataflowInfo(
        dataflow_module_name=module_name,
        process_infos=ProcessInfos(
            rows=[
                _final_process_info(
                    p, adb_info, process_infos, channel_infos, instance_path
                )
                for p in adb_info.pipe_processes
            ]
        ),
        channel_infos=ChannelInfos(
            rows=[
                _final_channel_info(
                    c, adb_info, channel_infos, reconfig_lines, instance_path
                )
                for c in adb_info.pipe_channels
            ]
        ),
    )


def _final_process_info(
    pipe_process: PipeProcessInfo,
    adb_info: AdbInfo,
    process_infos: list[CosimProcessInfo],
    channel_infos: list[CosimChannelInfo],
    instance_path: str,
) -> ProcessInfo:
    pinfo = get_cosim_info(pipe_process, adb_info, process_infos, instance_path)
    return ProcessInfo(
        process_name=na(pipe_process.name),
        cosim_category=na(
            get_category_text(
                get_process_cosim_category(
                    pipe_process, adb_info, channel_infos, instance_path
                )
            )
        ),
        cosim_stalling_time=na(percent(pinfo.stalling_time if pinfo else None)),
        fifo_empty=na(percent(pinfo.read_block_time if pinfo else None)),
        fifo_full=na(percent(pinfo.write_block_time if pinfo else None)),
        cosim_stall_no_start=na(percent(pinfo.stall_no_start if pinfo else None)),
        cosim_stall_no_continue=na(percent(pinfo.stall_no_continue if pinfo else None)),
        cosim_avg_ii=na(pinfo.avg_ii if pinfo else None),
        cosim_max_ii=na(pinfo.max_ii if pinfo else None),
        cosim_min_ii=na(pinfo.min_ii if pinfo else None),
        cosim_avg_latency=na(pinfo.avg_latency if pinfo else None),
        cosim_max_latency=na(pinfo.max_latency if pinfo else None),
        cosim_min_latency=na(pinfo.min_latency if pinfo else None),
    )


def _final_channel_info(
    pipe_channel: PipeChannelInfo,
    adb_info: AdbInfo,
    channel_infos: list[CosimChannelInfo],
    reconfig_lines: list[str],
    instance_path: str,
) -> ChannelInfo:
    cinfo = get_cosim_info(pipe_channel, adb_info, channel_infos, instance_path)
    rc_depth = get_reconfig_depth(pipe_channel, adb_info, reconfig_lines, instance_path)
    words_banks = get_words_and_banks(pipe_channel)
    return ChannelInfo(
        channel_name=na(pipe_channel.name),
        cosim_category=na(get_category_text(cinfo.category if cinfo else None)),
        fifo_empty=na(percent(cinfo.read_block_time if cinfo else None)),
        fifo_full=na(percent(cinfo.write_block_time if cinfo else None)),
        cosim_max_depth=na(cinfo.cosim_max_depth if cinfo else None),
        depth=na(rc_depth if rc_depth else pipe_channel.depth),
        worst_estimated_depth=na(pipe_channel.suggested_depth),
        channel_type=na(get_channel_type_text(pipe_channel.ctype)),
        sub_type=na(
            get_channel_subtype_text(pipe_channel.suggested_type, pipe_channel.ctype)
        ),
        bit_width=na(get_bit_width(pipe_channel, adb_info)),
        producer=na(get_producer_or_consumer(pipe_channel, adb_info, True)),
        consumer=na(get_producer_or_consumer(pipe_channel, adb_info, False)),
        bram=na(pipe_channel.bram),
        uram=na(pipe_channel.uram),
        words=na(words_banks.words if words_banks else None),
        banks=na(words_banks.banks if words_banks else None),
    )
