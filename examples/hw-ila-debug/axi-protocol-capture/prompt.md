<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# ILA AXI Protocol Capture — Quick Start Prompts

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

## Step 2 — Program the board and capture an AXI-Lite write

```
Use /hw-ila-debug to program input/axi_debug/axi_debug.runs/impl_1/axi_debug_bd_wrapper.pdi
onto the board via Vivado Hardware Manager. Associate the LTX probes file.
Then trigger the ILA on a rising edge of AWVALID. Use the VIO to drive a
write to address 0x10 with data 0xCAFEBABE, capture the ILA waveform, and
export to CSV.
```

## Step 3 — Capture an AXI-Stream packet

```
Now trigger the ILA on a rising edge of TVALID. Use the VIO to send an
8-beat AXI-Stream packet (pkt_length=8). Capture the waveform showing
TDATA incrementing and TLAST on the final beat. Export to CSV.
```
