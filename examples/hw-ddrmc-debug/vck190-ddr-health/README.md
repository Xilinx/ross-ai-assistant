<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VCK190 DDR Health Check

The ChipScoPy example design on a VCK190, debugged with the `hw-ddrmc-debug`
skill. You check the DDR memory controller's calibration and margins, then run a
2D read eye scan.

The design source in `input/` is a Tcl script that instantiates the Vivado
configurable example design `xilinx.com:design:chipscopy`, which ships with
Vivado. Its NoC connects one DDR4 memory controller (DDRMC) to the board's DDR4
UDIMM. The debug step depends on the **`hw-ddrmc-debug` skill**, which lives at
[`skills/hw-ddrmc-debug/`](../../../skills/hw-ddrmc-debug/) in this repo.

## Goal

Check the health of a real DDR4 interface on live hardware:

1. **Health check.** List every DDR controller site, with its location and
   calibration status. Tell a populated controller apart from an unused site,
   without running an eye scan.
2. **Margins.** Read the calibration-window margins per byte lane and nibble,
   for read and write, simple and complex patterns. Find the weakest lane.
3. **2D eye scan.** Sweep Vref and delay on the read path, then save the eye
   plot and the scan data.

## Skills Used

- **hw-ddrmc-debug** lists the DDR controllers, reports calibration stages,
  status registers and margins, and writes the DDR report files. It has two
  paths:
  - **Vivado MCP** (`get_hw_ddrmcs` / `report_hw_ddrmc`) is the primary path.
  - **ChipScoPy MCP** (`chipscope_ddr` / `chipscope_ddr_eye_scan`) is the
    fallback, and the only path for a true 2D eye scan.

## Prerequisites

- A **VCK190** board (`xcvc1902-vsva2197-2MP-e-S`) with its DDR4 UDIMM fitted.
- **`hw_server`** running on the board host, e.g. `TCP:<host>:3121`.
- **ChipScoPy MCP server**, chipscope-mcp 1.5.2 or newer. Without a
  `cs_server_url`, chipscope-mcp starts a local `cs_server` when `cs_server` is
  on `PATH`; it ships in Vivado's `bin` directory.
- **Vivado MCP server** (optional, recommended). When it is available the skill
  reads status and margins through Vivado Hardware Manager (`get_hw_ddrmcs`,
  `report_hw_ddrmc`), and uses ChipScoPy only for the eye scan. Use the same
  Vivado release as the board's `hw_server`. To register it with Claude Code:
  `claude mcp add vivado-mcp --scope user --transport stdio --env VIVADO_PATH=<vivado> -- <vivado-mcp-server> --stdio-bridge`.
- The **`hw-ddrmc-debug` skill** installed, invoked as `/hw-ddrmc-debug`.
- **Vivado 2026.1+**, only to build the design from source.

## Starting Point

Input files in `input/`:
- `create_project.tcl` creates a VCK190 project, picks the newest
  `xilinx.com:design:chipscopy` example design this Vivado ships, and
  instantiates it as the block design `chipscopy`. The block design has:
  - CIPS;
  - an AXI NoC (`axi_noc_0`) with one DDR4 memory controller;
  - a NoC traffic generator;
  - counters with ILA and VIO cores;
  - GTY quads with IBERT.
- `.gitignore` excludes the generated project (`ddr_debug/`) and logs.

You do not have to build anything. The `chipscopy` Python package, installed
with chipscope-mcp, carries a prebuilt copy of the same design. Run
`chipscopy-get-examples -y` and use
`chipscopy-examples/designs/vck190/production/chipscopy_ced/chipscopy_wrapper.{pdi,ltx}`.

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md).** There are five ordered steps:

1. **Get the design.** Build it from `input/create_project.tcl`, or use the
   prebuilt PDI and LTX from `chipscopy-get-examples`.
2. **Program.** Connect to `hw_server` and program the PDI with its LTX.
3. **Health check.** List every DDR controller and its calibration status,
   without an eye scan.
4. **Margins.** Read the calibration-window margins and find the weakest byte
   lane.
5. **2D eye scan.** Run a read-mode eye scan and save the plot and data.

## Expected Behavior

Measured on a VCK190 (Board Farm `systemlab-5`) on 2026-11-13 with the prebuilt
design and Claude Code (Sonnet) with this skill loaded. The setup was:

- hw_server and Vivado 2027.1;
- chipscope-mcp 1.5.2 with chipscopy 2027.1.0.0;
- vivado-mcp-server 2026.10.1.

The example was run twice: with Vivado MCP and chipscope-mcp, and with
chipscope-mcp only. Both gave the same results. With Vivado MCP, Steps 3 and 4
take the skill's Vivado path, and Step 5 uses ChipScoPy. Margins and eye numbers
move by a few picoseconds from one calibration to the next.

**Step 3, health check.** The design has four DDR controller sites. One is
populated.

| Vivado name | ChipScoPy name | Location | Calibration | Health |
| --- | --- | --- | --- | --- |
| `DDRMC_1` | `ddr_0` | `x0y0` | **PASS** | GOOD |
| `DDRMC_2` | `ddr_1` | — | disabled (no memory attached) | — |
| `DDRMC_3` | `ddr_2` | — | disabled | — |
| `DDRMC_4` | `ddr_3` | — | disabled | — |

- The enabled controller is DDR4 UDIMM, 1 slot, 1 rank, x64 (8 bytes,
  16 nibbles) at 1600 MHz.
- All 12 calibration stages pass, `CAL_STAGE.01_F0_PHY_BISC` through
  `CAL_STAGE.12_CAL_DONE`.
- Every DDRMC and UB interrupt-status register reads 0.
- The skill reports the three unused sites as **disabled, not failed**.
- It runs no eye scan, and writes `report_data.json` and `REPORT.md` to
  `vivado_agentic_ai_reports/hw-ddrmc-debug/`.

**Step 4, margins.** Frequency 0 only. Each value is the left or right margin
in picoseconds.

| Mode | Range (pS) |
| --- | --- |
| Read, simple, rising edge | 146–156 |
| Read, simple, falling edge | 140–152 |
| Read, complex, rising edge | 125–148 |
| Read, complex, falling edge | **114–129** (the tightest) |
| Write, simple | 142–153 |
| Write, complex | 129–138 |

- The weakest lane was Byte 4 Nibble 0, read complex falling edge, at about
  114 pS (58 taps).
- The spread across lanes is small, with no outliers.
- The skill adds `ddr_margins.csv`, with one row per controller, mode, byte
  and nibble.
- The skill also describes a `ddr_margin_window.png`. The agent did not render
  it in either run, because the skill forbids shell commands and the file tools
  cannot draw an image. If you want the chart, ask the agent to plot
  `ddr_margins.csv`.

**Step 5, 2D read eye scan.** On `ddr_0` nibble 0, complex pattern, rising
edge, Vref 25–50% (the tool's recommended range for DDR4 read), 15 steps:

- The maximum horizontal margin is about **300 pS** at Vref ≈ 36.4%. The
  default Vref is 37.5%, so the default is well centred.
- The vertical opening (Vref swing) is about **17.6%**.
- The rising edge has 147 taps of margin and the falling edge 142.
- The skill writes `ddr_eye_scan.png` and `ddr_eye_scan.csv`.

A shorter 5-step scan reads about 302 pS at Vref 37.5% with a 12.5% swing.

Each prompt in Steps 3–5 took 40–55 s of agent time, at about $0.30–0.70 per
prompt with Claude Sonnet. The first Vivado-path prompt takes about 100 s,
because it starts a Vivado session.

## Key Lessons

- **Unused controller sites are not failures.** The CED instantiates four
  DDRMC sites and populates one. The Vivado path shows an unused site as a
  `DDRMC_n` with no `CAL_STATUS` property; the ChipScoPy path shows it as
  `enabled: false`.
- **Vivado and ChipScoPy name controllers differently.** Vivado uses
  `DDRMC_1`; ChipScoPy uses `ddr_0`. Match them by location (`MC_LOC`,
  here `x0y0`), not by name.
- **Give the probes file when you program or connect.** With the LTX,
  chipscope-mcp reports `debug_ready: true`. If the chipscope session drops,
  reconnect with the same `ltx_path` before querying.
- **The eye scan is per nibble.** A run without `unit_index` scans nibble 0
  only. Ask for the nibble you care about, e.g. the weakest one from Step 4.
