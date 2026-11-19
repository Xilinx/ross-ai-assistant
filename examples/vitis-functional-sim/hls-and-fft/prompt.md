<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VFS Testbenches: Prompts

Run each task below with and without the `vitis-functional-sim` skill to see
what the skill adds. Each task is standalone.

## Step 1: Prepare each trial

Use a new working directory and a new agent conversation for each task and
condition. Use the same model and reasoning effort for both conditions.

Copy only `input/` into each working directory. Make sure neither condition
can read other installed skills, the skill repository, or earlier trial
directories. Then, for the with-skill condition only, copy the skill to the
agent's skills folder, for example `.cursor/skills/vitis-functional-sim/` or
`.claude/skills/vitis-functional-sim/`.

The agent needs Vitis 2026.2 or later, Python 3.9 to 3.14 with NumPy, and, for
Task 4, MATLAB. If the agent's shell does not already have them, give both
conditions the same one-line setup note, such as "Before running anything, run
`source <setup script>`." That note is setup, not a hint about the task.

Give the agent only the task text and the suffix for its condition. Do not give
it this file's expected results or the README.

## Step 2: Use the same task in both conditions

### Task 1: HLS stream kernel

```text
The HLS kernel scale_offset in input/stream_kernel/kernels.cpp reads one int32
sample and writes 3 * sample + 1. Write a Python VFS testbench for it that
drives 32 random samples in the range [-1000, 1000) with a fixed seed,
compares the output against a NumPy reference, reports the maximum mismatch
in LSBs, and exits non-zero on any mismatch. Then run it.
```

### Task 2: HLS kernel with an AXI-Stream input

```text
The HLS kernel packet_max in input/axis_kernel/kernels.cpp reads one
TLAST-delimited packet of int32 samples and writes its maximum. Write a Python
VFS testbench that sends the packets [5, -3, 42, 7] and [-10, -2, -8] in one
run and checks the outputs are 42 and -2. Then run it.
```

### Task 3: Versal RF FFT

```text
Write a Python VFS testbench for the Versal RF FFT: a 1024-point forward FFT in
scaled mode with 16-bit input and output, a scaling factor of 1/1024, and
natural-order output. Drive two tones (bins 64 and 200) and compare against
a quantized numpy.fft.fft reference divided by 1024, within 1 LSB. Then run
it.
```

### Task 4: AXI-Stream kernel in MATLAB

```text
The HLS kernel packet_max in input/axis_kernel/kernels.cpp reads one
TLAST-delimited packet of int32 samples and writes its maximum. Write a MATLAB
VFS testbench that sends the packets [5, -3, 42, 7] and [-10, -2, -8] in one
run and checks the outputs are 42 and -2. Then run it.
```

### Condition suffixes

Append this for the **with-skill** condition:

```text
Use the vitis-functional-sim skill provided in this workspace's skills folder
for this task. Do not read previous runs or other workspaces.
```

Append this for the **without-skill** condition:

```text
This is the no-skill baseline. Use VFS and its installed documentation
directly, without skill files. Do not read previous runs or other workspaces.
```

## Step 3: Record, then evaluate

Let each attempt finish without hints. Record tool calls, tokens, cost, model
time, and errors. Recovery within the same attempt is allowed, but a failed
final result stays a failure; do not replace it with a better retry.

After the agent finishes, check its testbench yourself:

- Run it. It should pass.
- Plant a bug in the kernel and run it again. It should fail. Change
  `3 * x + 1` to `3 * x + 2` for Task 1, or `if (v > best)` to `if (v < best)`
  for Tasks 2 and 4. Undo the change afterwards.
- Check that it uses the VFS 2026.2 API.

Expected results for each task:

| Task | Expected API | Expected result |
|---|---|---|
| 1 | `vfs.hlsKernel` | 0 LSB mismatch |
| 2 | input created with `axis=va.axis_config()`, packet ends marked with `arr.axis.tlast[...] = 1` | 42 and -2, one output per packet |
| 3 | `reorder_output = 1` in the `vrfFFT` configuration, not in `Control` | error within 1 LSB |
| 4 | `varray.int32(data, axis=varray.axis_config())`, packet ends marked with `arr.axis.tlast(k) = 1` | 42 and -2 |
