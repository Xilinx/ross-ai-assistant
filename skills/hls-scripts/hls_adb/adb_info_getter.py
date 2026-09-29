#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import Optional

import xml.etree.ElementTree as ET

from hls_common.file_path_util import adb_file_path
from hls_common.file_util import read_file
from hls_common.xml_util import (
    xml_attr,
    xml_child_text,
    xml_find_child,
    xml_find_all,
    parse_xml,
)
from .adb_info_model import AdbInfo, PipeProcessInfo, PipeChannelInfo, CdfgNodeInfo


class ChannelType:
    PIPO = "1"
    SMEM = "2"
    SVAR = "3"
    MERGE = "4"
    SPLIT = "5"


def get_adb_info(solution_folder: str, module_name: str) -> AdbInfo:
    return _parse_adb_info(adb_file_path(solution_folder, module_name))


def _parse_adb_info(adb_file_path_: str) -> AdbInfo:
    root = parse_xml(read_file(adb_file_path_))
    # adb is typically: boost_serialization/syndb/...
    syndb = xml_find_child(root, "syndb")
    cdfg = xml_find_child(syndb, "cdfg")
    # cdfg nodes
    cdfg_nodes: list[CdfgNodeInfo] = []
    for node in _items(xml_find_child(cdfg, "nodes")):
        value = xml_find_child(node, "Value")
        obj = xml_find_child(value, "Obj") if value is not None else None
        if obj is None:
            continue
        node_id = xml_child_text(obj, "id")
        if not node_id:
            continue
        cdfg_nodes.append(
            CdfgNodeInfo(
                id=node_id,
                rtl_name=xml_child_text(obj, "rtlName"),
                bitwidth=xml_child_text(value, "bitwidth"),
            )
        )

    # dataflow pipe regions
    pipe_processes: list[PipeProcessInfo] = []
    pipe_channels: list[PipeChannelInfo] = []

    regions_root = xml_find_child(syndb, "cdfg_regions")
    for region in _items(regions_root):
        if xml_child_text(region, "mIsDfPipe") != "1":
            continue
        df_pipe = xml_find_child(region, "mDfPipe")
        process_items = _items(xml_find_child(df_pipe, "process_list"))
        channel_items = _items(xml_find_child(df_pipe, "channel_list"))

        ssdm_map = _ssdmobj_id_map(process_items, channel_items)

        pipe_processes.extend([_parse_process(p) for p in process_items])
        pipe_channels.extend([_parse_channel(c, ssdm_map) for c in channel_items])

    return AdbInfo(
        cdfg_nodes=cdfg_nodes,
        pipe_processes=pipe_processes,
        pipe_channels=pipe_channels,
    )


def _items(parent: Optional[ET.Element]) -> list[ET.Element]:
    return xml_find_all(parent, "item")


def _parse_process(process_item: ET.Element) -> PipeProcessInfo:
    return PipeProcessInfo(
        name=xml_child_text(process_item, "name"),
        ssdmobj_id=xml_child_text(process_item, "ssdmobj_id"),
    )


def _parse_channel(
    channel_item: ET.Element, ssdm_map: dict[str, str]
) -> PipeChannelInfo:
    sources = [
        _inst_ssdmobj_id(i, ssdm_map) for i in _channel_insts(channel_item, True)
    ]
    sinks = [_inst_ssdmobj_id(i, ssdm_map) for i in _channel_insts(channel_item, False)]
    return PipeChannelInfo(
        name=xml_child_text(channel_item, "name"),
        ssdmobj_id=xml_child_text(channel_item, "ssdmobj_id"),
        ctype=xml_child_text(channel_item, "ctype"),
        depth=xml_child_text(channel_item, "depth"),
        bitwidth=xml_child_text(channel_item, "bitwidth"),
        suggested_type=xml_child_text(channel_item, "suggested_type"),
        suggested_depth=xml_child_text(channel_item, "suggested_depth"),
        sources=[s for s in sources if s],
        sinks=[s for s in sinks if s],
        bram=xml_child_text(channel_item, "bram_cost"),
        uram=xml_child_text(channel_item, "uram_cost"),
        storage=xml_child_text(channel_item, "storage_size"),
    )


def _channel_insts(channel_item: ET.Element, is_source: bool) -> list[ET.Element]:
    out: list[ET.Element] = []

    list_tag = "source_list" if is_source else "sink_list"
    single_tag = "source" if is_source else "sink"

    for it in _items(xml_find_child(channel_item, list_tag)):
        out.extend(xml_find_all(it, "inst"))

    single = xml_find_child(channel_item, single_tag)
    out.extend(xml_find_all(single, "inst"))

    return out


def _inst_ssdmobj_id(inst: ET.Element, ssdm_map: dict[str, str]) -> str:
    sid = xml_child_text(inst, "ssdmobj_id")
    if sid.strip():
        return sid.strip()
    ref = xml_attr(inst, "object_id_reference")
    return ssdm_map.get(ref, "")


def _ssdmobj_id_map(
    process_items: list[ET.Element], channel_items: list[ET.Element]
) -> dict[str, str]:
    insts: list[ET.Element] = []
    for p in process_items:
        for pin in _items(xml_find_child(p, "pins")):
            insts.extend(xml_find_all(pin, "inst"))

    for c in channel_items:
        insts.extend(_channel_insts(c, True))
        insts.extend(_channel_insts(c, False))

    m: dict[str, str] = {}
    for inst in insts:
        oid = xml_attr(inst, "object_id")
        sid = xml_child_text(inst, "ssdmobj_id")
        if oid.strip() and sid.strip():
            m[oid.strip()] = sid.strip()
    return m


def get_cdfg_node(adb_info: AdbInfo, node_id: str) -> Optional[CdfgNodeInfo]:
    node_id = (node_id or "").strip()
    if not node_id:
        return None
    for n in adb_info.cdfg_nodes:
        if n.id == node_id:
            return n
    return None


def get_source_or_target_channels(
    pipe_process: PipeProcessInfo, adb_info: AdbInfo, source: bool
) -> list[PipeChannelInfo]:
    key = pipe_process.ssdmobj_id
    return [
        c for c in adb_info.pipe_channels if key in (c.sources if source else c.sinks)
    ]


def get_source_or_target_processes(
    pipe_channel: PipeChannelInfo, adb_info: AdbInfo, source: bool
) -> list[PipeProcessInfo]:
    ids = set(pipe_channel.sources if source else pipe_channel.sinks)
    return [p for p in adb_info.pipe_processes if p.ssdmobj_id in ids]
