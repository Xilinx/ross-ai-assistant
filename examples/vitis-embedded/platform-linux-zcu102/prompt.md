<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Linux Platform (zcu102, with device tree) — Quick Start Prompts

Run both steps in the same Vitis workspace.

## Create a Linux platform with a device tree

```
Create a Linux embedded platform named gm_zcu102_lnx for the zcu102 board on
psu_cortexa53 with a device tree.
```

Expected: the platform is **created (not built)** — the skill reports the name,
board, OS (linux), CPU, domain (`linux_psu_cortexa53`), that device-tree
generation is enabled, and that it targets ZynqMP (FSBL). It then offers to
build.

## Build it when you're ready

```
Build gm_zcu102_lnx.
```
