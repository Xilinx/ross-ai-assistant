<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
<!-- Code in this file was compiled (x86sim + hw) and simulated (x86sim + aiesim) with Vitis 2026.2 on
     xcvc1902-vsva2197-2MP-e-S (AIE) and xcve2802-vsvh1760-2MP-e-S (AIE-ML) unless marked otherwise.
     All results matched a Python golden model bit-exactly. -->
# Multi-Kernel Graphs

This file extends the single-kernel templates (`hpp.md`, `cpp.md`, `graph.md`, `app.md`) to N kernels.
The conventions do not change:

| Item | Single kernel | N kernels |
|---|---|---|
| Kernel files | `KERNEL.hpp` / `KERNEL.cpp` | one `.hpp`/`.cpp` pair per kernel class (`gain_offset.hpp`, `decim2.hpp`, …). A kernel class used twice is written once |
| Graph file | `KERNEL_graph.h`, class `KERNEL_graph`, member `kk` | one top `<design>_graph.h`, class `<design>_graph`, members `k0, k1, …` (or descriptive names) |
| Graph ports | one per kernel port | one per **external** kernel port. Internal kernel-to-kernel connections have no graph port |
| `source(k)` | `"KERNEL.cpp"` | each kernel's own `.cpp` |
| App | `KERNEL_app.cpp` with the dummy graph | `<design>_app.cpp`, same template, instantiating `<design>_graph` |
| Makefile `MY_SOURCES` | kernel files | all kernel files plus `<design>_graph.h` |

Kernel port indices: `k.in[i]` / `k.out[j]` are numbered by the order of the input (output) arguments in
`run()`. Cascade ports count too. In `run(input_buffer& a, input_cascade* c, output_buffer& o)`,
`c` is `in[1]`.

## Kernel-to-kernel buffer (pipeline)

```cpp
class pipe_graph : public graph {
private:
    kernel k0, k1;
public:
    input_port  sig_i;
    output_port sig_o;
    pipe_graph(void)
    {
        k0 = kernel::create_object<gain_offset>();
        k1 = kernel::create_object<decim2>();
        source(k0) = "gain_offset.cpp";
        source(k1) = "decim2.cpp";
        runtime<ratio>(k0) = 0.9;
        runtime<ratio>(k1) = 0.9;

        connect<>(sig_i, k0.in[0]);
        dimensions(k0.in[0]) = { gain_offset::NSAMP_SIG_I };

        static_assert(gain_offset::NSAMP_SIG_O == decim2::NSAMP_SIG_I, "k0 -> k1 size mismatch");
        connect<>(k0.out[0], k1.in[0]);                       // kernel -> kernel buffer (ping-pong)
        dimensions(k0.out[0]) = { gain_offset::NSAMP_SIG_O };
        dimensions(k1.in[0])  = { decim2::NSAMP_SIG_I };

        connect<>(k1.out[0], sig_o);
        dimensions(k1.out[0]) = { decim2::NSAMP_SIG_O };
    }
};
```

Rules:
- The element type of the producer's output port must be the consumer's input port type.
- Set `dimensions()` on **both** ends, with the same sample count. Add a `static_assert` that the
  `NSAMP_*` constants match.
- A rate change happens *inside* a kernel. Both ends of one connection still carry equal sizes per iteration.
- Every kernel-to-kernel buffer is ping-pong (2x memory) by default. Add `single_buffer(k1.in[0]);` only for
  buffers larger than half a memory module (`device-rules.md` §6).
- A kernel with a margin input (`margin<M>`) may be fed by a buffer connection or a PLIO. The compiler
  copies the overlap.

## Kernel-to-kernel stream, FIFO depth

```cpp
        connect<> net_s(km.out[0], kw.in[0]);   // km: output_stream<int16>*, kw: input_stream<int16>*
        fifo_depth(net_s) = 64;                 // 32-bit words
```

- Use a FIFO when producer and consumer burst at different times, or when two stream paths reconverge
  (unequal latency can deadlock). Start with 32 to 64 words. The hw compile writes
  `Work/reports/FIFO_guidance_report.txt`. The Makefile `SIM_FIFO=true` option
  (`--aie.evaluate-fifo-depth`) reports the depths the simulation needed.
- AIE-ML: at most one input stream port and one output stream port per kernel. AIE: two of each.

## Broadcast and merge

```cpp
        // broadcast: one graph input feeds two kernels
        connect<>(sig_i, kg.in[0]);
        connect<>(sig_i, kf.in[0]);
        dimensions(kg.in[0]) = { gain_offset::NSAMP_SIG_I };
        dimensions(kf.in[0]) = { fir8::NSAMP_SIG_I };

        // merge: two kernel outputs into one kernel with two inputs
        connect<>(kg.out[0], km.in[0]);
        connect<>(kf.out[0], km.in[1]);
        dimensions(kg.out[0]) = { gain_offset::NSAMP_SIG_O };
        dimensions(kf.out[0]) = { fir8::NSAMP_SIG_O };
        dimensions(km.in[0])  = { add2::NSAMP_A_I };
        dimensions(km.in[1])  = { add2::NSAMP_B_I };
```

- Broadcast works for buffers (the DMA writes every consumer) and for streams (switch multicast).
  Broadcast to a margin kernel and to a plain kernel is legal (validated).
- Merge means separate input ports on the consumer. Never connect two outputs to one input port.

## Placement and tile sharing

| Call | Use |
|---|---|
| `runtime<ratio>(k) = r` | the kernel's share of a tile's cycles. Kernels whose ratios sum to ≤ 1 may share a tile (4 kernels at 0.4, one of them pinned, used 3 tiles) |
| `location<kernel>(k) = tile(col, row)` | pin a kernel. Use it only when needed (cascade neighbours, floorplan) and put it in the graph, not the app |
| `stack_size(k) = 2048;` | per-kernel stack, if `report_stack.txt` shows overflow |

Placement constraints that do not fit the device fail the hw compile, not x86sim. Run the hw compile
once (`make`) for every multi-kernel design.

## Cascade (accumulator chain)

Partial accumulators flow from tile to tile without a memory round trip. The last kernel rounds and
writes data.

```cpp
// acc48 is 48-bit native on AIE and maps to 64-bit on AIE-ML. 8 lanes = one cascade word on both
// (AIE: 8 x 48b = 384b cascade, AIE-ML: 8 x 64b = 512b cascade).
using casc_tag = acc48;
static constexpr unsigned ELANES = 8;

void energy_first::run(input_buffer<int16>& sig_i_0, output_cascade<casc_tag>* casc_o)
{
    auto pi = aie::begin_vector<ELANES>(sig_i_0);
    aie::accum<casc_tag, ELANES> acc = aie::zeros<casc_tag, ELANES>();
    for (unsigned i = 0; i < NSAMP_SIG_I_0 / ELANES; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_I_0 / ELANES, )
    {
        aie::vector<int16, ELANES> x = *pi++;
        acc = aie::mac(acc, x, x);
    }
    writeincr(casc_o, acc);                         // partial per-lane sums to the next tile
}

void energy_last::run(input_buffer<int16>& sig_i_1, input_cascade<casc_tag>* casc_i, output_buffer<int32>& sig_o)
{
    auto pi = aie::begin_vector<ELANES>(sig_i_1);
    aie::accum<casc_tag, ELANES> acc = readincr_v<ELANES>(casc_i);
    for (unsigned i = 0; i < NSAMP_SIG_I_1 / ELANES; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_I_1 / ELANES, )
    {
        aie::vector<int16, ELANES> x = *pi++;
        acc = aie::mac(acc, x, x);
    }
    aie::vector<int32, ELANES> lanes = acc.to_vector<int32>(SHIFT);
    *sig_o.data() = aie::reduce_add(lanes);
}
```

Graph: `connect<cascade>(k0.out[0], k1.in[1]);`. The cascade ports get no `dimensions()`.

Cascade rules:
- The port tag (`output_cascade<tag>`) must be **the accumulator's actual tag**. An `aie::mmul<…, int16,
  int16, acc48>` holds an `acc64` accumulator on AIE-ML, so its cascade port must be
  `output_cascade<acc64>` on AIE-ML and `<acc48>` on AIE (otherwise: "no matching function for call to
  'writeincr'"). Use `#if __AIE_ARCH__ >= 20`.
- One cascade word is 8 accumulator lanes on both architectures. Wider accumulators can be written in
  one call (`writeincr(c, acc16)`, `readincr_v<16>(c)`) or in 8-lane pieces
  (`writeincr(c, a.extract<8>(0)); writeincr(c, a.extract<8>(1));` and
  `aie::concat(readincr_v<8>(c), readincr_v<8>(c))`). Both forms were validated.
- Each read must match a write in order and count. A mismatch hangs the simulation.
- Cascade kernels sit on neighbouring tiles. The mapper does this for you, and `location<>` overrides it.
  AIE cascades run horizontally (snake rows). AIE-ML cascades run horizontally and vertically.
- Only the last kernel applies `to_vector(shift)`. Intermediate kernels never round.

## GEMM: tiled layout, tiling parameters, K-split

`aie::mmul<TM, TK, TN, TA, TB>` multiplies one TM x TK tile of A by one TK x TN tile of B. Each tile must
be contiguous in memory (tiled layout: tiles in row-major tile order, elements row-major inside a tile).
Shapes per type: `device-rules.md` §5. There are three ways to get tiled data:

| Method | AIE | AIE-ML | Cost |
|---|---|---|---|
| A. DMA tiling with `write_access`/`read_access` | **no** in practice: the hw compile fails with "DMA S2MM requires BD 32 : Limit 16 S2MM descriptors" for 16x16 and 16x32 int16 in 4x4 tiles. **x86sim does not catch this** (it passes) | **yes** | free (DMA) |
| B. In-kernel tiling (shuffles into a member array) | yes | yes | extra cycles and memory on the GEMM tile |
| C. Tiler/untiler kernels before and after the GEMM kernel | yes | yes | extra tiles. This is the Vitis DSP Library `matrix_mult` pattern (`TP_ADD_TILING_A/B`, `TP_ADD_DETILING_OUT`) |

Kernel loop (same for all methods; A, B, C in tiled layout):

```cpp
    using MMUL = aie::mmul<TM, TK, TN, int16, int16, acc48>;   // acc48: 48b on AIE, 64b on AIE-ML
    constexpr unsigned SZA = MMUL::size_A, SZB = MMUL::size_B, SZC = MMUL::size_C;
    for (unsigned m = 0; m < M / TM; m++)
        chess_loop_range(M / TM, )
    {
        for (unsigned n = 0; n < N / TN; n++)
            chess_prepare_for_pipelining chess_loop_range(N / TN, )
        {
            const int16* __restrict ta = pa + m * (K / TK) * SZA;   // tile (m, 0) of A
            const int16* __restrict tb = pb + n * SZB;              // tile (0, n) of B
            MMUL acc;
            acc.mul(aie::load_v<SZA>(ta), aie::load_v<SZB>(tb));
            for (unsigned k = 1; k < K / TK; k++)
                chess_unroll_loop()
            {
                ta += SZA;
                tb += (N / TN) * SZB;
                acc.mac(aie::load_v<SZA>(ta), aie::load_v<SZB>(tb));
            }
            aie::store_v(pc, acc.template to_vector<int16>(SHIFT));
            pc += SZC;
        }
    }
```

### A. DMA tiling (AIE-ML)

**On AIE (VCK190), use method B (in-kernel tiling) or C instead.** The AIE tile DMA has no
multi-dimensional addressing, so it runs out of buffer descriptors, and only the hw compile reports it.

The PLIO streams row-major data. The tile DMA writes it into the buffer in tiled order, and the output DMA
reads the tiled C buffer back in row-major order:

```cpp
// Row-major R x C matrix  <->  buffer of TR x TC tiles (tiles row-major, elements row-major in a tile).
// The buffer is described as 4-D {TC, TR, C/TC, R/TR} (dim 0 fastest); the DMA walks it in row-major
// matrix order, so a row-major stream lands tiled (write_access) or a tiled buffer leaves row-major (read_access).
inline tiling_parameters rowmajor_to_tiles(unsigned R, unsigned C, unsigned TR, unsigned TC)
{
    return tiling_parameters{
        .buffer_dimension = {TC, TR, C / TC, R / TR},
        .tiling_dimension = {TC, 1, 1, 1},
        .offset           = {0, 0, 0, 0},
        .tile_traversal   = {{.dimension = 2, .stride = 1, .wrap = C / TC},
                             {.dimension = 1, .stride = 1, .wrap = TR},
                             {.dimension = 3, .stride = 1, .wrap = R / TR}}};
}
// in the graph constructor, after connect<>() and dimensions():
        write_access(kk.in[0]) = tiling(rowmajor_to_tiles(mm::M, mm::K, mm::TM, mm::TK));
        write_access(kk.in[1]) = tiling(rowmajor_to_tiles(mm::K, mm::N, mm::TK, mm::TN));
        read_access(kk.out[0]) = tiling(rowmajor_to_tiles(mm::M, mm::N, mm::TM, mm::TN));
```

- A transpose is a 2-D `tiling` with `tiling_dimension = {1, C}` and a traversal over dimension 0
  (Vitis DSP Library `matrix_vector_mul`, `TP_DIM_A_LEADING`). Not validated here.
- Large matrices on AIE-ML: stage them in a memory tile with `shared_buffer<T>::create({…}, 1, 1)`, using
  `write_access`/`read_access` on its ports (Vitis DSP Library `vss_fft_ifft_1d` and `fft_ifft_2d`).
  Not validated here.

### B. In-kernel tiling for 4x4 int16 tiles (AIE and AIE-ML)

```cpp
// Row-major R x C int16 (C % 16 == 0) <-> 4x4 tiles in row-major tile order.
template <unsigned R, unsigned C>
static inline void to_tiles(const int16* __restrict in, int16* __restrict out)
{
    for (unsigned r = 0; r < R / 4; r++)
        for (unsigned c = 0; c < C / 16; c++)
            chess_prepare_for_pipelining
        {
            const int16* p = in + 4 * r * C + 16 * c;
            auto [a0, a1] = aie::interleave_zip(aie::load_v<16>(p),         aie::load_v<16>(p + C),     4);
            auto [b0, b1] = aie::interleave_zip(aie::load_v<16>(p + 2 * C), aie::load_v<16>(p + 3 * C), 4);
            auto [t0, t1] = aie::interleave_zip(a0, b0, 8);   // tiles 4c, 4c+1
            auto [t2, t3] = aie::interleave_zip(a1, b1, 8);   // tiles 4c+2, 4c+3
            int16* q = out + (r * (C / 4) + 4 * c) * 16;
            aie::store_v(q, t0); aie::store_v(q + 16, t1); aie::store_v(q + 32, t2); aie::store_v(q + 48, t3);
        }
}
```

`from_tiles` is the inverse: unzip with step 8, then with step 4, and store 4 rows. Keep the tiled copies
in aligned class members (`alignas(aie::vector_decl_align) int16 at[M * K];`), not on the stack.

### K-split across kernels (cascade)

For K too large for one tile, or for more throughput, split K over a cascade chain of CASC kernels:
- Kernel j receives the column block `A[:, j*K/CASC : (j+1)*K/CASC]` and the row block
  `B[j*K/CASC : (j+1)*K/CASC, :]` through its own graph input ports.
- Every kernel loops over the same (m, n) output tiles in the same order. The first kernel starts each tile
  from zero. The others start from the cascade:
  `MMUL acc(aie::concat(readincr_v<8>(c_i), readincr_v<8>(c_i)));`, then `mac` over their K block.
- Every kernel except the last writes `auto a = acc.to_accum();` as two 8-lane `writeincr` calls. The last
  kernel calls `to_vector<T>(SHIFT)`.
- Cascade tag: `acc48` on AIE, `acc64` on AIE-ML for the int16 `acc48` mmul (see the cascade rules above).
  (Validated with 2 kernels, int16 4x4x4, on both architectures.)

## FFT-style pipelines and widgets

Stage kernels connected by buffers, with small "widget" kernels at the edges that only change the
interface or the data format. This is how the Vitis DSP Library FFT is built:
- `fft_ifft_dit_1ch` splits the FFT ranks (stages) over `TP_CASC_LEN` kernels. Kernel k processes a rank
  range, and kernels are connected with `connect(k.out[0], k+1.in[0])` plus `dimensions()` on both ends.
- When the user asks for stream I/O, a `widget_api_cast` kernel converts stream(s) → buffer in front of the
  first stage and buffer → stream(s) after the last stage. On AIE it can merge two input streams
  (the core has 2).
- `widget_real2complex` sits in front of a complex FFT to turn real samples into complex ones (imag = 0).

### Stream → buffer widget

```cpp
// Widget: copies a stream into a buffer so that a buffer kernel can follow a stream source.
void s2b::run(input_stream<cint16>* sig_i, output_buffer<cint16>& sig_o)
{
    auto po = aie::begin_vector<V>(sig_o);           // V = 4: 4 x cint16 = 128 bits per stream access
    for (unsigned i = 0; i < NSAMP_SIG_O / V; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_O / V, )
    {
        *po++ = readincr_v<V>(sig_i);
    }
}
```

The buffer → stream widget is the mirror image: `writeincr(sig_o, *pi++)` with 128-bit vectors. Give
widgets a low `runtime<ratio>` (e.g. 0.5) so they can share a tile.

### Real → complex widget

```cpp
void r2c::run(input_buffer<int16>& sig_i, output_buffer<cint16>& sig_o)
{
    auto pi = aie::begin_vector<LANES>(sig_i);
    auto po = aie::begin_vector<LANES / 2>(sig_o);
    const aie::vector<int16, LANES> zero = aie::zeros<int16, LANES>();
    for (unsigned i = 0; i < NSAMP_SIG_I / LANES; i++)
        chess_prepare_for_pipelining chess_loop_range(NSAMP_SIG_I / LANES, )
    {
        auto [lo, hi] = aie::interleave_zip(*pi++, zero, 1);   // re0 0 re1 0 ...
        *po++ = aie::vector_cast<cint16>(lo);                    // 16 x int16 -> 8 x cint16
        *po++ = aie::vector_cast<cint16>(hi);
    }
}
```

Complex → real (keep the real part) is `aie::real(v)` or `aie::filter_even(aie::vector_cast<int16>(v))`.

### Chaining stages

```cpp
        connect<>(sig_i, kw.in[0]);                          // stream: no dimensions
        connect<>(kw.out[0], kk.in[0]);
        dimensions(kw.out[0]) = { s2b::NSAMP_SIG_O };
        dimensions(kk.in[0])  = { magsq::NSAMP_SIG_I };
        connect<>(kk.out[0], sig_o);
        dimensions(kk.out[0]) = { magsq::NSAMP_SIG_O };
```

For N stages, write a loop over a `kernel stage[N]` array with `connect<>(stage[i].out[0], stage[i+1].in[0])`
and matching `dimensions()`. Keep per-stage state (twiddles, coefficients) as LUT parameters of each stage
kernel (`hpp.md`).
