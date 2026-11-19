<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# System Project — vadd (hw_emu) on vck190

Compose a Vitis system project from the `vadd` template on an extensible base
platform, then build + package a deployable emulation image — showing the
**compose-only-by-default, build-on-request** behavior.

## Goal

1. **Compose** `sys_vadd` (vadd template) on your VCK190 extensible base platform
   (composed, unbuilt).
2. **Build + package** it for `hw_emu` (`container.xclbin` + `launch_hw_emu.sh`).

## Skills Used

- **vitis-system** — assembles binary containers, and (only when asked)
  runs the component + link build and packaging (`proj.build(target="hw_emu",
  build_comps=True, build_package=False)`). Consumes integration/AIE by name and
  offers to build an unlinked/unbuilt upstream. Runs via `runVitisPython` in the
  Vitis IDE, or headless via `vitis -s` elsewhere.

## Prerequisites

- A licensed **Vitis** install on `PATH` (`source <install>/Vitis/settings64.sh`).
- An **extensible base platform** — the VCK190 extensible base from your Vitis install (its name
  encodes the release, e.g. `xilinx_vck190_base_<release>_1`). Use the exact name from your
  installation; the agent confirms it resolves to a single `.xpfm`.

## How to Run

Follow the prompts in [`prompt.md`](prompt.md): compose first, then build + package.

## Expected Behavior

- After Step 1: `sys_vadd` is **composed (not built)**.
- After Step 2: the emulation package (`container.xclbin` + `launch_hw_emu.sh`) is produced.
