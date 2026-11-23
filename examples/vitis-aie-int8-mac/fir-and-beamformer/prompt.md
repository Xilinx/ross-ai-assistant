<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# FIR Filters and a Beamformer with AIE int8 MACs: Prompts

Each prompt is independent. Run it from a directory that holds the files it
names.

## Prompt 1: real FIR (buffer interface)

```
Design an AI Engine kernel for a 16-tap FIR filter on real int8 samples with
real int8 coefficients, using the AIE int8 MAC intrinsics, for AIE device
xcvc1902-vsva2197-2MP-e-S.

The bit-exact reference model is rfir16_ref.py:
  y[n] = sat8( round( sum_{t=0..15} h[t] * x[n-t] / 2^7 ) )
Accumulate at full precision, shift down by 7, round to nearest with ties away
from zero, then saturate to [-128, 127].

Interface: the kernel reads its input from a buffer and writes its output to a
buffer. One 64-bit input PLIO "in" reads data/input.txt and one 64-bit output
PLIO "out" writes data/output.txt, in the format written by
`python3 rfir16_ref.py gen`. One graph iteration processes 1024 samples.

Put the project in its own directory with app.cpp at its root. Verify it
bit-exact against the reference model over at least 4 graph iterations in
x86sim, and confirm it compiles for hw.
```

Expected: a buffer-based kernel that passes `rfir16_ref.py check` and compiles
for hw. Real samples on real taps hit the parity restriction on `xbuff`, so
the skill uses a shifted copy of the samples or puts them in `zbuff`.

## Prompt 2: complex FIR (stream interface)

```
Design an AI Engine kernel for a 16-tap FIR filter on complex int8 samples with
real int8 coefficients, using the AIE int8 MAC intrinsics, for AIE device
xcvc1902-vsva2197-2MP-e-S.

The bit-exact reference model is cfir16_ref.py:
  y[n] = sat8( round( sum_{t=0..15} h[t] * x[n-t] / 2^7 ) )
x is complex int8 with real and imaginary parts interleaved. Accumulate at full
precision, shift down by 7, round to nearest with ties away from zero, then
saturate to [-128, 127].

Interface: the kernel reads its input from a stream and writes its output to a
stream. One 64-bit input PLIO "in" reads data/input.txt and one 64-bit output
PLIO "out" writes data/output.txt, in the format written by
`python3 cfir16_ref.py gen`. One graph iteration processes 1024 complex
samples.

Put the project in its own directory with app.cpp at its root. Verify it
bit-exact against the reference model over at least 4 graph iterations in
x86sim, and confirm it compiles for hw.
```

Expected: a stream-based kernel that passes `cfir16_ref.py check` and compiles
for hw, with the interleaved real and imaginary parts handled by the column
stride rather than by deinterleaving.

## Prompt 3: 4x4 beamformer (buffer interface)

```
Design an AI Engine kernel for a 4-antenna, 4-layer receive beamformer on
complex int8 samples with complex int8 weights, using the AIE int8 MAC
intrinsics, for AIE device xcvc1902-vsva2197-2MP-e-S.

The bit-exact reference model is bf4x4_ref.py:
  y[l][t] = sat8( round( sum_{a=0..3} W[l][a] * x[a][t] / 2^7 ) )
x[a][t] is the complex sample of antenna a at time t, W is the 4x4 complex
weight matrix in bf4x4_ref.py, and the product is the full complex product.
Accumulate the real and imaginary parts at full precision, shift down by 7,
round to nearest with ties away from zero, then saturate to [-128, 127].

Interface: the kernel reads its input from a buffer and writes its output to a
buffer. One 64-bit input PLIO "in" reads data/input.txt and one 64-bit output
PLIO "out" writes data/output.txt, in the format written by
`python3 bf4x4_ref.py gen`, one time sample per line. One graph iteration
processes 256 time samples.

Put the project in its own directory with app.cpp at its root. Verify it
bit-exact against the reference model over at least 4 graph iterations in
x86sim, and confirm it compiles for hw.
```

Expected: a buffer-based kernel that passes `bf4x4_ref.py check` and compiles
for hw, with the real and imaginary outputs built from `mul`/`mac`/`msc` calls.

## Prompt 4: review existing int8 MAC calls

```
review_kernel.cpp contains six int8 MAC intrinsic calls, C1 to C6. The comment
above each call states what it is meant to read. review_app.cpp is a graph that
builds the kernel for AIE device xcvc1902-vsva2197-2MP-e-S.

For each call, decide whether its address parameters break a documented rule of
the AIE int8 MAC intrinsics. For every call you flag, give corrected parameters
that make the call read what its comment says, keeping the same intrinsic and
buffers, and state the parameter values the hardware actually uses for the
parameters you flagged. Do not modify review_kernel.cpp.
```

Expected: the flagged calls are the ones that break a rule even though the
kernel builds and simulates; the corrected parameters are checked with the
skill's lint script and lane map.
