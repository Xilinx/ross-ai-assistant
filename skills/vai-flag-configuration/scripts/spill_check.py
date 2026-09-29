# Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
"""Decide whether a compiled model's L3 spilling warrants a TSG handoff.

Reads a backend ``DetailedSpillingAnalysis.csv`` and classifies each spilling
layer using the report's own columns rather than reimplementing the backend's
L2-layout logic (which is context-dependent and easy to get wrong):

  * column D -- ``Spill or not`` -- did the layer spill at all;
  * column W -- ``Layer Spill Reason`` -- why it spilled.

A spill is *FM-driven* (something TSG can fix by tiling the OFM H/W) iff its
reason contains ``l2FM Size Overflow`` -- the backend's marker for a feature-map
that overflows L2. Every other
reason (successor-forced/decimated tiling, concat, double-buffer fanout,
pseudo-op, weights-driven, ...) is structural and TSG cannot help.

Decision-only: the actual tiling is done by the ``tsg-feature`` skill, which
is internal to FlexML. Note this is intentionally conservative:
a spill the backend attributes to another cause is not routed to TSG even if, in
some layouts, splitting the FMs might still help -- the report does not expose
that, so we do not guess.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

# Substring of the backend "Layer Spill Reason" that marks an FM-size L2
# overflow. Matched case-insensitively and as a substring, since the reason
# column concatenates multiple "; ..." fragments.
FM_OVERFLOW_MARKER = "l2FM Size Overflow"


@dataclass
class SpillLayer:
    name: str
    kernel_type: str
    fm_mem_tiles: float  # informational (reported), not used for the decision
    wts_mem_tiles: float  # informational (reported), not used for the decision
    spill_reason: str

    @property
    def is_fm_driven(self) -> bool:
        return FM_OVERFLOW_MARKER.lower() in self.spill_reason.lower()


def _num(value: object) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def load_spilling_layers(csv_path: str | Path) -> list[SpillLayer]:
    path = Path(csv_path)
    layers: list[SpillLayer] = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            row = {(k.strip() if k else k): v for k, v in raw.items()}
            if str(row.get("Spill or not", "")).strip().lower() != "yes":
                continue
            layers.append(
                SpillLayer(
                    name=str(row.get("LayerName", "")).strip(),
                    kernel_type=str(row.get("KernelType", "")).strip(),
                    fm_mem_tiles=_num(row.get("FM size in mem tiles")),
                    wts_mem_tiles=_num(row.get("Wts size in mem tiles")),
                    spill_reason=str(row.get("Layer Spill Reason", "")).strip(),
                )
            )
    return layers


def fm_driven_spills(csv_path: str | Path) -> list[SpillLayer]:
    """Spilling layers the backend attributes to an FM-size L2 overflow.

    These are the TSG candidates. Layers that spill for any other reason -- or
    reports missing the ``Layer Spill Reason`` column -- yield nothing here.
    """
    return [layer for layer in load_spilling_layers(csv_path) if layer.is_fm_driven]


def find_spilling_csv(work_dir: str | Path) -> Path | None:
    for path in sorted(Path(work_dir).rglob("DetailedSpillingAnalysis.csv")):
        return path
    return None
