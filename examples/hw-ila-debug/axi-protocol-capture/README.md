<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# ILA AXI Protocol Capture

A design with an AXI-Lite master, an AXI-Stream packet generator, and a
**System ILA** monitoring both buses, then debugged with the `hw-ila-debug`
skill.

Traffic is driven interactively through a VIO core so the user controls
exactly when and what the ILA captures. The design source in `input/` is
complete and self-contained (RTL + Tcl). The debug step depends on the
**`hw-ila-debug` skill**, which lives at
[`skills/hw-ila-debug/`](../../../skills/hw-ila-debug/) in this repo.

## Goal

Use the ILA to capture live AXI protocol transactions on hardware:

1. **AXI-Lite write** — trigger on `AWVALID` rising edge, capture the full
   write handshake (AWADDR → AWREADY → WDATA → WREADY → BRESP).
2. **AXI-Stream packet** — trigger on `TVALID` rising edge, capture a
   multi-beat packet with incrementing TDATA and TLAST on the final beat.
3. **Export** — write captured data to CSV for offline analysis.

## Skills Used

- **hw-ila-debug** — discovers ILA cores and probes, configures triggers,
  arms the ILA, waits for trigger, uploads captured data, and exports to CSV.
  All operations go through Vivado Hardware Manager Tcl via `vivado_execute`.

> **Companion:** This design also contains a VIO core — see
> [`examples/hw-vio-debug/axi-register-rw/`](../../hw-vio-debug/axi-register-rw/)
> for the VIO-focused tutorial using the same design.

## Prerequisites

- **Vivado MCP server** — builds the design from source and provides the
  Hardware Manager Tcl interface for ILA/VIO interaction.
- **`hw_server`** running on the hardware side (e.g. `TCP:<host>:3121`).
- The **`hw-ila-debug` skill** installed (invoked as `/hw-ila-debug`). In this
  repo it lives at [`skills/hw-ila-debug/`](../../../skills/hw-ila-debug/).
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
  (System ILA, 2 monitor slots) + axis_vio (VIO).

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md) to run this tutorial end to
end.** There are three ordered steps:

1. **Build** — `Step 1` sources `input/create_project.tcl` and implements to
   `write_device_image`, producing the PDI and LTX.
2. **ILA AXI-Lite capture** — `Step 2` uses the `/hw-ila-debug` skill to
   program the PDI, trigger on AWVALID, drive a write via VIO, and capture
   the AXI-Lite handshake.
3. **ILA AXI-Stream capture** — `Step 3` re-triggers on TVALID and captures
   a streaming packet.

## Expected Behavior

The `hw-ila-debug` skill will:
1. Connect to `hw_server` and program the PDI via Vivado Hardware Manager.
2. Associate the LTX probes file and discover the ILA core (hw_ila_1).
3. Set trigger on `AWVALID` rising edge, arm the ILA.
4. Drive a write (addr=0x10, data=0xCAFEBABE) via VIO to fire the trigger.
5. Upload and report: AWADDR=`0x00000010`, WDATA=`0xCAFEBABE`, BVALID pulse.
6. Re-trigger on `TVALID`, send an 8-beat stream packet via VIO.
7. Upload and report: TDATA increments 0x0→0x7, TLAST on beat 8.
