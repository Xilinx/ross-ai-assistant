// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

#ifdef __X86SIM__
#include <cstdio>
#endif

using namespace adf;

#ifndef __STRINGIFY
#define __STRINGIFY(a) #a
#endif
// Promise the compiler a minimum trip count so it can drop the zero-trip
// guard and software-pipeline the loop.
#define AIE_LOOP_MIN_ITERATION_COUNT(x)                                        \
  _Pragma(__STRINGIFY(clang loop min_iteration_count(x)))
// Disable the pre-RA pipeliner so the (better) post-RA pipeliner runs.
#define AIE_PIPELINE _Pragma("clang loop pipeline(disable)")

namespace custom_ops {

// lp_params layout (set by the tiling script):
//   [0] = T            -- output positions this core produces per call
//   [1] = R            -- number of reduction planes reduced (axis-0 extent)
//   [2] = plane_stride -- distance (elements) between planes in the L1 buffer
//                         == OVERLAY_ROWS * T
static constexpr int reducemax_lp_size = 3;

// bfloat16 vector granularity. The kernel now processes the T output positions
// in 32-wide chunks (full 512-bit bf16 vector, matching the MLLIB reducemax
// granularity) plus a 16-wide remainder step, so T only needs to be a multiple
// of 16 (it is: T=112=3*32+16). The OFM store base is bank-aligned and every
// j*32 / j*16 offset keeps it >=32-byte aligned, so the stores stay aligned.
//
// The IFM loads, however, are taken at base + row_off where row_off =
// core_row*T. With T=112 the odd-row offsets (112, 336 elems = 224, 672 bytes)
// are only 32-byte aligned, not the 64 bytes a 32-wide aligned load wants, so
// the 32-wide IFM loads use load_unaligned_v. The 16-wide tail stays naturally
// 32B aligned.
static constexpr int reducemax_vec_size = 32;
static constexpr int reducemax_vec_tail = 16;

// Max over R planes for a contiguous run of VEC output positions starting at p.
// Seeding with plane r = 0 keeps the reduction in bf16 lanes -- no accumulator
// and no -inf identity needed, since R >= 1.
template <int VEC, bool ALIGNED>
static inline __attribute__((always_inline)) void
reducemax_step(const bfloat16 __aie_dm_resource_a *__restrict p,
               bfloat16 __aie_dm_resource_b *__restrict out_p, uint32_t R,
               uint32_t plane_stride) {
  auto load = [](const bfloat16 __aie_dm_resource_a *__restrict q) {
    if constexpr (ALIGNED)
      return aie::load_v<VEC>(q);
    else
      return aie::load_unaligned_v<VEC>(q);
  };
  aie::vector<bfloat16, VEC> best = load(p); // r = 0
  for (uint32_t r = 1; r < R; ++r)
    best = aie::max(best, load(p + r * plane_stride));
  aie::store_v(out_p, best);
}

// Hot loop, isolated in its own function so __restrict and the DM-bank
// qualifiers actually apply to the pointers the loop dereferences (a reference
// marked __restrict on the ADF buffer does not propagate through .data()).
// IFM on bank A, OFM on bank B -> the load and the store can issue in parallel.
static void __attribute__((noinline))
reducemax_core(const bfloat16 __aie_dm_resource_a *__restrict in,
               bfloat16 __aie_dm_resource_b *__restrict out, uint32_t T,
               uint32_t R, uint32_t plane_stride) {

  const int n_vec = (int)T / reducemax_vec_size;

#ifndef __X86SIM__
  AIE_LOOP_MIN_ITERATION_COUNT(1)
  AIE_PIPELINE
#endif
  for (int j = 0; j < n_vec; ++j) {
    const uint32_t off = (uint32_t)j * reducemax_vec_size;
    reducemax_step<reducemax_vec_size, /*ALIGNED=*/false>(in + off, out + off,
                                                          R, plane_stride);
  }

  // 16-wide remainder for the trailing (T % 32) positions (16 when T=112).
  const uint32_t done = (uint32_t)n_vec * reducemax_vec_size;
  if (T - done >= (uint32_t)reducemax_vec_tail) {
    reducemax_step<reducemax_vec_tail, /*ALIGNED=*/true>(in + done, out + done,
                                                         R, plane_stride);
  }
}

// ReduceMax over axis 0: res[e] = max_{r=0..R-1} x[r, e].
//
// The op is distributed across the 4x4 overlay along the N independent output
// positions. IFM is column-broadcast, so each core in a column receives the
// same [R, ROWS*T] L1 buffer and selects its own T positions using its row
// index. The reduction axis (R planes) lives entirely inside each core.
template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void myreducemax_kernel(
    adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
    adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[reducemax_lp_size]) {

  const uint32_t T = lp_params[0];
  const uint32_t R = lp_params[1];
  const uint32_t plane_stride = lp_params[2];

  static uint16_t core_row = aie::tile::current().id().row % 4;
  const uint32_t row_off = (uint32_t)core_row * T;

  // row_off = core_row*T is a multiple of 16 (T is), so it is 32-byte aligned;
  // the 32-wide IFM loads in reducemax_core therefore use load_unaligned_v
  // (odd rows are only 32B, not 64B, aligned), while the 16-wide tail and all
  // OFM stores remain naturally aligned.
  reducemax_core((const bfloat16 __aie_dm_resource_a *__restrict)(ifm.data()) +
                     row_off,
                 (bfloat16 __aie_dm_resource_b *__restrict)(ofm.data()), T, R,
                 plane_stride);
}
} // namespace custom_ops
