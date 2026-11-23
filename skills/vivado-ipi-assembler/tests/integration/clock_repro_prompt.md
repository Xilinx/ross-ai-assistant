<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Clock Reproducibility Prompt

Create only the clocks required by the current design, using board revision
`amd.com:rave2:part0:1.3`, PCB A.0 and part `xc2ve3558-sfva1440-2MP-e-S`.
Board files are supplied under `inputs/rave2_board/board_files`.

In the following order, create these outputs. Nominal settings and exact
propagated rates are different requirements; do not substitute either for the
other and do not allow tolerance:

| Output | Nominal Setting (Hz) | Exact Propagated Rate (Hz) |
|---|---:|---:|
| display_1x | 74250000 | 74251394 |
| display_2x | 148500000 | 148502788 |
| audio | 22579200 | 22579371 |
| clk_250 | 250000000 | 249997500 |
| clk_400 | 400000000 | 399996000 |

The display clocks are one coherent group. Choose clock generator count,
partition and output order algorithmically; none is specified here. Do not
copy a reference implementation. This fixture checks clock generation, not
reset topology, full-system integration, analog accuracy or routed timing.

## Allowed Inputs

Read only this prompt, the supplied board directory, current installed Vivado
IP metadata, the current `vivado-ip-configurator` and `vivado-ipi-assembler` code, and files
created in your own new trial directory. Do not read prior trial outputs,
delivery projects, reference ZIP/BD/Tcl/semantic files, repository history or
memory. No web/reference-design lookup during a scored run. No edits to the
shared implementation or this prompt during a scored run.

## Trial Procedure

1. Create a new request JSON from the table, in your new trial directory. Use
   the same five IDs/order. Top-level fields: `part`, `board_part`, `clocks`.
   Each clock has `id`, `requested_hz`, `hz`, `tolerance_hz: 0`; add
   `coherent_group: "display"` only to the first two clocks.
2. Start a fresh Tcl-mode Vivado process. Use the supplied machine executable
   path, never a shell-launched batch process. Do not use an existing session.
3. Set the Tcl globals `clock_repro_root`, `clock_repro_request`,
   `clock_repro_output`, and `clock_repro_log` to the workspace root, your request,
   your fresh output directory and the exact MCP startup log path, respectively.
4. Source `tests/integration/test_clock_repro_live.tcl` from the assembler skill.
   The runner discovers the board source and available vector wizard, generates
   candidates using the shared planner and checks actual output pins.
5. Return the status, elapsed milliseconds, evidence path, selected candidate,
   grouping and five observed frequencies. Retain failures. No manual tuning,
   retries with changed requirements, or claims based only on requested CONFIG.
6. Stop only your trial session. A separate evaluator will compare all three
   results after generation is finished.

The design-specific values above come from `inputs/DesignDescription.md`, not
from an implementation. They belong in test data, never in reusable algorithms.