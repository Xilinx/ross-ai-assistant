<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# RTL Lint Multi-Violation

A single Verilog module with intentional RTL lint violations across multiple
categories, then analyzed with the `rtl-lint` skill.

The design in `input/` is a single RTL file — no Vivado project required. The
skill runs `synth_design -lint` directly on the source. It depends on the
**`rtl-lint` skill**, which lives at [`skills/rtl-lint/`](../../../skills/rtl-lint/)
in this repo.

## Goal

Trigger at least 8 distinct lint rule violations from different categories and
let the skill detect, report, and propose fixes for each one:

1. **ASSIGN-1** — Arithmetic overflow (result wider than output)
2. **ASSIGN-2** — Mixed signed/unsigned arithmetic
3. **ASSIGN-3** — Shift amount exceeds signal width
4. **ASSIGN-5** — Signal used but never assigned
5. **ASSIGN-6** — Signal assigned but never read (dead code)
6. **ASSIGN-14** — Duplicate case branches
7. **INFER-1** — Latch inferred (missing else in combinational block)
8. **INFER-2** — Incomplete case statement (no default)
9. **CLOCK-1** — Mixed clock edges (posedge + negedge same clock)

## Skills Used

- **rtl-lint** — loads the RTL file, runs `synth_design -lint`, parses the
  violation report, and generates a markdown report with per-violation analysis
  including source code context, root cause, and proposed fixes with diffs.
  All operations go through Vivado Tcl via `vivado_execute`.

## Prerequisites

- **Vivado MCP server** — provides the Tcl interface for `synth_design -lint`.
- Vivado 2026.1+ installed.
- No hardware or synthesis required — lint is a pre-synthesis check.

## Starting Point

Input files in `input/`:
- `src/lint_violation_top.v` — Single Verilog module with 10 intentional bugs:
  arithmetic overflow, mixed signedness, excessive shift, undriven signals,
  dead code, duplicate case items, latches, incomplete case, mixed clock edges,
  and incomplete reset coverage.

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md).** This is a single-step
tutorial:

1. **Lint** — The skill loads the RTL, runs `synth_design -lint`, parses the
   report, and generates a per-violation analysis with fix proposals.

## Expected Behavior

The `rtl-lint` skill will:
1. Load the Verilog file via `read_verilog`.
2. Run `synth_design -top lint_violation_top -part xcvc1902-vsva2197-2MP-e-S -lint -file lint_report.rpt`.
3. Parse the report (11 violations across 8 rule IDs).
4. For each violation, read the source code context, identify root cause,
   and propose a fix with diff.
5. Generate `rtl_lint_report.md` with summary table and per-violation sections.

### Verified Violations (Vivado 2026.1)

| Rule ID | Count | Severity | Description |
|---------|-------|----------|-------------|
| ASSIGN-1 | 1 | WARNING | Arithmetic overflow at line 137 |
| ASSIGN-2 | 1 | CRITICAL | Mixed signedness at line 45 |
| ASSIGN-3 | 1 | CRITICAL | Shift overflow at line 53 |
| ASSIGN-5 | 1 | WARNING | Undriven phantom_sig at line 59 |
| ASSIGN-6 | 2 | WARNING | Dead code (dead_signal, mixed_result bit 8) |
| ASSIGN-14 | 1 | CRITICAL | Duplicate case branch at line 75 |
| INFER-1 | 2 | CRITICAL | Latches (mux_out, sel_out) at lines 90, 101 |
| INFER-2 | 1 | CRITICAL | Incomplete case at line 100 |
| CLOCK-1 | 1 | CRITICAL | Mixed clock edges at line 127 |

Total: 11 violations, 6 CRITICAL + 5 WARNING.
