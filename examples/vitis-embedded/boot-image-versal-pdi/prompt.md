<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Versal Boot PDI — Quick Start Prompts

The `vitis-embedded` skill authors a BIF as the **create** step and runs
Bootgen as the **build** step. Run both steps in the same Vitis workspace.

## Step 1 — Author the BIF (create-only)

```
Create a Versal boot PDI from app app_vck190_std on platform gm_vck190_std.
```

Expected: the skill authors a Versal BIF from the platform XSA's own BIF (PLM + required
PL/NoC partitions), adds the app `.elf`, reports the BIF, and offers to run Bootgen — the image
is **not built yet**. If the app is unbuilt, it offers to build the app first.

## Step 2 — Run Bootgen (build the image)

```
Now run Bootgen to build the design.pdi.
```

Expected: Bootgen runs and produces `design.pdi` (validated non-trivial); the image is ready
for the user's preferred JTAG, flash, or SD-card flow.
