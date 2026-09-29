// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
/*
 *
 * Non-vectorized scalar 1x1 affinegrid2d kernel.
 * Same math as the vectorized kernel, one grid point at a time.
 * Same LP struct (3 params), sync buffers.
 */

#include <adf.h>
#include <aie_api/aie.hpp>

#ifdef __X86SIM__
#include <iostream>
#endif

namespace custom_ops {

static constexpr int affinegrid_lp_size = 3;

typedef struct affinegrid2d_lpstruct_t {
  uint32_t align_corners;
  uint16_t H_out;
  uint16_t W_out;
  uint32_t kernel_size;
  uint32_t core_start;
} affinegrid2d_lpstruct_t;

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
static void compute(dtype_ifm *__restrict theta_data,
                    dtype_wts *__restrict size_data,
                    dtype_ofm *__restrict grid_data,
                    affinegrid2d_lpstruct_t *__restrict lp) {
#ifdef __X86SIM__
  uint16_t size_H = (uint16_t)size_data[2];
  uint16_t size_W = (uint16_t)size_data[3];
  if (size_H != lp->H_out || size_W != lp->W_out) {
    std::cout << "ERROR: size input mismatch! size=[_,_," << size_H << ","
              << size_W << "] vs lp_params H=" << lp->H_out
              << " W=" << lp->W_out << "\n";
  }
#endif

  dtype_ifm H = (dtype_ifm)lp->H_out;
  dtype_ifm W = (dtype_ifm)lp->W_out;

  dtype_ifm x_div = lp->align_corners ? (W - (dtype_ifm)1) : W;
  dtype_ifm y_div = lp->align_corners ? (H - (dtype_ifm)1) : H;

  dtype_ifm scaleX = (dtype_ifm)2.0 / x_div;
  dtype_ifm scaleY = (dtype_ifm)2.0 / y_div;
  dtype_ifm biasX = lp->align_corners
                        ? (dtype_ifm)(-1.0)
                        : ((dtype_ifm)1.0 / x_div - (dtype_ifm)1.0);
  dtype_ifm biasY = lp->align_corners
                        ? (dtype_ifm)(-1.0)
                        : ((dtype_ifm)1.0 / y_div - (dtype_ifm)1.0);

  dtype_ifm theta_copy[6];
  for (uint8_t i = 0; i < 6; i++) {
    theta_copy[i] = theta_data[i];
  }

  uint32_t grid_idx = 0;
  for (uint32_t i = 0; i < lp->kernel_size; ++i) {
    uint32_t flat = lp->core_start + i;
    uint16_t h = (uint16_t)(flat / lp->W_out);
    uint16_t w = (uint16_t)(flat % lp->W_out);

    dtype_ifm y = (dtype_ifm)h * scaleY + biasY;
    dtype_ifm x = (dtype_ifm)w * scaleX + biasX;

    dtype_ifm x_new =
        (theta_copy[0] * x) + (theta_copy[1] * y) + (theta_copy[2]);
    dtype_ifm y_new =
        (theta_copy[3] * x) + (theta_copy[4] * y) + (theta_copy[5]);

    grid_data[grid_idx++] = x_new;
    grid_data[grid_idx++] = y_new;
  }
}

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) void affinegrid2d_kernel(
    adf::input_buffer_conf<dtype_ifm, adf::bpc_sync_0d> &__restrict ifm,
    adf::input_buffer_conf<dtype_wts, adf::bpc_sync_0d> &__restrict wts,
    adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[affinegrid_lp_size]) {
  affinegrid2d_lpstruct_t lp;
  lp.align_corners = lp_params[0];
  lp.H_out = lp_params[1];
  lp.W_out = lp_params[2];

  lp.kernel_size = (uint32_t)lp.H_out * (uint32_t)lp.W_out;
  lp.core_start = 0;

  dtype_ifm *theta_data = (dtype_ifm *)ifm.data();
  dtype_wts *size_data = (dtype_wts *)wts.data();
  dtype_ofm *grid_data = (dtype_ofm *)ofm.data();

  compute(theta_data, size_data, grid_data, &lp);

#ifdef __X86SIM__
  std::cout << "AffineGrid2D Non-vectorized Kernel Complete\n";
#endif
}

} // namespace custom_ops
