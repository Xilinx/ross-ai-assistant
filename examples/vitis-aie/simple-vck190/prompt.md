<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Simple AIE Component (vck190) — Quick Start Prompts

Run all steps in the same Vitis workspace.

## Step 1 — Create the component (create-only by default)

```
Create a simple AIE component named aie_simple for the vck190 part.
```

Expected: the component is **created (not built)** from the shipped `simple`
example on part `xcvc1902-vsva2197-2MP-e-S`; the skill offers to build/compile.

## Step 2 — Build it (x86sim), then simulate

```
Build aie_simple with x86sim, then run the x86sim functional simulation.
```

Expected: the build produces `libadf.a`; the `x86simulator` runs in a shell and
reports "Simulation completed successfully".

## Step 3 — Make it integration-ready (optional)

```
Name aie_simple's input PLIO DataIn1 and output PLIO DataOut1, then rebuild it.
```

Expected: the component is rebuilt with named PLIOs `DataIn1` / `DataOut1`, ready for the
system integration example.
