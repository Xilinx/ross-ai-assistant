#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, TypeVar

from hls_adb.adb_info_getter import (
    ChannelType,
    get_cdfg_node,
    get_source_or_target_processes,
    get_source_or_target_channels,
)

from hls_adb.adb_info_model import AdbInfo, PipeProcessInfo, PipeChannelInfo
from hls_process_channel_cosim.process_channel_cosim_info_model import (
    CosimChannelInfo,
    CosimCategory,
)


def get_category_text(value: Optional[str]) -> Optional[str]:
    if value == CosimCategory.NONE:
        return "none"
    elif value == CosimCategory.WRITE_BLOCK:
        return "write_block"
    elif value == CosimCategory.READ_BLOCK:
        return "read_block"
    elif value == CosimCategory.READ_BLOCK_WRITE_BLOCK:
        return "read_block and write_block"
    return None


def get_channel_type_text(channel_type: str) -> str:
    if channel_type == ChannelType.PIPO:
        return "PIPO"
    elif channel_type == ChannelType.SMEM:
        return "Shared Stable Memory"
    elif channel_type == ChannelType.SVAR:
        return "Shared Stable Scalar"
    elif channel_type == ChannelType.MERGE:
        return "Merge"
    elif channel_type == ChannelType.SPLIT:
        return "Split"
    return "FIFO"


def get_channel_subtype_text(suggested_type: str, channel_type: str) -> Optional[str]:
    if channel_type == ChannelType.PIPO:
        if suggested_type == "0":
            return "PIPO"
        elif suggested_type == "1":
            return "StreamOfBlocks"
    elif channel_type == ChannelType.SMEM:
        pass
    elif channel_type == ChannelType.SVAR:
        pass
    elif channel_type == ChannelType.MERGE:
        if suggested_type == "0":
            return "MergeLoadBalance"
        elif suggested_type == "1":
            return "MergeRoundRobin"
    elif channel_type == ChannelType.SPLIT:
        if suggested_type == "0":
            return "SplitLoadBalance"
        elif suggested_type == "1":
            return "SplitRoundRobin"
    else:
        if suggested_type == "0":
            return "Stream"
        elif suggested_type == "1":
            return "TaskLevel"
        elif suggested_type == "2":
            return "ScalarProp"
    return None


def percent(s: Optional[str]) -> Optional[str]:
    if s and s.strip():
        try:
            return f"{float(s) * 100.0:.2f}%"
        except ValueError:
            return s
    return s


def na(value: Optional[str]) -> str:
    return value if (value is not None and str(value) != "") else "N/A"


def get_process_cosim_category(
    pipe_process: PipeProcessInfo,
    adb_info: AdbInfo,
    channel_infos: list[CosimChannelInfo],
    instance_path: str,
) -> Optional[str]:
    src = _source_or_target_categories(
        pipe_process, adb_info, channel_infos, instance_path, True
    )
    dst = _source_or_target_categories(
        pipe_process, adb_info, channel_infos, instance_path, False
    )
    if any(c for c in (src + dst)):
        is_write = (
            CosimCategory.WRITE_BLOCK in src
            or CosimCategory.READ_BLOCK_WRITE_BLOCK in src
        )
        is_read = (
            CosimCategory.READ_BLOCK in dst
            or CosimCategory.READ_BLOCK_WRITE_BLOCK in dst
        )
        if is_read and is_write:
            return CosimCategory.READ_BLOCK_WRITE_BLOCK
        elif is_read:
            return CosimCategory.READ_BLOCK
        elif is_write:
            return CosimCategory.WRITE_BLOCK
        return CosimCategory.NONE
    return None


def _source_or_target_categories(
    pipe_process: PipeProcessInfo,
    adb_info: AdbInfo,
    channel_infos: list[CosimChannelInfo],
    instance_path: str,
    source: bool,
) -> list[Optional[str]]:
    cats: list[Optional[str]] = []
    for ch in get_source_or_target_channels(pipe_process, adb_info, source):
        info = get_cosim_info(ch, adb_info, channel_infos, instance_path)
        cats.append(info.category if info else None)
    return cats


def get_producer_or_consumer(
    pipe_channel: PipeChannelInfo, adb_info: AdbInfo, producer: bool
) -> str:
    return ",".join(
        p.name for p in get_source_or_target_processes(pipe_channel, adb_info, producer)
    )


def get_reconfig_depth(
    pipe_channel: PipeChannelInfo,
    adb_info: AdbInfo,
    reconfig_lines: list[str],
    instance_path: str,
) -> Optional[str]:
    node = get_cdfg_node(adb_info, pipe_channel.ssdmobj_id)
    if not node:
        return None

    rtl_name = node.rtl_name
    full_name = f"{instance_path}.{rtl_name}" if instance_path else rtl_name

    for line in reconfig_lines:
        first_dot = line.find(".")
        eq = line.find("=")
        if first_dot < 0 or eq <= first_dot:
            continue
        last_dot = line.rfind(".", 0, eq)
        if last_dot <= first_dot:
            continue

        if line[last_dot + 1 : eq].strip() != "DEPTH":
            continue
        if line[first_dot + 1 : last_dot] != full_name:
            continue

        rhs = line[eq + 1 :].strip()
        if rhs.startswith("'d") and rhs.endswith(";"):
            return rhs[2:-1]

    return None


def get_bit_width(pipe_channel: PipeChannelInfo, adb_info: AdbInfo) -> str:
    node = get_cdfg_node(adb_info, pipe_channel.ssdmobj_id)
    return node.bitwidth if node else pipe_channel.bitwidth


@dataclass(frozen=True)
class WordsAndBanks:
    words: str
    banks: str


def get_words_and_banks(pipe_channel: PipeChannelInfo) -> Optional[WordsAndBanks]:
    storage = pipe_channel.storage or ""
    parts = storage.split()
    if len(parts) >= 3:
        return WordsAndBanks(words=parts[1], banks=parts[2])
    return None


class HasSsdmObjId(Protocol):
    ssdmobj_id: str


class HasInstancePathAndRtlName(Protocol):
    instance_path: str
    rtl_name: str


TInfo = TypeVar("TInfo", bound=HasInstancePathAndRtlName)


def get_cosim_info(
    pipe_model: HasSsdmObjId,
    adb_info: AdbInfo,
    cosim_infos: list[TInfo],
    instance_path: str,
) -> Optional[TInfo]:
    node = get_cdfg_node(adb_info, pipe_model.ssdmobj_id)
    if not node:
        return None
    return next(
        (
            info
            for info in cosim_infos
            if info.instance_path == instance_path and info.rtl_name == node.rtl_name
        ),
        None,
    )
