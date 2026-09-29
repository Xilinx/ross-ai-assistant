// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>

using namespace adf;

namespace custom_ops {
// Size of the lp_params that the tiling defines.
static constexpr int negate_kernel_lp_size = 1;

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void
negate_kernel(adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
              adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
              const uint32_t (&lp_params)[negate_kernel_lp_size]) {

  // The tiling hands the lp_params here, and this one was d0*d1, so the
  // total number of elements.
  int trip_count = (int)lp_params[0];

  // Handles to input and output.
  dtype_ifm *in = ifm.data();
  dtype_ofm *out = ofm.data();

  // Negate one element at a time.
  for (int i = 0; i < trip_count; i++) {
    *out = -(*in);
    in++;
    out++;
  }
}
} // namespace custom_ops
