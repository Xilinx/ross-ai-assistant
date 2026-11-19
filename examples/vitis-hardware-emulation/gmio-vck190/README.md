<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Run a GMIO design in hardware emulation

This example shows how to run a pure AI Engine GMIO design for VCK190. It does not include the design. You supply the design and four files from one Vitis installation.

## Requirements

- A VCK190 base platform, a matching sysroot, a Yocto environment script, and a QEMU prebuilt image. All four come from the same Vitis release.

## Design you must supply

Set `TEST_DIR` in [`input/hwemu_inputs_vck190.md`](input/hwemu_inputs_vck190.md) to your design directory. These paths are relative to that directory, and each file must exist before you start:

- `aie/src/graph.cpp`: a pure AI Engine graph. The graph uses GMIO ports only. It has no PLIO ports and no programmable-logic kernels.
- `aie/aiecompiler.cfg`: the AI Engine compiler configuration. If you have no configuration file, set `AIE_COMPILE_CFG` to `none`.
- `system.cfg`: the link configuration for this GMIO design.
- `src/host.cpp`: the embedded host program that runs on the VCK190 Linux image.

Also replace the four `/absolute/path/to` values with the platform, sysroot, Yocto script, and QEMU directory on your computer.

Stop if any path in the inputs file does not exist. Do not start the build with a placeholder path.

## Run the example

1. Start the agent from this directory.
2. Submit the request in [`prompt.md`](prompt.md).

## Expected result

The agent builds the emulation package and host program, starts QEMU, runs the host program, and reports the result from the host program.
