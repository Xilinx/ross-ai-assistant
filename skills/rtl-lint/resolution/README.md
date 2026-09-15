<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# RTL Lint Resolution Guides

Per-violation fix references for `synth_design -lint`. Each file provides the Vivado error pattern, root cause, fix options with code examples, and validation TCL.

## Available Guides

| Rule | Severity | Description | Fix |
|------|----------|-------------|-----|
| [ASSIGN-1](ASSIGN-1.md) | WARNING | Arithmetic overflow / truncation | Widen output or saturate |
| [ASSIGN-2](ASSIGN-2.md) | CRITICAL | Mixed signed/unsigned arithmetic | `$signed()` casting |
| [ASSIGN-3](ASSIGN-3.md) | CRITICAL | Shift amount exceeds width | Correct parameter or add validation |
| [ASSIGN-5](ASSIGN-5.md) | WARNING | Signal used but not assigned | Remove or add driver |
| [ASSIGN-6](ASSIGN-6.md) | WARNING | Signal assigned but not read | Remove dead code |
| [ASSIGN-7](ASSIGN-7.md) | CRITICAL | Multiple drivers on same bits | Non-overlapping bit ranges |
| [ASSIGN-8](ASSIGN-8.md) | CRITICAL | Array dimension mismatch in comparison | Match operand dimensions |
| [ASSIGN-9](ASSIGN-9.md) | WARNING | Unassigned output port bits | Remove port or complete assignment |
| [ASSIGN-10](ASSIGN-10.md) | WARNING | Unused I/O port bits | Remove port or reduce width |
| [ASSIGN-12](ASSIGN-12.md) | CRITICAL | Port connected to tri-state (1'bz) | Tie to constant |
| [ASSIGN-14](ASSIGN-14.md) | CRITICAL | Duplicate case branches | Remove duplicates or correct values |
| [INFER-1](INFER-1.md) | CRITICAL | Latch inference | Add else/default or convert to sequential |
| [INFER-2](INFER-2.md) | CRITICAL | Incomplete case statement | Add default clause |
| [INFER-3](INFER-3.md) | INFO | `===` converted to `==` for synthesis | Replace with `==` or guard with translate_off |
| [CLOCK-1](CLOCK-1.md) | CRITICAL | Mixed clock edges (posedge/negedge) | Unify to single edge |
| [QOR-1](QOR-1.md) | WARNING | Arithmetic precision loss (operator merging) | Preserve full width or merge operators |
| [RESET-1](RESET-1.md) | CRITICAL | Multiple asynchronous resets | Single async reset |
| [RESET-2](RESET-2.md) | WARNING | Incomplete async reset coverage | Reset all registers in block |
| [RESET-3](RESET-3.md) | CRITICAL | Incomplete sync reset / enable driven by sync reset | Reset all registers in block |

## Adding New Guides

Create `<RULE-ID>.md` with: Vivado message pattern, root cause, fix options (before/after code), validation TCL, and references. See any existing guide as a template.
