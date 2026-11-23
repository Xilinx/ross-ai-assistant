<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Integration Project — AIE + HLS on vck190

Compose an integration project that links an AI Engine component with HLS data
movers on an extensible base platform, then run the V++ link — showing the
**compose-only-by-default, link-on-request** behavior.

## Goal

1. **Compose** `int_compose_simple` (`aie_simple` with named PLIOs + HLS `mm2s`/`s2mm`) on
   your VCK190 extensible base platform (composed, unlinked).
2. **Link** it for `hw_emu` (a fixed `binary_container_1.xsa` is produced).

## Skills Used

- **vitis-system** — composes container options + `system.cfg`
  connectivity, and (only when asked) runs the V++ link (`proj.build(target=
  "hw_emu")`). Consumes AIE components by name and offers to build an
  unbuilt one. Runs via `runVitisPython` in the Vitis IDE, or headless via
  `vitis -s` elsewhere.

## Prerequisites

- A licensed **Vitis** install on `PATH` (`source <install>/Vitis/settings64.sh`).
- An **extensible base platform** — the VCK190 extensible base from your Vitis install (its name
  encodes the release, e.g. `xilinx_vck190_base_<release>_1`). Use the exact name from your
  installation; the agent confirms it resolves to a single `.xpfm`.
- **Built HLS components** `mm2s` and `s2mm` (or their `.xo` files).
- A **built** AIE component with exactly **one named input PLIO and one named output PLIO**
  (`DataIn1` / `DataOut1`) — e.g. `aie_simple` after the optional naming step in the
  [vitis-aie example](../../vitis-aie/simple-vck190/); the skill offers to build an unbuilt one.
- The required **port names** — the `stream_connect` endpoints `system.cfg` wires
  (`DataIn1`, `DataOut1`, and the `mm2s`/`s2mm` AXI-Stream ports).

## How to Run

Follow the prompts in [`prompt.md`](prompt.md): compose first, then link.

## Expected Behavior

- After Step 1: `int_compose_simple` is **composed (not linked)** with `system.cfg`
  containing the `stream_connect` entries.
- After Step 2: the V++ link produces a fixed `.xsa`.
