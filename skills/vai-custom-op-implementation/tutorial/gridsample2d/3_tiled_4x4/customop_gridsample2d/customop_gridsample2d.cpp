// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.

#include <adf.h>
#include <aie_api/aie.hpp>

#ifdef __X86SIM__
#include <iostream>
#endif

#ifndef __X86SIM__
#define AIE_LOOP_MIN_ITERATION_COUNT(x)                                        \
  _Pragma(STRINGIFY(clang loop min_iteration_count(x)))
#define AIE_PIPELINE _Pragma("clang loop pipeline(disable)")
#endif

namespace custom_ops {

#define ROWS 4
#define GS2D_VECTOR_LEN 16

typedef aie::vector<bfloat16, GS2D_VECTOR_LEN> vecf16;
typedef aie::vector<bfloat16, GS2D_VECTOR_LEN * 2> vecf16x2;
typedef aie::vector<int16_t, GS2D_VECTOR_LEN> veci16;

typedef struct gridsample2d_lpstruct_t {
  uint16_t mode;
  uint16_t align_corners;
  uint16_t kernelratio_ifm;
  uint16_t kernelratio_wts;
  uint16_t kernelratio_ofm;
  uint16_t H;
  uint16_t W;
  uint16_t HW_grid;
  uint16_t HW_grid_sub;
} gridsample2d_lpstruct_t;

__attribute__((always_inline)) static inline vecf16
gridsample2d_getpixel_vec(bfloat16 *__restrict XX, veci16 xx, veci16 yy,
                          int16_t H, int16_t W) {
  aie::mask<GS2D_VECTOR_LEN> in_bounds =
      aie::ge(xx, (int16_t)0) & aie::lt(xx, W) & aie::ge(yy, (int16_t)0) &
      aie::lt(yy, H);

  veci16 xc = aie::max(aie::min(xx, (int16_t)(W - 1)), (int16_t)0);
  veci16 yc = aie::max(aie::min(yy, (int16_t)(H - 1)), (int16_t)0);
  veci16 idx = aie::to_vector<int16_t>(aie::add(aie::mul(yc, W), xc));

  vecf16 result;
#ifndef __X86SIM__
  AIE_LOOP_MIN_ITERATION_COUNT(GS2D_VECTOR_LEN)
  AIE_PIPELINE
#endif
  for (uint16_t i = 0; i < GS2D_VECTOR_LEN; ++i)
    result[i] = XX[idx[i]];

  return aie::select(aie::zeros<bfloat16, GS2D_VECTOR_LEN>(), result,
                     in_bounds);
}

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
static void
compute(dtype_ifm *__restrict X_data, dtype_wts *__restrict grid_data,
        dtype_ofm *__restrict Y_data, gridsample2d_lpstruct_t *__restrict lp) {
  uint16_t i, k, N, M;

  vecf16 Y_vec;
  vecf16 x_norm_vec, y_norm_vec;
  vecf16 x_vec, y_vec;
  veci16 x0_vec, x1_vec, y0_vec, y1_vec;
  vecf16 wx_vec, wy_vec, wxsub_vec, wysub_vec;
  vecf16 v00_vec, v01_vec, v10_vec, v11_vec;

  const int16_t cnst1_0i = 1;
  const bfloat16 cnst1_0f = 1.0;
  const bfloat16 cnst0_5f = 0.5;
  const bfloat16 cnst0_0f = 0.0;

  const bfloat16 scaleX =
      cnst0_5f * (bfloat16)(lp->align_corners ? (lp->W - 1) : lp->W);
  const bfloat16 scaleY =
      cnst0_5f * (bfloat16)(lp->align_corners ? (lp->H - 1) : lp->H);
  const bfloat16 offset =
      cnst0_5f * (bfloat16)(lp->align_corners ? (bfloat16)0 : cnst1_0f);
  const bfloat16 biasX = scaleX - offset;
  const bfloat16 biasY = scaleY - offset;

  N = GS2D_VECTOR_LEN * (uint32_t)(lp->HW_grid_sub / GS2D_VECTOR_LEN);
  M = lp->HW_grid_sub % GS2D_VECTOR_LEN;

  if (lp->mode == 1) // Bilinear
  {
    const veci16 neg1i = aie::broadcast<int16_t, GS2D_VECTOR_LEN>((int16_t)-1);
    const veci16 zero_i = aie::zeros<int16_t, GS2D_VECTOR_LEN>();
    const veci16 pos1i = aie::broadcast<int16_t, GS2D_VECTOR_LEN>(cnst1_0i);
    const vecf16 pos1f = aie::broadcast<bfloat16, GS2D_VECTOR_LEN>(cnst1_0f);
    const vecf16 neg1f =
        aie::broadcast<bfloat16, GS2D_VECTOR_LEN>((bfloat16)(-1.0));
    const vecf16 zero_f = aie::zeros<bfloat16, GS2D_VECTOR_LEN>();

    for (i = 0; i < lp->HW_grid_sub; i += GS2D_VECTOR_LEN) {
      // --- Grid data load ---
      if (i < N) {
        // 4 × 128-bit loads (16-byte aligned) + deinterleave xy pairs
        vecf16 grid_lo = aie::concat(aie::load_v<8>(grid_data + 2 * i),
                                     aie::load_v<8>(grid_data + 2 * i + 8));
        vecf16 grid_hi = aie::concat(aie::load_v<8>(grid_data + 2 * i + 16),
                                     aie::load_v<8>(grid_data + 2 * i + 24));
        auto unzipped = aie::interleave_unzip(grid_lo, grid_hi, 1);
        x_norm_vec = unzipped.first;
        y_norm_vec = unzipped.second;
      } else {
        for (k = 0; k < M; ++k) {
          x_norm_vec[k] = grid_data[2 * i + 2 * k];
          y_norm_vec[k] = grid_data[2 * i + 2 * k + 1];
        }
      }

      // Pixel coords via float32 accumulator: x_pixel = x_norm * scaleX + biasX
      auto acc_x = aie::add(aie::mul(x_norm_vec, scaleX), biasX);
      auto acc_y = aie::add(aie::mul(y_norm_vec, scaleY), biasY);

      // Floor via bf16 (needed for integer pixel indices)
      x_vec = static_cast<vecf16>(acc_x);
      y_vec = static_cast<vecf16>(acc_y);

      x0_vec = aie::to_fixed_floor<int16_t>(x_vec, 0);
      y0_vec = aie::to_fixed_floor<int16_t>(y_vec, 0);

      // Fractional weights via float32 accumulator:
      // wx = x_norm * scaleX + (biasX - floor_x)
      // This avoids bf16 precision loss from subtracting two large, close
      // values.
      vecf16 x0_float = static_cast<vecf16>(aie::to_float<bfloat16>(x0_vec));
      vecf16 y0_float = static_cast<vecf16>(aie::to_float<bfloat16>(y0_vec));
      vecf16 bias_minus_x0 = aie::sub(biasX, x0_float);
      vecf16 bias_minus_y0 = aie::sub(biasY, y0_float);

      wx_vec = static_cast<vecf16>(
          aie::mac(aie::mul(x_norm_vec, scaleX), bias_minus_x0, cnst1_0f));
      wy_vec = static_cast<vecf16>(
          aie::mac(aie::mul(y_norm_vec, scaleY), bias_minus_y0, cnst1_0f));

      // Floor correction: bf16 rounding of pixel coord may cross an integer
      // boundary, making the initial floor off by 1.
      //   wx < 0  => floor was too high, adjust x0 -= 1 and wx += 1
      //   wx >= 1 => floor was too low,  adjust x0 += 1 and wx -= 1
      // aie::select(v_false, v_true, mask): v_true where mask==1, v_false where
      // mask==0
      aie::mask<GS2D_VECTOR_LEN> xhi = aie::lt(wx_vec, cnst0_0f);
      aie::mask<GS2D_VECTOR_LEN> xlo = aie::ge(wx_vec, cnst1_0f);
      aie::mask<GS2D_VECTOR_LEN> yhi = aie::lt(wy_vec, cnst0_0f);
      aie::mask<GS2D_VECTOR_LEN> ylo = aie::ge(wy_vec, cnst1_0f);

      // x0 adjustment: xhi => -1, xlo => +1, else => 0
      x0_vec = aie::add(
          x0_vec, aie::select(aie::select(zero_i, pos1i, xlo), neg1i, xhi));
      y0_vec = aie::add(
          y0_vec, aie::select(aie::select(zero_i, pos1i, ylo), neg1i, yhi));

      // wx adjustment: xhi => +1, xlo => -1, else => 0
      wx_vec = aie::add(
          wx_vec, aie::select(aie::select(zero_f, neg1f, xlo), pos1f, xhi));
      wy_vec = aie::add(
          wy_vec, aie::select(aie::select(zero_f, neg1f, ylo), pos1f, yhi));

      x1_vec = aie::add(x0_vec, cnst1_0i);
      y1_vec = aie::add(y0_vec, cnst1_0i);

      wxsub_vec = aie::sub(cnst1_0f, wx_vec);
      wysub_vec = aie::sub(cnst1_0f, wy_vec);

      v00_vec = gridsample2d_getpixel_vec(X_data, x0_vec, y0_vec, lp->H, lp->W);
      v01_vec = gridsample2d_getpixel_vec(X_data, x0_vec, y1_vec, lp->H, lp->W);
      v10_vec = gridsample2d_getpixel_vec(X_data, x1_vec, y0_vec, lp->H, lp->W);
      v11_vec = gridsample2d_getpixel_vec(X_data, x1_vec, y1_vec, lp->H, lp->W);

      // 4-point bilinear: weights in bf16, final sum in float32 accumulator
      vecf16 w00 = static_cast<vecf16>(aie::mul(wxsub_vec, wysub_vec));
      vecf16 w01 = static_cast<vecf16>(aie::mul(wxsub_vec, wy_vec));
      vecf16 w10 = static_cast<vecf16>(aie::mul(wx_vec, wysub_vec));
      vecf16 w11 = static_cast<vecf16>(aie::mul(wx_vec, wy_vec));

      auto acc_out = aie::mul(w00, v00_vec);
      acc_out = aie::mac(acc_out, w01, v01_vec);
      acc_out = aie::mac(acc_out, w10, v10_vec);
      acc_out = aie::mac(acc_out, w11, v11_vec);
      Y_vec = static_cast<vecf16>(acc_out);

      // --- Output store ---
      if (i < N) {
        aie::store_v(Y_data + i, Y_vec);
      } else {
        for (k = 0; k < M; ++k) {
          Y_data[i + k] = Y_vec[k];
        }
      }
    }
  } else // Nearest
  {
    for (i = 0; i < lp->HW_grid_sub; i += GS2D_VECTOR_LEN) {
      if (i < N) {
        vecf16 grid_lo = aie::concat(aie::load_v<8>(grid_data + 2 * i),
                                     aie::load_v<8>(grid_data + 2 * i + 8));
        vecf16 grid_hi = aie::concat(aie::load_v<8>(grid_data + 2 * i + 16),
                                     aie::load_v<8>(grid_data + 2 * i + 24));
        auto unzipped = aie::interleave_unzip(grid_lo, grid_hi, 1);
        x_norm_vec = unzipped.first;
        y_norm_vec = unzipped.second;
      } else {
        for (k = 0; k < M; ++k) {
          x_norm_vec[k] = grid_data[2 * i + 2 * k];
          y_norm_vec[k] = grid_data[2 * i + 2 * k + 1];
        }
      }

      x_vec =
          static_cast<vecf16>(aie::add(aie::mul(x_norm_vec, scaleX), biasX));
      y_vec =
          static_cast<vecf16>(aie::add(aie::mul(y_norm_vec, scaleY), biasY));

      x0_vec = aie::to_fixed_floor<int16_t>(
          static_cast<vecf16>(aie::add(x_vec, cnst0_5f)), 0);
      y0_vec = aie::to_fixed_floor<int16_t>(
          static_cast<vecf16>(aie::add(y_vec, cnst0_5f)), 0);
      Y_vec = gridsample2d_getpixel_vec(X_data, x0_vec, y0_vec, lp->H, lp->W);

      if (i < N) {
        aie::store_v(Y_data + i, Y_vec);
      } else {
        for (k = 0; k < M; ++k) {
          Y_data[i + k] = Y_vec[k];
        }
      }
    }
  }
}

static constexpr int gridsample2d_lp_size = 9;

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) void gridsample2d_kernel(
    adf::input_buffer_conf<dtype_ifm, adf::bpc_async_0d> &__restrict ifm,
    adf::input_buffer_conf<dtype_wts, adf::bpc_async_0d> &__restrict wts,
    adf::output_buffer_conf<dtype_ofm, adf::bpc_async_0d> &__restrict ofm,
    const uint32_t (&lp_params)[gridsample2d_lp_size]) {
#ifdef __X86SIM__
  static int32_t iter = -1;
  iter++;
#endif

  gridsample2d_lpstruct_t lp;

  lp.mode = lp_params[0];
  lp.align_corners = lp_params[1];
  lp.kernelratio_ifm = lp_params[2];
  lp.kernelratio_wts = lp_params[3];
  lp.kernelratio_ofm = lp_params[4];
  lp.H = lp_params[5];
  lp.W = lp_params[6];
  lp.HW_grid = (uint32_t)lp_params[7] * (uint32_t)lp_params[8];
  lp.HW_grid_sub = lp.HW_grid / ROWS;

  static uint16_t iter_ifm = 0;
  static uint16_t iter_wts = 0;
  static uint16_t iter_ofm = 0;

  static uint16_t core_row = aie::tile::current().id().row % 4;
  static uint16_t core_col = aie::tile::current().id().col % 4;

#ifdef __X86SIM__
  std::cout << "Iter: " + std::to_string(iter) +
                   ", Core Row: " + std::to_string(core_row) +
                   ", Core Col: " + std::to_string(core_col) + "\n";
#endif

  if (iter_ifm == 0)
    ifm.acquire();
  if (iter_wts == 0)
    wts.acquire();
  if (iter_ofm == 0)
    ofm.acquire();

  const uint32_t col_offset = (uint32_t)(lp.HW_grid_sub * core_col);
  const uint32_t row_offset = (uint32_t)(lp.HW_grid_sub * core_row);

  dtype_ifm *X_data = (dtype_ifm *)ifm.data();
  dtype_wts *grid_data = ((dtype_wts *)wts.data()) + (row_offset * 2);
  dtype_ofm *Y_data = ((dtype_ofm *)ofm.data());

  compute(X_data, grid_data, Y_data, &lp);

  iter_ifm++;
  if (iter_ifm >= lp.kernelratio_ifm) {
    iter_ifm = 0;
    ifm.release();
  }

  iter_wts++;
  if (iter_wts >= lp.kernelratio_wts) {
    iter_wts = 0;
    wts.release();
  }

  iter_ofm++;
  if (iter_ofm >= lp.kernelratio_ofm) {
    iter_ofm = 0;
    ofm.release();
  }
}

} // namespace custom_ops
