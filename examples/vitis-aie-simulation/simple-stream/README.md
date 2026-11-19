<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Simulate a simple AI Engine stream

This example compiles and simulates a small AI Engine graph. The kernel copies eight 32-bit values from one PLIO to another PLIO.

## Requirements

- An AI Engine-capable Versal target
- The files in [`input/`](input/)

## Directories

Use two directories. They are not the same directory.

- Compile from `input/`. `input/aie/src/graph.h` names the kernel as `aie/src/kernel.cpp`. That path is valid only from `input/`.
- Give the simulator the input directory `input/aie`. The graph reads `data/input.txt` from that directory. The reference output is `input/aie/data/golden.txt`.

## Run the example

1. Start the agent from this directory.
2. Submit the request in [`prompt.md`](prompt.md).
3. Keep the generated work files outside `input/`.

## Expected result

The agent compiles the graph for `xcvc1902-vsva2197-2MP-e-S`, runs `aiesimulator`, compares the output with `input/aie/data/golden.txt`, and reports whether the values match.
