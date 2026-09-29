/*
    Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
    SPDX-License-Identifier: MIT

    concat4 kernel -- innermost 4-way concatenation (element alternation).

    Two input buffers, one per source memory tile (see the tiling):
      ab : [HALF, Q]        A,B for this core's COLUMN, broadcast down the
   column. Laid out as [HALF, ROWS, SEG]; this core (row r) uses the r-th
   SEG-segment: A/B[j] = ab[var*Q + r*SEG + j]. cd : [HALF, COLS*SEG] C,D for
   this core's ROW, broadcast across the row. Laid out as [HALF, COLS, SEG];
   this core (col c) uses the c-th SEG-segment: C/D[j] = cd[var*Q + c*SEG + j]
                            (COLS*SEG == Q).

    The core (col c, row r) owns global input element n = c*Q + r*SEG + j, and
    global output element NVARS*n + v is input v's element n. So the core writes
   a contiguous [NVARS*SEG] tile alternating the four inputs:

        out[NVARS*j + 0] = A[j]   out[NVARS*j + 1] = B[j]
        out[NVARS*j + 2] = C[j]   out[NVARS*j + 3] = D[j]

    A kernel (rather than a pure DMA copy) is required because the element word
    size is bf16 (16 bits, below 32), so the alternation cannot be expressed as
   a DMA access pattern. Two inputs (rather than one) are required on the STX
    overlay: each memory tile has too few L3->L2 ports to merge all four inputs,
    so A,B and C,D live on separate tiles and reach the core over the column-
   and row-broadcast channels respectively.
*/

#include <adf.h>
#include <aie_api/aie.hpp>

using namespace adf;

namespace custom_ops {

static constexpr int concat4_kernel_lp_size = 2;
static constexpr int HALF = 2;

template <typename dtype_ab, typename dtype_cd, typename dtype_ofm>
__attribute__((noinline)) void
concat4_kernel(adf::input_buffer_conf<dtype_ab, bpc_sync_0d> &__restrict ab,
               adf::input_buffer_conf<dtype_cd, bpc_sync_0d> &__restrict cd,
               adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
               const uint32_t (&lp_params)[concat4_kernel_lp_size]) {

  const uint32_t Q = lp_params[0];   // quarter length (per var, per column/row)
  const uint32_t SEG = lp_params[1]; // per-core segment length (per var)

  const uint16_t core_row = aie::tile::current().id().row;
  const uint16_t core_col = aie::tile::current().id().col;
  const uint32_t ab_off = (uint32_t)SEG * core_row; // this core's row segment
  const uint32_t cd_off = (uint32_t)SEG * core_col; // this core's col segment

  dtype_ab *__restrict ab_in = ab.data(); // [HALF, Q]
  dtype_cd *__restrict cd_in = cd.data(); // [HALF, Q]
  dtype_ofm *__restrict out = ofm.data(); // [SEG, NVARS] flattened

  for (uint32_t j = 0; j < SEG; ++j) {
    // A, B from the column buffer; C, D from the row buffer.
    out[j * 4 + 0] = ab_in[0 * Q + ab_off + j];
    out[j * 4 + 1] = ab_in[1 * Q + ab_off + j];
    out[j * 4 + 2] = cd_in[0 * Q + cd_off + j];
    out[j * 4 + 3] = cd_in[1 * Q + cd_off + j];
  }
}

} // namespace custom_ops
