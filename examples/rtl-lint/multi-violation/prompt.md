<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# RTL Lint Multi-Violation — Quick Start Prompts

The example ships as a single RTL source file with intentional lint violations.
No project creation is needed — the skill runs `synth_design -lint` directly on
the Verilog file.

## Step 1 — Run RTL lint on the design

```
Use /rtl-lint to lint the file input/src/lint_violation_top.v targeting part
xcvc1902-vsva2197-2MP-e-S. The design has intentional violations across
multiple categories (ASSIGN, INFER, CLOCK). Detect all violations, generate
the lint report, and propose fixes for each one.
```
