// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

namespace custom_ops {

// Size of the lp_params that the tiling defines:
//   [0] = T  -- tile size (number of output elements per kernel call)
//   [1] = K  -- kernel (filter) size
//   [2] = calls_per_wts -- number of kernel calls during which the
//                          asynchronous WTS buffer must stay live
static constexpr int conv1d_kernel_lp_size = 3;

// 1-D single-channel convolution kernel.
//
// The overlay broadcasts the IFM down each column of the 4x4 grid, so all 4
// cores in one column receive the same L1 IFM buffer of 4*T + halo elements.
// Each core picks its own T-element slice using its row index, then computes
// the K-tap dot product for each of the T output samples.
//
// The WTS port is asynchronous: the same filter weights are reused across
// `calls_per_wts` consecutive kernel invocations (the tiler sets that ratio).
template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) void conv1d_kernel(
    adf::input_buffer_conf<dtype_ifm, adf::bpc_sync_0d> &__restrict ifm,
    adf::input_buffer_conf<dtype_wts, adf::bpc_async_0d> &__restrict wts,
    adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[conv1d_kernel_lp_size]) {

  const uint32_t T = lp_params[0];
  const uint32_t K = lp_params[1];
  const uint32_t calls_per_wts = lp_params[2];

  static uint16_t core_row = aie::tile::current().id().row % 4;
  const uint32_t ifm_offset = T * core_row;

  // Buffer acquisition: the WTS buffer is acquired on the first call after a
  // rotation.
  static uint32_t wts_call_count = 0;
  if (wts_call_count == 0) {
    wts.acquire();
  }

  auto *ifm_data = (dtype_ifm *__restrict)(ifm.data());
  auto *wts_data = (dtype_wts *__restrict)(wts.data());
  auto *ofm_data = (dtype_ofm *__restrict)(ofm.data());

  // Actual computation: one tile of output is computed for each core.
  for (uint32_t j = 0; j < T; ++j) {
    float acc = 0.f;
    for (uint32_t k = 0; k < K; ++k) {
      acc += float(ifm_data[ifm_offset + j + k]) * float(wts_data[k]);
    }
    ofm_data[j] = dtype_ofm(acc);
  }

  // Buffer release: the WTS buffer is released after the last call.
  wts_call_count += 1;
  if (wts_call_count == calls_per_wts) {
    wts.release();
    wts_call_count = 0;
  }
}

} // namespace custom_ops
