<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# System Project (vadd, hw_emu) — Quick Start Prompts

The `vitis-system` skill composes by default and builds + packages only
when asked. Run both steps in the same Vitis workspace. Replace
`<your VCK190 extensible base platform>` with the exact VCK190 extensible base platform name
from your Vitis install (e.g. `xilinx_vck190_base_<release>_1`).

## Step 1 — Compose (compose-only by default)

```
Create a system project named sys_vadd from the vadd template on
<your VCK190 extensible base platform>.
```

Expected: the project is **composed (not built)** — containers assembled — and
the skill offers to build + package.

## Step 2 — Build + package (hw_emu)

```
Now build and package sys_vadd for hw_emu.
```

Expected: the component + link build runs and the emulation package
(`container.xclbin` + `launch_hw_emu.sh`) is produced. The agent then offers to run the
emulation package (in QEMU).
