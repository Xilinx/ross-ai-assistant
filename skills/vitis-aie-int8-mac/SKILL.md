---
name: vitis-aie-int8-mac
description: Configure the AIE int8xint8 vector MAC intrinsics (mul8/mac8, mul16/mac16) for a target algorithm. Use when choosing or debugging xstart/xoffsets/xstep/xsquare and zstart/zoffsets/zstep/zsquare, when a lane is reading the wrong buffer element, when deciding between mul8 and mul16, when mapping a FIR/beamformer/matrix operation onto AIE int8 MACs, or when an aiecompiler "must be a multiple of" chess_error appears. AIE only - these intrinsics do not exist on AIE-ML or AIE-MLv2. Not for the `aie::mul`/`aie::mac` API or other AIE API questions.
compatibility: Requires Vitis 2026.1+ (AIE devices only)
license: MIT
metadata:
  author: Faisal El-Shabani
  version: "1.0.1"
  stage: beta
---

<!--
  Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
  SPDX-License-Identifier: MIT
-->

# AIE int8 x int8 vector MAC configuration

Turns an algorithm into a legal, verified set of address-generator parameters
for the AIE 8-bit MAC family. Scope is **configuration and correctness**, not
performance tuning.

Covered: `mul8`/`mac8` and `mul16`/`mac16`, both `v64int8` and `v128int8`
x-buffers, signed and unsigned. `zbuff` is always `v32int8`.

Out of scope: floating-point and non-int8 MAC widths, AIE-ML/AIE-MLv2
equivalents, and graph-level dataflow and buffer placement.

## The shape of the instruction

| Intrinsic | Lanes | Columns (MAC terms per lane) | Accumulator |
|---|---|---|---|
| `mul8` / `mac8` | 8 | 16 | `v8acc48` |
| `mul16` / `mac16` | 16 | 8 | `v16acc48` |

Both perform 128 MACs. Choose by **how many independent outputs you need in
parallel**, not by MAC count: `mul16` gives 16 outputs of 8 terms each, `mul8`
gives 8 outputs of 16 terms each.

Each call reads two operand buffers through independent address generators:

```
mul16(xbuff, xstart, xoffsets, xstep, xsquare,
      zbuff, zstart, zoffsets, zstep, zsquare)
mac16(acc, ...same...)      // acc is argument 0, everything shifts by one
```

## Workflow

### 1. State the application inputs

Before touching parameters, write down:

- operand semantics (real / complex, signed / unsigned) and memory layout
- how many outputs are needed per call, and what each lane must produce
- how many terms accumulate into each output (taps, antennas, matrix depth)
- which operand is the "data" and which is the "coefficient" side

The side with **per-lane variation** normally goes in `xbuff`: its address
generator is the more expressive one. A coefficient set shared by all lanes
goes in `zbuff`.

### 2. Pick the instruction

Outputs per call decides lanes. Terms per output decides how many calls
accumulate: use `mul*` for the first, `mac*` for each subsequent one. If terms
exceed the column count, split across calls and shift the window with `xstart`
or the coefficients with `zoffsets` (see `references/examples.md`, TC1).

Each call does 128 int8 multiplies, so the minimum is `T * P / 128` calls per
output, with `T` terms per output and `P` = 1 (real x real), 2 (complex data x
real coefficients) or 4 (complex x complex). Start from the matching pattern in
`references/patterns.md`, which gives measured parameters and utilization for
each shape.

### 3. Check feasibility BEFORE writing code

Run the constraint rules first. They eliminate whole classes of design. Not
every rule is enforced by the tools, so the last column matters:

| Parameter | Rule | If broken |
|---|---|---|
| `xstart` | multiple of 4 (xbuff granularity is 32-bit) | hw compile error; x86sim run-time `chess_error` |
| `xstep` | multiple of 4 | hw compile error; x86sim run-time `chess_error` |
| `zstart` | multiple of 2 (zbuff granularity is 16-bit), compile-time constant | hw compile error; x86sim run-time `chess_error` |
| `zstart` | **in [0,15]**: only the 4 LSB are used | hw compile **warning only**, x86sim silent; 16 behaves as 0 |
| `zstep` | multiple of 2 | hw compile error; x86sim run-time `chess_error` |
| `xsquare`/`zsquare` | 4 hex digits, each 0..3 | **no diagnostic anywhere**; each digit is masked to 2 bits, so 4 behaves as 0 |

`xstep` and `zstep` have no range limit. The step is applied as written and the
address wraps modulo the buffer length (64 or 128 for x, 32 for z).

**The parity lock.** `xstart`, `xoffsets` nibbles (unit 4) and `xstep` are all
multiples of 4. The only sub-4 adjustments available are +1 from lane parity
and +2 from column parity. Therefore **each lane can only reach x indices of a
single parity**. A real FIR needing `x[n..n+15]` contiguously in one lane is
impossible in one call with the data in `xbuff`: it needs a one-sample shifted
copy and a second call, or the data in `zbuff` (`references/patterns.md`, B and
C). Discover this now, not after a day of debugging.

Lint every candidate parameter set. This is required: it is the only check
that rejects `zstart` > 15 and square digits > 3, which build and simulate.

```bash
python3 scripts/check_params.py --lanes 16 --xlen 64 \
    --xstart 0 --xoffsets 0x30303030 --xstep 4 --xsquare 0x3210 \
    --zstart 0 --zoffsets 0xCC884400 --zstep 2 --zsquare 0x1010
```

### 4. Derive the lane map

Work forwards from the closed form in `references/addressing.md`. It gives
`idx(lane, column)` for both buffers in about a dozen lines of arithmetic, so
parameters are **derived**, never searched for. Typical designs fall out
directly:

- need lane `r` and lane `r+1` one element apart? That is free — the `rr = 1`
  case adds exactly 1.
- need groups of 4 lanes to share an operand set? Repeat the offsets nibble
  pattern every 4 lanes (TC2's `0x30303030`).
- need to reach past element 15 of `zbuff`? Use an offsets nibble, since
  `n[rz]*2` scales, while `zstart` is capped at 15.

Then confirm the derivation by measurement — that is step 5, and it is not
optional. The closed form is a transcription of the workbook formulas, not an
authority.

Read programmatically, the workbooks show only the lane maps they were last
saved with; measure new parameters with `probe/` (`references/spreadsheets.md`).

### Output stage: accumulator to int8

`acc.to_vector<int8>(shift)` divides by 2^shift, rounds and saturates in one
step, using the tile's current modes. Set both at the top of the kernel rather
than relying on defaults:

```cpp
aie::set_rounding(aie::rounding_mode::symmetric_inf);  // round half away from zero
aie::set_saturation(aie::saturation_mode::saturate);   // clamp to [-128, 127]
```

Map the specification's wording to a mode (measured on 2026.1; x86sim and
aiesimulator agree):

| Specification says | `rounding_mode` |
|---|---|
| truncate toward -inf | `floor` |
| toward +inf | `ceil` |
| round half up | `positive_inf` |
| round half down | `negative_inf` |
| round half away from zero (MATLAB and C `round`) | `symmetric_inf` |
| round half toward zero | `symmetric_zero` |
| round half to even (convergent) | `conv_even` |
| round half to odd | `conv_odd` |

| Specification says | `saturation_mode` |
|---|---|
| wrap on overflow | `none` |
| clamp to [-128, 127] | `saturate` |
| clamp to [-127, 127] | `symmetric` |

AIE has no 8-element int8 vector, so an 8-lane accumulator (`mul8`/`mac8`)
cannot be converted on its own. Join two first:

```cpp
writeincr(out, aie::concat(acc_a, acc_b).to_vector<int8>(7));  // 16 int8 values
```

### 5. Verify — in this order

1. **Lint every parameter set** with `scripts/check_params.py` (step 3).
2. **Run the design in x86sim** against a bit-exact reference, then check the
   log as well as the exit status. A granularity violation prints a
   `chess_error` line at run time, yet x86sim still prints
   `Simulation completed successfully` and exits 0, leaving the output file
   empty. Require no `chess_error` in the log **and** the expected number of
   output samples.
3. **Compile the design for hw** (`v++ --compile --mode aie --target hw`) and
   fail on any `chess_error` **or** `chess_warning` in the log. `zstart` > 15
   builds successfully with only a warning.
4. **If a lane map is in doubt, measure it.** Add a `measure_xmap<...>` /
   `measure_zmap<...>` line to `probe/probe.cpp` and run `make -C probe run`
   (x86sim only). If the parameters came from a spreadsheet,
   `scripts/compare_maps.py` diffs the two automatically.

`examples/int8_mac_test.cpp` is the pattern for a self-checking kernel
(`make -C examples x86all` prints `Summary: 0 failure(s)`). To try parameter
sets before a design exists, add `CHECK_MUL16(...)` / `CHECK_MUL8(...)` lines
to `legality/legality.cpp` and run `make -C legality check`; it compiles only
the sets listed there.

x86sim is more permissive than the hw compiler in other ways too: `alignas(64)`
on a stack array simulates fine but fails the hw compile with
`stack allocation with alignment 64 > maximum stack alignment 32`.

### 6. Read the errors

Argument numbers are for `mul*`; add one for `mac*`/`msc*`, whose argument 0 is
the accumulator.

| Message | Cause |
|---|---|
| `Argument number 2 of this operation must be a multiple of 4` | `xstart` |
| `Argument number 4 of this operation must be a multiple of 4` | `xstep` |
| `Argument number 7 of this operation must be a multiple of 2` | `zstart` |
| `Argument number 7 of this operation must be in range [0,15]` (`chess_warning`) | `zstart` > 15; it is masked to 4 bits |
| `Argument number 9 of this operation must be a multiple of 2` | `zstep` |
| x86sim `Simulation completed successfully` but the output is empty or short | a granularity violation; the `chess_error` text is earlier in the log |
| `implicit instantiation of undefined template 'aie::detail::vector_storage<signed char, 8>'` | `to_vector<int8>` on an 8-lane accumulator; `concat` two first (output stage) |
| lane reads the right elements but the wrong phase | parity lock; shift the data |
| results correct in x86sim, hw compile fails | step 5.3 skipped |

## Tools in this skill

| Path | Purpose |
|---|---|
| `probe/` | x86sim project that measures the true lane maps. The authoritative source |
| `examples/` | The two worked examples (TC1, TC2) plus coverage cases TC3–TC5, self-checking in x86sim. `make -C examples x86all` |
| `legality/` | Minimal hw compile of the `CHECK_*` sets in `legality.cpp`, for trying parameters before a design exists. Fails on `chess_error` or `chess_warning` |
| `scripts/check_params.py` | Required lint of every parameter set. The only check for `zstart` > 15 and square digits > 3 |
| `scripts/read_spreadsheet.py` | Read parameter cells and cached lane maps from the workbooks |
| `scripts/compare_maps.py` | Regression check: workbook maps vs measured maps. Run after editing a workbook |

## Toolchain

Vitis 2026.1 or later. Source the `settings64.sh` of your Vitis install before
running any of the `make` targets.

Part: `xcvc1902-vsva2197-2MP-e-S`, or any AIE device. Override with
`make PART=<part>`. These intrinsics are AIE-only. To confirm a part is AIE,
check which architecture `aiecompiler` selects for it:

```bash
grep -o -- '--aiearch [a-z2-]*' compile_hw.log
```

`--aiearch aie` is AIE and works. `--aiearch aie-ml` (AIE-ML) and
`--aiearch aie2ps` (AIE-MLv2) do not have `mul8`/`mul16`, and the compile fails
with `use of undeclared identifier 'mul16'`.

## References

- `references/addressing.md` — what each parameter does, and the parity lock
- `references/patterns.md` — cost formula and measured patterns: complex FIR, real FIR, complex x complex
- `references/spreadsheets.md` — cell map, how to read the tables, caveats
- `references/examples.md` — TC1 (32-tap complex FIR) and TC2 (4-layer MIMO beamformer)

## License

Copyright © 2025-2026 Advanced Micro Devices, Inc

SPDX-License-Identifier: MIT

[Terms and Conditions](https://www.amd.com/en/corporate/copyright)
