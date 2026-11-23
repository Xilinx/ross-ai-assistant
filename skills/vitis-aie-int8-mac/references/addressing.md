<!--
  Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
  SPDX-License-Identifier: MIT
-->

# Address generator reference

What each parameter does, and which designs the rules make impossible. Exact
lane maps are **measured**, not predicted: run `make -C probe run`.

Contents:
- [Operand shapes](#operand-shapes)
- [Parameter roles](#parameter-roles)
- [Closed-form mapping](#closed-form-mapping)
- [Legality rules](#legality-rules)
- [The parity lock](#the-parity-lock)
- [Verifying a configuration](#verifying-a-configuration)

## Operand shapes

| | `mul8` / `mac8` | `mul16` / `mac16` |
|---|---|---|
| lanes (outputs) | 8 | 16 |
| columns (terms accumulated per lane) | 16 | 8 |
| accumulator | `v8acc48` | `v16acc48` |
| `xbuff` | `v64int8` / `v64uint8` / `v128int8` / `v128uint8` | same |
| `zbuff` | `v32int8` / `v32uint8` | same |

Signed and unsigned overloads exist for each buffer independently, so each
intrinsic has 8 overloads (x small/large x signed/unsigned, z signed/unsigned).

Every lane computes `acc[r] = sum over columns c of xbuff[xmap[r][c]] * zbuff[zmap[r][c]]`.
The address parameters define `xmap` and `zmap`. The x and z generators are
independent — this is why the workbooks give them separate sheets.

## Parameter roles

**`xstart` / `zstart`** — base index into the buffer. `zstart` must be a
compile-time constant, and only its 4 least-significant bits are used, so it
cannot exceed 15 no matter what you write. To reach `z[16..31]`, use
`zoffsets`, not `zstart` (see TC1 step B).

**`xoffsets` / `zoffsets`** — a 4-bit offset per lane group, LSB nibble
applying to the first group. On the x side the nibble unit is 4 elements and
the second lane of each pair reads one element further (the "every second lane
is previous + 1" rule). On the z side the unit is 2 elements and, within each
group of four lanes, lanes 0 and 2 share one nibble and lanes 1 and 3 the next.
The exact rules are in the closed form below.

**`xstep` / `zstep`** — advance applied per column *pair*, not per column. The
second column of each pair is a fixed +2 on the x side and +1 on the z side.
This is why a column stride of 1 is unreachable on the x side. Neither step has
a range limit: the value is applied as written and the address wraps modulo
the buffer length.

**`xsquare` / `zsquare`** — a mini-permute over the 2x2 sub-block of the
(lane, column) grid. Four hex digits, each selecting one of four positions.
`0x3210` is the identity and is the default. Squares reorder which pre-square
cell each lane/column pair ends up reading; they do not extend reach.

## Closed-form mapping

The same formulas apply to `mul8`/`mac8` and `mul16`/`mac16`; only `L` and `C`
differ.

Let `L` = lanes (8 or 16), `C` = columns (16 or 8), `XLEN` = 64 or 128.
Nibble `k` of an offsets word is counted from the LSB, so `k = 0` is the
low nibble.

### x side, before the square

```
rx = floor(r / 2)
rr = r mod 4
o[k] = nibble k of xoffsets,  o[-1] = 0

              o[rx]*4                              if rr = 0
offset(r)  =  o[rx]*4 + 1                          if rr = 1
              o[rx]*4 + (o[rx-1] + 1)*4            if rr = 2
              o[rx]*4 + (o[rx-1] + 1)*4 + 1        if rr = 3

step(c)    = floor(c / 2) * xstep + (c mod 2) * 2

idx(r,c)   = (xstart + offset(r) + step(c)) mod XLEN
```

### z side, before the square

```
rz = floor(r / 4)*2 + (r mod 2)
n[k] = nibble k of zoffsets

offset(r)  = n[rz] * 2
step(c)    = floor(c / 2) * zstep + (c mod 2)

idx(r,c)   = (zstart + offset(r) + step(c)) mod 32
```

### The square

The square does not compute an index; it chooses which **pre-square cell** a
given lane/column reads. `q` is one nibble of the square word.

```
x:  pos   = floor((r mod 4) / 2) * 2 + (c mod 2)
    q     = nibble pos of xsquare
    src_r = floor(r/4)*4 + floor(q/2)*2 + ((r mod 4) mod 2)
    src_c = floor(c/2)*2 + (q mod 2)

z:  pos   = ((r mod 4) mod 2) * 2 + (c mod 2)
    q     = nibble pos of zsquare
    src_r = floor(r/4)*4 + floor((r mod 4)/2)*2 + floor(q/2)
    src_c = floor(c/2)*2 + (q mod 2)

final(r,c) = idx(src_r, src_c)
```

### Status of this transcription

It reproduces the workbooks' saved configurations (see
`references/spreadsheets.md`) and both worked examples. Use it to **derive**
parameters; use `probe/` to **confirm** them. If the two ever disagree, the
probe is right and this section is wrong.

The parity lock below follows directly from it: `offset(r)` is odd exactly when
`rr` is odd, and `step(c)` contributes `(c mod 2)*2`, which is always even.

### Where the AMD documentation is incomplete or wrong

Sources: the intrinsics page `group__vect__mult__8x8.html` and UG1079 "MAC on
8x8 bits". UG1079 matches the closed form. Square source-cell selection is
described in `group__vect__mult__intrinsics__explained.html#data_selection_adv`.

- `xoffsets` fields are 4-bit nibbles in units of 4 bytes (`zoffsets`: 2
  bytes). `0x03020100` is nibbles `0,0,1,0,2,0,3,0`, not lane offsets 0..3
  (TC1's lane offsets 0..7 come from combining it with `xsquare=0x2110`).
- `mul8` uses only the low 16 bits of `xoffsets` (4 nibbles for 8 lanes);
  `mul16` uses all eight nibbles.
- `mul8`/`mac8` use the same `rx = floor(r/2)` lane pairing as
  `mul16`/`mac16`, although the intrinsics page words it as one nibble per lane.
- The column step applies per 2x2 block (UG1079: "if a pair of offset
  parameters select a 2 * 2 block, the step applies to the next 2 * 2 block").
- For `mul16`/`mac16` the intrinsics page describes `zoffsets` as nibble
  `floor(r/2)`. The hardware uses `rz = floor(r/4)*2 + (r mod 2)`, scaled by 2
  (UG1079: the 2x2 coefficient block "is duplicated to construct a 4 * 2
  block"). With `zoffsets=0x43322110`, `zstep=2`, `zstart=0`, `zsquare=0x3210`,
  column 0 reads:

  ```
  lane      0  1  2  3   4  5  6  7   8  9 10 11  12 13 14 15
  z index   0  2  0  2   2  4  2  4   4  6  4  6   6  8  6  8
  ```

  Trust the closed form, not that page.
- Addresses wrap modulo the buffer length, which neither source states: an x
  address of 64 in a `v64int8` reads `x[0]`.

## Legality rules

See SKILL.md step 3 (rules) and step 6 (compiler messages).

## The parity lock

The single most important consequence of the rules.

On the x side, every coarse contribution is a multiple of 4: `xstart` is a
multiple of 4, `xoffsets` nibbles are scaled by 4, and `xstep` is a multiple of
4. The only adjustments finer than 4 are:

- **+1** from lane parity (the "every second lane is previous + 1" rule)
- **+2** from column parity (the second column of each pair)

So within a lane, the reachable x indices all share the parity of that lane.
**No lane can read both even and odd x elements in one call.**

Consequences:

- A real FIR that needs `x[n], x[n+1], ..., x[n+15]` accumulated into one lane
  cannot be expressed in a single call with the data in `xbuff`. It needs a
  one-sample shifted copy of the window and a second `mac` (pattern B in
  `references/patterns.md`), or the data in `zbuff` (pattern C).
- Adding taps does not help; the constraint is on tap *parity*, not tap count.
  Zero-padding a filter to more taps leaves the odd-offset taps just as
  unreachable.
- Polyphase decomposition does not rescue it either, because it needs a unit
  column stride and `xstep` must be a multiple of 4.

No `xsquare` value gives `index(lane, col) = lane + 1 + 2*col`: squares
permute, they do not shift.

The z side has no equivalent lock: its column-pair increment is +1, so both
parities are reachable. But z row addressing cannot distinguish lane `r` from
lane `r+2`, so those two lanes always read the same data. Pattern C uses this:
delayed taps in `xbuff` turn the second lane into the next output.

## Verifying a configuration

Follow SKILL.md step 5.

## License

Copyright © 2025-2026 Advanced Micro Devices, Inc

SPDX-License-Identifier: MIT

[Terms and Conditions](https://www.amd.com/en/corporate/copyright)
