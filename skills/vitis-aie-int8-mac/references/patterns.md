<!--
  Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
  SPDX-License-Identifier: MIT
-->

# Design patterns

Measured mappings for the common shapes. Every pattern below was run
bit-exact against a scalar reference in x86sim and aiesimulator on 2026.1, and
compiles cleanly for hw.

Contents:
- [Cost first](#cost-first)
- [A: complex data, real taps](#a-complex-data-real-taps-tc1)
- [B: real x real, shifted copy](#b-real-x-real-shifted-copy)
- [C: real x real, data in `zbuff`](#c-real-x-real-data-in-zbuff)
- [D: complex x complex](#d-complex-x-complex-tc2)
- [E: more terms than columns](#e-more-terms-than-columns-tc1-step-b)
- [F: lane groups sharing an operand](#f-lane-groups-sharing-an-operand-tc2)

## Cost first

Each `mul8`/`mul16` call does 128 int8 multiplies. A design needs at least

```
calls per output = T * P / 128
```

- `T` = terms per output (taps, antennas, matrix depth)
- `P` = real multiplies per term: 1 for real x real, 2 for complex data x real
  coefficients (re and im are each multiplied), 4 for complex x complex

For example, a `T`-tap FIR on complex data with real taps needs `T * 2 / 128`
calls per complex output. Utilization is ideal calls divided by actual calls.

The table gives what one call, or one fixed group of calls, covers. Scale it to
the design's output count and term count.

| Pattern | Shape | Coverage | Utilization |
|---|---|---|---|
| A | complex data x real taps | 1 `mul8`: 4 complex outputs x 16 taps | 100% |
| B | real x real, shifted copy | `mul16` + `mac16`: 16 outputs x 16 taps | 100% |
| C | real x real, data in `zbuff` | 3 calls: 16 outputs x 16 taps | 67% |
| D | complex x complex | 4 calls: 16 complex outputs x 8 terms | 100% |
| E | more terms than columns | add `mac` calls | unchanged |
| F | lane groups sharing an operand | free | unchanged |

Notation: samples `x[n]`, taps `h[k]`, and `y[m] = sum_k h[k] * x[m + k]`.
Reverse the taps in the coefficient buffer for the convolution form.

## A: complex data, real taps (TC1)

Interleaved samples `[re0, im0, re1, im1, ...]` in `xbuff`, taps in `zbuff`:

```cpp
mul8(xbuff, xstart, 0x03020100, 4, 0x2110, zbuff, 0, 0x00000000, 2, 0x3210);
```

Lane `r` reads byte `xstart + r + 2c` in column `c`, and every lane reads
`z[c]` in column `c`:

| Lane | Products | Output |
|---|---|---|
| 0 | z[0]·x[0] + z[1]·x[2] + ... + z[15]·x[30] | re of output 0 |
| 1 | z[0]·x[1] + z[1]·x[3] + ... + z[15]·x[31] | im of output 0 |
| 2 | z[0]·x[2] + z[1]·x[4] + ... + z[15]·x[32] | re of output 1 |
| ... | | |
| 7 | z[0]·x[7] + ... + z[15]·x[37] | im of output 3 |

The column stride of 2 bytes steps one complex sample per tap, so the
interleaving costs nothing. Each call produces 4 consecutive complex outputs;
for the next 4, advance `xstart` by 8 bytes (4 complex samples). The window must
hold the previous `taps - 1` samples ahead of the new block, so every output
sees its full history.

## B: real x real, shifted copy

The parity lock stops one lane reading `x[m]` and `x[m + 1]` in the same call.
Split the taps into even and odd, and give the odd taps a copy of the window
shifted by one sample:

```cpp
// ze = {h[0], h[2], ..., h[14]}, zo = {h[1], h[3], ..., h[15]}
acc = mul16(x,       0, 0x03020100, 4, 0x2110, ze, 0, 0x00000000, 2, 0x3210);
acc = mac16(acc, xs, 0, 0x03020100, 4, 0x2110, zo, 0, 0x00000000, 2, 0x3210);
```

Lane `r` reads `x[r + 2c]` in the first call and `xs[r + 2c] = x[r + 2c + 1]` in
the second:

| Lane | First call | Second call | Output |
|---|---|---|---|
| 0 | h[0]·x[0] + h[2]·x[2] + ... + h[14]·x[14] | h[1]·x[1] + h[3]·x[3] + ... + h[15]·x[15] | y[0] |
| 1 | h[0]·x[1] + h[2]·x[3] + ... + h[14]·x[15] | h[1]·x[2] + ... + h[15]·x[16] | y[1] |
| ... | | | |
| 15 | h[0]·x[15] + ... + h[14]·x[29] | h[1]·x[16] + ... + h[15]·x[30] | y[15] |

Make the shifted copy with `aie::shuffle_down(x, 1)` or
`aie::load_unaligned_v<64>(p + 1)`; both are measured. The shift is extra work
outside the multiplier.

## C: real x real, data in `zbuff`

The z side has no parity lock: its column step is +1. Put a 32-sample data
window in `zbuff` and the taps in `xbuff`, each tap duplicated. Bytes 0..47 hold
`h[k]` and bytes 48..95 hold the taps delayed by one, `h[k - 1]` with
`h[-1] = 0`:

```cpp
// xt[2k] = xt[2k + 1] = h[k];  xt[48 + 2k] = xt[48 + 2k + 1] = h[k - 1]
acc = mul16(xt,       0, 0xB0B0B0B0, 4, 0x3210, zd, 0, 0x76543210, 2, 0x3210);
acc = mac16(acc, xt, 16, 0xB0B0B0B0, 4, 0x3210, zd, 0, 0xBA987654, 2, 0x3210);
acc = mac16(acc, xt, 32, 0xB0B0B0B0, 4, 0x3210, zd, 0, 0xFEDCBA98, 2, 0x3210);
```

Lanes `r` and `r + 2` read the same data, so the delayed taps turn the second
lane of each such pair into the next output. Lane `r` holds `y[m]` with
`m = 2 * (floor(r/4)*2 + r mod 2) + (1 if r mod 4 >= 2 else 0)`:

| Lanes 0..7 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| Output | y[0] | y[2] | y[1] | y[3] | y[4] | y[6] | y[5] | y[7] |

Lanes 8..15 repeat the pattern for y[8..15]. The third call is mostly zero taps,
hence 67%. Reads past `z[31]` wrap, but only against zero taps. Choose C over B
when the shifted copy costs more than one extra call.

## D: complex x complex (TC2)

Four real products per term, into two accumulators:

```
acc_re = x_re * w_re - x_im * w_im      mul16, then msc16
acc_im = x_re * w_im + x_im * w_re      mul16, then mac16
```

With 8 terms per output, 16 complex outputs cost 8 x 4 x 16 / 128 = 4 calls.
Parameters and lane maps are in `references/examples.md`.

## E: more terms than columns (TC1 step B)

Accumulate across calls with `mac`. Advance the data with `xstart` (a multiple
of 4) and the coefficients with `zoffsets`, because `zstart` stops at 15: TC1
reaches taps 16..31 with `zoffsets = 0x88888888`.

## F: lane groups sharing an operand (TC2)

Repeat an offsets nibble pattern every 4 lanes to give each lane group the same
operand: TC2's `xoffsets = 0x30303030` gives every time slot the same 4
weight vectors, while `zoffsets = 0xCC884400` moves each group to the next time
slot's samples.

## License

Copyright © 2026 Advanced Micro Devices, Inc

SPDX-License-Identifier: MIT

[Terms and Conditions](https://www.amd.com/en/corporate/copyright)
