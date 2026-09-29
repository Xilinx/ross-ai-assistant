// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

namespace custom_ops {

// Size of the lp_params that the tiling defines.
static constexpr int mul_kernel_lp_size = 1;

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) void
mul_kernel(adf::input_buffer_conf<dtype_ifm, adf::bpc_sync_0d> &__restrict a,
           adf::input_buffer_conf<dtype_wts, adf::bpc_sync_0d> &__restrict b,
           adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict out,
           const uint32_t (&lp_params)[mul_kernel_lp_size]) {

  auto *a_data = (dtype_ifm *__restrict)(a.data());
  auto *b_data = (dtype_wts *__restrict)(b.data());
  auto *out_data = (dtype_ofm *__restrict)(out.data());

  uint32_t size = lp_params[0];

  for (unsigned i = 0; i < size; ++i) {
    out_data[i] = a_data[i] * b_data[i];
  }
}

} // namespace custom_ops
