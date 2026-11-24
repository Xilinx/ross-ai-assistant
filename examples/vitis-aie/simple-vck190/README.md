<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Simple AIE Component on vck190

Create an AI Engine component from the shipped `simple` example on an AIE-capable
Versal part, then build (x86sim) and simulate it — showing the
**create-only-by-default, build-on-request** behavior.

## Goal

1. **Create** `aie_simple` from the `simple` example (created, unbuilt).
2. **Build** it with `x86sim` (`libadf.a` produced), then run the functional simulator.

## Skills Used

- **vitis-aie** — calls `create_aie_component(...)` from the shipped
  example, and (only when asked) `comp.build(target="x86sim")`. The simulator is
  a separate shell step. Runs via `runVitisPython` in the Vitis IDE, or headless
  via `vitis -s` elsewhere.

## Prerequisites

- A licensed **Vitis** install on `PATH` (`source <install>/Vitis/settings64.sh`).
- An **AIE-capable Versal part** (e.g. `xcvc1902-vsva2197-2MP-e-S`); embedded /
  ZynqMP targets have no AI Engine and are rejected.

## How to Run

Follow the prompts in [`prompt.md`](prompt.md): create first, then build + simulate.

## Expected Behavior

- After Step 1: `aie_simple` exists **created (not built)**.
- After Step 2: the build produces `libadf.a` and the `x86simulator` reports success.

## Make it integration-ready (optional)

The shipped `simple` example's PLIOs are **unnamed**, so `aie_simple` as-is is a standalone
build/sim example. To reuse it in the
[system integration example](../../vitis-system/integration-compose-aie-hls/), name its PLIOs
and rebuild: set the input PLIO to `DataIn1` and the output PLIO to `DataOut1`, then rebuild the
component. The integration step wires those stable names.
