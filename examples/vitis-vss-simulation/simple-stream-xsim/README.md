<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Simulate an AI Engine subsystem with XSim

This example connects a small AI Engine graph to a bounded Verilog testbench. It uses XSim to run the Vitis Subsystem simulation.

## Requirements

- Vivado and XSim from the same installation
- The files in [`input/`](input/)

## Directories

Change to `input/` before the agent reads `vss_inputs.md`. Every path in that file is relative to `input/`. `TEST_DIR` and `COMPILED_OUT_DIRECTORY` are `.` in that directory.

`aie/src/graph.cpp`, `vss.cfg`, `vss_tb.v`, and `send_data.v` are in `input/`. The graph names the kernel as `aie/src/kernel.cpp`. That path is valid from `input/`.

`vss_tb.v` connects to the subsystem wrapper that the skill generates. Simulate the generated subsystem. A standalone run of `vss_tb.v` has no AI Engine ports to drive.

## Run the example

1. Start the agent from this directory.
2. Submit the request in [`prompt.md`](prompt.md).

## Expected result

The agent compiles the AI Engine graph, builds the subsystem simulation, runs the testbench, and reports that the simulation completed.
