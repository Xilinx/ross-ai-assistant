<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VIO AXI Register Read/Write — Quick Start Prompts

The example ships as source only (RTL + Tcl). Build the PDI first, then debug it.

## Step 1 — Build the PDI from source

Either run it yourself:

```bash
cd input
vivado -mode batch -source create_project.tcl
# then, in the same session or a new -mode tcl session:
#   launch_runs impl_1 -to_step write_device_image -jobs 8
#   wait_on_run impl_1
```

or ask the agent:

```
Build this example: source input/create_project.tcl in Vivado, then run
implementation to write_device_image to produce the PDI.
```

Resulting PDI and LTX:

```
input/axi_debug/axi_debug.runs/impl_1/axi_debug_bd_wrapper.pdi
input/axi_debug/axi_debug.runs/impl_1/axi_debug_bd_wrapper.ltx
```

## Step 2 — Program the board and discover VIO probes

```
Use /hw-vio-debug to program input/axi_debug/axi_debug.runs/impl_1/axi_debug_bd_wrapper.pdi
onto the board via Vivado Hardware Manager. Associate the LTX probes file.
Discover the VIO core and list all input and output probes with their
current values.
```

## Step 3 — Write and read back a register

```
Using the VIO, write 0xDEADBEEF to BRAM address 0x20, then read it back.
Report the rd_data value to confirm the round-trip.
```

## Step 4 — Send an AXI-Stream packet and check count

```
Using the VIO, send a 16-beat AXI-Stream packet. Report the pkt_count
and stream_busy status after the transfer completes.
```
