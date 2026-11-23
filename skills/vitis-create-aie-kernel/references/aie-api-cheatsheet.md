<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
<!-- Every call listed was compiled for x86sim with Vitis 2026.2 on xcvc1902-vsva2197-2MP-e-S (AIE) and
     xcve2802-vsvh1760-2MP-e-S (AIE-ML) unless marked "AIE only" / "AIE-ML only".
     Full reference: AIE API documentation shipped with Vitis (aietools/include/aie_api), UG1079. -->
# AIE API Cheat Sheet

Include `<aie_api/aie.hpp>` (and `<aie_api/utils.hpp>` for printing helpers). Everything is in the
`aie::` namespace. `L` = lanes, `T` = element type. Legal `T`/`L` per architecture: `device-rules.md`.

## Types and construction

| Call | Meaning | Notes |
|---|---|---|
| `aie::vector<T, L> v;` | vector register value | 128 to 1024 bits are native |
| `aie::accum<tag, L> a;` | accumulator (`acc32`, `acc48`, `acc64`, `acc80`, `cacc48`, `cacc64`, `accfloat`, `caccfloat`) | `acc80` is AIE only. `acc48` = 48 b on AIE, 64 b on AIE-ML |
| `aie::mask<L> m;` | lane predicate | returned by comparisons |
| `aie::zeros<T, L>()` / `aie::zeros<tag, L>()` | zero vector / zero accumulator | |
| `aie::broadcast<T, L>(s)` | all lanes = `s` | |
| `aie::concat(a, b)` | `[a b]`, doubles L | |
| `v.extract<L2>(i)` | i-th sub-vector of size L2 | |
| `v.insert(i, sub)` | write sub-vector | |
| `v.grow<L2>()` | widen (upper lanes undefined) | |
| `v[i]`, `v.get(i)`, `v.set(x, i)` | element access | scalar: keep it out of hot loops |
| `aie::vector_cast<T2>(v)` | reinterpret bits (e.g. 16 x int16 ↔ 8 x cint16) | no conversion |

## Memory and I/O

| Call | Meaning | Notes |
|---|---|---|
| `aie::load_v<L>(ptr)` / `aie::store_v(ptr, v)` | aligned vector load/store | ptr aligned to the access size (16/32 B) |
| `aie::load_unaligned_v<L>(ptr)` / `aie::store_unaligned_v(ptr, v)` | any alignment | slow on AIE |
| `aie::begin_vector<L>(buf)` | random-access vector iterator over a buffer port | `*it++`, `it[k]` |
| `aie::begin_vector_circular<L>(buf)` | wraps at the buffer end | circular buffers |
| `aie::begin_restrict_vector<L>(buf)` | iterator with no-alias promise | |
| `buf.data()` | raw `T*` to the buffer start (the margin comes first) | |
| `readincr(s)` / `writeincr(s, x)` | scalar stream read/write | |
| `readincr_v<L>(s)` / `writeincr(s, v)` | 128-bit vector stream access (`L*sizeof(T)=16`) | both architectures |
| `readincr_v<L>(casc)` / `writeincr(casc, acc)` | cascade read/write of an `aie::accum` | `input_cascade<tag>*` / `output_cascade<tag>*` |
| `alignas(aie::vector_decl_align)` | alignment for LUTs and member arrays | |

## Arithmetic

| Call | Result | Notes |
|---|---|---|
| `aie::add(a, b)`, `aie::sub(a, b)` | vector (wraps for integers) | `b` may be a scalar |
| `aie::add(acc, v)` | accumulator + vector | |
| `aie::add(acc1, acc2)` | accumulator + accumulator | **AIE-ML only** ("no matching function for call to 'add'" on AIE) |
| `aie::neg(v)`, `aie::abs(v)` | vector | |
| `aie::mul(a, b)` | accumulator, default tag (`device-rules.md` §3) | `b` may be a scalar |
| `aie::mul<tag>(a, b)` | accumulator with the requested tag | required to get acc48 from int16 x int16 on AIE-ML |
| `aie::mac(acc, a, b)` / `aie::msc(acc, a, b)` | `acc ± a*b` | |
| `aie::mul(aie::op_conj(a), b)` | `conj(a) * b` | complex |
| `aie::sliding_mul<L, P>(coeff, c_start, data, d_start)` | `out[l] = Σ_p coeff[c_s+p] * data[d_s+l+p]` | FIR. int16: L=16 on both |
| `aie::sliding_mac<L, P>(acc, coeff, c_start, data, d_start)` | accumulate the next P taps | longer FIRs |
| `aie::sliding_mul_sym<L, P>(...)` | symmetric FIR with pre-add | AIE: compiles (hardware pre-add). AIE-ML: `<16, 8>` int16 gives "no matching function" (no pre-add hardware). Use `sliding_mul` |
| `aie::mmul<M, K, N, TA, TB[, tag]>` | `C.mul(A, B)`, `C.mac(A, B)`, `C.to_vector<T>(s)` | tiled inputs, `size_A/B/C` members |
| `aie::reduce_add(v)`, `aie::reduce_max(v)`, `aie::reduce_min(v)` | scalar | after the loop |
| `aie::max(a, b)`, `aie::min(a, b)` | vector | `b` may be a scalar |
| `aie::div(a, b)`, `aie::inv(v)`, `aie::sqrt(v)`, `aie::invsqrt(v)` | float vector | compiles on both. Slow: not in hot loops |
| `aie::abs_square<int32>(cv, s)` | `re²+im²` | **AIE-ML ignores `s` and wraps**. Use `mul<acc48>(re,re)+mac(im,im)` |
| `aie::sincos(x)` | sin/cos | **AIE only** |

## Accumulator conversion

| Call | Meaning |
|---|---|
| `acc.from_vector(v, s)` | `acc = v * 2^s` |
| `acc.to_vector<T>(s)` | `round(acc / 2^s)` → saturate to `T` (current modes) |
| `aie::to_fixed<int32>(fv, s)` / `aie::to_float(iv, s)` | float ↔ fixed with `s` fraction bits |
| `acc.to_vector<float>()` | float accumulator → vector |

The same-width case `acc32 → int32` (AIE-ML) is a bit-cast: no shift, no rounding, no saturation.

## Bits, shuffles, selection

| Call | Meaning | Notes |
|---|---|---|
| `aie::interleave_zip(a, b, step)` | interleave chunks of `step` → pair | `auto [lo, hi] = ...` |
| `aie::interleave_unzip(a, b, step)` | de-interleave → pair (even chunks, odd chunks) | |
| `aie::filter_even(v)`, `aie::filter_odd(v)` | half-length vector of even/odd elements | |
| `aie::shuffle_down(v, n)`, `aie::shuffle_up(v, n)` | shift lanes by n | |
| `aie::reverse(v)` | reverse lane order | |
| `aie::real(cv)`, `aie::imag(cv)`, `aie::conj(cv)` | complex parts | |
| `aie::upshift(v, n)`, `aie::downshift(v, n)` | per-lane `<<`, `>>` (arithmetic) | |
| `aie::bit_and(s, v)`, `bit_or`, `bit_xor`, `bit_not` | bitwise | a scalar operand goes **first** |
| `aie::lt/le/gt/ge/eq/neq(a, b)` | `aie::mask<L>` | `b` may be a scalar |
| `aie::select(a, b, m)` | `m ? b : a` per lane | |

## Configuration (set in the kernel constructor)

| Call | Values |
|---|---|
| `aie::set_rounding(aie::rounding_mode::X)` | `floor`, `ceil`, `positive_inf`, `negative_inf`, `symmetric_inf`, `symmetric_zero`, `conv_even`, `conv_odd`. AIE-ML adds `symmetric_floor`, `symmetric_ceil` |
| `aie::set_saturation(aie::saturation_mode::X)` | `none`, `saturate`, `symmetric` |

The power-up state is `floor` with no saturation. Always set both.

## Chess compiler pragmas and qualifiers

| Item | Where | Effect |
|---|---|---|
| `chess_prepare_for_pipelining` | after the `for (...)` of the hot loop | software pipelining |
| `chess_loop_range(min, max)` | same place | trip-count bounds (max may be empty) |
| `chess_unroll_loop(n)` / `chess_unroll_loop()` | short inner loop | unroll n times / fully |
| `chess_flatten_loop` | tiny inner loop | fully flatten into the parent |
| `__restrict` | pointer declarations | no-alias promise, better scheduling |
| `chess_storage(%chess_alignof(T))` | rarely needed | storage/alignment hint. Prefer `alignas` |

## Graph-side calls (in `*_graph.h`)

| Call | Meaning |
|---|---|
| `connect<>(a, b)` | buffer or stream connection (type from the kernel ports) |
| `connect<cascade>(k0.out[i], k1.in[j])` | cascade between kernels on adjacent tiles |
| `dimensions(k.in[i]) = {N}` | samples per buffer port (not for streams) |
| `single_buffer(k.in[i])` | turn off ping-pong for a large buffer |
| `fifo_depth(net) = D` | stream FIFO depth in 32-bit words (`connect<> net(a, b);`) |
| `runtime<ratio>(k) = r` | fraction of a tile's cycles. Kernels with a total ≤ 1 can share a tile |
| `location<kernel>(k) = tile(col, row)` | placement |
| `write_access(k.in[i]) = tiling(...)` / `read_access(k.out[i]) = tiling(...)` | DMA address pattern (AIE-ML, limited on AIE) |
| `stack_size(k) = bytes` | per-kernel stack |
