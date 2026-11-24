<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VFS Testbenches in C++ and for the FFT, FIR and RF IPs: Prompts

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

The agent needs Vitis 2026.2 or later, GCC 9 or later for Task 1, and Python
3.9 to 3.14 with NumPy for Tasks 2 to 5. If the agent's shell does not already
have them, give both conditions the same one-line setup note, such as "Before
running anything, run `source <setup script>`." That note is setup, not a hint
about the task.

Give the agent only the task text and the suffix for its condition. Do not give
it this file's expected results or the README.

## Step 2: Use the same task in both conditions

### Task 1: HLS kernel feeding an AIE graph, in C++

```text
The HLS kernel scale_offset in input/stream_kernel/kernels.cpp writes
3 * sample + 1 for each int32 sample. The AI Engine graph in
input/scale_graph/graph.cpp has one 32-bit PLIO input and output, and its
kernel writes 2 * sample - 5. Write a single-file C++ VFS testbench that feeds
64 random int32 samples in [-1000, 1000) with a fixed seed to the HLS kernel,
passes its output straight to the AIE graph, and compares the final output
against the expected result of the whole chain. Return non-zero from main on
any mismatch or missing output. Then compile and run it.
```

### Task 2: Xilinx FFT

```text
Write a Python VFS testbench for the xFFT IP: a 1024-point forward FFT with the
pipelined streaming architecture, 16-bit fixed-point input and the default
fixed scaling schedule, which scales the result by 1/1024. Drive two complex
tones, amplitude 0.25 at bin 30 and 0.125 at bin 100, quantized to 16 bits.
Compare the output against numpy.fft.fft of the input divided by 1024, and
exit non-zero if the error exceeds 2 LSBs of a 16-bit output or the overflow
flag is set. Then run it.
```

### Task 3: FIR Compiler

```text
Write a Python VFS testbench for the FIR Compiler IP. Use a 31-tap lowpass
filter with coefficients h[n] = 0.25 * sinc(0.25 * (n - 15)) * hamming(31)[n],
as 16-bit coefficients with 15 fractional bits, and 16-bit data with 14
fractional bits. Drive 512 seeded random samples in [-1, 1) on the 16-bit data
grid, split across two run() calls of 200 and 312 samples. Check that the
combined output equals numpy.convolve of the input with the coefficients
rounded to 15 fractional bits (first 512 samples, exact match), and that after
reset() one 512-sample run gives the same output. Then run it.
```

### Task 4: Versal RF Channelizer

```text
Write a Python VFS testbench for the Versal RF Channelizer in receive mode
(mode 0) with the default configuration, 8 channels and 64 samples per
channel. Load a 128-tap prototype lowpass filter
h[n] = sinc((n - 64) / 8) * hamming(128)[n], normalized to unit sum and scaled
to integers as round(h * 2**17), and set fft_scaling to 1. For channels
k = 1, 2 and 5, drive the complex tone round(0.4 * 2**17 * exp(2j * pi * k * n / 8)),
rounding the real and imaginary parts. Ignoring the first 8 output samples of
each channel, check that the driven channel has at least 30 dB more power than
every other channel and that no overflow flag is set. Then run it.
```

### Task 5: RF data converter loopback

```text
Write a Python VFS testbench for a vrfADC to vrfDAC loopback, both with their
default configurations. Drive all four ADC channels with the same 4096-sample
real signal, round(3000 * cos(2 * pi * 0.07 * n)) plus seeded random integers
in [-500, 500). Feed the ADC output straight into the DAC. Check that every DAC
channel output equals the ADC input sample for sample, and that band 1 of
every ADC channel is empty. Then run it.
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
- For Task 1, plant a bug in the kernel and run it again. It should fail.
  Change `3 * x + 1` to `3 * x + 2` in `input/stream_kernel/kernels.cpp`, then
  undo the change.
- Check that it uses the VFS API listed below.

Expected results for each task:

| Task | Expected API | Expected result |
|---|---|---|
| 1 | `vfs::hlsKernel` and `vfs::aieGraph`, built against `libamdvfs.so` with GCC 9 or later | Every output equals 6 * sample - 3 |
| 2 | `vfs.xfft.xfft` with `c_arch = 3`, `c_input_width = 16` | Error within 2 LSBs; no overflow |
| 3 | `vfs.firCompiler` with `reset()` | Exact match; the split and single runs agree |
| 4 | `vfs.vrfChannelizer`, output reshaped to `(samples, 8)` | Channel k has the most power; the others are at least 30 dB lower |
| 5 | `vfs.vrfADC.vrfADC` and `vfs.vrfDAC.vrfDAC` | DAC output equals ADC input on every channel |
