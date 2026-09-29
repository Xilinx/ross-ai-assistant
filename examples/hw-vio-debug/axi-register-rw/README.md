<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VIO AXI Register Read/Write

A design with a VIO-controlled AXI-Lite master and AXI-Stream packet
generator, then debugged with the `hw-vio-debug` skill.

The user drives AXI-Lite register writes and reads, and controls streaming
traffic, entirely through VIO output probes — observing results through VIO
input probes. The design source in `input/` is complete and self-contained
(RTL + Tcl). The debug step depends on the **`hw-vio-debug` skill**, which
lives at [`skills/hw-vio-debug/`](../../../skills/hw-vio-debug/) in this repo.

## Goal

Use the VIO to interactively control and observe an AXI-based design on live
hardware:

1. **Discover** VIO cores and list all probes with names, directions, widths.
2. **Write a register** — drive wr_addr + wr_data via VIO outputs, pulse
   start_wr, observe the done pulse and busy deassertion.
3. **Read back** — drive rd_addr, pulse start_rd, read rd_data from VIO input
   to confirm the round-trip.
4. **Stream control** — set pkt_length, pulse start_stream, monitor pkt_count
   incrementing.

## Skills Used

- **hw-vio-debug** — discovers VIO cores and probes, reads input values
  (`refresh_hw_vio`), drives output values (`set_property OUTPUT_VALUE` +
  `commit_hw_vio`), checks activity, and resets outputs. All operations go
  through Vivado Hardware Manager Tcl via `vivado_execute`.

> **Companion:** This design also contains an ILA core — see
> [`examples/hw-ila-debug/axi-protocol-capture/`](../../hw-ila-debug/axi-protocol-capture/)
> for the ILA-focused tutorial using the same design.

## Prerequisites

- **Vivado MCP server** — builds the design from source and provides the
  Hardware Manager Tcl interface for ILA/VIO interaction.
- **`hw_server`** running on the hardware side (e.g. `TCP:<host>:3121`).
- The **`hw-vio-debug` skill** installed (invoked as `/hw-vio-debug`). In this
  repo it lives at [`skills/hw-vio-debug/`](../../../skills/hw-vio-debug/).
- Vivado 2026.1+ installed.
- A Versal board (developed on VCK190, `xcvc1902-vsva2197-2MP-e-S`).

## Starting Point

Input files in `input/`:
- `src/axi_lite_master.v` — VIO-controlled AXI4-Lite master. On a `start_wr`
  pulse, issues one write (address + data set by VIO). On `start_rd`, issues
  one read. Status fed back to VIO (rd_data, busy, done).
- `src/axis_pkt_gen.v` — VIO-controlled AXI-Stream packet generator. On a
  `start_stream` pulse, sends one packet of `pkt_length` beats with
  incrementing TDATA. Status: stream_busy, pkt_count.
- `create_project.tcl` — builds the block design: Versal CIPS + AXI-Lite
  master → BRAM controller + AXI-Stream generator → FIFO + axis_ila
  (System ILA) + axis_vio (VIO, 8 outputs + 5 inputs).

### VIO Probe Map

| Probe | Direction | Width | Signal |
|-------|-----------|-------|--------|
| probe_out0 | OUT | 1 | start_wr |
| probe_out1 | OUT | 1 | start_rd |
| probe_out2 | OUT | 32 | wr_addr |
| probe_out3 | OUT | 32 | wr_data |
| probe_out4 | OUT | 32 | rd_addr |
| probe_out5 | OUT | 1 | start_stream |
| probe_out6 | OUT | 8 | pkt_length |
| probe_out7 | OUT | 1 | sink_read_en |
| probe_in0 | IN | 32 | rd_data |
| probe_in1 | IN | 1 | busy |
| probe_in2 | IN | 1 | done |
| probe_in3 | IN | 1 | stream_busy |
| probe_in4 | IN | 16 | pkt_count |

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md) to run this tutorial end to
end.** There are four ordered steps:

1. **Build** — `Step 1` sources `input/create_project.tcl` and implements to
   `write_device_image`, producing the PDI and LTX.
2. **Discover** — `Step 2` uses the `/hw-vio-debug` skill to program the PDI
   and list all VIO probes.
3. **Write + Read** — `Step 3` drives a register write then reads it back.
4. **Stream** — `Step 4` sends an AXI-Stream packet and checks pkt_count.

## Expected Behavior

The `hw-vio-debug` skill will:
1. Connect to `hw_server` and program the PDI via Vivado Hardware Manager.
2. Associate the LTX probes file and discover the VIO core (hw_vio_1).
3. List all 13 probes with names, directions, widths, and current values.
4. Drive wr_addr=0x20, wr_data=0xDEADBEEF, pulse start_wr → busy=1→0.
5. Drive rd_addr=0x20, pulse start_rd → rd_data=`0xDEADBEEF` (round-trip).
6. Set pkt_length=16, pulse start_stream → pkt_count increments to 1.
