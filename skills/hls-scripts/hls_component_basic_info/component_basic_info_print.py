#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import asdict
import json
import sys

from .component_basic_info_getter import get_component_basic_info


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <component_location>", file=sys.stderr)
        return 2
    component_location = sys.argv[1]
    info = get_component_basic_info(component_location)
    if info:
        # Keep output machine-readable while still human-friendly.
        print(json.dumps(asdict(info), indent=2, ensure_ascii=False))
    else:
        print(
            f"Error: No Vitis component found at '{component_location}'",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
