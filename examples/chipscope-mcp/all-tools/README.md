<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# ChipScoPy MCP All-Tools Validation

A single VCK190 design that exercises **all 13 chipscope-mcp tools** that
produce meaningful end-to-end results: session management, device programming,
debug-core discovery, VIO read/write, ILA trigger/capture/export, SysMon
temperature, NoC discovery, DDR health + eye scan, and sysdbg NoC analysis.

## Goal

Validate every chipscope-mcp tool in one session:

| # | Tool | Action demonstrated |
|---|------|---------------------|
| 1 | `chipscope_session` | connect, status, tree, disconnect |
| 2 | `chipscope_device` | list, program |
| 3 | `chipscope_scan` | reset, scan (with LTX) |
| 4 | `chipscope_vio` | write 0xCAFEBABE → read back |
| 5 | `chipscope_ila_core` | status, probes, trigger_immediate |
| 6 | `chipscope_ila_capture` | get_data, export CSV |
| 7 | `chipscope_sysmon` | read_all (temperature + voltage) |
| 8 | `chipscope_noc` | discover NMU/NSU elements |
| 9 | `chipscope_memory` | read 16 bytes from BRAM via NoC |
| 10 | `chipscope_ddr` | health, calibration |
| 11 | `chipscope_ddr_eye_scan` | read eye scan with margin data |
| 12 | `sysdbg_noc` | analyze subsystem errors |
| 13 | `sysdbg_noc_timeout` | show/set timeout registers |

## Skills Used

- **hw-ila-debug** — ILA trigger, capture, export via chipscope-mcp
- **hw-vio-debug** — VIO probe read/write via chipscope-mcp

> **Note:** The chipscope-mcp server provides all tool operations directly.
> Skills are optional workflow wrappers that sequence multiple tool calls.

## Prerequisites

- **Vivado MCP server** — builds the design and provides HW Manager for
  programming.
- **ChipScoPy MCP server** — provides all `chipscope_*` and `sysdbg_*` tools.
  Validated with ChipScoPy 2026.1.
- **`hw_server`** running on the board host (e.g. `TCP:<hostname>:3121`).
- **`cs_server`** running on the board host (e.g. `TCP:<hostname>:3042`).
- Vivado 2026.1+ installed.
- VCK190 board (`xcvc1902-vsva2197-2MP-e-S`).

## Starting Point

Input files in `input/`:

- `src/axi_lite_master.v` — VIO-controlled AXI4-Lite master. On `start_wr`
  pulse writes one word; on `start_rd` reads one word. VIO observes rd_data,
  busy, done.
- `create_project.tcl` — builds the block design:
  - **CIPS** → NoC → LPDDR4 (board-automated, 2 channels)
  - **CIPS** → NoC → AXI BRAM (for `chipscope_memory`)
  - **axi_lite_master** → BRAM controller (AXI4-Lite direct)
  - **VIO** on lite_master control signals
  - **System ILA** monitoring the AXI-Lite bus

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md).** Three steps:

1. **Build** — source the TCL script to create the project and run through
   `write_device_image`, producing the PDI + LTX.
2. **Program & Discover** — program the board and discover all debug cores.
3. **Validate all tools** — exercise each tool in sequence.

## Expected Behavior

After programming and `chipscope_scan reset` + `scan`:
- **VIO:** write 0xCAFEBABE to address 0x10, read back 0xCAFEBABE
- **ILA:** capture showing AWADDR=0x10, WDATA=0xCAFEBABE
- **DDR:** health=GOOD, calibration=PASS on ddr_1 (run eye scan first)
- **DDR Eye Scan:** ~200+ pS eye width, ~20% swing
- **SysMon:** temperature ~30-40°C, voltages nominal
- **NoC:** 9 elements (NMU128, NSU512, DDRMC)
- **Memory:** 4 words read from DDR at 0x50000000
- **sysdbg_noc:** clean scan, 0 errors (250 modules)
- **sysdbg_noc_timeout:** timeout registers readable and configurable

## Key Lesson Learned

**After every reprogram, you MUST call `chipscope_scan(action='reset')` before
rescanning.** Without this, the scan returns 0 cores due to stale ChipScoPy
internal state — even when Vivado HW Manager can see the cores. This is the
single most common pitfall when using chipscope-mcp tools.
