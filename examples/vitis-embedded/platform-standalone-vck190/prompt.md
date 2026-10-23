<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Standalone Platform (vck190) — Quick Start Prompts

The `vitis-embedded` skill creates by default and builds only when
asked. These two prompts show the create step, then the explicit build. Run both steps in the
same Vitis workspace.

## Step 1 — Create the platform (create-only by default)

```
Create a standalone embedded platform named gm_vck190_std for the vck190 board on
psv_cortexa72_0.
```

Expected: the platform is **created (not built)** — the skill reports the name,
board, OS (standalone), CPU, and domain (`standalone_psv_cortexa72_0`), and
offers to build it.

## Step 2 — Build it (explicit follow-up)

```
Now build gm_vck190_std.
```

Expected: the BSP compiles and a `.xpfm` is produced; the skill reports the
build succeeded and where the `.xpfm` is.
