<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Add ILA probes to a synthesized design

This example shows the `vivado-netlist-debug-insert` skill on a small two-clock design. You ask in plain words for the signals you want to watch. The agent adds ILA cores to the synthesized netlist. Your RTL is never edited.

## The design

`rtl/nd_top.v` has two clock domains:

- `clk_a`: an AXI-Stream producer (`m_axis_tdata`, `m_axis_tvalid`, `m_axis_tready`, `m_axis_tlast`), driven by a packet state machine `state`.
- `clk_b`: a 16-bit counter `count` and an 8-bit control register `ctrl`. The top four bits of `ctrl` are always zero.

## Build the checkpoint

You need Vivado 2026.1 or later. Pick one part:

```bash
vivado -mode batch -source build.tcl -tclargs usp    build/usp.dcp      # UltraScale+, xcku5p-ffvb676-2-e
vivado -mode batch -source build.tcl -tclargs versal build/versal.dcp   # Versal, xcvm1802-vsva2197-2MP-e-S
```

Each build takes a few minutes and stops after synthesis. The Versal build wraps the design in a block design with a CIPS, because the skill needs a CIPS with a PL reset on Versal.

## What to ask the agent

Connect the Vivado MCP server, then ask, for example:

> build/usp.dcp is a synthesized design. Add ILA debug probes on the AXI-Stream interface m_axis, the state machine state, bits 7 down to 0 of count, and the register ctrl. Use a depth of 2048. Save the result as build/usp_instrumented.dcp.

## What comes out

- An instrumented checkpoint at the path you named. The skill saves only when you name a path.
- A report in plain words: what was probed on which clock, and what could not be probed.
- The next steps, which are yours to run: `opt_design`, then `write_debug_probes` for the probes file, then place, route and the device image.

## What to check

- Two ILAs, one per clock. The stream and the state machine are on `clk_a`. `count` and `ctrl` are on `clk_b`.
- The report says synthesis recoded `state` to one-hot, so its registers are named `FSM_onehot_state_reg`.
- The report says the top four bits of `ctrl` are constant and were not probed.
- After your `opt_design`, `write_debug_probes` writes a probes file that lists every probed signal.

The suite `tests/vivado-netlist-debug-insert/netlist-debug-insert-core/` runs this example on both parts.
