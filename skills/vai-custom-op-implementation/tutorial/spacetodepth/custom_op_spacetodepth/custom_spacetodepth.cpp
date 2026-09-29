// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
/**
 * SpaceToDepth Custom Op Kernel (4x4 distributed, HC/8W8 vectorized output)
 *
 * Rearranges spatial data into the channel dimension with blocksize=2.
 * Input:  [C=3, H=8, W=320] (shared among 4 cores in column)
 * Output: [H=1, C_VEC=2, W=160, VEC=8] - HC/8W8 vectorized format
 *
 * Each core processes 2 input rows based on its row ID:
 *   Output row 0: input rows 0-1
 *   Output row 1: input rows 2-3
 *   Output row 2: input rows 4-5
 *   Output row 3: input rows 6-7
 *
 * ONNX SpaceToDepth DCR channel ordering (default mode):
 *   c_out = bh * blocksize * C_in + bw * C_in + c_in
 * For C_in=3, blocksize=2: c_out = bh*6 + bw*3 + c_in
 *   Channel  0: bh=0,bw=0,cin=0  Channel  1: bh=0,bw=0,cin=1
 *   Channel  2: bh=0,bw=0,cin=2  Channel  3: bh=0,bw=1,cin=0
 *   Channel  4: bh=0,bw=1,cin=1  Channel  5: bh=0,bw=1,cin=2
 *   Channel  6: bh=1,bw=0,cin=0  Channel  7: bh=1,bw=0,cin=1
 *   Channel  8: bh=1,bw=0,cin=2  Channel  9: bh=1,bw=1,cin=0
 *   Channel 10: bh=1,bw=1,cin=1  Channel 11: bh=1,bw=1,cin=2
 *
 * Packed into HC/8W8 vectors (12 channels padded to 16, 2 groups of 8):
 *   Group 0 (c_vec=0): ch0-ch7 (see above)
 *   Group 1 (c_vec=1): ch8-ch11, pad, pad, pad, pad
 */

#include <adf.h>
#include <aie_api/aie.hpp>

namespace custom_ops {

static constexpr int spacetodepth_kernel_lp_size = 2;

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void spacetodepth_kernel(
    adf::input_buffer_conf<dtype_ifm, adf::bpc_sync_0d> &__restrict a,
    adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict out,
    const uint32_t (&lp_params)[spacetodepth_kernel_lp_size]) {

  auto *ifm = (dtype_ifm *__restrict)(a.data());
  auto *ofm = (dtype_ofm *__restrict)(out.data());

  static constexpr int INPUT_CHANNELS = 3;
  static int INPUT_HEIGHT = lp_params[0]; // Shared rows in the column (8)
  static int INPUT_WIDTH = lp_params[1];  // Input width (320)
  static constexpr int OUTPUT_C_VEC = 2;
  static int OUTPUT_WIDTH = INPUT_WIDTH / 2; // 160
  static constexpr int VEC_SIZE = 8;

  // Get core row ID to determine which 2 rows to process
  static uint16_t core_row = aie::tile::current().id().row % 4;
  uint16_t row_idx = core_row % 4;

#define IFM_IDX(c, h, w)                                                       \
  ((c) * (INPUT_HEIGHT * INPUT_WIDTH) + (h) * INPUT_WIDTH + (w))
#define OFM_IDX(c_vec, w, v)                                                   \
  ((c_vec) * (OUTPUT_WIDTH * VEC_SIZE) + (w) * VEC_SIZE + (v))

  unsigned even_row = row_idx * 2;
  unsigned odd_row = row_idx * 2 + 1;

  for (int out_col = 0; out_col < OUTPUT_WIDTH; out_col++) {
    int even_col = out_col * 2;
    int odd_col = out_col * 2 + 1;

    // Group 0 (c_vec=0): channels 0-7 in ONNX SpaceToDepth DCR order
    // DCR: c_out = bh * bs * Cin + bw * Cin + cin
    // ch0: bh=0,bw=0,cin=0  ch1: bh=0,bw=0,cin=1  ch2: bh=0,bw=0,cin=2
    // ch3: bh=0,bw=1,cin=0  ch4: bh=0,bw=1,cin=1  ch5: bh=0,bw=1,cin=2
    // ch6: bh=1,bw=0,cin=0  ch7: bh=1,bw=0,cin=1
    ofm[OFM_IDX(0, out_col, 0)] =
        ifm[IFM_IDX(0, even_row, even_col)]; // bh=0,bw=0,cin=0
    ofm[OFM_IDX(0, out_col, 1)] =
        ifm[IFM_IDX(1, even_row, even_col)]; // bh=0,bw=0,cin=1
    ofm[OFM_IDX(0, out_col, 2)] =
        ifm[IFM_IDX(2, even_row, even_col)]; // bh=0,bw=0,cin=2
    ofm[OFM_IDX(0, out_col, 3)] =
        ifm[IFM_IDX(0, even_row, odd_col)]; // bh=0,bw=1,cin=0
    ofm[OFM_IDX(0, out_col, 4)] =
        ifm[IFM_IDX(1, even_row, odd_col)]; // bh=0,bw=1,cin=1
    ofm[OFM_IDX(0, out_col, 5)] =
        ifm[IFM_IDX(2, even_row, odd_col)]; // bh=0,bw=1,cin=2
    ofm[OFM_IDX(0, out_col, 6)] =
        ifm[IFM_IDX(0, odd_row, even_col)]; // bh=1,bw=0,cin=0
    ofm[OFM_IDX(0, out_col, 7)] =
        ifm[IFM_IDX(1, odd_row, even_col)]; // bh=1,bw=0,cin=1

    // Group 1 (c_vec=1): channels 8-11 + padding
    // ch8: bh=1,bw=0,cin=2  ch9: bh=1,bw=1,cin=0
    // ch10: bh=1,bw=1,cin=1  ch11: bh=1,bw=1,cin=2
    ofm[OFM_IDX(1, out_col, 0)] =
        ifm[IFM_IDX(2, odd_row, even_col)]; // bh=1,bw=0,cin=2
    ofm[OFM_IDX(1, out_col, 1)] =
        ifm[IFM_IDX(0, odd_row, odd_col)]; // bh=1,bw=1,cin=0
    ofm[OFM_IDX(1, out_col, 2)] =
        ifm[IFM_IDX(1, odd_row, odd_col)]; // bh=1,bw=1,cin=1
    ofm[OFM_IDX(1, out_col, 3)] =
        ifm[IFM_IDX(2, odd_row, odd_col)]; // bh=1,bw=1,cin=2
    ofm[OFM_IDX(1, out_col, 4)] = 0;       // padding
    ofm[OFM_IDX(1, out_col, 5)] = 0;       // padding
    ofm[OFM_IDX(1, out_col, 6)] = 0;       // padding
    ofm[OFM_IDX(1, out_col, 7)] = 0;       // padding
  }

#undef IFM_IDX
#undef OFM_IDX
}

} // namespace custom_ops
