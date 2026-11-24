<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VFS Testbenches in C++ and for the FFT, FIR and RF IPs

Use the `vitis-functional-sim` skill to write and run C++ and Python
functional verification testbenches with Vitis Functional Simulation (VFS).

## Goal

1. Verify an HLS kernel feeding an AI Engine graph with a C++ testbench.
2. Check the Xilinx FFT (xFFT) IP against `numpy.fft.fft`.
3. Check that the FIR Compiler IP matches `numpy.convolve` bit for bit.
4. Check that the Versal RF Channelizer puts a tone in the right channel.
5. Check that a vrfADC to vrfDAC loopback returns its input.

## Skills Used

- **vitis-functional-sim**: identifies the component and picks the matching VFS
  class (`vfs::hlsKernel` and `vfs::aieGraph` in C++, or `vfs.xfft`,
  `vfs.firCompiler`, `vfs.vrfChannelizer`, `vfs.vrfADC` and `vfs.vrfDAC` in
  Python). It then builds the stimulus, runs the testbench, and compares the
  output with a reference.

## Prerequisites

- Vitis 2026.2 or later on `PATH` (`source <install>/Vitis/settings64.sh`).
  VFS ships with Vitis.
- For Task 1: GCC 9 or later. The GCC bundled with Vitis works.
- For Tasks 2 to 5: Python 3.9 to 3.14 with NumPy.

## Files

| Path | Contents |
|---|---|
| `input/stream_kernel/kernels.cpp`, `kernels.h` | HLS kernel `scale_offset`: reads one int32 sample and writes 3 * sample + 1 |
| `input/scale_graph/graph.cpp`, `graph.h` | AIE graph: one 32-bit PLIO in and out, one kernel |
| `input/scale_graph/src/kernels.cpp` | `scale_sub`: writes 2 * sample - 5 |

The IP tasks need no source files.

## How to Run

Follow [`prompt.md`](prompt.md) to run each task in a fresh working directory,
once with the skill and once without, and compare the results.

## Expected Behavior

- The agent writes one testbench per task and runs it.
- The C++ chain output equals 6 * sample - 3 for every sample.
- The xFFT output is within 2 LSBs of `numpy.fft.fft(x) / 1024`.
- The FIR output equals `numpy.convolve` with the 16-bit coefficients exactly.
- The channelizer's driven channel is at least 30 dB above every other
  channel.
- Every DAC channel returns the ADC input sample for sample.
