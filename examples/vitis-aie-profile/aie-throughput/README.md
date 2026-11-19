<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Select an AI Engine throughput measurement

This example answers a question. It does not build or run a design.

The question is which profiling method can measure the average throughput of one output PLIO.

## Input

- `input/aie/src/graph.h`: graph and PLIO names. The kernel source is not included.
- `input/aie_trace_metadata.json`: metadata from the current xclbin
- `input/xrt.ini`: a draft runtime configuration

Use these files as they are. Do not compile `graph.h`.

## Run the example

Start the agent from this directory and submit [`prompt.md`](prompt.md).

## Expected result

The agent explains that no rebuild is required. The supplied xclbin metadata shows that event trace was not enabled. The agent replaces the draft with `[Debug] aie_profile=true` and, under `[AIE_profile_settings]`, an interface-tile metric for `DataOut1` using `s2mm_throughputs` (older name `output_throughputs`; UG1076 Profiling for Interface Tiles): `graph_based_interface_tile_metrics=fir_graph:DataOut1:s2mm_throughputs`. It points out that `input_output_throughputs` is not a valid metric and that the draft's settings are in the trace section rather than the profile section. The result appears in Vitis Analyzer (`xrt.run_summary`).
