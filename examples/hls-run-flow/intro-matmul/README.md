<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Matrix Multiplication — C++ to HLS

Introductory example for the C++ to HLS skill flow.

## Goal

Start with a simple triple-nested loop matrix multiplication (`C = A x B`) and transform it into an optimized HLS implementation targeting 8x baseline throughput.

## Skills Used

- **hls-architect** — generates HLS DATAFLOW architecture (load/compute/store)
- **hls-optimize** — iterates pragmas until throughput target is met
- **hls-run-flow** — runs csim, csynth, cosim

## Prerequisites

- Vitis HLS 2025.1+ installed

## Starting Point

Input files in `input/`:
- `kernel.cpp` — baseline triple-nested loop implementation
- `kernel.hpp` — header defining `MAX_SIZE=4096` and function signature
- `main.cpp` — testbench with random matrices and ULP-based float comparison

## Prompt

```
Use /hls-architect to generate a dataflow architecture for this matrix multiplication, targeting 8x baseline throughput.
```

## Expected Behavior

The skill chain will:
1. **hls-architect** — generate HLS DATAFLOW architecture (load -> compute -> store)
2. **hls-optimize** — iterate pragmas (tiling, pipelining, array partitioning) until 8x baseline throughput is met
