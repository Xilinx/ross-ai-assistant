#Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReportInfo:
    postRoute: str
    placeRouteResourceSummary: str
    placeRouteFailFast: str
