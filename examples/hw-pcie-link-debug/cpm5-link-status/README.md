<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# CPM5 PCIe Link Status

Check the CPM5 PCIe controller of a VPK120 with the `hw-pcie-link-debug`
skill: confirm CPM5 is live, read the validated link state, look for an LTSSM
history, and write the skill's report.

There is nothing to build. The example uses the prebuilt ChipScoPy CPM5 PIO
example design for the VPK120. The skill lives at
[`skills/hw-pcie-link-debug/`](../../../skills/hw-pcie-link-debug/).

No PCIe card needs to be plugged into the board. With the slot empty there is
no link partner, so the right answer is "CPM5 is live, the link is not up". If
a host or card is connected, the same prompts report the trained link instead.

## Goal

1. **Gate**: identify the device and confirm a live CPM5 controller.
2. **Link state**: read each controller's validated link state, PHY-ready
   handshake, DPLL lock and interrupt status.
3. **LTSSM**: if the design has a PCIe link-debug core, read the LTSSM
   state-transition history.
4. **Report**: write the skill's report files.

## Skills Used

- **hw-pcie-link-debug**: gates on CPM5 and reads the validated controller
  state (`sysdbg_analyze` with `subsystem='cpm'`), reads LTSSM history
  (`chipscope_pcie`), and, only when the link is up, reads configuration space
  (`chipscope_memory`). All through the ChipScoPy MCP server.

## Prerequisites

- **chipscope-mcp** 1.5.2 or later, with ChipScoPy 2026.1 or later.
- **A VPK120** (Versal Premium, `xcvp1202`) with its `hw_server` running (for
  example `TCP:<host>:3121`). The VCK190 has no CPM5, so this skill stops on
  it at the version gate.
- Download `cpm_pcie_pio_wrapper.pdi` and `cpm_pcie_pio_wrapper.ltx` from the
  [public ChipScoPy VPK120 design](https://github.com/Xilinx/chipscopy/tree/923b8cc15cfced3f3f59c6cb62834590b7c5367c/chipscopy/examples/designs/vpk120/production/pcie_pio_ced).
  Save both files in one directory accessible to ChipScoPy.
- The **`hw-pcie-link-debug` skill** installed.

## How to Run

Follow [`prompt.md`](prompt.md). It has three steps: program and gate, read
the link state, then report.

## Expected Behavior

The skill will:

1. Connect to the `hw_server` (chipscope-mcp starts a local `cs_server` if none
   is reachable) and program the PDI, because the prompt asks for it. The
   skill never programs a device on its own.
2. Confirm the device is a Versal Premium part and that CPM5 is live.
3. Report each controller's link state as the tool returns it. With the slot
   empty this is a not-up state (for example `PHY Never Ready` or
   `Not in L0`), not `L0 (Link Up)`.
4. Note that this is a PIO design, so the DMA status aperture is absent and
   reported as `Status Aperture Absent (PIO design)`. That is expected, not an
   error.
5. Skip configuration-space reads, which only mean something on a trained
   link.
6. Write `REPORT.md` and `report_data.json` under
   `vivado_agentic_ai_reports/hw-pcie-link-debug/`.
