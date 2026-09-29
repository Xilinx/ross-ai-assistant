#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import asdict
import json
import sys


from .dataflow_info_getter import get_dataflow_infos
from hls_common.solution_folder_getter import get_solution_folder


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <component_location>", file=sys.stderr)
        return 2
    dataflow_infos = get_dataflow_infos(get_solution_folder(sys.argv[1]) or "")
    # Keep output machine-readable while still human-friendly.
    print(
        json.dumps(
            [asdict(dataflow_info) for dataflow_info in dataflow_infos],
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
