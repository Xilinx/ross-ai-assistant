<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VCK190 NoC Bandwidth — Quick Start Prompts

The example ships as source only (a Tcl script that instantiates the ChipScoPy
example design). Build the PDI first, or use the prebuilt copy that comes with
ChipScoPy. Then program the board, start the design's traffic generator, and
measure the traffic with the `hw-noc-perfmon` skill.

Replace `<host>` with the machine running `hw_server` for your VCK190, and
`<design>` with the directory that holds `chipscopy_wrapper.pdi` and
`chipscopy_wrapper.ltx`.

## Step 1 — Get the design

**Option A — build from source** (takes 40–50 minutes):

```bash
cd input
vivado -mode batch -source create_project.tcl
# then, in the same session or a new -mode tcl session:
#   launch_runs impl_1 -to_step write_device_image -jobs 8
#   wait_on_run impl_1
```

With `-jobs 8` the build used about 32 GB of memory. On a machine with less
free memory, use `-jobs 4`.

or ask the agent:

```
Build this example: source input/create_project.tcl in Vivado, then run
implementation to write_device_image to produce the PDI.
```

The build writes the PDI and LTX, and the NoC artifacts that Step 4 uses, to
`input/noc_perf/noc_perf.runs/impl_1/`:

```
chipscopy_wrapper.pdi   chipscopy_wrapper.ltx
chipscopy_wrapper.ncr   chipscopy_wrapper.nts
```

**Option B — use the prebuilt design** that ships with the `chipscopy` Python
package (installed with chipscope-mcp). It has no `.ncr` or `.nts`, so skip
Step 4.

```bash
chipscopy-get-examples -y
ls chipscopy-examples/designs/vck190/production/chipscopy_ced/
# chipscopy_wrapper.pdi  chipscopy_wrapper.ltx  manifest.json
```

## Step 2 — Program the board and start the traffic

The design's NoC traffic generator comes up idle, and chipscope-mcp cannot
start it, so this step runs a script. It programs the PDI, then starts
continuous DDR reads through the NoC. The traffic keeps running after the
script exits.

```bash
cs_server &    # in Vivado's bin directory; listens on TCP:localhost:3042
python scripts/start_traffic.py --hw-server TCP:<host>:3121 \
    --pdi <design>/chipscopy_wrapper.pdi --ltx <design>/chipscopy_wrapper.ltx
# Traffic running: continuous DDR reads through the NoC (...)
```

## Step 3 — Find and measure the traffic

```
The VCK190 at TCP:<host>:3121 is already programmed with
<design>/chipscopy_wrapper.pdi (probes: <design>/chipscopy_wrapper.ltx), and
its NoC traffic generator is running. Do not program or reset the device.
Measure NoC performance: find which NoC master unit (NMU) carries the traffic,
and measure its steady-state read and write bandwidth and latency. I have no
NoC design artifacts. The DDR controller runs at 800 MHz. Configuring the NoC
performance monitors is authorized; pick up to 8 sites yourself and include
the DDR controller. Write the report files.
```

## Step 4 — Compare with the design's NoC traffic spec (Option A only)

```
Here are the NoC design artifacts for this design:
input/noc_perf/noc_perf.runs/impl_1/chipscopy_wrapper.nts (traffic spec) and
input/noc_perf/noc_perf.runs/impl_1/chipscopy_wrapper.ncr (routing solution).
Measure the traffic again on the generator's NMU and the DDR controller, label
the sites with their design names, and compare the measured bandwidth with the
configured requirement and QoS class. Do not program or reset the device.
Write the report files.
```

## Step 5 — Switch the traffic to writes (optional)

The script programs the device again before it starts the new pattern, so the
agent reconnects on its next measurement.

```bash
python scripts/start_traffic.py --hw-server TCP:<host>:3121 \
    --pdi <design>/chipscopy_wrapper.pdi --ltx <design>/chipscopy_wrapper.ltx --pattern write
```

```
The traffic generator now writes to DDR instead of reading. Measure the same
sites again and tell me how the traffic changed. Do not program or reset the
device.
```

## Step 6 — Stop the traffic

```bash
python scripts/start_traffic.py --hw-server TCP:<host>:3121 \
    --ltx <design>/chipscopy_wrapper.ltx --stop
```
