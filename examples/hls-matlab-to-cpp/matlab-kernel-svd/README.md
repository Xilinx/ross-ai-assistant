<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# MATLAB SVD Jacobi to HLS C++

Convert a MATLAB complex SVD (Jacobi method) kernel to synthesizable C++.

## Goal

Convert `svd_jacobi_complex.m` — a complex-valued iterative SVD using Jacobi rotations — to C++ for Vitis HLS.

## Skills Used

- **hls-matlab-to-cpp** — MATLAB to C++ conversion with complex type mapping

## Prerequisites

- Vitis HLS 2025.1+ installed
- MATLAB (for golden reference)

## Starting Point

Input files in `input/`:
- `svd_jacobi_complex.m` — SVD Jacobi complex kernel

## Prompt

```
Convert svd_jacobi_complex.m to C++ for HLS.
```

## Expected Behavior

The skill generates C++ with `ap_fixed` and `std::complex` types, handling the iterative convergence loop and complex arithmetic.
