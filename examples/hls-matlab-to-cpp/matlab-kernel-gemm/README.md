<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# MATLAB GEMM Kernel to HLS C++

Convert a MATLAB general matrix multiply (GEMM) kernel to synthesizable C++.

## Goal

Convert `gemm.m` to C++ suitable for Vitis HLS synthesis.

## Skills Used

- **hls-matlab-to-cpp** — MATLAB to C++ conversion with type mapping

## Prerequisites

- Vitis HLS 2025.1+ installed
- MATLAB (for golden reference)

## Starting Point

Input files in `input/`:
- `gemm.m` — GEMM kernel
- `test_gemm.m` — testbench

## Prompt

```
Convert gemm.m to C++ for HLS.
```

## Expected Behavior

The skill generates C++ with appropriate fixed-point types and HLS interface pragmas, verified against the MATLAB golden reference.
