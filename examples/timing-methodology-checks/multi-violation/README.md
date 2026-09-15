<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Multi-Violation Timing Methodology

A dual-clock design with deliberately incorrect timing constraints, then
analyzed and resolved with the `timing-methodology-checks` skill.

The design source in `input/` is complete and self-contained (RTL + XDC + Tcl).
The skill operates on the **synthesized design** — synthesis must complete
before running methodology checks. It depends on the
**`timing-methodology-checks` skill**, which lives at
[`skills/timing-methodology-checks/`](../../../skills/timing-methodology-checks/)
in this repo.

## Goal

Trigger multiple timing methodology violations from different categories and
let the skill resolve them through its prioritized GROUP pipeline:

1. **TIMING-18** — Duplicate `create_clock` on the same port (redundant definition).
2. **TIMING-17** — `set_false_path` referencing a non-existent clock (typo).
3. **Unconstrained clk_b** — Missing primary clock on the second clock port.
4. **Missing clock groups** — Asynchronous CDC paths without `set_clock_groups`.
5. **Missing I/O delays** — Unconstrained input/output ports.

## Skills Used

- **timing-methodology-checks** — runs `report_methodology`, parses violations,
  prioritizes by GROUP, traces clocks, proposes XDC fixes (remove redundant
  constraints, add missing clocks, establish clock relationships), and generates
  a resolution report. All operations go through Vivado Tcl via `vivado_execute`.

## Prerequisites

- **Vivado MCP server** — builds the design and provides the Tcl interface.
- Vivado 2026.1+ installed.
- No hardware required (post-synthesis timing analysis only).

## Starting Point

Input files in `input/`:
- `src/timing_violation_top.v` — Dual-clock design (clk_a 100 MHz, clk_b 150 MHz)
  with pipeline logic in both domains and a 2-stage CDC synchronizer crossing
  from domain A to domain B.
- `constraints/timing_broken.xdc` — Intentionally incorrect constraints:
  - Correct `create_clock` for clk_a at 100 MHz
  - Duplicate `create_clock` on clk_a with different name (TIMING-18)
  - `set_false_path` to non-existent clock `clk_b_typo` (TIMING-17)
  - Missing `create_clock` for clk_b (unconstrained paths)
  - Missing `set_clock_groups` for async CDC (TIMING-6 area)
  - Missing `set_input_delay` / `set_output_delay` (TIMING-9/10)
- `create_project.tcl` — creates the project and adds sources.

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md) to run this tutorial end to
end.** There are two ordered steps:

1. **Build + Synthesize** — `Step 1` sources `input/create_project.tcl` and
   runs synthesis to produce the elaborated netlist.
2. **Resolve violations** — `Step 2` uses the `/timing-methodology-checks`
   skill to run `report_methodology`, identify all violations, prioritize
   by GROUP, and generate fixes + resolution report.

## Expected Behavior

The `timing-methodology-checks` skill will:
1. Open the synthesized run (`open_run synth_1`).
2. Run `report_methodology -json methodology.json`.
3. Extract constraints via `write_xdc`.
4. Identify violations and prioritize by GROUP (GROUP 1 first):
   - **TIMING-18**: Remove duplicate `create_clock clk_a_dup`.
   - **TIMING-17**: Remove or fix `set_false_path` with non-existent clock.
   - Add missing `create_clock -period 6.667 -name clk_b [get_ports clk_b]`.
   - Add `set_clock_groups -asynchronous -group clk_a -group clk_b`.
5. Re-run `report_methodology` after fixes to confirm resolution.
6. Generate `METHODOLOGY_RESOLUTION_REPORT.md` with before-after comparison.
