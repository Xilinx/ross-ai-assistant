<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Standalone Platform on vck190

Create a standalone (baremetal) Vitis embedded platform for the Versal **vck190**
board on `psv_cortexa72_0`, then build it — demonstrating the skill's
**create-only-by-default, build-on-request** behavior.

## Goal

1. **Create** a standalone platform `gm_vck190_std` (created, unbuilt).
2. **Build** it on an explicit follow-up (BSP compiled, `.xpfm` produced).

## Skills Used

- **vitis-embedded** — resolves the board/CPU/domain, calls
  `create_platform_component(...)`, and (only when asked) `platform.build()`.
  Inside the Vitis IDE this runs via `runVitisPython`; from other agents it runs
  headless via `vitis -s`.

## Prerequisites

- A licensed **Vitis** install on `PATH` (`source <install>/Vitis/settings64.sh`).
- No hardware required — this creates/builds a platform, it does not program a board.

## How to Run

Follow the prompts in [`prompt.md`](prompt.md): create first, then build.

## Expected Behavior

- After Step 1: `gm_vck190_std` exists as a **created (not built)** platform;
  the skill reports name/board/OS/CPU/domain and offers to build.
- After Step 2: the build succeeds and the skill reports the `.xpfm` path.
