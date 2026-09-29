#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


def read_file(file_path: str) -> str:
    try:
        return Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def read_json(file_path: str) -> Optional[Any]:
    try:
        return json.loads(read_file(file_path))
    except Exception:
        return None


def write_json(file_path: str, obj: Any) -> None:
    try:
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    except Exception:
        return


def path_exists(file_path: str) -> bool:
    try:
        return Path(file_path).exists()
    except Exception:
        return False
