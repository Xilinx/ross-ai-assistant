#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from pathlib import Path
import os
from typing import Callable, Optional


def _path_segments(path: str) -> list[str]:
    return [
        seg
        for segment in [segment for segment in path.split("/") if segment.strip()]
        for seg in [seg for seg in segment.split("\\") if seg.strip()]
    ]


def _split_path(raw_path: str) -> list[str]:
    path = raw_path.strip()
    segments = _path_segments(path)
    return (
        [
            path[0:1] + segments[0] if index == 0 else segments[index]
            for index in range(len(segments))
        ]
        if path.startswith("/") or path.startswith("\\")
        else segments
    )


def join_and_normalize(*segments: str) -> str:
    return os.path.normpath(
        os.path.join(*[seg for segment in segments for seg in _split_path(segment)])
    )


def is_absolute(path: str) -> bool:
    return Path(path).is_absolute()


def get_file_name(path: str) -> str:
    return Path(path).name


def get_relative_path(path: str, base_path: str) -> str:
    try:
        return os.path.relpath(path, base_path)
    except ValueError:
        return (
            path  # If paths are on different drives (Windows), return the original path
        )


def find_file(folder: str, func: Callable[[Path], bool]) -> Optional[str]:
    try:
        folder_path = Path(folder)
        if not folder_path.exists():
            return None
        for p in folder_path.iterdir():
            if p.is_file() and func(p):
                return str(p)
        for p in folder_path.iterdir():
            if p.is_dir():
                found = find_file(str(p), func)
                if found:
                    return found
    except OSError:
        pass
    return None


def _db_folder(solution_folder: str) -> str:
    return join_and_normalize(solution_folder, ".autopilot", "db")


def find_design_file_path(solution_folder: str) -> str:
    try:
        db = Path(_db_folder(solution_folder))
        if not db.exists():
            return ""
        for p in db.iterdir():
            if p.name.endswith(".design.xml") and p.is_file():
                return str(p)
    except OSError:
        pass
    return ""


def adb_file_path(solution_folder: str, module_name: str) -> str:
    return join_and_normalize(_db_folder(solution_folder), f"{module_name}.adb")


def process_csv_file_path(solution_folder: str) -> str:
    return join_and_normalize(_db_folder(solution_folder), "process_info.csv")


def process_zip_file_path(solution_folder: str) -> str:
    return join_and_normalize(
        _db_folder(solution_folder), "process_stalling_info", "process.zip"
    )


def channel_csv_file_path(solution_folder: str) -> str:
    return join_and_normalize(_db_folder(solution_folder), "channel_info.csv")


def channel_zip_file_path(solution_folder: str) -> str:
    return join_and_normalize(
        _db_folder(solution_folder), "channel_depth_info", "channel.zip"
    )


def loop_csv_file_path(solution_folder: str) -> str:
    return join_and_normalize(_db_folder(solution_folder), "loop_info.csv")


def loop_zip_file_path(solution_folder: str) -> str:
    return join_and_normalize(
        _db_folder(solution_folder), "loop_performance_info", "loop.zip"
    )


def reconfig_file_path(solution_folder: str) -> str:
    return join_and_normalize(_db_folder(solution_folder), "CosimGUI", "fifo_para.vh")


def global_setting_file_path(solution_folder: str) -> str:
    return join_and_normalize(_db_folder(solution_folder), "global.setting.tcl")


def cosim_rpt_file_path(solution_folder: str, top_module_name: str) -> str:
    rpt_dir = join_and_normalize(solution_folder, "sim", "report")
    rpt_file = join_and_normalize(rpt_dir, top_module_name + "_cosim.rpt")
    return rpt_file if Path(rpt_file).exists() else _find_cosim_rpt_file_path(rpt_dir)


def _find_cosim_rpt_file_path(rpt_dir: str) -> str:
    try:
        rpt_dir_path = Path(rpt_dir)
        if rpt_dir_path.exists():
            for p in rpt_dir_path.iterdir():
                if p.name.endswith("_cosim.rpt") and p.is_file():
                    return str(p)
    except OSError:
        pass
    return ""
