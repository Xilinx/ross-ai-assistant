<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Resolution Guide Integration

This file explains the resolution guide system and how to use it when processing
linter violations.

---

## Overview

Resolution guides are pre-written, validated fix templates for known violation types.
They live in `resolution/<RULE-ID>.md` and provide:

- **Consistency** — Same violation type always gets the same fix pattern
- **Accuracy** — Fixes validated against IEEE standards and Xilinx documentation
- **Completeness** — Includes rationale, verification, and documentation references
- **Speed** — Pre-written templates reduce analysis time

---

## Available Guides

| Rule ID    | Description |
|------------|-------------|
| ASSIGN-1   | Width mismatch in assignments |
| ASSIGN-2   | Signed/unsigned arithmetic mixing |
| ASSIGN-3   | Shift overflow (right/left shift exceeding operand width) |
| ASSIGN-5   | Unassigned signal usage |
| ASSIGN-6   | Signal assigned but never used |
| ASSIGN-7   | Multiple assignments to same signal bit |
| ASSIGN-8   | Array dimension mismatch in comparison |
| ASSIGN-9   | Unassigned output port bits |
| ASSIGN-10  | Unused I/O port bits |
| ASSIGN-12  | Unconnected/tri-state port connections |
| ASSIGN-14  | Duplicate case branches |
| INFER-1    | Latch inference (unintended combinational latch) |
| INFER-2    | Incomplete case statement (missing default) |
| INFER-3    | Case equality (`===`) converted to `==` for synthesis |
| CLOCK-1    | Mixed clock edges (posedge/negedge) |
| QOR-1      | Arithmetic precision loss preventing operator merging |
| RESET-1    | Multiple async reset/set on same register |
| RESET-2    | Incomplete async reset coverage |
| RESET-3    | Incomplete sync reset / enable driven by sync reset |

*Check `resolution/` directory for the latest list — more guides are added as violations
are encountered and documented.*

---

## Mandatory Workflow (Per Violation)

```
1. Detect violation in lint_report.rpt (e.g., ASSIGN-3)
2. Check for guide: resolution/ASSIGN-3.md
3. If guide EXISTS:
   a. Load complete guide with read_file
   b. Follow fix recommendations exactly as documented
   c. Use provided code templates (replace placeholders with actual design objects)
   d. Include the rationale from the guide in your analysis
   e. Reference the documentation links provided
4. If guide does NOT exist:
   a. Use vivado_doc_search + general knowledge
   b. Note in report: "No resolution guide available for [RULE-ID]"
```

---

## Example Workflow — ASSIGN-3

```
Step 1: Parse lint_report.rpt
   Found: ASSIGN-3 violation at top.v:21

Step 2: Load resolution guide
   read_file → resolution/ASSIGN-3.md

Step 3: Extract violation specifics
   - Signal: a[12:0] (13 bits)
   - Operator: >> (right shift)
   - Shift amount: 30
   - File: top.v, Line: 21

Step 4: Select fix from guide
   Fix Option 1: Correct shift parameter (SHIFTSIZE = 30 → 2)

Step 5: Generate report section using guide template
   - Include issue description from guide
   - Show before/after code using diff syntax
   - Add rationale from guide
   - Reference UG901 documentation

Step 6: Output formatted markdown
   See "Example Agent Output Template" in ASSIGN-3.md
```

---

## Naming Convention for New Guides

- File: `resolution/<RULE-ID>.md` (e.g., `TIMING-18.md`, `CDC-1.md`, `SYNTH-8.md`)
- Required sections: Problem Statement, Detection Pattern, Standard Resolution, Fix Template
