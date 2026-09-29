// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include "aie.hpp"
#include <adf.h>

#ifdef __X86SIM__
#include <cstdio>
#endif

// Portable loop pragmas (self-contained). On the AIE build (__AIECC__) they
// expand to the real `#pragma clang loop` directives; on any other front-end
// (e.g. x86sim, whose clang rejects those options) they expand to nothing.
#if defined(__AIECC__)
#ifndef __STRINGIFY
#define __STRINGIFY(a) #a
#endif
#define AIE_LOOP_MIN_ITERATION_COUNT(x)                                        \
  _Pragma(__STRINGIFY(clang loop min_iteration_count(x)))
#define AIE_LOOP_MAX_ITERATION_COUNT(x)                                        \
  _Pragma(__STRINGIFY(clang loop max_iteration_count(x)))
#define AIE_LOOP_RANGE(a, ...)                                                 \
  AIE_LOOP_MIN_ITERATION_COUNT(a)                                              \
  __VA_OPT__(AIE_LOOP_MAX_ITERATION_COUNT(__VA_ARGS__))
#define AIE_PREPARE_FOR_PIPELINING // no-op; pipelining on by default
#else
#define AIE_LOOP_MIN_ITERATION_COUNT(x)
#define AIE_LOOP_MAX_ITERATION_COUNT(x)
#define AIE_LOOP_RANGE(a, ...)
#define AIE_PREPARE_FOR_PIPELINING
#endif

using namespace adf;
using namespace aie;

namespace custom_ops {

// lp_params: [T, K]
//   T = number of reductions this core performs per kernel invocation
//   K = number of contiguous elements reduced per reduction (innermost axis)
constexpr int reducemax_lp_size = 2;

// This op is an inner-most-axis max of K = 4 contiguous bf16 elements:
//     out[r] = max_{k=0..K-1} in[r*K + k]
//
// Vectorization strategy (K is fixed at 4):
//   Load 32 contiguous bf16 = 8 groups of 4. A 4-way de-interleave (two
//   stages of filter_even/filter_odd) splits the 32-lane vector into the
//   four component lanes c0..c3 (8 lanes each), where
//       c0 = {g0.e0, g1.e0, ...}, c1 = {g0.e1, ...}, etc.
//   The group max is then max(max(c0,c1), max(c2,c3)) -- a pairwise tree, so
//   the lanes stay in bf16 and no accumulator is needed -- and the 8 results
//   are written with a single 8-wide vector store, which avoids the sub-word
//   (scalar bf16) store read-modify-write penalty of the scalar version.
//
// Pointers are __restrict and on distinct DM banks (IFM=a, OFM=b) so the loads
// and the store can issue in parallel and the loop can be software-pipelined.
//
// VEC_GROUPS (=8) divides T (=392) exactly, so there is normally no remainder;
// a scalar tail is kept for robustness if T is ever not a multiple of 8.
static constexpr int VEC_IN = 32;    // contiguous bf16 loaded per iteration
static constexpr int VEC_GROUPS = 8; // = VEC_IN / K (K == 4), outputs per iter

template <typename dtype_ifm, typename dtype_ofm>
void __attribute__((noinline))
reducemax_core(dtype_ifm __aie_dm_resource_a *__restrict in,
               dtype_ofm __aie_dm_resource_b *__restrict out,
               uint16 nb_reductions) {
  const int vec_iters = nb_reductions / VEC_GROUPS;
  const int rem = nb_reductions - vec_iters * VEC_GROUPS;

  AIE_PREPARE_FOR_PIPELINING
  AIE_LOOP_RANGE(1, )
  for (int i = 0; i < vec_iters; i++) {
    aie::vector<dtype_ifm, VEC_IN> v = aie::load_v<VEC_IN>(in);
    in += VEC_IN;

    // Stage 1: split even/odd elements (interleaved component pairs).
    auto e = aie::filter_even(v); // {e0,e2,e4,...}
    auto o = aie::filter_odd(v);  // {e1,e3,e5,...}
    // Stage 2: separate the four components, 8 lanes each.
    auto c0 = aie::filter_even(e);
    auto c2 = aie::filter_odd(e);
    auto c1 = aie::filter_even(o);
    auto c3 = aie::filter_odd(o);

    auto best = aie::max(aie::max(c0, c1), aie::max(c2, c3));

    aie::store_v(out, best);
    out += VEC_GROUPS;
  }

  // Scalar tail (only if T is not a multiple of VEC_GROUPS).
  for (int r = 0; r < rem; r++) {
    dtype_ifm m = *in++;
    for (int k = 1; k < 4; k++) {
      dtype_ifm e = *in++;
      m = aie::max(m, e);
    }
    *out++ = (dtype_ofm)m;
  }
}

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void reducemax_adf_wrapper(
    adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
    adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
    const uint32_t (&layer_params)[reducemax_lp_size]) {

  static uint16_t core_row = aie::tile::current().id().row % 4;

  // No rounding mode is set here: a max only ever selects one of its bf16
  // inputs, so unlike the summing variant there is no accfloat->bf16 narrowing
  // to round. The result is bit-exact against a float reference.

  uint16 T = (uint16)layer_params[0];
  uint16 K = (uint16)layer_params[1];

  // IFM is column-broadcast: every core in a column receives all OVERLAY_ROWS
  // slices, so each core selects its own [T, K] slice via its row index.
  // offset = core_row * T * K is a multiple of 32 elements (T*K = 1568), i.e.
  // 64-byte aligned, satisfying the 32-wide bf16 load alignment requirement.
  auto *in = (dtype_ifm *)ifm.data() + (uint32_t)core_row * T * K;
  auto *out = (dtype_ofm *)ofm.data();

  reducemax_core<dtype_ifm, dtype_ofm>(
      (dtype_ifm __aie_dm_resource_a *__restrict)in,
      (dtype_ofm __aie_dm_resource_b *__restrict)out, T);
}

} // namespace custom_ops
