#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from pathlib import Path

from hls_common.file_util import path_exists, read_file

from .report_info_model import ReportInfo

_REPORT_SPLIT_MARKER = "Name Prefix: '+' for module, 'o' for loop, '*' for dataflow"
_PRAGMA_REPORT_SPLIT_MARKER = "== Pragma Report"
_LOG_INFO_KEEP_KEYWORDS = ("violation", "array_partition", "unroll", "flatten")


def get_report_info(solution_folder: str) -> ReportInfo:
    synth_report, pragma_report = _get_synthesis_report_content(solution_folder)
    log_content = _filter_info_msg_in_log(_synthesis_log_path(solution_folder))
    return ReportInfo(
        synthesisLogContent=log_content,
        synthesisReportContent=synth_report,
        synthesisPragmaReportContent=pragma_report,
    )


def _csynth_rpt_path(solution_folder: str) -> str:
    return str(Path(solution_folder) / "syn" / "report" / "csynth.rpt")


def _synthesis_log_path(solution_folder: str) -> str:
    return str(Path(solution_folder) / ".." / "logs" / "hls_compile.log")


def _get_synthesis_report_content(solution_folder: str) -> tuple[str, str]:
    pragma_report = "Pragama report content not found"
    report_path = _csynth_rpt_path(solution_folder)

    if not path_exists(report_path):
        return "Synthesis report file does not exist", pragma_report

    content = read_file(report_path)

    index = content.find(_REPORT_SPLIT_MARKER)
    if index == -1:
        return "Synthesis report content not found", pragma_report

    synth_report = content[: index + len(_REPORT_SPLIT_MARKER)]

    pragma_index = content.find(_PRAGMA_REPORT_SPLIT_MARKER)
    if pragma_index != -1:
        pragma_report = content[pragma_index:]

    return synth_report, pragma_report


def _filter_info_msg_in_log(log_path: str) -> str:
    if not path_exists(log_path):
        return "Synthesis log file does not exist"
    content = read_file(log_path)
    return "\n".join(_filter_log_lines(content.split("\n")))


def _filter_log_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        if "INFO:" not in line:
            if "[HLS 207-" in line:
                continue
            result.append(line)
            continue
        lower = line.lower()
        if any(kw in lower for kw in _LOG_INFO_KEEP_KEYWORDS):
            result.append(line)
    return result
