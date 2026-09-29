// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

#include <array>

// Loop pragmas, no-ops under x86sim.
#ifndef __X86SIM__
#define AIE_LOOP_UNROLL_FULL _Pragma("clang loop unroll(full)")
#define AIE_LOOP_NO_UNROLL _Pragma("clang loop unroll(disable)")
#else
#define AIE_LOOP_UNROLL_FULL
#define AIE_LOOP_NO_UNROLL
#endif

namespace custom_ops {

// 1x1 CONVOLUTION (pointwise conv) == bias-folded GEMM, weight-streaming
// variant.
//   A 1x1 conv is a GEMM with N = output channels, K = input channels (the
//   reduction), and M = flattened H*W spatial positions:
//       Conv1x1(X, W) + bias  ==  C[N, M] = W[N, K] . A[K, M] + bias[N]
//   Nothing here is conv-specific; the GEMM identifiers below carry that
//   meaning. For a real spatial (3x3) conv with halo/sliding-window/int8
//   requant, see the sibling conv3x3_int8_2stamp tutorial instead.
//
// Bias is folded into the weights as one extra reduction tile (W_aug[:, K] =
// bias, padded to the next 8-tile edge); see the tiling comment.
//
// Distribution over one 4x4 stamp: output rows N split across the 4 overlay
// ROWS, free dim M split across the 4 overlay COLS. The WEIGHTS are streamed
// rather than resident: each call receives only MB output rows' worth of
// weights (mb_tiles = MB/8 row-tiles) in wts_data, and produces exactly those
// MB rows for the current free-tile T.
//
// Buffer roles (set by the tiler, mirrored here):
//   * IFM  ASYNC  -- the A slice for the current free-tile is HELD across the
//                    calls_per_ifm (= n_ob) calls that stream successive weight
//                    blocks, because every output row reduces over the SAME
//                    activation columns. Acquired once per hold, released after
//                    calls_per_ifm calls (see the call-count logic below).
//   * WTS  SYNC   -- a fresh MB-row block arrives every call, double-buffered
//                    in L1 by the tiler so the next block's DMA overlaps
//                    compute.
//   * OFM  SYNC   -- one [MB, T] block drained per call (double-buffered).
//
// Per-call work is therefore mb_tiles x (K/8) mmuls plus one folded-bias mmul,
// scattered into this call's [MB, T] output block.
//
// lp_params (set by the tiler):
//   [0] = T             -- free-dim tile size (output columns per kernel call)
//   [1] = K             -- REAL reduction length (bias tile handled after)
//   [2] = MB            -- output rows produced per call (one N_per_row block)
//   [3] = calls_per_ifm -- calls the async IFM buffer stays live (= n_ob)
static constexpr int gemm_bias_kernel_lp_size = 4;

static constexpr unsigned MM = 8;         // output rows per mmul tile (N)
static constexpr unsigned KK = 8;         // reduction per mmul tile (K)
static constexpr unsigned NN = 8;         // free positions per mmul tile (M)
static constexpr unsigned TILE = MM * KK; // 64 elements per 8x8 tile

// Free-dim 8-tiles held live as independent mmul accumulators. mmul<8,8,8> uses
// 2 cml registers per tile, so NT=4 fills all 8 cml registers and gives the
// pipeliner enough independent accumulators to hide the mac latency.
static constexpr unsigned NT = 4;

// A (IFM) on bank A, W on bank C, C (OFM) on bank B: the two mmul operand loads
// issue on separate banks and the store lands on a third.
#define IFM_DM_BANK __aie_dm_resource_a
#define WTS_DM_BANK __aie_dm_resource_c
#define OFM_DM_BANK __aie_dm_resource_b

// Compute MB output rows (mb_tiles = MB/MM row-tiles) x T columns for this call
// and write them into this call's [MB, T] OFM block.
template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) static void gemm_bias_compute(
    const dtype_ifm IFM_DM_BANK
        *__restrict ifm_data, // A, held [red_tiles, Ntot, 8, 8]
    const dtype_wts WTS_DM_BANK
        *__restrict wts_data, // W_aug [mb_tiles, red_tiles+1, 8, 8]
    dtype_ofm OFM_DM_BANK *__restrict ofm_data, // C, this call's [MB, T]
    uint32_t T, uint32_t K, uint32_t MB) {

  using MMUL = aie::mmul<MM, KK, NN, dtype_ifm, dtype_wts>;

  const uint32_t Mt = MB / MM;       // output-row 8-tiles this call
  const uint32_t red_tiles = K / KK; // real reduction 8-tiles (from A)
  const uint32_t red_tiles_w =
      red_tiles + 1;             // weight tiles per row (incl. bias)
  const uint32_t Ntot = T / NN;  // free-dim 8-tiles this call
  const uint32_t NB = Ntot / NT; // free-dim sub-blocks of NT tiles

  // Constant B-tile for the folded-bias step: b[ki*NN+ni] = 1 iff ki == 0, so
  // mmul of the bias A-tile (col 0 = bias, else 0) yields bias[row] in every
  // free lane.
  aie::vector<dtype_ifm, MMUL::size_B> bones_tmp =
      aie::zeros<dtype_ifm, MMUL::size_B>();
  AIE_LOOP_UNROLL_FULL
  for (unsigned ni = 0; ni < NN; ++ni)
    bones_tmp.set((dtype_ifm)1.0f, ni);
  const aie::vector<dtype_ifm, MMUL::size_B> bones = bones_tmp;

  for (uint32_t mt = 0; mt < Mt; ++mt) {
    for (uint32_t nb = 0; nb < NB; ++nb) {
      std::array<MMUL, NT> C;

      // wp walks the mt-th output-row group's weight tiles (red_tiles_w of
      // them, bias tile last); ip walks this free sub-block's NT A-tiles,
      // advancing by a whole free-row (Ntot tiles) per reduction step.
      const dtype_wts WTS_DM_BANK *__restrict wp =
          wts_data + mt * red_tiles_w * TILE;
      const dtype_ifm IFM_DM_BANK *__restrict ip = ifm_data + nb * NT * TILE;

      // kt == 0 peeled: seed the NT accumulators with mul().
      {
        const aie::vector<dtype_wts, MMUL::size_A> a =
            aie::load_v<MMUL::size_A>(wp);
        wp += TILE;
        AIE_LOOP_UNROLL_FULL
        for (unsigned nt = 0; nt < NT; ++nt) {
          const aie::vector<dtype_ifm, MMUL::size_B> b =
              aie::load_v<MMUL::size_B>(ip + nt * TILE);
          C[nt].mul(a, b);
        }
        ip += Ntot * TILE;
      }

      // kt = 1..red_tiles-1 reduction (rolled; NT independent accumulators
      // supply the ILP for the pipeliner, so no unroll needed).
      AIE_LOOP_NO_UNROLL
      for (unsigned kt = 1; kt < red_tiles; ++kt) {
        const aie::vector<dtype_wts, MMUL::size_A> a =
            aie::load_v<MMUL::size_A>(wp);
        wp += TILE;
        AIE_LOOP_UNROLL_FULL
        for (unsigned nt = 0; nt < NT; ++nt) {
          const aie::vector<dtype_ifm, MMUL::size_B> b =
              aie::load_v<MMUL::size_B>(ip + nt * TILE);
          C[nt].mac(a, b);
        }
        ip += Ntot * TILE;
      }

      // Folded-bias tile: wp now points at the bias A-tile; mac it against the
      // constant-1 B-tile to add bias[row] to every accumulator.
      {
        const aie::vector<dtype_wts, MMUL::size_A> abias =
            aie::load_v<MMUL::size_A>(wp);
        AIE_LOOP_UNROLL_FULL
        for (unsigned nt = 0; nt < NT; ++nt) {
          C[nt].mac(abias, bones);
        }
      }

      // Epilogue: take each 8x8 result straight from the accumulator register
      // file (never a scratch DM buffer) and scatter it into this call's
      // [MB, T] OFM block at row-tile mt.
      AIE_LOOP_UNROLL_FULL
      for (unsigned nt = 0; nt < NT; ++nt) {
        const aie::vector<dtype_ofm, TILE> res =
            C[nt].to_accum().template to_vector<dtype_ofm>();
        AIE_LOOP_UNROLL_FULL
        for (unsigned oi = 0; oi < MM; ++oi) {
          aie::store_v(ofm_data + (mt * MM + oi) * T + (nb * NT + nt) * NN,
                       res.template extract<NN>(oi));
        }
      }
    }
  }
}

// ADF entry point: acquires the async IFM once per hold and releases it after
// the calls_per_ifm weight blocks that reduce over it (see Buffer roles above).
template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) void gemm_bias_kernel(
    adf::input_buffer_conf<dtype_ifm, adf::bpc_async_0d> &__restrict ifm,
    adf::input_buffer_conf<dtype_wts, adf::bpc_sync_0d> &__restrict wts,
    adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[gemm_bias_kernel_lp_size]) {

  const uint32_t T = lp_params[0];
  const uint32_t K = lp_params[1];
  const uint32_t MB = lp_params[2];
  const uint32_t calls_per_ifm = lp_params[3];

  // Acquire the async IFM once at the start of each hold; release after the
  // calls_per_ifm weight blocks that reduce over it have been consumed.
  static uint32_t call_count = 0;
  if (call_count == 0) {
    ifm.acquire();
  }

  gemm_bias_compute<dtype_ifm, dtype_wts, dtype_ofm>(
      (const dtype_ifm IFM_DM_BANK *__restrict)ifm.data(),
      (const dtype_wts WTS_DM_BANK *__restrict)wts.data(),
      (dtype_ofm OFM_DM_BANK *__restrict)ofm.data(), T, K, MB);

  call_count += 1;
  if (call_count == calls_per_ifm) {
    ifm.release();
    call_count = 0;
  }
}

} // namespace custom_ops
