<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
<!-- All code blocks below were compiled and run in x86sim and aiesim (hw target) with Vitis 2026.2 for
     xcvc1902-vsva2197-2MP-e-S (AIE) and xcve2802-vsvh1760-2MP-e-S (AIE-ML), and matched a Python golden
     model bit-exactly (float: within 1e-5 relative). -->
# Implement the Algorithm (`run()` body)

Turn a plain-English algorithm into a vectorized `run()` that follows UG1079 coding practice. Work
through the five steps in order. Do not start writing code before step 2 is written down.

```text
spec → numeric contract → lanes + accumulator → I/O access + state → loop + code
```

## Step 1: Restate the spec as equations

Write the computation per output sample with explicit indices, for example
`y[n] = sum_{k=0..7} c[k] * x[n-k]`. Then answer these questions:
- Is the computation per sample (element-wise), per frame (reduction), or over neighbouring samples
  (sliding window)?
- Does it need samples from the previous `run()` call (state)?
- What is the rate change (decimate, interpolate, 1:1)?

## Step 2: Numeric contract

Fix the contract and write it as a comment at the top of `run()`. The golden model (`verify.md`)
implements the same contract.

| Item | Decide | Default |
|---|---|---|
| Input/output/coefficient types | e.g. int16 in, int16 coeff, int16 out | from the spec |
| Fixed-point format | Q-format of coefficients (Q15 = value * 2^15) | Q15 for int16 gains |
| Accumulator | tag and width (see Step 3) | `acc48` for integer sums |
| Output shift | `SHIFT` applied in `to_vector<T>(SHIFT)` | number of coefficient fraction bits |
| Rounding | `aie::rounding_mode::*` | `conv_even` |
| Saturation | `aie::saturation_mode::*` | `saturate` |
| State | margin, delay line, or none; initial value | zeros |
| Order of operations | e.g. "shift each lane, then sum lanes in int32" | as written |

### What `to_vector<T>(shift)` does

`acc.to_vector<T>(shift)` computes `round(acc / 2^shift)` using the current rounding mode, then
saturates to `T` if the saturation mode is not `none`. `acc.from_vector(v, shift)` loads `v * 2^shift`.
Results measured on both architectures for `to_vector<int16>(2)`, i.e. x/4:

| x/4 | -2.5 | -1.5 | -1.25 | -0.5 | 0.5 | 1.25 | 1.5 | 2.5 |
|---|---|---|---|---|---|---|---|---|
| `floor` | -3 | -2 | -2 | -1 | 0 | 1 | 1 | 2 |
| `conv_even` | -2 | -2 | -1 | 0 | 0 | 1 | 2 | 2 |
| `symmetric_inf` | -3 | -2 | -1 | -1 | 1 | 1 | 2 | 3 |
| `positive_inf` | -2 | -1 | -1 | 0 | 1 | 1 | 2 | 3 |
| `symmetric_zero` | -2 | -1 | -1 | 0 | 0 | 1 | 1 | 2 |

- Saturation `saturate` clamps to [-32768, 32767]. `symmetric` clamps to [-32767, 32767]. `none` wraps.
- **Power-up state is `floor` and no saturation** (measured). Always set both in the constructor:
  `aie::set_rounding(...)` and `aie::set_saturation(...)`, as in `cpp.md`.
- Golden model for `conv_even`, which is Python's `round()` on the exact rational value:
  ```python
  def srs(v, s, lo=-32768, hi=32767):
      q, r = divmod(v, 1 << s); half = 1 << (s - 1)
      if r > half or (r == half and q & 1): q += 1
      return max(lo, min(hi, q))
  ```
- **AIE-ML trap:** `to_vector<int32>()` from an `acc32` accumulator is a bit-cast. The shift, rounding
  and saturation are ignored. Use `acc48` (64 b on AIE-ML) when the output is 32-bit.
- Plain vector `aie::add/sub/mul` on integer vectors (not accumulators) wraps. Do the additions in the
  accumulator if the contract says "saturate".
- Float has no shift, rounding mode or saturation. On AIE-ML float mul is emulated with bfloat16 and differs
  from IEEE fp32 by about 1e-7 relative.

## Step 3: Lanes and accumulator

| Data x coeff | AIE lanes / acc | AIE-ML lanes / acc | Portable choice |
|---|---|---|---|
| int16 x int16, element-wise | 16 / acc48 | 32 / acc32 | branch on `__AIE_ARCH__` (example below) |
| int16 x int16, sum can exceed 2^31 | 16 / acc48 | 16 / acc48 (= 64 b) | 16 lanes, `acc48` |
| int16 x int16, `sliding_mul` (FIR) | 16 / acc48 | 16 / acc32 or acc48 | 16 lanes |
| cint16 x cint16 / int16 | 8 / cacc48 | 8 / cacc64 | 8 lanes |
| float | 8 / accfloat | 16 / accfloat | branch on `__AIE_ARCH__` |
| int32 x int16 (from the API docs, not validated here) | 8 / acc48 or acc80 | 16 / acc64 | 8 lanes, `acc64` on AIE-ML |

Rules:
- LANES must divide the samples per `run()`, and `LANES * sizeof(T)` should be 32 B (AIE) or 64 B (AIE-ML).
- Use an explicit accumulator tag whenever the default could overflow (check `device-rules.md` §3).
- Put LANES and the tag in the class as `static constexpr` so the hpp, cpp and graph agree.

```cpp
#if __AIE_ARCH__ >= 20
    static constexpr unsigned LANES = 32;       // AIE-ML: 32 x int16, acc32
    using acc_tag = acc32;
#else
    static constexpr unsigned LANES = 16;       // AIE: 16 x int16, acc48
    using acc_tag = acc48;
#endif
```

## Step 4: I/O access and state

| Port | Read | Write |
|---|---|---|
| `input_buffer<T>& in` | `auto pi = aie::begin_vector<L>(in); v = *pi++;` or `aie::load_v<L>(in.data() + off)` | |
| `output_buffer<T>& out` | | `auto po = aie::begin_vector<L>(out); *po++ = v;` or `aie::store_v(out.data() + off, v)` |
| `input_stream<T>* in` | `readincr_v<128/bits(T)>(in)` (128-bit vector) or `readincr(in)` (scalar) | |
| `output_stream<T>* out` | | `writeincr(out, v)` where `v` is 128 bits, or a scalar |
| `input_cascade<tag>* in` | `aie::accum<tag, L> a = readincr_v<L>(in);` | |
| `output_cascade<tag>* out` | | `writeincr(out, acc)` |

- Buffer pointers from `data()` and `begin_vector` iterators are aligned for vector access.
  `load_v`/`store_v` need aligned addresses. Use `aie::load_unaligned_v` for any other offset (slow on AIE).
- Concatenate stream reads to build wider vectors:
  `aie::concat(readincr_v<8>(s), readincr_v<8>(s))` gives 16 x int16. This works on both architectures.
- On AIE-ML a kernel may have **at most one input stream and one output stream**
  ("The AI Engine kernel has 2 input stream ports, but the maximum number...").

State across `run()` calls (sliding windows, IIR, phase accumulators):

| Method | When | How |
|---|---|---|
| Buffer margin | buffer input, history ≤ a few hundred samples | `input_buffer<T, extents<inherited_extent>, margin<M>>`. The buffer then holds M old samples followed by the new ones. The first call sees M zeros. Use M ≥ history, rounded up to 32 B / `sizeof(T)` so that vector loads stay aligned |
| Internal delay line | stream input, or a small history | class member `aie::vector<T, L> hist;` (or an aligned array), initialized in the constructor and updated at the end of `run()` |
| Scalars (phase, counters) | any | class members. Never use `static` locals: they are shared by every instance on the tile |

## Step 5: Loop and code

```cpp
for (unsigned i = 0; i < NSAMP / LANES; i++)
    chess_prepare_for_pipelining chess_loop_range(NSAMP / LANES, )
{
    // only vector loads, vector math, vector stores
}
```

- `chess_prepare_for_pipelining`: put it on the innermost hot loop. `chess_loop_range(min, max)` tells the
  compiler the trip count (the max can be left empty). Without it, small loops may not pipeline.
- `chess_unroll_loop()`: put it on short fixed inner loops (for example the K loop of an mmul) so that the
  outer loop is the pipelined one.
- `__restrict` on raw pointers that never alias (`const int16* __restrict p = in.data();`).
- Do no scalar per-sample arithmetic, `if` on sample values, function calls that are not inlined, or `/` and
  `%` in the hot loop. Use `aie::select` for conditions, and multiply by a precomputed reciprocal instead of dividing.
- Do reductions (sum, max) lane-wise inside the loop and once across lanes after the loop.
- Keep loop-invariant vectors (coefficients, constants) outside the loop.

## Patterns (copy, rename, adapt)

### Element-wise with offset and gain: `y = sat(round((x*G + OFF*2^S) / 2^S))`

```cpp
void gain_offset::run(input_buffer<int16>& sig_i, output_buffer<int16>& sig_o)
{
    auto pi = aie::begin_vector<LANES>(sig_i);
    auto po = aie::begin_vector<LANES>(sig_o);
    const aie::vector<int16, LANES> off = aie::broadcast<int16, LANES>(OFFSET);
    for (unsigned i = 0; i < NSAMP_SIG_I / LANES; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_I / LANES, )
    {
        aie::accum<acc_tag, LANES> acc;
        acc.from_vector(off, SHIFT);                 // acc = OFFSET << SHIFT
        acc = aie::mac(acc, *pi++, GAIN);            // acc += x * GAIN (vector x scalar)
        *po++ = acc.to_vector<int16>(SHIFT);         // round + saturate
    }
}
```

With `acc32` on AIE-ML, `|x*G + OFF*2^S|` must stay below 2^31.

### Float element-wise: no shift, no rounding mode

```cpp
    const aie::vector<float, LANES> off = aie::broadcast<float, LANES>(0.25f);
    for (unsigned i = 0; i < NSAMP_SIG_I / LANES; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_I / LANES, )
    {
        aie::accum<accfloat, LANES> acc;
        acc.from_vector(off);
        acc = aie::mac(acc, *pi++, 0.7071f);
        *po++ = acc.to_vector<float>();
    }
```

### Complex input, magnitude squared: `y = sat32(round((re² + im²) / 2^S))`

```cpp
    auto pi = aie::begin_vector<8>(sig_i);           // input_buffer<cint16>
    auto po = aie::begin_vector<8>(sig_o);           // output_buffer<int32>
    for (unsigned i = 0; i < NSAMP_SIG_I / 8; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_I / 8, )
    {
        aie::vector<cint16, 8> x = *pi++;
        aie::vector<int16, 8> re = aie::real(x), im = aie::imag(x);
        aie::accum<acc48, 8> acc = aie::mul<acc48>(re, re);   // acc48: correct on both architectures
        acc = aie::mac(acc, im, im);
        *po++ = acc.to_vector<int32>(SHIFT);
    }
```

`aie::abs_square<int32>(x, SHIFT)` gives the same result on AIE but **wrong** results on AIE-ML (no
shift, wraps). Other complex helpers: `aie::conj(v)`, `aie::mul(a, b)` on cint16 vectors (gives cacc48
on AIE, cacc64 on AIE-ML), `aie::mul(aie::op_conj(a), b)` to fold the conjugate into the multiply, and
`aie::vector_cast<cint16>(int16_vec)` to reinterpret interleaved re/im data.

### Sliding window with a margin: 8-tap real FIR `y[n] = sum_k c[k] x[n-k]`

Header (port and LUT):

```cpp
    static constexpr unsigned NTAPS  = 8;
    static constexpr unsigned MARGIN = 16;   // >= NTAPS-1, rounded up to 32 bytes of int16
    static constexpr unsigned LANES  = 16;   // legal for int16 x int16 sliding_mul on AIE and AIE-ML
    static constexpr unsigned TAPS_SIZE = 16;
    alignas(32) int16 (&TAPS)[TAPS_SIZE];    // TAPS[p] = c[NTAPS-1-p], zero padded
    void run(input_buffer<int16, extents<inherited_extent>, margin<MARGIN>>& sig_i,
             output_buffer<int16>& sig_o);
```

Body:

```cpp
    const aie::vector<int16, 16> taps = aie::load_v<16>(TAPS);
    auto pi = aie::begin_vector<LANES>(sig_i);       // starts at the oldest margin sample
    auto po = aie::begin_vector<LANES>(sig_o);
    aie::vector<int16, LANES> lo = *pi++;
    for (unsigned i = 0; i < NSAMP_SIG_O / LANES; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_O / LANES, )
    {
        aie::vector<int16, LANES> hi = *pi++;
        // out[l] = sum_p taps[p] * data[d_s + l + p], d_s = MARGIN - (NTAPS-1)
        auto acc = aie::sliding_mul<LANES, NTAPS>(taps, 0, aie::concat(lo, hi), MARGIN - (NTAPS - 1));
        *po++ = acc.template to_vector<int16>(SHIFT);
        lo = hi;
    }
```

The graph keeps `dimensions(kk.in[0]) = { NSAMP_SIG_I }`. The compiler adds the margin, and the first
call sees zeros in it. `sliding_mul` needs `data` to hold `d_s + LANES + NTAPS - 1` samples. More taps:
use more `aie::sliding_mac` calls on further coefficient/data offsets, or split the taps over a cascade
(`multi-kernel-graph.md`).

### Sliding window with an internal delay line (stream I/O)

```cpp
// hpp: aie::vector<int16, LANES> hist;   constructor: hist = aie::zeros<int16, LANES>();
void fir8s::run(input_stream<int16>* sig_i, output_stream<int16>* sig_o)
{
    const aie::vector<int16, 16> taps = aie::load_v<16>(TAPS);
    aie::vector<int16, LANES> lo = hist;
    for (unsigned i = 0; i < NSAMP_SIG_I / LANES; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_I / LANES, )
    {
        aie::vector<int16, LANES> hi = aie::concat(readincr_v<8>(sig_i), readincr_v<8>(sig_i));
        auto acc = aie::sliding_mul<LANES, NTAPS>(taps, 0, aie::concat(lo, hi), LANES - (NTAPS - 1));
        aie::vector<int16, LANES> y = acc.template to_vector<int16>(SHIFT);
        writeincr(sig_o, y.extract<8>(0));
        writeincr(sig_o, y.extract<8>(1));
        lo = hi;
    }
    hist = lo;                                       // state for the next run()
}
```

### Rate change: decimate by 2, `y[n] = round((x[2n] + x[2n+1]) / 2)`

```cpp
    for (unsigned i = 0; i < NSAMP_SIG_O / LANES; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_O / LANES, )
    {
        aie::vector<int16, LANES> a = *pi++;
        aie::vector<int16, LANES> b = *pi++;
        auto [ev, od] = aie::interleave_unzip(a, b, 1);   // even / odd samples of a:b
        aie::accum<acc48, LANES> acc;
        acc.from_vector(ev, 0);
        acc = aie::add(acc, od);
        *po++ = acc.to_vector<int16>(1);
    }
```

`interleave_zip`/`unzip` and `filter_even`/`filter_odd` use the shuffle unit. They are fine for
rate changes and layout conversion, but keep them to a minimum in MAC-bound loops.

### Reduction and compare/select: per-frame max and min

```cpp
    auto pi = aie::begin_vector<LANES>(sig_i);
    aie::vector<int16, LANES> vmax = *pi;
    aie::vector<int16, LANES> vmin = *pi++;
    for (unsigned i = 1; i < NSAMP_SIG_I / LANES; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_I / LANES - 1, )
    {
        aie::vector<int16, LANES> x = *pi++;
        vmax = aie::max(vmax, x);                          // lane-wise running max
        vmin = aie::select(vmin, x, aie::lt(x, vmin));     // compare + select
    }
    int16* po = sig_o.data();
    po[0] = aie::reduce_max(vmax);                         // cross-lane only after the loop
    po[1] = aie::reduce_min(vmin);
```

For sums, accumulate lane-wise with `aie::mac(acc, x, x)` / `aie::add(acc, x)`, convert once with
`to_vector<int32>(S)`, then call `aie::reduce_add`. State in the contract whether you shift before or after the
cross-lane sum. Masks: `aie::lt/le/gt/ge/eq/neq(a, b)` return `aie::mask<L>`, and
`aie::select(a, b, m)` picks `b` where `m` is set.

### Look-up tables

- Declare and register the LUT as in `hpp.md` (reference member + `REGISTER_PARAMETER`). The graph
  passes a `std::vector<T>` with the values (`graph.md`).
- Make the size a multiple of the vector size and pad with zeros. Load with `aie::load_v<L>(LUT + k*L)`,
  which needs 32 B alignment (`alignas(aie::vector_decl_align)`).
- Sequential access (oscillators, window functions, twiddles): step a vector pointer through it, no gather.
- Random index per lane: element-wise reads (`v[l] = LUT[idx[l]]`) become scalar code. Use them only
  outside the hot loop, or restructure the algorithm. AIE has `aie::sincos` for sin/cos. AIE-ML does not,
  so use a LUT.
- Total LUT bytes count against the tile memory budget (`device-rules.md` §6).

## Worked flow: "8-tap low-pass FIR on int16 samples, 256 samples per call, Q15 taps"

1. **Equation:** `y[n] = sum_{k=0..7} c[k] x[n-k]`. Sliding window, 1:1 rate, needs 7 old samples.
2. **Contract:** int16 x int16 → int16. Taps in Q15, `SHIFT = 15`, `conv_even`, `saturate`. History
   starts at zero. The accumulator never overflows (8 x 2^30 < 2^47).
3. **Lanes:** `sliding_mul<16, 8>`. 16 lanes is legal on both architectures, so no `#if` is needed.
   The data window is 32 samples.
4. **I/O and state:** buffer ports. `margin<16>` (≥ 7, 32 B multiple). `TAPS` LUT of 16 entries, reversed,
   zero padded.
5. **Loop:** 256 / 16 = 16 iterations with `chess_prepare_for_pipelining chess_loop_range(16, )`. Two
   vectors `lo`/`hi` slide through the buffer. The code is the margin FIR pattern above.
6. **Golden:** `y = srs(sum(c[k]*xp[n+7-k]), 15)` with `xp` = 7 zeros + input (see `verify.md`).
   Expect 0 LSB error.
