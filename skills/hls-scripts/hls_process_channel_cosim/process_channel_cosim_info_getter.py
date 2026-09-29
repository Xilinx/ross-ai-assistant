#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

import zipfile
from typing import Optional

from .process_channel_cosim_info_model import (
    CosimLoopInfo,
    CosimProcessInfo,
    CosimChannelInfo,
)
from hls_common.file_path_util import (
    channel_csv_file_path,
    channel_zip_file_path,
    loop_csv_file_path,
    loop_zip_file_path,
    process_csv_file_path,
    process_zip_file_path,
    reconfig_file_path,
)
from hls_common.file_util import path_exists, read_file


def get_process_cosim_infos(solution_folder: str) -> list[CosimProcessInfo]:
    z = _open_zip(process_zip_file_path(solution_folder))
    return [
        _parse_process(line, z)
        for line in _read_csv_lines(process_csv_file_path(solution_folder))
    ]


def _parse_process(line: str, z: Optional[zipfile.ZipFile]) -> CosimProcessInfo:
    values = [v.strip() for v in line.strip().split(",")]
    status = _read_zip_lines(z, _array_value(values, 3))
    return CosimProcessInfo(
        instance_path=_array_value(values, 0),
        rtl_name=_array_value(values, 1),
        stalling_time=_percentage(status, 0, 1),
        avg_ii=_array_value(status, 2),
        max_ii=_array_value(status, 3),
        min_ii=_array_value(status, 4),
        avg_latency=_array_value(status, 5),
        max_latency=_array_value(status, 6),
        min_latency=_array_value(status, 7),
        stall_no_start=_percentage(status, 8, 1),
        stall_no_continue=_percentage(status, 9, 1),
        read_block_time=_percentage(status, 10, 1),
        write_block_time=_percentage(status, 11, 1),
    )


def get_channel_cosim_infos(solution_folder: str) -> list[CosimChannelInfo]:
    z = _open_zip(channel_zip_file_path(solution_folder))
    return [
        _parse_channel(line, z)
        for line in _read_csv_lines(channel_csv_file_path(solution_folder))
    ]


def _parse_channel(line: str, z: Optional[zipfile.ZipFile]) -> CosimChannelInfo:
    values = [v.strip() for v in line.strip().split(",")]
    status = _read_zip_lines(z, _array_value(values, 3))
    return CosimChannelInfo(
        instance_path=_array_value(values, 0),
        rtl_name=_array_value(values, 1),
        category=_array_value(status, 0),
        read_block_time=_percentage(status, 2, 3),
        write_block_time=_percentage(status, 1, 3),
        cosim_max_depth=_array_value(status, 4),
    )


def get_loop_cosim_infos(solution_folder: str) -> list[CosimLoopInfo]:
    z = _open_zip(loop_zip_file_path(solution_folder))
    return [
        _parse_loop(line, z)
        for line in _read_csv_lines(loop_csv_file_path(solution_folder))
    ]


def _parse_loop(line: str, z: Optional[zipfile.ZipFile]) -> CosimLoopInfo:
    values = [v.strip() for v in line.strip().split(",")]
    status = _read_zip_lines(z, _array_value(values, 2))
    return CosimLoopInfo(
        instance_path=_array_value(values, 0),
        loop_name=_array_value(values, 1),
        loop_avg_latency=_array_value(status, 0),
        loop_max_latency=_array_value(status, 1),
        loop_min_latency=_array_value(status, 2),
        loop_avg_ii=_array_value(status, 3),
        loop_max_ii=_array_value(status, 4),
        loop_min_ii=_array_value(status, 5),
        iter_avg_latency=_array_value(status, 6),
        iter_max_latency=_array_value(status, 7),
        iter_min_latency=_array_value(status, 8),
        iter_avg_ii=_array_value(status, 9),
        iter_max_ii=_array_value(status, 10),
        iter_min_ii=_array_value(status, 11),
    )


def get_reconfig_file_lines(solution_folder: str) -> list[str]:
    p = reconfig_file_path(solution_folder)
    if not path_exists(p):
        return []
    return [ln.strip() for ln in read_file(p).splitlines() if ln.strip()]


def _open_zip(zip_path: str) -> Optional[zipfile.ZipFile]:
    try:
        if path_exists(zip_path):
            return zipfile.ZipFile(zip_path)
    except Exception:
        return None
    return None


def _read_zip_lines(z: Optional[zipfile.ZipFile], entry_name: str) -> list[str]:
    if not z or not entry_name:
        return []
    try:
        with z.open(entry_name) as f:
            return f.read().decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return []


def _read_csv_lines(csv_path: str) -> list[str]:
    try:
        if path_exists(csv_path):
            return [ln for ln in read_file(csv_path).splitlines() if ln.strip()]
    except Exception:
        pass
    return []


def _array_value(values: list[str], idx: int) -> str:
    if idx < len(values):
        v = values[idx].strip()
        return "" if v == "4294967295" else v
    return ""


def _percentage(values: list[str], numerator_idx: int, denominator_idx: int) -> str:
    num_s = _array_value(values, numerator_idx)
    den_s = _array_value(values, denominator_idx)
    if not num_s or not den_s:
        return ""
    try:
        num = float(num_s)
        den = float(den_s)
    except ValueError:
        return ""
    if den == 0:
        return "0" if num == 0 else ""
    return str(num / den)
