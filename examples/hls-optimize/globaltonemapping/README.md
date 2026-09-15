<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Global Tone Mapping — HLS Optimization

Optimize a Vitis Vision GTM baseline for latency reduction.

## Goal

Start with a Vitis Vision L1 Global Tone Mapping baseline (NPPC=1, no optimizations) and optimize to reduce true-latency by 2x while constraining resource growth to 1.5x.

## Skills Used

- **hls-optimize** — iteratively applies optimizations (NPPC increase, pragmas)
- **hls-run-flow** — runs synthesis and cosim to measure results

## Prerequisites

- Vitis HLS 2025.1+ installed
- OpenCV (set `OPENCV_INCLUDE`, `OPENCV_LIB`, `LD_LIBRARY_PATH`)

## Starting Point

Input files in `input/`:
- `xf_gtm_accel.cpp` — top-level HLS kernel with AXI interfaces
- `xf_gtm_tb.cpp` — C testbench with OpenCV reference comparison
- `xf_config_params.h` — design parameters (HEIGHT=168, WIDTH=256, NPPC=1)
- `ltm_input_s.png` — test input image
- `include/` — Vitis Vision library headers

Generate the HLS config before running:
```bash
./scripts/gen_config.sh
```

## Prompt

```
Optimize xf_gtm_accel.cpp to reduce true-latency by 2x while using no more than 1.5x the resources.
```

## Expected Behavior

The skill analyzes the baseline, applies optimizations (parallel pixel processing with NPPC=2/4, loop pragmas), and iterates until the 2x latency target is met within the resource budget.
