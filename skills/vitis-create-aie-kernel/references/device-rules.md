<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
<!-- Validated with Vitis 2026.2 (v++ / aiecompiler 2026.2, AIE API shipped with it) on
     xcvc1902-vsva2197-2MP-e-S (AIE) and xcve2802-vsvh1760-2MP-e-S (AIE-ML). -->
# Device Rules: AIE vs AIE-ML

Check every rule in this file **before** writing any file. If a rule blocks the request, follow
[Refusal procedure](#refusal-procedure). Each rule is marked:

- **[C]**: confirmed by a compile (x86sim and/or hw) with Vitis 2026.2.
- **[D]**: taken from the AIE API documentation (`aie_api/aie_doc.hpp`, "Supported vector types", "Default accumulator tag").
- **[L]**: device limit from UG1079 / the AIE API docs. Check it by reading the compiler reports.

## 1. Part → architecture

| Part | Board | Architecture | `__AIE_ARCH__` | Data memory / tile | Program memory / tile | Memory tiles |
|---|---|---|---|---|---|---|
| `xcvc1902-vsva2197-2MP-e-S` | VCK190 | AIE (AIE1) | 10 | 32 KB | 16 KB | none |
| `xcve2802-vsvh1760-2MP-e-S` | VEK280 | AIE-ML | 20 | 64 KB | 16 KB | yes (512 KB each) |

Kernel code can branch on the architecture at compile time with `#if __AIE_ARCH__ >= 20` (AIE-ML) /
`#else` (AIE). The macro is defined in both x86sim and hw compiles **[C]**. If the user names another
part, find its architecture in the part datasheet. If you cannot, ask the user. Do not guess.

## 2. Data types

| Type | AIE | AIE-ML | Notes |
|---|---|---|---|
| `int8`, `uint8` | yes **[C]** | yes **[C]** | int8 x int8 mul through the AIE API works on both. Tuning AIE `mac16`/`v32int8` intrinsics belongs to the `vitis-aie-int8-mac` skill |
| `int16`, `int32` | yes **[C]** | yes **[C]** | int32 x int32 mul is **emulated** on AIE-ML (two 32b x 16b muls): legal, slower **[D]** |
| `uint16`, `uint32`, `int4`, `uint4` | **no** **[D]** | yes **[D]** | |
| `cint16`, `cint32` | yes **[C]** | yes **[C]** | complex mul is emulated with real muls on AIE-ML **[D]** |
| `float` | yes, native **[C]** | yes **[C]**, mul **emulated** with bfloat16 muls **[D]** | add/sub native on both. AIE-ML float mul differs from IEEE fp32 by about 1e-7 relative (measured) |
| `cfloat` | yes, native **[C]** | yes, **emulated** **[C]** | legal on AIE-ML (AIE API compiles it). The Vitis DSP Library chooses not to support it, which is a library choice, not a device limit. Warn that it is slow |
| `bfloat16`, `cbfloat16` | **no** **[C]** | yes, native **[C]** | AIE hw compile fails with "constraints not satisfied for class template 'vector' [with T = bfloat16]". The AIE x86sim compile **crashes** instead ("Floating point exception (core dumped)" in x86Sim Graph Analysis) |
| `float16` | no | no | AIE-MLv2 only **[D]** |

Port element types (`input_buffer<T>`, `input_stream<T>`, plio data) follow the same table.

## 3. Accumulators

The AIE API picks the default accumulator from the operand pair **[D]**. You can pass a wider tag
explicitly (`aie::mul<acc48>(a, b)`, `aie::accum<acc48, N>`). A tag is a *minimum*: the API maps it to
the nearest native width **[D]**.

| Operands | AIE default | AIE-ML default | Recommended portable tag |
|---|---|---|---|
| int8 x int8, int8 x int16 | acc48 | **acc32** | acc48 when sums can exceed 2^31 |
| int16 x int16 | acc48 | **acc32** | `acc48` (48 b on AIE, 64 b on AIE-ML) whenever the sum can exceed 2^31 or the output is int32 with a shift **[C]** |
| int16 x int32 | acc48 | acc64 | acc48 / acc64 |
| int32 x int32 | acc80 | acc64 (emulated) | `acc64` on AIE-ML. `acc80` does **not exist** on AIE-ML ("no matching function for call to 'mul'") **[C]** |
| cint16 x int16/cint16 | cacc48 | cacc64 | cacc48 |
| cint32 x anything | cacc48/cacc80 | cacc64 | cacc64 on AIE-ML |
| float, cfloat | accfloat / caccfloat | accfloat / caccfloat (emulated mul) | |
| bfloat16 | n/a | accfloat | |

Native accumulator widths: AIE has 48 b and 80 b lanes. AIE-ML has 32 b and 64 b lanes **[D]**.
`acc32`/`acc40`/`acc48` all map to 48 b on AIE. `acc40`..`acc64` map to 64 b on AIE-ML.

**AIE-ML trap [C]:** converting `acc32` to `int32` with `to_vector<int32>(shift)` is a bit-cast. The
shift, rounding and saturation are ignored, and an `acc32` sum wraps at 2^31. Example:
`aie::abs_square<int32>(cint16_vec, 1)` on AIE-ML returned `re²+im²` unshifted and wrapped
(-2^31 for -32768-32768j), while AIE gave the correct shifted, saturated result. Use an explicit `acc48`
accumulator for such outputs (see `algorithm.md`).

Register budget: AIE has 8 x 384-bit fixed-point accumulator registers (4 x 768-bit), and float MACs use
the shared vector registers. AIE-ML has 9 x 1024-bit (= 18 x 512 / 36 x 256) accumulator registers [L].
Keep at most 2 to 4 accumulators live in a hot loop on AIE and up to 8 on AIE-ML.

## 4. Vector widths and lanes

| | AIE | AIE-ML |
|---|---|---|
| Vector register sizes | 128/256/512/1024 b | 128/256/512/1024 b |
| Load/store width per access | 128 b or 256 b (2 loads + 1 store / cycle) | 128 b or 256 b (512 b as two accesses) |
| Natural data vector (use for LANES) | **256 b** | **512 b** |
| int16 elementwise lanes | 16 **[C]** | 32 **[C]** |
| int16 x int16 `sliding_mul` lanes | 8 or 16 **[D]**, 16 **[C]** | 16 **[D][C]** |
| float lanes | 8 **[C]** | 16 **[C]** |
| cint16 lanes | 8 **[C]** | 8 or 16 **[C]** (8 used) |
| Stream access (`readincr_v`) | 128 b per vector read **[C]** | 128 b per vector read **[C]** |

Any `aie::vector<T, N>` whose size is 128/256/512/1024 bits is legal on both. Larger vectors are split
by the API **[D]**. On AIE, `vector<int16, 8>` (128 b) is legal but `vector<int16, 4>` (64 b) is not.
Pick LANES so that the per-iteration sample count divides by it.

## 5. Matrix multiply (`aie::mmul<M, K, N, TA, TB>`) shapes

These are the recommended shapes, the ones used by the Vitis DSP Library `matrix_mult` (tiling scheme) and
listed in the AIE API "Matrix Multiplication Modes" table **[D]**. `4x4x4 int16` is confirmed on both
architectures **[C]**. Other shapes in the API table are legal but may need several intrinsics per call.

| TA x TB | AIE | AIE-ML |
|---|---|---|
| int8 x int8 | 4x8x4 | 4x8x8 |
| int16 x int8 | 4x4x4 | 4x4x8 |
| int16 x int16 | 4x4x4 **[C]** | 4x4x4 **[C]** |
| int32 x int16, cint16 x int16 | 4x4x2 | 4x4x4 |
| int32 x int32, cint16 x cint16 | 4x4x2 | 4x4x4 / 1x4x8 (cint16 x cint16, emulated) |
| cint32 x int16 | 2x4x2 | 2x4x8 |
| cint32 x cint16 | 2x2x2 | 2x4x8 |
| float x float | 4x4x2 | 4x8x4 (emulated with bfloat16) |
| bfloat16 x bfloat16 | n/a | 4x8x4 |

A, B and C must be in **tiled layout** (each M x K block contiguous) in memory. See
`multi-kernel-graph.md` §GEMM.

## 6. Memory and buffer limits

| Limit | AIE | AIE-ML |
|---|---|---|
| Data memory per tile (one memory module) | 32 KB | 64 KB |
| One buffer (including its margin) | ≤ one memory module: 32 KB | ≤ 64 KB |
| Default buffering | ping-pong: **2 x size** per kernel-to-kernel or PLIO buffer | same |
| Memory a kernel can reach | own module + neighbours (up to 4 modules) | own + neighbours |
| Program memory | 16 KB | 16 KB |
| Stack (default) | 1024 B per core (`Work/reports/report_stack.txt`) **[C]** | same |
| Tile DMA tiling (`tiling_parameters`) | limited: tiling a 16x16 int16 buffer into 4x4 tiles failed with "DMA S2MM requires BD 32 : Limit 16 S2MM descriptors" **[C]** | multi-dimensional DMA, works **[C]** |
| Stream ports per kernel | 2 in, 2 out (32 b each) **[C]** | 1 in, 1 out (32 b): "The AI Engine kernel has 2 input stream ports, but the maximum number…" **[C]** |
| Cascade word | 384 b (8 x acc48) | 512 b (8 x acc64 / 16 x acc32) |
| Cascade direction | horizontal, snake rows | horizontal and vertical |
| Vector load/store alignment | 16 B for 128 b, 32 B for 256 b. Graph buffers are aligned for you | same; 32 B for 256 b access |
| Rounding modes | floor, ceil, positive_inf, negative_inf, symmetric_inf, symmetric_zero, conv_even, conv_odd | the same plus symmetric_floor, symmetric_ceil |

Budget per kernel (keep it on one tile):

```text
sum over buffer ports (NSAMP * sizeof(T) * 2 if ping-pong) + margins + LUT bytes + class members + 1 KB stack
    <= 32 KB (AIE) or 64 KB (AIE-ML)
```

- A port larger than half a module must be `single_buffer(kk.in[0]);`. This costs throughput, because
  the kernel and the DMA can no longer overlap.
- A port larger than a whole module must be split: fewer samples per iteration, or more kernels.
- On AIE-ML, large matrices go through a memory tile (`shared_buffer`), not a core tile.

## 7. Operations

| Operation | AIE | AIE-ML |
|---|---|---|
| vector int add/sub/mul/mac, compare/select, max/min | yes **[C]** | yes **[C]** |
| `aie::div` (float vectors) | compiles **[C]** (slow; avoid in hot loops) | compiles **[C]** |
| `aie::inv`, `aie::sqrt` (float) | compiles **[C]** | compiles **[C]** |
| integer division `/` `%` by a non power of two | scalar software routine, **never** in a hot loop | same |
| `aie::to_float` / `aie::to_fixed` (int32 ↔ float) | yes **[C]** | yes **[C]** |
| `aie::abs_square<int32>(cint16, shift)` | correct **[C]** | shift ignored, wraps (see §3) **[C]** |
| sin/cos | `aie::sincos` / `aie::sincos_complex` (AIE only) **[D]** | not available: use a LUT |
| Low-level intrinsics (`mul16`, `mac16`, `v16acc48`, `srs`) | AIE only | **not available**. Use the AIE API only |

Use the AIE API (`aie::`) for every kernel. It is the only portable way to target both architectures.

## Refusal procedure

When a rule above blocks the request as stated:

1. **Write no files.** Do not generate a partial project.
2. Reply with:
   - **a first sentence of the form "`<item>` is not supported on `<device>` (`<part>`, `<architecture>`)."**
     Use the words "not supported" or "not available". Do not call it only a "conflict" or say it "needs
     to be resolved".
   - the rule behind it (for example "AIE-ML has no 80-bit accumulator")
   - the nearest legal alternative or alternatives, with the trade-off
3. Wait for the user to choose. Continue only with an alternative the user accepts.

| Request | Nearest legal alternative |
|---|---|
| bfloat16 on VCK190 | float on VCK190 (2x memory), or target VEK280 |
| uint16/uint32/int4 on VCK190 | int32 (or int16 with offset), or target VEK280 |
| acc80 / int32 x int32 at full 80-bit precision on VEK280 | acc64 (emulated 32x32), or target VCK190 |
| `mul16`/`mac16` intrinsics on VEK280 | the same algorithm with `aie::mul/mac` / `aie::sliding_mul` |
| float16 on VCK190/VEK280 | bfloat16 (VEK280) or float |
| buffer > one memory module | fewer samples per `run()`, split across kernels (`multi-kernel-graph.md`), or a memory tile (AIE-ML) |
| DMA tiling of large matrices on VCK190 | in-kernel tiling or a tiler kernel (`multi-kernel-graph.md` §GEMM) |
| cfloat on VEK280 | allowed (emulated): proceed, but warn about throughput and suggest float or cint32 if speed matters |

Example reply:

> bfloat16 is not supported on VCK190 (xcvc1902, AIE architecture): AIE has no bfloat16 vector support,
> and the compiler crashes on it. Nearest legal options: (a) the same kernel in `float` on VCK190, which
> doubles the buffer memory to 2 KB per port, or (b) keep bfloat16 and target VEK280 (xcve2802). Which one
> do you want?

> acc80 is not supported on VEK280 (xcve2802, AIE-ML): AIE-ML has no 80-bit accumulator, and `aie::mul`
> with an `acc80` tag fails with "no matching function for call to 'mul'". Nearest legal options: (a) keep
> VEK280 and use `acc64` (emulated 32x32 multiply), or (b) keep `acc80` and target VCK190 (xcvc1902).
> Which one do you want?
