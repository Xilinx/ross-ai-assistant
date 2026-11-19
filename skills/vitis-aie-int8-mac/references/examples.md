<!--
  Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
  SPDX-License-Identifier: MIT
-->

# Worked examples

Two applications from the int8xint8 reference design, presented as the reasoning
path from application inputs to parameters. Both are implemented and
self-checked as TC1 and TC2 in `examples/int8_mac_test.cpp`
(`make -C examples x86all` -> `Summary: 0 failure(s)`). TC3–TC5 in the same
file are coverage cases: default parameters, a non-default square permute, and
`msc8` with a `v64int8` xbuff.

Contents:
- [TC1 — 32-tap complex FIR](#tc1--32-tap-complex-fir)
- [TC2 — 4-layer MIMO beamformer](#tc2--4-layer-mimo-beamformer)

---

## TC1 — 32-tap complex FIR

### Application inputs

- Samples: complex int8, stored real/imag interleaved: `[xr0, xi0, xr1, xi1, ...]`
- Taps: 32 **real** int8 taps
- Wanted: 4 complex output samples

### Deriving the choice

Real taps applied to complex data means the real and imaginary streams need the
same coefficients. If interleaved samples sit in `xbuff` and taps in `zbuff`,
then a lane producing a real output and the lane next to it producing the
imaginary output differ only by +1 in the sample index — exactly what the
"every second lane is previous + 1" rule provides for free. So put samples in x
and taps in z.

4 complex outputs = 8 lanes -> **`mul8`/`mac8`**, which gives 16 columns.
32 taps into 16 columns needs 2 calls: one `mul8` plus one `mac8`.

Samples span 32 taps x 2 (interleaved) = 64 elements plus the lane spread, so
the large buffer is needed: **`v128int8`**.

### Parameters

Step A, taps `z[0..15]`:

```cpp
mul8(xbuff_128, /*xstart*/ 0,  /*xoffsets*/ 0x03020100, /*xstep*/ 4, /*xsquare*/ 0x2110,
     zbuff_32,  /*zstart*/ 0,  /*zoffsets*/ 0x00000000, /*zstep*/ 2, /*zsquare*/ 0x3210);
```

Step B, taps `z[16..31]`:

```cpp
mac8(acc,
     xbuff_128, /*xstart*/ 32, /*xoffsets*/ 0x03020100, /*xstep*/ 4, /*xsquare*/ 0x2110,
     zbuff_32,  /*zstart*/ 0,  /*zoffsets*/ 0x88888888, /*zstep*/ 2, /*zsquare*/ 0x3210);
```

Two details worth transferring to other designs:

- The second half of the taps is reached with `zoffsets = 0x88888888`
  (nibble 8 -> 8 x 2 = 16 elements), **not** with `zstart`. `zstart` is 4-bit
  and cannot express 16.
- The sample window advances with `xstart = 32`, which is a legal multiple of 4.

### Measured lane map (x86sim, step A)

```
        c0    c1    c2    c3   ...  c15
acc[0]  x[0]  x[2]  x[4]  x[6] ...  x[30]
acc[1]  x[1]  x[3]  x[5]  x[7] ...  x[31]
acc[2]  x[2]  x[4]  x[6]  x[8] ...  x[32]
acc[3]  x[3]  x[5]  x[7]  x[9] ...  x[33]
```

z is identical for all 8 lanes: `z[0..15]` in step A, `z[16..31]` in step B.

Even lanes carry real outputs, odd lanes imaginary. Column stride is 2 in x,
which steps one complex sample per tap — the interleaving is absorbed by the
addressing rather than costing an instruction.

---

## TC2 — 4-layer MIMO beamformer

### Application inputs

- 8 antennas, 4 time-consecutive snapshots, complex int8
- 4 complex receive weight vectors (one per layer)
- Wanted: 16 complex outputs = 4 time slots x 4 layers

### Deriving the choice

16 outputs -> **`mul16`/`mac16`**, 16 lanes x 8 columns. 8 columns matches the
8 antennas exactly, so one call covers a full combine.

The weights are static across snapshots and vary per lane (per layer), so they
belong in `xbuff` — the expressive side. The antenna samples, shared by all
layers within a time slot, go in `zbuff`. This is the opposite assignment to
TC1, and it is driven purely by which operand varies per lane.

Complex x complex needs four real products, so four calls into two
accumulators:

```
acc_yI = xI*wI - xQ*wQ        mul16 then msc16
acc_yQ = xI*wQ + xQ*wI        mul16 then mac16
```

Weights are packed into one `v64int8` with I in bytes 0..31 and Q in bytes
32..63, so switching between `wI` and `wQ` is just `xstart = 0` vs `xstart = 32`.

### Data layout

```
xbuff_64[2k]      = wI[L0][k]      k = 0..7 antenna
xbuff_64[2k + 1]  = wI[L1][k]
xbuff_64[16 + 2k] = wI[L2][k]
xbuff_64[17 + 2k] = wI[L3][k]
xbuff_64[32 + n]  = the wQ weight for the same layer and antenna as xbuff_64[n]
zbuff_32_I[8t + k] = xI[t][k]      t = 0..3 time slot, k = 0..7 antenna
zbuff_32_Q[8t + k] = xQ[t][k]
```

If the source delivers interleaved complex in one `v64int8`, split it with
`aie::filter_even(zbuff_64, 1)` and `aie::filter_odd(zbuff_64, 1)`.

### Parameters

```cpp
// acc_yI += xI * wI
mul16(xbuff_64,   0, 0x30303030, 4, 0x3210,  zbuff_32_I, 0, 0xCC884400, 2, 0x1010);
// acc_yI -= xQ * wQ
msc16(acc_yI, xbuff_64, 32, 0x30303030, 4, 0x3210,  zbuff_32_Q, 0, 0xCC884400, 2, 0x1010);
// acc_yQ += xI * wQ
mul16(xbuff_64,  32, 0x30303030, 4, 0x3210,  zbuff_32_I, 0, 0xCC884400, 2, 0x1010);
// acc_yQ += xQ * wI
mac16(acc_yQ, xbuff_64,  0, 0x30303030, 4, 0x3210,  zbuff_32_Q, 0, 0xCC884400, 2, 0x1010);
```

### Measured lane maps (x86sim)

x side, `xstart = 0` — the four layers repeat every 4 lanes, so the same weight
vector serves all four time slots:

```
acc[ 0]  x[ 0] x[ 2] ... x[14]    <- wI[L0]
acc[ 1]  x[ 1] x[ 3] ... x[15]    <- wI[L1]
acc[ 2]  x[16] x[18] ... x[30]    <- wI[L2]
acc[ 3]  x[17] x[19] ... x[31]    <- wI[L3]
acc[ 4..7], acc[ 8..11], acc[12..15]  repeat the same four rows
```

z side, `zoffsets = 0xCC884400` — each group of 4 lanes advances by one time
slot's worth of antennas:

```
acc[ 0.. 3]  z[ 0.. 7]    t=0
acc[ 4.. 7]  z[ 8..15]    t=1
acc[ 8..11]  z[16..23]    t=2
acc[12..15]  z[24..31]    t=3
```

The accumulator layout falls out as `acc[4t + L]`.

## License

Copyright © 2025-2026 Advanced Micro Devices, Inc

SPDX-License-Identifier: MIT

[Terms and Conditions](https://www.amd.com/en/corporate/copyright)
