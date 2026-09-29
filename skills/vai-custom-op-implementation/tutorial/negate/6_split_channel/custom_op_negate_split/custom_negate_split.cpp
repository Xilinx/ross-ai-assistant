/*
    Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
    SPDX-License-Identifier: MIT

    split-channel negate kernel -- elementwise negate.

    Each core receives (column-broadcast) the c-th quarter of the input, [Q]
    elements. The 4 cores of a column split that quarter across rows: this core
    (row r) negates the SEG-element segment at offset r*SEG, producing a
    contiguous [SEG] tile. The interesting behaviour is in the tiling, which
    stages the single L3 input into one L2 buffer over two DMA channels; the
    kernel is just a negating copy of its own segment.
*/

#include <adf.h>
#include <aie_api/aie.hpp>

using namespace adf;

namespace custom_ops {

static constexpr int negate_split_kernel_lp_size = 2;

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void negate_split_kernel(
    adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
    adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[negate_split_kernel_lp_size]) {

  const uint32_t SEG = lp_params[1]; // per-core segment length

  const uint16_t core_row = aie::tile::current().id().row;
  const uint32_t row_offset = (uint32_t)SEG * core_row;

  dtype_ifm *__restrict in = ifm.data() + row_offset; // [Q] broadcast
  dtype_ofm *__restrict out = ofm.data();             // [SEG]

  for (uint32_t j = 0; j < SEG; ++j) {
    out[j] = -in[j];
  }
}

} // namespace custom_ops
