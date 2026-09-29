#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from pathlib import Path

from hls_common.file_util import path_exists, read_file

from .report_info_model import ReportInfo


_PLACE_ROUT_TIMING_SPLIT_MARKER = '== Place & Route Timing Summary'
_PLACE_ROUT_RESOURCE_SPLIT_MARKER = '== Place & Route Resource Summary'
_PLACE_ROUT_FAIL_SPLIT_MARKER = '== Place & Route Fail Fast'


def get_impl_info(solution_folder: str) -> ReportInfo:
    post_route, place_route_resource, place_route_fail = _get_impl_info_tuple(solution_folder)
    return ReportInfo(
        postRoute=post_route,
        placeRouteResourceSummary=place_route_resource,
        placeRouteFailFast=place_route_fail,
    )


def _impl_rpt_path(solution_folder: str) -> str:
    """Get the path to the implementation report file.

    Checks for both verilog and vhdl report directories and returns the first valid path.

    Args:
        solution_folder: Path to the solution folder

    Returns:
        Path to the export_impl.rpt file as a string

    Raises:
        FileNotFoundError: If neither verilog nor vhdl report directory exists
    """
    base_path = Path(solution_folder) / "impl" / "report"

    # Try verilog first, then vhdl
    for hdl_type in ["verilog", "vhdl"]:
        report_path = base_path / hdl_type / "export_impl.rpt"
        if path_exists(str(report_path)):
            return str(report_path)

    # If neither exists, return the verilog path as default
    # (the calling function will handle the non-existent file)
    return str(base_path / "verilog" / "export_impl.rpt")


def _get_impl_info_tuple(solution_folder: str) -> tuple[str, str, str]:
    """Extract place & route information from the implementation report.

    Args:
        solution_folder: Path to the solution folder

    Returns:
        A tuple containing:
        - Post-route timing value (str) or warning message (str)
        - Place & Route Resource Summary (str)
        - Place & Route Fail Fast content (str)
    """
    report_path = _impl_rpt_path(solution_folder)

    if not path_exists(report_path):
        error_msg = "Implementation report file does not exist"
        return error_msg, error_msg, error_msg

    content = read_file(report_path)

    # Extract post-route timing value
    post_route = _extract_post_route_timing(content)

    # Extract place & route resource summary
    place_route_resource = _extract_section(content, _PLACE_ROUT_RESOURCE_SPLIT_MARKER)

    # Extract place & route fail fast
    place_route_fail = _extract_section(content, _PLACE_ROUT_FAIL_SPLIT_MARKER)

    return post_route, place_route_resource, place_route_fail


def _extract_post_route_timing(content: str) -> str:
    """Extract the post-route timing value from the report content.

    Args:
        content: The full content of the implementation report

    Returns:
        Post-route timing value, or warning message as string
    """
    # Find the Place & Route Timing Summary section
    section_start = content.find(_PLACE_ROUT_TIMING_SPLIT_MARKER)
    if section_start == -1:
        return "Warning: Place & Route Timing Summary section not found"

    # Find the Post-Route line
    post_route_marker = '| Post-Route'
    post_route_pos = content.find(post_route_marker, section_start)

    if post_route_pos == -1:
        return "Warning: Post-Route timing value not found in report"

    # Extract the line containing Post-Route
    line_start = post_route_pos
    line_end = content.find('\n', post_route_pos)
    line = content[line_start:line_end]

    # Parse the value from the line: "| Post-Route     | 3.952       |"
    # Split by '|' and get the value from the second column
    parts = line.split('|')
    if len(parts) >= 3:
        try:
            value_str = parts[2].strip()           
            return value_str
        except ValueError:
            return "Warning: Could not parse Post-Route value"
    else:
        return "Warning: Could not parse Post-Route value"


def _extract_section(content: str, marker: str) -> str:
    """Extract marker section from the report content.

    Args:
        content: The full content of the implementation report
        marker: The marker string to search for

    Returns:
        Marker content str
    """
    # Find marker section
    section_start = content.find(marker)
    if section_start == -1:
        return "Warning: the section not found"

    # Find the end of this section (next == marker or end of file)
    # Look for the next section separator (line starting with ==)
    next_section_pos = content.find("\n\n", section_start + 1)
    if next_section_pos == -1:
        section_end = len(content)
    else:
        section_end = next_section_pos

    # Extract the section content
    section_content = content[section_start:section_end].rstrip()
    return section_content
