<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Integration Project (AIE + HLS, vck190) — Quick Start Prompts

The `vitis-system` skill composes by default and runs the V++
link only when asked. Run both steps in the same Vitis workspace. Replace
`<your VCK190 extensible base platform>` with the exact VCK190 extensible base platform name
from your Vitis install (e.g. `xilinx_vck190_base_<release>_1`).

## Step 1 — Compose (compose-only by default)

```
Create an integration project named int_compose_simple that links AIE component aie_simple
(input PLIO DataIn1, output PLIO DataOut1) with HLS kernels mm2s and s2mm on
<your VCK190 extensible base platform>.
```

Expected: the project is **composed (not linked)** — the skill wires the AIE +
HLS kernels and writes `system.cfg`, then offers to run the V++ link. If
`aie_simple` is created-but-unbuilt, it offers to build it first.

## Step 2 — Link it (V++, hw_emu)

```
Now build int_compose_simple for hw_emu.
```

Expected: the V++ link runs and produces a fixed `binary_container_1.xsa`.
