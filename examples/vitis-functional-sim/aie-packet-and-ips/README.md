<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VFS Testbenches for Packet-Switched AIE Graphs and DSP IPs

Use the `vitis-functional-sim` skill to write and run Python or MATLAB
functional verification testbenches with Vitis Functional Simulation (VFS).

## Goal

1. Verify a packet-switched AI Engine graph (`input/packet_graph/`) against a
   NumPy reference, packet by packet.
2. Check that the Poly Block Channelizer puts a tone in the right channel.
3. Check that the Versal LDPC decoder corrects channel errors on a Wi-Fi code.
4. Check the output rate and tone frequency of the Poly Block Arbitrary
   Resampler.
5. Repeat the packet-switching test in MATLAB.

## Skills Used

- **vitis-functional-sim**: identifies the component and picks the matching VFS
  class (`vfs.aieGraph`, `vfs.pbChannelizer`, `vfs.versalLDPC` or
  `vfs.pbArbResampler`). It then builds `varray` stimulus, runs the testbench,
  and compares the output with a reference.

## Prerequisites

- Vitis 2026.2 or later on `PATH` (`source <install>/Vitis/settings64.sh`).
  VFS ships with Vitis.
- For Python: Python 3.9 to 3.14 with NumPy.
- For MATLAB: a MATLAB release supported by your VFS version.

## Files

| Path | Contents |
|---|---|
| `input/packet_graph/graph.cpp`, `graph.h` | AIE graph: one 32-bit PLIO, `pktsplit<2>` to two kernels, `pktmerge<2>` to one output PLIO |
| `input/packet_graph/src/kernels.cpp` | `add_offset` (instance `offset`) adds 100; `negate` (instance `neg`) negates each sample |

The IP tasks need no source files.

## How to Run

Follow [`prompt.md`](prompt.md) to run each task in a fresh working directory,
once with the skill and once without, and compare the results.

## Expected Behavior

- The agent writes one testbench per task and runs it.
- Every packet from the AIE graph matches the reference for the kernel that
  produced it, in both Python and MATLAB.
- The channelizer's driven channel is at least 30 dB above every other
  channel.
- The LDPC decoder returns all zeros with parity passing for every frame.
- The resampler returns 1.5 times as many samples, with the tone at 0.05 / 1.5
  cycles per sample.
