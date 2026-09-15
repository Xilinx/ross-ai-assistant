<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# MATLAB Edge Detection to HLS C++

End-to-end MATLAB to HLS conversion for an RGB edge detector.

## Goal

Convert a MATLAB RGB edge detector (Sobel-style 3x3 stencil, 3 operating modes) to C++ for Vitis HLS, then architect the dataflow and optimize for 4400 FPS at 303 MHz.

## Skills Used

- **hls-matlab-to-cpp** — converts MATLAB to C++ with golden reference verification
- **hls-architect** — produces dataflow architecture (gradient -> magnitude -> threshold)
- **hls-optimize** — iterates pragmas until throughput target is met

## Prerequisites

- Vitis HLS 2025.1+ installed
- MATLAB (for golden reference comparison)

## Starting Point

Input files in `input/`:
- `rgbEdgeDetector.m` — main edge detection function (luminance, perChannel, maxGradient modes)
- `testbench_edgeDetector.m` — verification suite for all modes
- `edge_detection_runme.m` — entry-point script

## Prompt

```
Convert rgbEdgeDetector.m to C++ for HLS targeting 4400 FPS at 303 MHz on xczu9eg-ffvb1156-2-e.
```

## Expected Behavior

The skill chain will:
1. **hls-matlab-to-cpp** — generate `golden/`, `sample_based/`, `frame_based/` with C++ verified against MATLAB
2. **hls-architect** — produce hierarchical dataflow: gradient -> magnitude -> threshold
3. **hls-optimize** — iterate pragmas until 4400 FPS @ 303 MHz is met
