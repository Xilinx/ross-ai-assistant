<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# AIE API Reference (detailed)

A detailed reference for the AIE API (`aie::` namespace, from
`<aie_api/aie.hpp>`) primitives used in kernels -- giving, for each primitive,
the **per-dtype support, whether it vectorizes (and at what lane width), the
accumulator it lands in, and the idiomatic usage**. This is the depth the raw
header doc-comments do not surface at a glance, so an agent can answer three
questions without reading the whole header tree:

1. **Does this primitive exist for my dtype?** (bf16 / fp32 = `accfloat` / int8)
2. **Is it vectorized, and what does it accumulate into?**
3. **What is the idiomatic, fused primitive for my math pattern?**
   (so we don't write `mul`+`sub` where `msc_square` exists)

**Scope.** Populated for **reduction / normalization** kernels today (LayerNorm,
RMSNorm, Softmax statistics, `Reduce*`), covering both the **core** primitives
(load/store, arithmetic, reductions) and the **fused / advanced** ones
(`mac_square`, `msc_square`, `reduce_add_v`, `invsqrt`, `to_float`/`to_fixed`).
It also covers **data-movement / shuffle / de-interleave** primitives
(`load_unaligned_v`, `filter_even`/`filter_odd`, `shuffle_up_fill` /
`shuffle_down_fill`, `broadcast`) needed by **windowed / strided spatial** ops
(pooling, conv, stride-2 resample) where the output straddles even/odd lanes or
a tap is not vector-aligned. It also covers the **matrix-multiply** primitive
`aie::mmul` (GEMM / matmul / linear / 1x1-conv), which is the one non-vector
datapath here. The structure is generic and can be extended to other op
families.

This reference is part of the `/vai-custom-op` skill (kernel authoring, see Phase 2c)
and complements `/vai-aie-compiler-oriented-optimizations` (compiler/pipelining
hints). It is not an exhaustive signature dump -- for the full overload set of
any primitive, [discover it from the headers](#if-its-not-here-discover-from-the-headers).

---

## How to use this reference

1. Start from your **math pattern**, not an API name -> [Pattern -> API](#pattern--api).
2. Confirm dtype support / vectorization / accumulator in the
   [API catalog](#api-catalog).
3. Sanity-check against the [precision & vectorization rules](#precision--vectorization-rules).
4. If the primitive you need is not here ->
   [discover it from the headers](#if-its-not-here-discover-from-the-headers).
   Then consider adding it back here for the next agent.

---

## Precision & vectorization rules

Generic rules that decide *how* you call these primitives, independent of family:

- **Accumulate bf16 in `accfloat`, never in bf16.** Use
  `aie::accum<accfloat, N>`; widen with `acc.from_vector(vec)` and read back with
  `acc.to_vector<T>()`. Summing many bf16 values in bf16 loses precision fast.
- **Prefer the fused accumulate primitive over `add`/`mac` of a hand-built term.**
  `mac_square(acc, v)` and `msc_square(acc, v)` keep the square in the
  accumulator datapath; the naive form spends extra vector ops and a wider
  dependency chain.
- **Stay in the vector datapath for stats when you can.** Dropping to a scalar
  (`reduce_add` -> scalar `mean`/`var`/`invsqrt`) creates a serial
  reduce->invsqrt critical path. `reduce_add_v` + vector `invsqrt` keeps the
  chain in vector registers. (Whether this is worth it depends on whether the op
  is compute-bound vs DMA-bound -- measure with `/vai-perf-analysis` first.)
- **Batching a transcendental across rows still stays in the FPU.** When you
  need each row's stat in its own lane (e.g. one `invsqrt` over R rows), place
  row r's reduced scalar into lane r with the `vector::set(value, idx)` member
  (or `vec[idx] = value`) / a shift-in, *not* by writing `reduce_add` scalars to
  a scratch array and reloading. The scratch-array round-trip leaves the vector
  FPU, adds L1 traffic, and grows the stack -- it defeats the batching it was
  meant to enable.
- **Match lane width to dtype** (same tuning the main skill uses): bf16 = 32
  lanes, int8 = 64, int32 = 16. Size tiles to an exact multiple so there is no
  scalar remainder.
- **Scalar `float` math is emulated -- keep arithmetic in vectors.** The scalar
  unit has no FPU: a plain `float` `*`, `/`, `int->float` cast, or compare lowers
  to a soft-float library call (`__mulsf3`, `__floatsisf`, `__gesf2`, ...). Those
  calls are un-inlinable and force the compiler to spill live values to the stack
  around each one, so scalar float costs both cycles and stack frame. Do the math
  on `aie::vector` (even a small lane count) and reserve scalars for lane
  moves (`v.get`/`v.set`) and index math. Diagnostic: `grep` the kernel
  `.calltree` for `__*sf*` symbols -- nonzero means scalar float is still there.
- **int8 in/out needs explicit (de)quant**: `aie::to_float(v.unpack(), shift)` on
  the way in, `aie::to_fixed<int8>(acc.to_vector<bf16>(), out_shift)` on the way
  out. There is no implicit int8<->float conversion.
- **For complex math (`exp` / `exp2`, and similar):** if the output is int8,
  implement it with an exact LUT -- it is both more accurate and as fast as the
  HW math. If the output is bf16/fp32, use the matching AIE API (`aie::exp2`,
  etc.). Details: [Complex Math functions implementation](#complex-math-functions-implementation).
- **Prefer aligned `load_v` over `load_unaligned_v`.** Vector loads want a
  vector-aligned address. When a tap is not aligned (a halo/shifted window in a
  stencil), `load_unaligned_v` works but the compiler lowers it to an aligned
  load + a `vshift`, so it is not free. Where possible, keep the base
  vector-aligned and derive shifted lanes with `shuffle_*` from vectors you have
  already loaded, or lay out an L1 scratch with a left pad so the shifted base
  stays aligned (see the [de-interleave example](#windowed--strided-de-interleave-examples)).

---

## Pattern -> API

Map the math you need to the idiomatic primitive. (`acc` = `aie::accum<accfloat,N>`.)

| You need...                              | Use                                              | Do NOT hand-roll as            |
|------------------------------------------|--------------------------------------------------|--------------------------------|
| sum of a vector into fp32 acc            | `acc = aie::add(acc, v)`                          | scalar accumulate loop         |
| **sum of squares** into fp32 acc         | **`acc = aie::mac_square(acc, v)`**              | `aie::mac(acc, v, v)`          |
| **variance `E[x^2] - mean^2`**           | **`aie::msc_square(Ex2_acc, mean_vec)`**         | scalar `sumsq*inv - mean*mean` |
| horizontal reduce, keep result in vector | **`aie::reduce_add_v(acc.to_vector<float>())`**  | `aie::reduce_add` (-> scalar)  |
| `1/sqrt(x)` over a vector                | **`aie::invsqrt(v)`**                            | Newton iters / scalar `1/sqrt` |
| clamp variance to `>= eps` (stability)   | **`aie::max(var_eps, broadcast(eps))`**          | branchy scalar guard           |
| affine `a*x + b` fused                   | `aie::mac(aie::accum(b_vec), x_vec, a_vec)`      | separate `mul` then `add`      |
| **matrix multiply / GEMM tile** (contract two 8x8 tiles) | **`aie::mmul<8,8,8,TA,TB>` + `.mul`/`.mac`** | a doubly-nested lane-wise `aie::mac` loop (mmul issues a full 8x8x8 tile step = 512 MACs) |
| int8 -> float dequant (with shift)       | **`aie::to_float(v.unpack(), shift)`** (vector only) | manual `* scale`, or a **scalar** `to_float` (silently returns 0) |
| float -> int8 quant (with shift)         | **`aie::to_fixed<int8>(v, out_shift)`** (vector only) | manual round/clamp/cast, or a **scalar** `to_fixed` (silently returns 0) |
| **split even/odd lanes** (stride-2 gather, K-reduction) | **`aie::filter_even(v)` / `aie::filter_odd(v)`** | scalar strided index loop |
| **shifted / halo tap** (offset not vector-aligned) | **`aie::load_unaligned_v<N>(ptr)`** or `shuffle_*` of a loaded vec | `load_v` at a misaligned addr (reads wrong data) |
| **shift lanes by n, fill vacated lanes** (pad/halo carry) | **`aie::shuffle_up_fill(v, fill, n)` / `shuffle_down_fill`** | scratch store at +/-1 offset (unaligned) |
| **pad sentinel** for max/min windows (`-inf`/`+inf`) | **`aie::broadcast<bf16,N>((bfloat16)-3.0e38f)`** | per-lane scalar store of the pad |

---

## API catalog

Columns: **dtypes** (in), **vec** (vectorized?), **accum** (lands in), **lanes**,
**notes** (idiomatic usage / why), **header**.

### Core primitives

The everyday load/store, arithmetic, and reduction ops. Included here with the
per-dtype / vectorization / accumulator detail rather than just the name.

| API | math | dtypes | vec | accum | lanes | notes | header |
|---|---|---|---|---|---|---|---|
| `aie::load_v<N>(ptr)` / `aie::store_v(ptr, v)` | vector load / store | any | yes | n/a | N | address must be vector-aligned (see compiler skill ch.11) | `detail/*ld_st*.hpp` |
| `aie::broadcast<T,N>(s)` | splat scalar to N lanes | any | yes | n/a | N | build a vector constant (e.g. `eps`, `inv_D`, or a `-inf`/`LOWEST` pad sentinel for max/min windows) | `detail/broadcast*.hpp` |
| `aie::add(acc, v)` / `aie::add(v, v)` | add | bf16, fp32, int | yes | `accfloat` (acc form) | 16/32/64 | acc form widens bf16 into fp32 -- use for reduction sums | `detail/add*.hpp` |
| `aie::sub(a, b)` | subtract | bf16, fp32, int | yes | `accfloat`/vec | 16/32/64 | -- | `detail/sub*.hpp` |
| `aie::mul(a, b)` | multiply (-> acc) | bf16, fp32, int | yes | `accfloat` | 16/32 | returns an accumulator; narrow with `.to_vector<T>()` | `detail/mul*.hpp` |
| `aie::mac(acc, a, b)` | `acc += a*b` | bf16, fp32, int | yes | `accfloat` | 16/32 | fused multiply-accumulate; basis of affine `a*x+b` | `detail/mul*.hpp` |
| `aie::max(a,b)` / `aie::min(a,b)` | elementwise max/min | any | yes | vec | 16/32/64 | also used as branchless clamp (e.g. `max(var,eps)`); bf16 lowers to `vmax_lt.bf16`; basis of pooling reductions | `detail/max_min.hpp` |
| `aie::abs(v)` / `aie::neg(v)` | abs / negate | any | yes | vec | 16/32/64 | -- | `detail/abs.hpp` / `detail/neg.hpp` |
| `aie::reduce_add(v)` / `reduce_max` / `reduce_min` | horizontal reduce -> **scalar** | bf16, fp32, int | yes | scalar | -> 1 | pulls onto scalar unit; prefer `reduce_add_v` to stay vector | `detail/reduce*.hpp` |
| `v.set(val, idx)` / `v.get(idx)` / `v[idx]` | write / read a **single lane** | any | n/a | n/a | 1 lane | member fns (arg order is **value-first**: `set(val, idx)`); use to place a scalar at a lane (e.g. row->lane batching). **No** free `aie::insert(vec,idx,scalar)`; the `v.insert(idx, subvec)` member is *sub-vector-at-partition-index*, not scalar-at-lane | `vector.hpp` |
| `tanh(v)` / `exp2(v)` | transcendentals | bf16, fp32 (acc) | yes | n/a | 16/32 | HW-accelerated per arch; use the library form, never `std::` (no stdc++ on AIE) | `detail/elementary*.hpp` |

### Fused / advanced primitives

The easy-to-miss or hand-rolled-by-accident ones. These are the highest-value
entries -- prefer them over the naive composition shown in the last column.

| API | math | dtypes | vec | accum | lanes | notes (vs hand-rolled) | header |
|---|---|---|---|---|---|---|---|
| `aie::mac_square(acc, v)` | `acc += v*v` | bf16, fp32 | yes | `accfloat` | 16/32 | square+accumulate in 1 op; tighter dep chain than `mac(v,v)` | `detail/mul*.hpp` |
| `aie::msc_square(acc, v)` | `acc - v*v` | bf16, fp32 | yes | `accfloat` | 16/32 | the whole `E[x^2]-mean^2` variance step, fused & vectorized; vs scalar `sumsq*inv - mean*mean` | `detail/mul*.hpp` |
| `aie::reduce_add_v(v)` | horizontal sum, **returns vector** | fp32, bf16 | yes | n/a | lane->lane | keeps reduction in vector regs (no scalar FPU round-trip); vs `reduce_add` | `detail/reduce*.hpp` |
| `aie::invsqrt(v)` | `1/sqrt(v)` | bf16, fp32 | yes | n/a | 16/32 | HW reciprocal-sqrt; vs scalar `1.0f/aie::sqrt(v)` or Newton | `detail/sqrt.hpp` / `elementary` |
| `aie::inv(v)` | `1/v` | bf16, fp32 | yes | n/a | 16/32 | HW reciprocal; avoids scalar divide | `detail/*inv*.hpp` |
| `aie::to_float(v, shift)` | int->float w/ shift | int8/16/32 | **vector only** | n/a | 16/32/64 | fused dequant; pair with `.unpack()` for int8. **No scalar overload** -- a scalar arg silently returns 0 (no error) | `detail/to_float*.hpp` |
| `aie::to_fixed<T>(v, shift)` | float->int w/ shift | bf16, fp32 | **vector only** | n/a | 16/32 | fused round+scale+clamp to int output. **No scalar overload** -- a scalar arg silently returns 0 (no error) | `detail/to_fixed*.hpp` |
| `acc.from_vector(v)` / `acc.to_vector<T>()` | widen / narrow | any | yes | `accfloat`/... | N | the canonical bf16<->fp32-acc bridge | `aie.hpp` (accum) |

### Data movement, shuffle & de-interleave primitives

Lane-rearrangement and non-aligned access. These are the primitives that make
**windowed / strided spatial** ops (pooling, stride-2 resample, stencils)
vectorizable: an odd window with an even stride makes the output straddle even
and odd input lanes, so you must de-interleave, and halo taps land at
non-vector-aligned offsets. All lower to `vshuffle` / `vshift` on the vector
unit (visible as such in the Peano `.lst`).

| API | math | dtypes | vec | accum | lanes | notes (idiomatic usage / why) | header |
|---|---|---|---|---|---|---|---|
| `aie::load_unaligned_v<N>(ptr, aligned_elems=1)` | vector load from a non-vector-aligned address | any | yes | n/a | N | for a shifted/halo tap whose offset is not a multiple of the vector width (e.g. the `A[2*wo-1]` left tap of a stride-2 pool). Lowers to aligned load + `vshift`, so **not free** -- prefer keeping the base aligned and deriving shifts via `shuffle_*`, or pad the L1 scratch so the base stays aligned | `aie.hpp` (`load_unaligned_v`) / `detail/*ld_st*.hpp` |
| `aie::filter_even(v, chunk=1)` / `aie::filter_odd(v, chunk=1)` | keep even- / odd-indexed lanes (de-interleave) | bf16, int, fp32 | yes | n/a | N -> N/2 | the stride-2 de-interleave: `filter_even([a0,a1,a2,a3,...]) = [a0,a2,...]`. Use for pooling/conv with stride 2 (output = even lanes) and for grouped inner-axis reductions (two stages split a 4-way group into 4 lane-streams -- see `reducemax/reduce_max_axis_2`). Optional `chunk` keeps groups of `chunk` consecutive elements together. Lowers to `vshuffle` | `aie.hpp` (`filter_even`/`filter_odd`) / `detail/filter*.hpp` |
| `aie::shuffle_up_fill(v, fill, n)` / `aie::shuffle_down_fill(v, fill, n)` | shift lanes up/down by `n`, filling the vacated lanes from `fill` | any | yes | n/a | N | carry a halo/pad element across a vector boundary without an unaligned access: e.g. build the `A[2*wo-1]` tap as `shuffle_up_fill(odd_lanes, prev_or_pad, 1)`. `fill` supplies the entering lanes (use a `broadcast(LOWEST)` for a max-pool pad). Lowers to `vshuffle` | `aie.hpp` (`shuffle_*`) / `detail/shuffle*.hpp` |
| `aie::shuffle_up(v,n)` / `aie::shuffle_down(v,n)` (+ `_rotate` / `_replicate` variants) | shift lanes by `n` (zero/undef, rotate, or edge-replicate fill) | any | yes | n/a | N | same as the `_fill` forms but with a fixed fill policy: `_rotate` wraps around, `_replicate` repeats the edge lane. Use when you don't need a specific pad value | `aie.hpp` (`shuffle_*`) / `detail/shuffle*.hpp` |

Notes:
- `mac_square` / `msc_square` are the matched pair for any
  mean/variance reduction (LayerNorm, RMSNorm, BatchNorm stats).
- `reduce_add_v` vs `reduce_add`: the `_v` form returns a vector (broadcast the
  lane you want); the bare form returns a scalar and pulls you onto the scalar
  unit.
- `filter_even`/`filter_odd` return a **half-width** vector (N -> N/2). To keep a
  full-width store, produce two halves and recombine, or size the loop so each
  iteration consumes a full input vector and emits one half-width output vector
  (the natural shape for stride-2 pooling: 16 input lanes -> 8 outputs).

---

## Complex Math functions implementation

To implement a complex mathematical function, prefer a LUT if using 8-bit inputs. It will in general be faster and less expensive than using the floating-point hardware.

**Op-specific advice: ** for `exp` and `exp2`, pick your `exp` by the **output** dtype:

- **bf16 / fp32 output -> use HW `aie::exp2`.** Write `exp(x)` as
  `exp2(x*log2(e))`. It is one fast vector op. It is only ~bf16-accurate
  (~12 bits), but that is good enough for a bf16/fp32 output.
- **int8 output -> use an exact lookup table (LUT).** The int8 input has only
  **256 possible values**, so a table of `exp` is *exact*. HW `exp2` is too
  rough here -- its small error flips int8 output bits.

> **Do NOT implement int8 `exp` with a polynomial / range-reduction approximation.**

**For int8 input, how the LUT works:**

- A full 256-entry fp32 table is 1 KB and does **not** fit the core's
  static-data budget. So store **two 16-entry tables** (128 B) and multiply them:
  `exp(scale*q) = exp(hi part) * exp(lo part)`. Align each table to
  `alignas(aie::vector_decl_align)` (64 B) if vector-loaded.
- At runtime, build the full table **once into an oversized OFM ping/pong L1 buffer allocated by the tiler/ADF**
  and look values up in the loop.
- Do the lookup with **`aie::parallel_lookup`** -- a vectorised HW gather,
  16 lanes/op. Its 4 parallel accesses need **4 interleaved copies across two DM
  banks**, so it only works when the table lives in the framework buffer above,
  never in `.rodata`. **Even/odd bank layout:** each 64 B table line is really
  two 32 B halves (an "even" half and an "odd" half), and `parallel_lookup`
  reads both at once -- so the same values must sit in **both** halves. Write
  each entry as **8 floats copied into both halves**
  (`concat(e.extract<8>(0), e.extract<8>(0))`). If you copy in 4-float chunks
  instead, lanes read the wrong half: it still compiles, but the board gives
  wrong answers.
- Keep everything **fp32** in between (the exp values, the sum, the 1/sum).
  Using bf16 here loses too much and flips output bits.

For int8, the LUT wins on **both** counts: it runs about as fast as HW `exp2`
(a plain table lookup, no transcendental in the loop) **and** it is exact, so it
is both **faster and more accurate** than the alternatives.

> Rule of thumb: **bf16 out -> `exp2`; int8 out -> exact LUT; never a polynomial
> for int8** (slower than the LUT and buys no accuracy).

---

## LUT table using aie::parallel_lookup

Use the following information about `aie::parallel_lookup`:

- **Construct the lookup in the SAME function as `.fetch()`.** Its addressing
  state lives in registers and does NOT survive being passed across a (noinline)
  call boundary. Rebuilding it at the fetch site is free.
- **Build the table in a NOINLINE helper called from the wrapper, not the hot
  core.**
- **Build once per physical buffer.** The L1 tail is double-buffered and never
  DMA-drained: cache the (up to 2) ping/pong addresses and skip the rebuild on
  later invocations.

**It's memory-heavy -- decide if you need it.** `parallel_lookup<4>` keeps 4
bank-interleaved copies of the table, and double-buffering doubles that again
(vs a single flat copy for a scalar-gather table). It only pays when the lookup
is the hot op (gather-bound, e.g. channel-vectorized). If you vectorize the other
axis (positions) so sum/reciprocal/normalize are full-width vector work, a scalar
gather hides behind that arithmetic -- use it and save memory.

---

## Matrix-multiply (GEMM) with `aie::mmul`

The one **non-vector** primitive: `aie::mmul<M,K,N,TA,TB>` contracts an `MxK`
A-tile with a `KxN` B-tile into an `MxN` accumulator via the hardware matrix
mode. `bf16 x bf16 -> fp32` runs **8x8x8** natively (512 MACs/issue, ~10x a
lane-wise `aie::mac`). Use it for matmul / linear / 1x1-conv; skip it when the
reduction is tiny (`K < 8`, e.g. depthwise), shapes force heavy 8-padding, or
the op isn't matmul-shaped. Full worked kernels + tiling shipped with the
`vai-custom-op-implementation` skill -- the gemm tutorial (bf16 bias-folded
GEMM = 1x1 conv) and the conv2d tutorial
`conv3x3_int8_2stamp` (int8 spatial conv: halo, sliding window, multi-call depth
reduction, requant).

| API | math | notes | header |
|---|---|---|---|
| `aie::mmul<M,K,N,TA,TB>` | `MxN` tile accumulator contracting over `K` | bf16xbf16->fp32 / int combos (fp32 matrix mode limited); A=`size_A=M*K` `a[mi*K+ki]`, B=`size_B=K*N` `b[ki*N+ni]`; holds 2 cml regs so ~4 live tiles fill the file | `detail/*/mmul*.hpp` |
| `C.mul(a,b)` / `C.mac(a,b)` | `C = a.b` (seed) / `C += a.b` | operands are `aie::vector` of `size_A`/`size_B`; `mul` on the first reduction step, `mac` after | same |
| `C.to_accum()` / `C.to_vector<T>()` | read `MxN` result | narrow for the store; `res.extract<N>(mi)` pulls output row `mi` | same |

Idioms (see the tutorial kernel): pre-block operands to contiguous 8x8 tiles on
the L2->L1 DMA (one `load_v` each); cap live accumulators at ~4 and loop
sub-blocks rather than growing them; roll the reduction loop
(`#pragma clang loop unroll(disable)`) to keep operand loads out of the stack;
fold bias as one extra reduction tile `mac`'d against a constant-`1.0` operand.

---

## Idiomatic / fused choices

If you find yourself coding the right column, stop and use the left:

| Use this | instead of | gain |
|---|---|---|
| `aie::mac_square(acc, v)` | `aie::mac(acc, v, v)` | idiomatic; one square op, tighter chain |
| `aie::msc_square(Ex2, mean)` | scalar `sumsq*inv - mean*mean` | variance stays vectorized & fused |
| `aie::reduce_add_v(...)` | `aie::reduce_add(...)` + scalar math | avoids scalar-FPU serialization |
| `vec.set(reduce_add(v), r)` / `vec[r] = reduce_add(v)` (row->lane for batched `invsqrt`) | scratch array `buf[r]=...` then reload | keeps per-row stats in the FPU; no L1/stack round-trip (there is no free `aie::insert`; `vector::insert` is sub-vector-at-partition, not scalar-at-lane) |
| `aie::invsqrt(v)` | `1.0f / aie::sqrt(v)` or Newton | single HW op, no divide |
| `aie::max(var_eps, eps)` | `if (var < eps) var = eps;` | branchless, vectorized stability clamp |
| `aie::filter_even(v)` / `aie::filter_odd(v)` | scalar strided-index copy for stride-2 output | one `vshuffle` de-interleaves 16 lanes vs 8 scalar loads/stores |
| `aie::store_v(out, vec)` of an assembled result | per-element scalar `out[i]=...` bf16 stores | avoids the sub-32-bit (bf16) store read-modify-write penalty |
| `aie::shuffle_up_fill(od, pad, 1)` for a halo tap | `aie::load_unaligned_v` at a `-1` offset per iteration | keeps the base aligned; no loop-carried unaligned load |

---

## Windowed / strided de-interleave example

**Key insight:** an odd window (k=3) with an even stride (s=2) makes the output
straddle even and odd input lanes, so a stride-2 pool
(`out[wo] = max(A[2*wo-1], A[2*wo], A[2*wo+1])`, `A` = vertically-maxed row) is a
**de-interleave**: `filter_even`/`filter_odd` split the taps and a shifted load
supplies the third.

```cpp
static constexpr int VEC = 16;                    // bf16 lanes
// `A` is a 32B-aligned L1 scratch with a left pad (A[-1]=LOWEST, pad >= VEC so
// `A-2` stays vector-aligned). Emit 8 outputs/iter, no scalar tail.
aie::vector<bfloat16, VEC> v  = aie::load_v<VEC>(A + b * VEC);
aie::vector<bfloat16, VEC> vm = aie::load_unaligned_v<VEC>(A + b * VEC - 2);
auto o = aie::max(aie::max(aie::filter_even(v),   // A[2*wo]   center tap
                           aie::filter_odd(v)),   // A[2*wo+1] right tap
                           aie::filter_odd(vm));  // A[2*wo-1] left tap (8-wide)
aie::store_v(out + b * (VEC / 2), o);
```

To avoid the per-iteration unaligned load, derive the left tap from lanes you
already have with `aie::shuffle_up_fill(od, prev_od_or_pad, 1)` (costs a
loop-carried value). Confirm the choice from the loop's initiation interval in
the `.lst`, and only invest here if the op is compute-bound -- large-IFM pooling
is usually DMA-dominated.

---

## If it's not here: discover from the headers

This catalog is not exhaustive. To confirm a primitive exists, see its full
overload set, and pick the right accumulator/return type, read the **active
environment's** `aie_api` headers.

**Resolve the header directory first.** The headers ship inside the active Python
environment, under `<site-packages>/include/aie_api/` (`sysconfig`'s `purelib`
gives you site-packages). Do **not** hardcode an absolute path -- it moves per
release/host. Resolve it once from the environment in effect for this run, keep
it in `$AIE_API_DIR`, and reuse it for every lookup.

**Scope every search to `$AIE_API_DIR`.** Never `find` or `grep -r` from the env
root or its prefix: that tree is large enough that the search stalls and gets
backgrounded or aborted. In the unlikely case that site-packages does not
resolve, a **depth-capped** `find` under the env prefix (`sys.prefix`) is the
only acceptable fallback.

**Where to read what:**

- `aie.hpp` -- the public entry header (declarations + doc comments); pulls in
  vectors, accumulators, and the free-function API (`aie::add`, `aie::mul`,
  `aie::mac_square`, `aie::reduce_add`, ...). Grep a primitive name here to get
  its overload set and doc block.
- `vector.hpp`, `accum.hpp` -- the `aie::vector<T, N>` / `aie::accum<Acc, N>`
  storage types; check which `(T, N)` pairs the target arch supports.
- `mmul.hpp`, `sliding_mul.hpp` -- matrix multiply and sliding-window multiplies
  (the right primitives for conv / matmul kernels).
- `operators.hpp`, `utils.hpp` -- overloaded operators and helpers (broadcast,
  zeros, iterators).
- `detail/` -- the implementations, one header per op class (`add.hpp`, `mul.hpp`,
  `max_min.hpp`, `neg.hpp`, `lut.hpp`, `linear_approx.hpp`, `elementary.hpp`,
  ...; `ls` it to see which op families ship at all), with architecture-specific
  variants under `detail/aie2p/`, `detail/aie2ps/`, etc. Read it to see what a
  call lowers to and where a transcendental is specialized per arch; never
  `#include` it directly.

```bash
grep -nE "mac_square|msc_square|reduce_add_v|invsqrt" "$AIE_API_DIR/aie.hpp"
ls "$AIE_API_DIR/detail"                 # which op classes exist
# Where is it defined / specialized per arch? Recursive grep, scoped.
grep -rln "tanh" "$AIE_API_DIR"          # -> detail/elementary.hpp, detail/aie2p/elementary.hpp
```

Write the call from what the header declares (exact name, vector vs accum
argument types, return type). If a primitive is missing, **compose it from
existing ops** (e.g. sigmoid from `tanh`, `mac` chains, `aie::inv` /
`aie::invsqrt`) before falling back to a scalar `std::`-style loop.
