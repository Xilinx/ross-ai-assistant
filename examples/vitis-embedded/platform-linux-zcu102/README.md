<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Linux Platform on zcu102 (with device tree)

Create a Linux Vitis embedded platform for the ZynqMP **zcu102** board on
`psu_cortexa53`, with device-tree generation enabled.

## Goal

- **Create** a Linux platform `gm_zcu102_lnx` (created, unbuilt) with a device tree.
- Optionally **build** it on a follow-up.

## Skills Used

- **vitis-embedded** — resolves the ZynqMP board/CPU, adds a
  `linux_psu_cortexa53` domain with device-tree generation, and (only when
  asked) builds it. Runs via `runVitisPython` in the Vitis IDE, or headless via
  `vitis -s` from other agents.

## Prerequisites

- A licensed **Vitis** install on `PATH` (`source <install>/Vitis/settings64.sh`).
- No hardware required.

## How to Run

Follow the prompts in [`prompt.md`](prompt.md).

## Expected Behavior

- The Linux platform `gm_zcu102_lnx` is **created (not built)** with the
  `linux_psu_cortexa53` domain and device-tree generation enabled (ZynqMP, FSBL).
- On the follow-up build, the BSP compiles and a `.xpfm` is produced.
