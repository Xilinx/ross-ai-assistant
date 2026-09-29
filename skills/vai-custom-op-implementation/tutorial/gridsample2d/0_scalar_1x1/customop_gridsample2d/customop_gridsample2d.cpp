// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
/*
 *
 * Non-vectorized scalar 1x1 gridsample2d kernel (legacy math).
 * Same LP struct (11 params), async buffers with acquire/release.
 */

#include <adf.h>
#include <aie_api/aie.hpp>

namespace custom_ops {

typedef struct gridsample2d_lpstruct_t {
  uint16_t mode;
  uint16_t align_corners;
  uint16_t kernelratio_ifm;
  uint16_t kernelratio_wts;
  uint16_t kernelratio_ofm;
  uint16_t H;
  uint16_t W;
  uint16_t D;
  uint16_t HW_grid;
  uint16_t HW_grid_sub;
  uint16_t D_grid;
} gridsample2d_lpstruct_t;

static inline bfloat16 getpixel(bfloat16 *__restrict img, int16_t x, int16_t y,
                                uint16_t H, uint16_t W) {
  if (x >= 0 && x < W && y >= 0 && y < H)
    return img[y * W + x];
  return (bfloat16)0.0f;
}

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
static void compute(dtype_ifm *__restrict X, dtype_wts *__restrict grid,
                    dtype_ofm *__restrict Y,
                    gridsample2d_lpstruct_t *__restrict lp) {

  const bfloat16 one = 1.0f;
  const bfloat16 half = 0.5f;

  const bfloat16 scaleX =
      half * (bfloat16)(lp->align_corners ? (lp->W - 1) : lp->W);
  const bfloat16 scaleY =
      half * (bfloat16)(lp->align_corners ? (lp->H - 1) : lp->H);
  const bfloat16 offset =
      half * (bfloat16)(lp->align_corners ? (bfloat16)0 : one);

  for (uint16_t i = 0; i < lp->HW_grid_sub; ++i) {
    bfloat16 gx = grid[2 * i];
    bfloat16 gy = grid[2 * i + 1];

    bfloat16 px =
        (bfloat16)((float)(bfloat16)((float)(gx + one) * (float)scaleX) -
                   (float)offset);
    bfloat16 py =
        (bfloat16)((float)(bfloat16)((float)(gy + one) * (float)scaleY) -
                   (float)offset);

    if (lp->mode == 1) {
      int16_t x0 = (int16_t)(float)px;
      int16_t y0 = (int16_t)(float)py;
      int16_t x1 = x0 + 1;
      int16_t y1 = y0 + 1;

      bfloat16 wx = (bfloat16)((float)px - (float)x0);
      bfloat16 wy = (bfloat16)((float)py - (float)y0);

      bfloat16 v00 = getpixel(X, x0, y0, lp->H, lp->W);
      bfloat16 v01 = getpixel(X, x0, y1, lp->H, lp->W);
      bfloat16 v10 = getpixel(X, x1, y0, lp->H, lp->W);
      bfloat16 v11 = getpixel(X, x1, y1, lp->H, lp->W);

      bfloat16 wxsub = (bfloat16)(1.0f - (float)wx);
      bfloat16 wysub = (bfloat16)(1.0f - (float)wy);

      float result =
          (float)(bfloat16)((float)wxsub *
                            (float)(bfloat16)((float)wysub * (float)v00)) +
          (float)(bfloat16)((float)wxsub *
                            (float)(bfloat16)((float)wy * (float)v01)) +
          (float)(bfloat16)((float)wx *
                            (float)(bfloat16)((float)wysub * (float)v10)) +
          (float)(bfloat16)((float)wx *
                            (float)(bfloat16)((float)wy * (float)v11));

      Y[i] = (bfloat16)result;
    } else {
      int16_t x0 = (int16_t)((float)px + 0.5f);
      int16_t y0 = (int16_t)((float)py + 0.5f);
      Y[i] = getpixel(X, x0, y0, lp->H, lp->W);
    }
  }
}

static constexpr int gridsample2d_lp_size = 11;

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) void gridsample2d_kernel(
    adf::input_buffer_conf<dtype_ifm, adf::bpc_async_0d> &__restrict ifm,
    adf::input_buffer_conf<dtype_wts, adf::bpc_async_0d> &__restrict wts,
    adf::output_buffer_conf<dtype_ofm, adf::bpc_async_0d> &__restrict ofm,
    const uint32_t (&lp_params)[gridsample2d_lp_size]) {

  gridsample2d_lpstruct_t lp;
  lp.mode = lp_params[0];
  lp.align_corners = lp_params[1];
  lp.kernelratio_ifm = lp_params[2];
  lp.kernelratio_wts = lp_params[3];
  lp.kernelratio_ofm = lp_params[4];
  lp.H = lp_params[5];
  lp.W = lp_params[6];
  lp.D = lp_params[7];
  lp.HW_grid = (uint32_t)lp_params[8] * (uint32_t)lp_params[9];
  lp.HW_grid_sub = lp.HW_grid;
  lp.D_grid = lp_params[10];

  static uint16_t iter_ifm = 0;
  static uint16_t iter_wts = 0;
  static uint16_t iter_ofm = 0;

  if (iter_ifm == 0)
    ifm.acquire();
  if (iter_wts == 0)
    wts.acquire();
  if (iter_ofm == 0)
    ofm.acquire();

  compute((dtype_ifm *)ifm.data(), (dtype_wts *)wts.data(),
          (dtype_ofm *)ofm.data(), &lp);

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
