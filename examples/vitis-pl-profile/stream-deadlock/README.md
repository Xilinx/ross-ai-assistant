<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Diagnose a streaming deadlock in hardware emulation

This example answers a question about the supplied files. It does not build or run a design.

The supplied hardware-emulation log shows that the run does not finish. This directory has no platform, host program, or xclbin.

## Input

- `input/src/krnl_stream.cpp`: producer and consumer kernels
- `input/link.cfg`: stream connections
- `input/xrt.ini`: current runtime settings
- `input/hw_emu_run.log`: run output

Use these files as they are.

## Run the example

Start the agent from this directory and submit [`prompt.md`](prompt.md).

## Expected result

The agent identifies that `krnl_s2mm` reads two stream words per iteration (2n = 2048) while `krnl_mm2s` writes n = 1024, and says to enable `compiler.deadlockDetection=true` on the kernel compile (`v++ -c ... --advanced.param compiler.deadlockDetection=true`, UG1702) and rebuild for `hw_emu`.
