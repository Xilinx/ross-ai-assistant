<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Versal Boot PDI

Author a BIF and run Bootgen to produce a Versal boot PDI from a built
application — showing the skill's **author-the-BIF (create) then run-Bootgen
(build)** split.

## Goal

1. **Author** the Versal BIF from the platform XSA's own BIF — PLM + the required boot
   partitions, including any PL/NoC (`pl_cfi`) configuration — then add the app `.elf` for
   `app_vck190_std` on `gm_vck190_std` (create step — image not emitted).
2. **Run Bootgen** to produce `design.pdi` (build step).

## Skills Used

- **vitis-embedded** — a shell skill: it resolves boot components, authors a
  BIF from the source-cited references, and (only when asked) runs the standalone
  `bootgen` CLI. It consumes a **built** app `.elf` + platform boot components,
  and offers to build an unbuilt prerequisite first.

## Prerequisites

- A licensed **Vitis** install on `PATH` so `bootgen` is available
  (`source <install>/Vitis/settings64.sh`).
- A **built** app `.elf` and platform boot components (PLM); the skill offers to
  build them if missing.

## How to Run

Follow the prompts in [`prompt.md`](prompt.md): author the BIF first, then run Bootgen.

## Expected Behavior

- After Step 1: the Versal BIF is authored and reported; **no image yet**.
- After Step 2: Bootgen produces `design.pdi` (validated); the image is ready for the user's
  preferred JTAG, flash, or SD-card flow.
