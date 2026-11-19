<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VCK190 DDR Health Check — Quick Start Prompts

The example ships as source only (a Tcl script that instantiates the ChipScoPy
example design). Build the PDI first, or use the prebuilt copy that comes with
ChipScoPy, then check the DDR memory controller.

Replace `<host>` with the machine running `hw_server` for your VCK190.

## Step 1 — Get the design

**Option A — build from source** (takes about an hour):

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
input/ddr_debug/ddr_debug.runs/impl_1/chipscopy_wrapper.pdi
input/ddr_debug/ddr_debug.runs/impl_1/chipscopy_wrapper.ltx
```

**Option B — use the prebuilt design** that ships with the `chipscopy` Python
package (installed with chipscope-mcp):

```bash
chipscopy-get-examples -y
ls chipscopy-examples/designs/vck190/production/chipscopy_ced/
# chipscopy_wrapper.pdi  chipscopy_wrapper.ltx  manifest.json
```

## Step 2 — Program the board

```
Connect to the hw_server at TCP:<host>:3121 and program the VCK190 with
<path>/chipscopy_wrapper.pdi and its probes file <path>/chipscopy_wrapper.ltx.
```

## Step 3 — DDR health check

```
Check DDR health on the board: list every DDR memory controller with its
location and calibration status, and say whether any failed or raised
warnings. This is a quick status check; do not run an eye scan.
```

## Step 4 — Calibration margins

```
Show the calibration-window margins of the enabled DDR controller for read and
write, simple and complex patterns, and tell me which byte lane has the
smallest margin. Write the DDR report files.
```

## Step 5 — 2D eye scan

```
Run a read-mode 2D DDR eye scan on the enabled controller with the
recommended Vref range, save the eye plot and the scan data, and summarize the
eye opening.
```
