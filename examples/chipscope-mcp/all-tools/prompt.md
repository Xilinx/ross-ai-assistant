<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# ChipScoPy MCP All-Tools Validation — Quick Start Prompts

The example ships as source only (RTL + Tcl). Build the PDI first, then
validate all chipscope-mcp tools in one session.

## Step 1 — Build the design

Either run it yourself:

```bash
cd input
vivado -mode batch -source create_project.tcl
```

or ask the agent:

```
Build this example: source input/create_project.tcl in Vivado, then run
synthesis and implementation to write_device_image to produce the PDI and LTX.
```

Resulting artifacts:

```
input/all_tools_demo/all_tools_demo.runs/impl_1/all_tools_bd_wrapper.pdi
input/all_tools_demo/all_tools_demo.runs/impl_1/all_tools_bd_wrapper.ltx
```

## Step 2 — Program the board and discover cores

```
Connect to chipscope-mcp (hw_server: TCP:<host>:3121, cs_server: TCP:<host>:3042).
Program the VCK190 with the PDI from Step 1.
Then reset the scan state and rediscover all debug cores using the LTX file.
Tell me how many ILA, VIO, DDR, NoC, and SysMon cores were found.
```

## Step 3 — Validate VIO + ILA (write round-trip)

```
Using chipscope_vio, write address 0x10 and data 0xCAFEBABE to the VIO output
probes, then pulse start_wr. After the transaction completes, set rd_addr=0x10
and pulse start_rd. Read back the VIO input probes and confirm rd_data equals
0xCAFEBABE.

Then trigger the ILA immediately to capture the AXI bus activity and export
the waveform to CSV.
```

## Step 4 — Validate DDR health and eye scan

> **Note:** Run DDR eye scan *before* SysMon `read_all` in the same session.
> SysMon starts a persistent live display that conflicts with DDR eye scan.

```
Check DDR health and calibration status on ddr_1.
Run a DDR read eye scan on ddr_1 (nibble 0, 15 steps).
Report the eye width and voltage swing.
```

## Step 5 — Validate SysMon + NoC + Memory

```
Read all SysMon sensors (temperature and voltage).
Discover all NoC elements.
Read 4 words from memory address 0x50000000 (DDR behind the NoC).
Run sysdbg_noc analyze to check for NoC errors.
Show the current sysdbg_noc_timeout settings.
```

## Step 6 — Disconnect

```
Disconnect the chipscope session.
```
