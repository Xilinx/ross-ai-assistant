<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Export Mixed-Source IPI Project

A mixed-source Vivado project (RTL + standalone XCI IP + IPI Block Design +
XDC constraints) exported for version control using the
`vivado-revision-control` skill.

The design source in `input/` is complete and self-contained (RTL + Tcl). The
skill operates on the *open project* — no synthesis or implementation required.
It depends on the **`vivado-revision-control` skill**, which lives at
[`skills/vivado-revision-control/`](../../../skills/vivado-revision-control/)
in this repo.

## Goal

Demonstrate the full 5-step revision-control pipeline on a project that
contains all major source types:

1. **Detect** project flow type (Standard RTL + IPI).
2. **Analyze** source locations — `top.v` and `timing.xdc` are added from
   `input/src/` and `input/constraints/`, which sit *outside* the project
   directory (`input/revctrl_demo/`), so the scenario is **Mixed**: they stay
   remote, while the IP and BD (generated inside the project's own tree) are
   local.
3. **Export** IP via `write_ip_tcl` to `Sources/IP/` and BD via
   `write_bd_tcl` to `Sources/BD/`. `top.v` and `timing.xdc` are *not*
   copied — see [Imported vs. Added Files](#imported-vs-added-files) below.
4. **Capture** non-default project settings (PART, BOARD_PART, TOP, etc.).
5. **Generate** `build.tcl` that recreates the project from scratch.

### Imported vs. Added Files

The skill distinguishes two ways a source can end up back in the recreated
project, based on where it lived in the *original* project:

- **Imported (local)** — the file lives inside the project directory. It's
  physically copied into `Sources/<Category>/` during export, and `build.tcl`
  brings it back with `import_files`.
- **Added (remote)** — the file lives outside the project directory (as
  `top.v` and `timing.xdc` do here, since `input/revctrl_demo/` is a
  subdirectory of `input/`, not the other way around). It's never copied;
  `build.tcl` references it in place at its original absolute path with
  `add_files`.

A project is classified **Push Button** if every source is local, **Remote**
if every source is remote, and **Mixed** if it's a combination — as it is in
this example.

## Skills Used

- **vivado-revision-control** — detects project type, exports all sources,
  captures settings, and generates a portable build script. All operations
  go through Vivado Tcl procedures via `vivado_execute`.

## Prerequisites

- **Vivado MCP server** — provides the Tcl interface to the open project.
- Vivado 2026.1+ installed.
- No hardware required (this is a project-management skill, not HW debug).

## Starting Point

Input files in `input/`:
- `src/top.v` — RTL top module that instantiates a Clocking Wizard (XCI)
  and a Block Design wrapper. Contains a 32-bit counter on the 200 MHz domain.
- `constraints/timing.xdc` — clock constraints + output delay + false path.
- `create_project.tcl` — creates the project with:
  - RTL source (`top.v`)
  - Standalone XCI IP (`clk_wiz_0`: 100 MHz → 200 MHz)
  - IPI Block Design (`bd_subsystem`: CIPS providing pl0_ref_clk + pl0_resetn)
  - Constraint file (`timing.xdc`)

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md) to run this tutorial end to
end.** There are three ordered steps:

1. **Create project** — `Step 1` sources `input/create_project.tcl`.
2. **Export** — `Step 2` uses the `/vivado-revision-control` skill to export
   all sources and generate `build.tcl`.
3. **Verify** — `Step 3` runs the generated `build.tcl` to confirm the
   project recreates correctly.

## Expected Behavior

The `vivado-revision-control` skill will:
1. Detect the project as "Standard" flow type, "Mixed" scenario (see
   [Imported vs. Added Files](#imported-vs-added-files)).
2. Export local sources to a `RevisionControl/` directory structure:
   - `Sources/IP/clk_wiz_0.tcl` (via `write_ip_tcl`)
   - `Sources/BD/bd_subsystem.tcl` (via `write_bd_tcl`)
   - `top.v` and `timing.xdc` are **not** exported here — they remain at
     their original location under `input/`, and `build.tcl` adds them back
     in place with `add_files` pointing at that absolute path.
3. Capture project settings (PART, BOARD_PART, TOP) to `Scripts/project_settings.tcl`.
4. Generate `Scripts/build.tcl` — a single-command project recreation script.
5. Verification: running `build.tcl` produces a project with the same TOP,
   IP count, and BD as the original.
