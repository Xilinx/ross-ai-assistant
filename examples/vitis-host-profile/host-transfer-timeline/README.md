<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Check host and device overlap

This example answers a question. It does not build or run a design.

The question uses exported trace data from four iterations of a Vitis application. It asks whether host transfers overlap with kernel execution.

## Input

- `input/host_loop.cpp`: an excerpt of the host loop. It is not a complete program.
- `input/native_trace_export.csv`: host API trace
- `input/device_trace_export.csv`: device trace
- `input/xrt.ini`: runtime profiling settings

Use these files as they are. Do not compile `host_loop.cpp`.

## Run the example

Start the agent from this directory and submit [`prompt.md`](prompt.md).

## Expected result

The loop is fully serialized. User H2D time is 16 ms (8 × 2 ms) and D2H time is 4 ms (4 × 1 ms). The three 1 MiB `xdp_trace_buf_0` syncs on the `xdp_offload` thread are trace offload from `continuous_trace` (10 ms interval), not user transfers.
