#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import asdict
import json
import sys

from .report_info_getter import get_report_info
from hls_common.solution_folder_getter import get_solution_folder


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <component_location>", file=sys.stderr)
        return 2
    report_info = get_report_info(get_solution_folder(sys.argv[1]) or "")
    # Keep output machine-readable while still human-friendly.
    print(json.dumps(asdict(report_info), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
