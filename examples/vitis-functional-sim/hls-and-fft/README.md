<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VFS Testbenches for HLS Kernels and the Versal RF FFT

Use the `vitis-functional-sim` skill to write and run Python or MATLAB
functional verification testbenches with Vitis Functional Simulation (VFS).

## Goal

1. Verify an HLS stream kernel (`input/stream_kernel/`) against a NumPy reference.
2. Verify an HLS kernel with an AXI-Stream input (`input/axis_kernel/`), which needs
   TLAST to mark packet boundaries.
3. Verify the Versal RF FFT IP against `numpy.fft.fft`.
4. Repeat the AXI-Stream test in MATLAB.

## Skills Used

- **vitis-functional-sim**: detects the component type, wraps it with the VFS
  API (`vfs.hlsKernel` or `vfs.vrfFFT.vrfFFT`), builds the stimulus as
  `varray` data, compares the output against a reference in LSBs, and runs the
  testbench.

## Prerequisites

- Vitis 2026.2 or later on `PATH` (`source <install>/Vitis/settings64.sh`).
  VFS ships with Vitis.
- For Python: Python 3.9 to 3.14 with NumPy.
- For MATLAB: a MATLAB release supported by your VFS version.

## Files

| Directory | Kernel | Interface |
|---|---|---|
| `input/stream_kernel/` | `scale_offset`: writes `3 * x + 1` for each int32 sample | `hls::stream<int32_t>` in and out |
| `input/axis_kernel/` | `packet_max`: writes the maximum of each TLAST-delimited packet | `hls::stream<ap_axis<32,0,0,0>>` in, `hls::stream<int32_t>` out |

The FFT task needs no source files. The MATLAB task uses `input/axis_kernel/`.

## How to Run

Follow [`prompt.md`](prompt.md) to run each task in a fresh working directory,
once with the skill and once without, and compare the results.

## Expected Behavior

- The agent writes one testbench per task and runs it.
- The stream kernel matches the reference exactly (0 LSBs).
- The AXI-Stream kernel returns 42 and -2, one output per packet, in both Python
  and MATLAB.
- The FFT matches the reference within 1 LSB.
