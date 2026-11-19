<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# FIR Filters and a Beamformer with AIE int8 MACs

Use the `vitis-aie-int8-mac` skill to map int8 filters and a beamformer onto the
AI Engine `mul8`/`mul16` vector MAC intrinsics, verify them bit-exact in x86sim,
and review existing intrinsic calls for parameter mistakes the tools do not
report.

## Goal

1. **Real FIR**: a 16-tap FIR on real int8 samples, buffer interface.
2. **Complex FIR**: a 16-tap FIR on complex int8 samples with real taps, stream
   interface.
3. **Beamformer**: a 4-antenna, 4-layer receive beamformer on complex int8
   samples with complex weights, buffer interface.
4. **Parameter review**: check six existing int8 MAC calls against the
   documented address-parameter rules.

## Skills Used

- **vitis-aie-int8-mac**: chooses `mul8` or `mul16`, checks feasibility (for
  example, why a real FIR with the samples in `xbuff` needs two calls), derives
  the address parameters from the closed-form lane map, lints them, and verifies
  the kernel in x86sim and with a hw compile.

## Prerequisites

- A licensed **Vitis** install (2026.1 or later) on `PATH`
  (`source <install>/Vitis/settings64.sh`).
- An **AIE** device. The prompts use `xcvc1902-vsva2197-2MP-e-S` (VCK190). The
  int8 MAC intrinsics do not exist on AIE-ML or AIE-MLv2 devices.
- Python 3 for the reference models.

## Files

| File | Used by | Purpose |
|---|---|---|
| `rfir16_ref.py` | Prompt 1 | Bit-exact model of the real FIR; generates input and checks output |
| `cfir16_ref.py` | Prompt 2 | Bit-exact model of the complex FIR |
| `bf4x4_ref.py` | Prompt 3 | Bit-exact model of the beamformer |
| `review_kernel.cpp`, `review_app.cpp` | Prompt 4 | Kernel with six int8 MAC calls, and a graph that builds it |

Each model prints its usage: `python3 <model> --help`.

## How to Run

Copy this folder to a working directory, open it in your coding agent with the
skill installed, and run the prompts in [`prompt.md`](prompt.md). Each prompt
is independent.

## Expected Behavior

- **Prompts 1–3**: the agent creates a project directory with `app.cpp`, a
  kernel and a graph, runs x86sim against the reference model with no mismatches
  over at least 4 graph iterations, and compiles the project for hw with no
  `chess_error` or `chess_warning`. The number of MAC calls per output block is
  close to the ideal given by the skill's cost formula.
- **Prompt 4**: the agent lists the calls that break a documented rule, gives a
  corrected parameter set for each, and states the values the hardware actually
  uses for the broken parameters. Calls whose values look unusual but are legal
  are not flagged.
