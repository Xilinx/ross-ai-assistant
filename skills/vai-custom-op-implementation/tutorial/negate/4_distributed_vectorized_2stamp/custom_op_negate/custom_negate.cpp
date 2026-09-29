// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

using namespace adf;

#ifndef __STRINGIFY
#define __STRINGIFY(a) #a
#endif
#define AIE_LOOP_MIN_ITERATION_COUNT(x)                                        \
  _Pragma(__STRINGIFY(clang loop min_iteration_count(x)))
// Disable the early pipelining to get the (better) post-pipelining algorithm.
#define AIE_PIPELINE _Pragma("clang loop pipeline(disable)")

namespace custom_ops {
// Size of the lp_params that the tiling defines.
static constexpr int negate_kernel_lp_size = 1;

// See
// https://download.amd.com/docnav/aiengine/xilinx2025_1/aiengine_api/aie_api/doc/group__group__basic__types.html
// for available vector sizes per data type.
static constexpr int negate_vec_size = 16;

static void kernel(bfloat16 *__restrict ifm, bfloat16 *__restrict ofm,
                   int trip_count) {
  // IMPORTANT: The ifm and ofm addresses must be aligned to the vector size
  // or they will silently read/write wrong data.
  auto in = aie::cbegin_restrict_vector<negate_vec_size>(ifm);
  auto out = aie::begin_restrict_vector<negate_vec_size>(ofm);

  // The AIE_LOOP_RANGE macro promises to the compiler that the loop
  // will at least iterate the specified number of times.
  // This is important to optimize the loop.
  // If the loop runs fewer iterations than promised here, it can produce
  // invalid results.
#ifndef __X86SIM__
  AIE_LOOP_MIN_ITERATION_COUNT(16)
  // Pipeline the loop to improve throughput.
  AIE_PIPELINE
#endif
  for (int i = 0; i < trip_count; i++) {
    // Load 16 elements.
    aie::vector<bfloat16, negate_vec_size> vin = *in++;
    // Negate them.
    aie::vector<bfloat16, negate_vec_size> negated = aie::neg(vin);
    // Store 16 elements.
    *out++ = negated;
  }
}

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void
negate_kernel(adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
              adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
              const uint32_t (&lp_params)[negate_kernel_lp_size]) {

  // The tiling hands the lp_params here, and this one was d0*d1, so the
  // total number of elements.
  int trip_count = (int)lp_params[0];

  // Because the ifm is broadcast to all cores in a column, each core only
  // processes a fraction of the input data. We take this into account by
  // offsetting the input pointer based on the core row.
  static uint16_t core_row = aie::tile::current().id().row % 4;
  const uint32_t row_offset = (uint32_t)(trip_count * core_row);

  // Handles to input and output.
  // IMPORTANT: row_offset must be a multiple of negate_vec_size
  // because the vector loads require the addresses to be aligned.
  dtype_ifm *in = ifm.data() + row_offset;
  dtype_ofm *out = ofm.data();

  // We call a function here because this is the only way on peano
  // to apply 'restrict' to 'in' and 'out', i.e. to tell the compiler
  // that these pointers do not alias.
  kernel(in, out, trip_count / negate_vec_size);
}
} // namespace custom_ops
