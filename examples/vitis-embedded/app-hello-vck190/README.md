<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Hello-World App on vck190

Create a standalone hello_world Vitis embedded application on a built vck190
platform, then build it — showing the **create-only-by-default, build-on-request**
behavior.

## Goal

1. **Create** the app `app_vck190_std` on `gm_vck190_std` (created, unbuilt).
2. **Build** it on an explicit follow-up (an `.elf` is produced).

## Skills Used

- **vitis-embedded** — resolves the platform's domain, calls
  `create_app_component(...)`, and (only when asked) `app.build()`. If the target
  platform is created-but-unbuilt, it offers to build the platform first. Runs
  via `runVitisPython` in the Vitis IDE, or headless via `vitis -s` elsewhere.

## Prerequisites

- A licensed **Vitis** install on `PATH` (`source <install>/Vitis/settings64.sh`).
- A **built** platform; pass its full `.xpfm` path
  (`<workspace>/gm_vck190_std/export/gm_vck190_std/gm_vck190_std.xpfm`), not just the name — the
  skill offers to build one if needed.

## How to Run

Follow the prompts in [`prompt.md`](prompt.md): create first, then build.

## Expected Behavior

- After Step 1: `app_vck190_std` exists **created (not built)**; the skill reports
  platform/OS/domain/template and offers to build.
- After Step 2: the build succeeds and the skill reports the `.elf` path.
