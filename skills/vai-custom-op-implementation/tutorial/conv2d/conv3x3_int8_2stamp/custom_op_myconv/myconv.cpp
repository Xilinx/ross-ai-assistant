// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

// Self-contained INT8 3x3 stride-1 convolution kernel (no MLLIB dependency).
//
// The kernel is a clean-room aie::mmul implementation that consumes the exact
// same native-faithful L1 tiles the tiling delivers and produces the same int8
// output as the previous mllib::conv2d path (to the same +/-1 requant
// rounding):
//
//   IFM L1  [H_L2=12, CG_PER=5, W_SUB=16, V=8]  -- one IFM depth chunk (40
//   in-ch)
//           over 12 rows (10 output + 2 halo) x 16-wide W window; element
//           (h,cg,w,v) = in-channel (chunk*40 + cg*8 + v), row h, col w.
//   WTS L1  [WTS_SV=5888]                        -- one wave for a 16-ch output
//           tile x one 40-in-ch depth chunk. Layout (see
//           reference_implementation.py):
//             weights[oc, cg, ky, kx][k(in), n(out)]  -> 2*5*3*3 * 8*8 = 5760 B
//             bias tail (int32, = b_q * 64) at offset 5760, 16 values.
//   OFM L1  [OFM_L1_ROWS=21, CG_OFM_TILE=2, W_BLK=8, V=8] -- first HT_OUT=10
//   rows
//           hold the int8 output; element (h,cgt,w,v) = out-channel
//           (tile_base + cgt*8 + v), row h, col w.
//
// Compute (mmul<4,8,8> int8xint8 -> int32): for each output tile the kernel is
// called NUM_DEPTH=4 times (once per IFM depth chunk); it accumulates the int32
// partial sums across the 4 calls in a resident psum scratch, seeded on the
// first call with the folded bias (b_q*64). On the last call it requantises
//   out = sat_int8( round_even( (sum_{160ch,3x3} x_q*w_q + b_q*64) >> 8 ) )
// which is exactly si*sw/so = 2^-8 and sb/so = 2^-2 for this layer's scales
// (si=2^-4, sw=2^-9, so=2^-5, sb=2^-7). OFM is async ratio 4: acquire on the
// first depth chunk, release on the last.
//
// lp_params is unused: this kernel's geometry is compile-time fixed (that is
// what lets the compiler pipeline the mmul loops). The signature keeps a
// single dummy word because the binding requires at least one.

// ===========================================================================
// ADAPTING THIS KERNEL TO A DIFFERENT CONV TENSOR (READ FIRST)
// ===========================================================================
// This is a WORKED EXAMPLE for one specific layer: INT8, 3x3, stride 1,
// Cin=160, Cout=16-per-tile, output 10 rows x W window, scales below. The next
// custom op will be a DIFFERENT tensor, so treat every constant here as a knob,
// not a law. The geometry is intentionally COMPILE-TIME fixed (not read from
// lp_params) because that lets the compiler pipeline the mmul loops; the price
// is that you re-derive the constants for each new shape. What drives each one:
//
//   Cin (input channels)   -> CG_PER * NUM_DEPTH * V must equal Cin.
//                             Here 5 * 4 * 8 = 160. Split Cin into NUM_DEPTH
//                             chunks (async ratio) of CG_PER 8-channel groups.
//                             Pick NUM_DEPTH so one chunk's IFM+WTS fits L1;
//                             more chunks = more calls but smaller L1.
//   Cout (per output tile) -> CG_OFM * V (=16 here). Raise CG_OFM for more
//                             out-channels per call (more mmul reuse of each
//                             loaded IFM tap) until the accumulator register
//                             file / L1 WTS buffer runs out.
//   Output rows            -> HT_OUT (=10). IFM needs HT_OUT + (KH-1) rows in
//                             L1 (H_L2 = HT_OUT + halo). Bigger HT_OUT
//                             amortizes weight loads over more rows; costs IFM
//                             L1.
//   Output W width         -> W_BLK (=8), split into N_WG groups of WG(=4) cols
//                             (WG is the mmul M dim). IFM W window W_SUB must
//                             cover W_BLK + (KW-1) taps for the sliding window.
//   Kernel size            -> KH, KW (=3,3). For 1x1 use the gemm
//                             (pure GEMM) tutorial instead -- no halo/shuffle.
//   Stride != 1            -> the shuffle_down_fill window step (off = kx*V)
//                             assumes stride 1; for stride s the tap offsets
//                             and W_SUB change (and rows skip by s).
//
// mmul shape: aie::mmul<WG, V, V> = <4,8,8> int8 (M=4 spatial, K=8 in-ch,
// N=8 out-ch). K and N are fixed by the int8 8x8x8 hardware tile; only WG (M)
// is a free choice (4 keeps a 32-lane accumulator; the 8-way C0..C7 block then
// covers 2 H rows x N_WG W-groups x CG_OFM out-CGs to hide mac latency -- see
// fix #1 below). If you change WG/CG_OFM/N_WG you must re-list the
// accumulators.
//
// REQUANT (the arithmetic that MUST track the new layer's scales):
//   out = sat_int8( round_even( (sum x_q*w_q + bias_q_scaled) >> SHIFT_OUT ) )
//   SHIFT_OUT = -log2(si*sw/so) and the bias must be pre-scaled so that
//   sb/so == 2^-(bias_extra_shift). Here si=2^-4, sw=2^-9, so=2^-5 give
//   si*sw/so = 2^-8 -> SHIFT_OUT=8, and sb=2^-7,so=2^-5 -> bias tail carries
//   b_q*64 (2^6) folded so the single >>8 lands it at 2^-2. For a new layer
//   recompute SHIFT_OUT from ITS scales and re-fold the bias in the weight blob
//   accordingly; a wrong shift is the #1 cause of "close but off-by-N" output.
//   (If scales are per-channel, SHIFT_OUT becomes a per-out-channel vector and
//   the single >> becomes a per-lane multiply+shift.)
// ===========================================================================

using namespace adf;

namespace custom_ops {

static constexpr int myconv_kernel_lp_size = 1;

// ---- Fixed L1 tile geometry (must match myconv_tiling.py) ----
// Each constant is a TUNING KNOB for a new tensor -- see "ADAPTING THIS KERNEL"
// above for what drives it. Keep these in lock-step with myconv_tiling.py.
static constexpr int H_L2 = 12;   // IFM rows in L1 (10 output + 2 halo)
static constexpr int CG_PER = 5;  // in-CGs per depth chunk (40 in-ch)
static constexpr int W_SUB = 16;  // IFM W window per sub-tile
static constexpr int V = 8;       // 8-channel inner block
static constexpr int HT_OUT = 10; // output rows per tile
static constexpr int W_BLK = 8;   // output W block width
static constexpr int CG_OFM = 2;  // output CGs per tile (16 out-ch)
static constexpr int KH = 3;
static constexpr int KW = 3;
static constexpr int NUM_DEPTH = 4;     // IFM depth chunks (= OFM async ratio)
static constexpr int SHIFT_OUT = 8;     // si*sw/so = 2^-8
static constexpr int WG = 4;            // output W positions per mmul (M dim)
static constexpr int N_WG = W_BLK / WG; // 2 groups of 4 output cols

static constexpr int WTS_TAP_BYTES = V * V; // 64 B per 8x8
// Offset of the bias tail within the L1 weight tile. Bias is a SEPARATE ONNX
// operand, but it rides in the same L1 buffer: there are only 4 row-broadcast
// L2->L1 ports and the weights hold them all, so the tiling concatenates the
// two in L2 (two L3->L2 transfers, disjoint slices) and one stream carries
// both.
static constexpr int WTS_WT_BYTES =
    CG_OFM * CG_PER * KH * KW * WTS_TAP_BYTES; // 5760
// Output int8 element count of one tile; the int32 psum scratch lives in the
// OFM L1 buffer right after it (the tiling reserves the extra rows -- see
// OFM_L1_ROWS).
static constexpr int OFM_OUT_ELEMS = HT_OUT * CG_OFM * W_BLK * V; // 1280

using MMUL = aie::mmul<WG, V, V, int8, int8>; // C[4x8] += A[4x8] * B[8x8]

// Build the 32-lane (4 spatial x 8 out-ch) folded-bias accumulator seed for
// output CG `oc` from the wave's int32 bias tail (values already = b_q * 64).
static inline aie::vector<int32, WG * V> bias_seed(const int8 *__restrict wp,
                                                   int oc) {
  const int32 *__restrict bp = (const int32 *)(wp + WTS_WT_BYTES) + oc * V;
  aie::vector<int32, V> b8 = aie::load_v<V>(bp); // 32B-aligned bias tail
  return aie::concat(aie::concat(b8, b8), aie::concat(b8, b8));
}

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) void myconv_kernel(
    adf::input_buffer_conf<dtype_ifm, adf::bpc_sync_0d> &__restrict ifm,
    adf::input_buffer_conf<dtype_wts, adf::bpc_sync_0d> &__restrict wts,
    adf::output_buffer_conf<dtype_ofm, adf::bpc_async_0d> &__restrict ofm,
    const uint32_t (&lp_params)[myconv_kernel_lp_size]) {

  static uint32_t depth_iter = 0;
  static bool initialized = false;

  if (!initialized) {
    aie::tile::current().set_saturation(aie::saturation_mode::saturate);
    aie::tile::current().set_rounding(aie::rounding_mode::conv_even);
    initialized = true;
  }

  const bool first = (depth_iter == 0);
  const bool last = (depth_iter + 1 == NUM_DEPTH);

  if (first)
    ofm.acquire();

  const int8 *__restrict ip = (const int8 *)ifm.data();
  const int8 *__restrict wp = (const int8 *)wts.data();
  int8 *__restrict op = (int8 *)ofm.data();
  // int32 partial-sum scratch for one 16-ch output tile (2 CG x 10 H x 8 W),
  // stored in the OFM buffer just past the int8 output. The 4 depth chunks of a
  // tile share the same acquired OFM buffer (acquire on depth 0, release on the
  // last), so the accumulator persists across the calls without any core
  // static.
  int32 *__restrict psum = (int32 *)(op + OFM_OUT_ELEMS);

  // ---- 8-way accumulators (fix #1) + fully-aligned memory (fix #3) ----
  // fix #1: a single mmul accumulator serialises the multi-cycle mac chain, so
  // the MAC unit stalls. Native conv2d.h keeps 8 independent accumulators in
  // flight to hide that latency. We mirror it: a 2x2x2 block of (2 output H
  // rows) x (2 output W groups) x (2 output CGs) = 8 tiles computed together;
  // each IFM tap feeds both output CGs and each weight tap feeds both W groups.
  //
  // fix #3: kill unaligned vector traffic. Weights, psum and output all sit at
  // >=32B-aligned offsets, so they now use plain aligned load_v/store_v (the
  // unaligned intrinsics forced vshift/vsel realignment even for aligned
  // addresses). The IFM taps slide by 1 W (8 B) per kx and cannot all be 32B
  // aligned, so instead of an unaligned load per tap we load the aligned 128 B
  // L1 row once per (row, cg) as three 32-lane blocks (W0-3 / W4-7 / W8-11) and
  // slice each 4-wide window from registers with shuffle_down_fill -- exactly
  // native's aligned-load + in-register shift pattern. Data (hence the result)
  // is identical, only the access pattern changes.
  auto blk = [&](int oh, int owg, int oc) {
    return ((oh * CG_OFM + oc) * W_BLK + owg * WG) * V;
  };
  auto seed = [&](int oh, int owg, int oc) -> aie::vector<int32, WG * V> {
    return first ? bias_seed(wp, oc)
                 : aie::load_v<WG * V>(psum + blk(oh, owg, oc));
  };
  // 4-wide (32-lane) window at element offset `off` (0/8/16) within
  // concat[lo:hi].
  auto win = [](const aie::vector<int8, WG * V> &lo,
                const aie::vector<int8, WG * V> &hi,
                int off) -> aie::vector<int8, WG * V> {
    return aie::shuffle_down_fill(lo, hi, off);
  };

  for (int oh0 = 0; oh0 < HT_OUT; oh0 += 2) {
    const int oh1 = oh0 + 1;

    MMUL C0(seed(oh0, 0, 0), 0), C1(seed(oh0, 0, 1), 0), C2(seed(oh0, 1, 0), 0),
        C3(seed(oh0, 1, 1), 0), C4(seed(oh1, 0, 0), 0), C5(seed(oh1, 0, 1), 0),
        C6(seed(oh1, 1, 0), 0), C7(seed(oh1, 1, 1), 0);

    for (int cg = 0; cg < CG_PER; ++cg) {
      for (int ky = 0; ky < KH; ++ky) {
        // Aligned 128 B L1 rows for the two output-H rows, as 32-lane blocks:
        // B0=W0-3, B1=W4-7, B2=W8-11. owg0 taps slice {B0,B1}, owg1 {B1,B2}.
        const int rb0 = ((oh0 + ky) * CG_PER + cg) * W_SUB * V;
        const int rb1 = ((oh1 + ky) * CG_PER + cg) * W_SUB * V;
        aie::vector<int8, WG * V>
            r0b0 = aie::load_v<WG * V>(ip + rb0 + 0 * WG * V),
            r0b1 = aie::load_v<WG * V>(ip + rb0 + 1 * WG * V),
            r0b2 = aie::load_v<WG * V>(ip + rb0 + 2 * WG * V),
            r1b0 = aie::load_v<WG * V>(ip + rb1 + 0 * WG * V),
            r1b1 = aie::load_v<WG * V>(ip + rb1 + 1 * WG * V),
            r1b2 = aie::load_v<WG * V>(ip + rb1 + 2 * WG * V);
        for (int kx = 0; kx < KW; ++kx) {
          const int off = kx * V; // 0, 8 or 16 lanes
          aie::vector<int8, WG * V> a00 = win(r0b0, r0b1, off),
                                    a01 = win(r0b1, r0b2, off),
                                    a10 = win(r1b0, r1b1, off),
                                    a11 = win(r1b1, r1b2, off);
          // B = weights[oc, cg, ky, kx] -> 8 in-ch (k) x 8 out-ch (n),
          // 64B-aligned.
          const int wb = (cg * KH + ky) * KW + kx;
          aie::vector<int8, V * V> b0 = aie::load_v<V * V>(
              wp + (0 * CG_PER * KH * KW + wb) * WTS_TAP_BYTES);
          aie::vector<int8, V * V> b1 = aie::load_v<V * V>(
              wp + (1 * CG_PER * KH * KW + wb) * WTS_TAP_BYTES);
          C0.mac(a00, b0);
          C1.mac(a00, b1);
          C2.mac(a01, b0);
          C3.mac(a01, b1);
          C4.mac(a10, b0);
          C5.mac(a10, b1);
          C6.mac(a11, b0);
          C7.mac(a11, b1);
        }
      }
    }

    if (last) {
      aie::store_v(op + blk(oh0, 0, 0), C0.to_vector<int8>(SHIFT_OUT));
      aie::store_v(op + blk(oh0, 0, 1), C1.to_vector<int8>(SHIFT_OUT));
      aie::store_v(op + blk(oh0, 1, 0), C2.to_vector<int8>(SHIFT_OUT));
      aie::store_v(op + blk(oh0, 1, 1), C3.to_vector<int8>(SHIFT_OUT));
      aie::store_v(op + blk(oh1, 0, 0), C4.to_vector<int8>(SHIFT_OUT));
      aie::store_v(op + blk(oh1, 0, 1), C5.to_vector<int8>(SHIFT_OUT));
      aie::store_v(op + blk(oh1, 1, 0), C6.to_vector<int8>(SHIFT_OUT));
      aie::store_v(op + blk(oh1, 1, 1), C7.to_vector<int8>(SHIFT_OUT));
    } else {
      aie::store_v(psum + blk(oh0, 0, 0), C0.to_vector<int32>(0));
      aie::store_v(psum + blk(oh0, 0, 1), C1.to_vector<int32>(0));
      aie::store_v(psum + blk(oh0, 1, 0), C2.to_vector<int32>(0));
      aie::store_v(psum + blk(oh0, 1, 1), C3.to_vector<int32>(0));
      aie::store_v(psum + blk(oh1, 0, 0), C4.to_vector<int32>(0));
      aie::store_v(psum + blk(oh1, 0, 1), C5.to_vector<int32>(0));
      aie::store_v(psum + blk(oh1, 1, 0), C6.to_vector<int32>(0));
      aie::store_v(psum + blk(oh1, 1, 1), C7.to_vector<int32>(0));
    }
  }

  depth_iter = last ? 0 : depth_iter + 1;
  if (last)
    ofm.release();
}

} // namespace custom_ops
