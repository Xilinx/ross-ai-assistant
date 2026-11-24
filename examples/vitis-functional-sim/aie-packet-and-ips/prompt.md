<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VFS Testbenches for Packet-Switched AIE Graphs and DSP IPs: Prompts

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
Task 5, MATLAB. If the agent's shell does not already have them, give both
conditions the same one-line setup note, such as "Before running anything, run
`source <setup script>`." That note is setup, not a hint about the task.

Give the agent only the task text and the suffix for its condition. Do not give
it this file's expected results or the README.

## Step 2: Use the same task in both conditions

### Task 1: Packet-switched AIE graph

```text
The AI Engine graph in input/packet_graph/graph.cpp routes packets from one
32-bit PLIO to two kernels with pktsplit and merges their results with
pktmerge. Kernel offset adds 100 to every int32 sample, and kernel neg negates
every sample. Write a Python VFS testbench that sends, in one run, three
packets of random int32 samples in the range [-1000, 1000) with a fixed seed:
5 samples to offset, 7 to neg, then 3 to offset. Check every returned packet
against a NumPy reference for the kernel that produced it, and exit non-zero
on any mismatch or missing packet. Then run it.
```

### Task 2: Poly Block Channelizer

```text
Write a Python VFS testbench for the Poly Block Channelizer in forward mode
with default settings. For channels 2 and 5, use a fresh instance and drive a
complex tone at the centre of that channel (k/8 cycles per sample, amplitude
0.4, at least 512 samples). Ignoring the first 16 output frames, check that
the output power in the driven channel is at least 30 dB above every other
channel and that no status flag is set. Then run it.
```

### Task 3: Versal LDPC decoder

```text
Write a Python VFS testbench for the Versal LDPC decoder with the Wi-Fi 802.11
rate-1/2, length-648 code. Transmit the all-zero codeword with BPSK over an
AWGN channel at Eb/N0 = 4 dB, using a fixed seed. Decode 10 frames with at most
20 iterations each. Check that the noisy samples contain bit errors before
decoding, and that every decoded frame is all zeros and passes its parity
check. Then run it.
```

### Task 4: Poly Block Arbitrary Resampler

```text
Write a Python VFS testbench for the Poly Block Arbitrary Resampler configured
for a 1.5x rate change. Feed it 20 blocks of 256 samples of a complex tone at
0.05 cycles per input sample, with amplitude 0.5. Check three results: the
total output length is 1.5 times the input length, the output tone is within
one FFT bin of 0.05 / 1.5 cycles per output sample, and no overflow flag is
set. Then run it.
```

### Task 5: Packet-switched AIE graph in MATLAB

```text
The AI Engine graph in input/packet_graph/graph.cpp routes packets from one
32-bit PLIO to two kernels with pktsplit and merges their results with
pktmerge. Kernel offset adds 100 to every int32 sample, and kernel neg negates
every sample. Write a MATLAB VFS testbench that sends, in one run, the packets
[1 2 3] to offset and [-4 5] to neg, checks that offset returns [101 102 103]
and neg returns [4 -5], and errors on any mismatch or missing packet. Then run
it.
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
- For Tasks 1 and 5, plant a bug in the kernel and run it again. It should
  fail. Change `+ 100` to `+ 101` in `input/packet_graph/src/kernels.cpp`,
  then undo the change.
- Check that it uses the VFS API listed below.

Expected results for each task:

| Task | Expected API | Expected result |
|---|---|---|
| 1 | `graph.getPacketIds()`, `aiePacketUtils.pack_packets` and `unpack_packets` | Every packet matches; results are matched by packet ID |
| 2 | `vfs.pbChannelizer` in `mode=0` | Driven channel at least 30 dB above the others; status clear |
| 3 | `vfs.versalLDPC` with `getCodeNum("wifi802_11_cr1_2_648")`, LLR input | 10 frames decode to all zeros; raw channel errors present |
| 4 | `vfs.pbArbResampler` with `phase_inc_value=16` | 1.5x samples; tone at 0.0333 cycles per sample |
| 5 | `varray.aiePacketUtils.pack_packets` with a cell of `{id, data}` pairs | [101 102 103] and [4 -5] |
