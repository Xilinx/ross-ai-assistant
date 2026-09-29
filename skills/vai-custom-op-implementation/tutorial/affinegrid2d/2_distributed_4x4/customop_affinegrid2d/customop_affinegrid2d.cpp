// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

#include <adf.h>
#include <aie_api/aie.hpp>

#ifdef __X86SIM__
#include <iostream>
#endif

namespace custom_ops {

static constexpr int affinegrid_lp_size = 3;
static constexpr int affinegrid_vec_size = 16;
static constexpr int DIST_COLS = 4;
static constexpr int DIST_ROWS = 4;
static constexpr int NUM_CORES = DIST_COLS * DIST_ROWS;

typedef aie::vector<bfloat16, affinegrid_vec_size> vecf16;

typedef struct affinegrid2d_lpstruct_t {
  uint32_t align_corners;
  uint16_t H_out;
  uint16_t W_out;
  uint32_t kernel_size;
  uint32_t core_start;
} affinegrid2d_lpstruct_t;

/**
 * Non-vectorised AffineGrid2D for remainder elements or when W < vec_size.
 * Uses precomputed scale/bias instead of division for better bf16 precision.
 * Processes `count` grid points starting from global flat index `global_start`,
 * writing output sequentially to grid_data.
 */
template <typename dtype_ifm, typename dtype_ofm>
void affinegrid2D_nonvectorised(dtype_ifm *theta_data, dtype_ofm *grid_data,
                                dtype_ifm scaleX, dtype_ifm biasX,
                                dtype_ifm scaleY, dtype_ifm biasY, uint16_t W,
                                uint32_t global_start, uint32_t count) {
  dtype_ifm x, y, x_new, y_new;
  uint32_t grid_idx = 0;

  for (uint32_t i = 0; i < count; ++i) {
    uint32_t flat = global_start + i;
    uint16_t h = (uint16_t)(flat / W);
    uint16_t w = (uint16_t)(flat % W);

    y = (dtype_ifm)h * scaleY + biasY;
    x = (dtype_ifm)w * scaleX + biasX;

    x_new = (theta_data[0] * x) + (theta_data[1] * y) + (theta_data[2]);
    y_new = (theta_data[3] * x) + (theta_data[4] * y) + (theta_data[5]);

    grid_data[grid_idx++] = x_new;
    grid_data[grid_idx++] = y_new;
  }
}

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

  // Precompute scale and bias for coordinate normalization.
  // align_corners=1: coord = 2*idx/(size-1) - 1  =>  idx * (2/(size-1)) + (-1)
  // align_corners=0: coord = (2*idx+1)/size - 1   =>  idx * (2/size) + (1/size
  // - 1)
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

  if (lp->W_out >= affinegrid_vec_size) {
    aie::vector<dtype_ifm, affinegrid_vec_size> h_vec, w_vec;
    for (uint8_t i = 0; i < affinegrid_vec_size; ++i) {
      uint32_t flat = lp->core_start + i;
      h_vec[i] = static_cast<dtype_ifm>(flat / lp->W_out);
      w_vec[i] = static_cast<dtype_ifm>(flat % lp->W_out);
    }

    auto zip_mode = aie::interleave_zip_mode<dtype_ofm, affinegrid_vec_size>(1);
    auto out = aie::begin_restrict_vector<affinegrid_vec_size>(grid_data);

    uint32_t iters = lp->kernel_size / affinegrid_vec_size;
    for (uint32_t i = 0; i < iters; ++i) {
      auto x = static_cast<vecf16>(aie::add(aie::mul(w_vec, scaleX), biasX));
      auto y = static_cast<vecf16>(aie::add(aie::mul(h_vec, scaleY), biasY));

      auto acc_x = aie::mac(aie::mul(theta_copy[0], x), theta_copy[1], y);
      auto x_new = aie::to_vector<dtype_ifm>(aie::add(acc_x, theta_copy[2]));

      auto acc_y = aie::mac(aie::mul(theta_copy[3], x), theta_copy[4], y);
      auto y_new = aie::to_vector<dtype_ifm>(aie::add(acc_y, theta_copy[5]));

      auto zipped_pair = aie::interleave_zip(x_new, y_new, zip_mode);
      *out++ = zipped_pair.first;
      *out++ = zipped_pair.second;

      if (i + 1 < iters) {
        auto w_next = aie::add(w_vec, (dtype_ifm)(affinegrid_vec_size));
        auto w_wrap =
            aie::sub(w_vec, (dtype_ifm)(W - (dtype_ifm)affinegrid_vec_size));
        auto w_mask =
            aie::ge(w_vec, (dtype_ifm)(W - (dtype_ifm)affinegrid_vec_size));
        w_vec = aie::select(w_next, w_wrap, w_mask);

        auto h_next = aie::select(h_vec, aie::add(h_vec, (dtype_ifm)1), w_mask);
        auto h_mask = aie::ge(h_next, H);
        h_vec = aie::select(h_next, aie::sub(h_next, H), h_mask);
      }
    }

    if (lp->kernel_size % affinegrid_vec_size != 0) {
      uint32_t vec_done = iters * affinegrid_vec_size;
      affinegrid2D_nonvectorised(theta_copy, grid_data + vec_done * 2, scaleX,
                                 biasX, scaleY, biasY, (uint16_t)lp->W_out,
                                 lp->core_start + vec_done,
                                 lp->kernel_size - vec_done);
    }
  } else {
    affinegrid2D_nonvectorised(theta_copy, grid_data, scaleX, biasX, scaleY,
                               biasY, (uint16_t)lp->W_out, lp->core_start,
                               lp->kernel_size);
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

  uint32_t total = (uint32_t)lp.H_out * (uint32_t)lp.W_out;
  lp.kernel_size = total / NUM_CORES;

  static aie::tile_id id = aie::tile::current().id();
  uint32_t core_idx = id.col * DIST_ROWS + id.row;
  lp.core_start = core_idx * lp.kernel_size;

  dtype_ifm *theta_data = (dtype_ifm *)ifm.data();
  dtype_wts *size_data = (dtype_wts *)wts.data();
  dtype_ofm *grid_data = (dtype_ofm *)ofm.data();

  compute(theta_data, size_data, grid_data, &lp);

#ifdef __X86SIM__
  std::cout << "AffineGrid2D Distributed Kernel Complete (core "
            << (int)core_idx << ")\n";
#endif
}

} // namespace custom_ops
