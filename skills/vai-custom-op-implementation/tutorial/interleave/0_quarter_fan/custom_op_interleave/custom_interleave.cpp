/*
    Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
    SPDX-License-Identifier: MIT

    interleave kernel -- pure data-movement copy.

    Each core receives (column-broadcast) the c-th quarter of the four inputs
    packed as [4, Q] = [A_q | B_q | C_q | D_q]. The 4 cores of a column split
    that quarter across rows: this core (row r) copies, for each of the 4
    variables, the SEG-element segment at offset r*SEG, producing a contiguous
    [4*SEG] tile [A_seg | B_seg | C_seg | D_seg]. All the interesting behaviour
    is in the tiling's L2 read pattern; the kernel is just a strided copy.
*/

#include <adf.h>
#include <aie_api/aie.hpp>

using namespace adf;

namespace custom_ops {

static constexpr int interleave_kernel_lp_size = 2;
static constexpr int NVARS = 4;

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void interleave_kernel(
    adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
    adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[interleave_kernel_lp_size]) {

  const uint32_t Q = lp_params[0];   // quarter length (per var, per column)
  const uint32_t SEG = lp_params[1]; // per-core segment length (per var)

  const uint16_t core_row = aie::tile::current().id().row;
  const uint32_t row_offset = (uint32_t)SEG * core_row;

  dtype_ifm *__restrict in = ifm.data();  // [NVARS, Q] flattened
  dtype_ofm *__restrict out = ofm.data(); // [NVARS, SEG] flattened

  for (uint32_t v = 0; v < NVARS; ++v) {
    const dtype_ifm *__restrict src = in + v * Q + row_offset;
    dtype_ofm *__restrict dst = out + v * SEG;
    for (uint32_t j = 0; j < SEG; ++j) {
      dst[j] = src[j];
    }
  }
}

} // namespace custom_ops
