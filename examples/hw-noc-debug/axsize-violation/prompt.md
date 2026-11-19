<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# NoC Illegal AxSIZE Protocol Error — Quick Start Prompts

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

Resulting PDI:

```
input/noc_axsize_error/noc_axsize_error.runs/impl_1/noc_axsize_wrapper.pdi
```

## Step 2 — Debug the NoC error on hardware

```
Use /hw-noc-debug to program input/noc_axsize_error/noc_axsize_error.runs/impl_1/noc_axsize_wrapper.pdi onto the board and root-cause any NoC errors.
```
