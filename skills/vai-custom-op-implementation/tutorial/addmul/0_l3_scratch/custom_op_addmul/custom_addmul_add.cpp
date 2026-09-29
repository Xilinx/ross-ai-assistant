// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

namespace custom_ops {

static constexpr int add_kernel_lp_size = 1;

// Phase 0 of the two-phase addmul op: t = A + B.
//
// A arrives on the column broadcast as [ROWS, SEG] -- this core takes row
// `core_row`. B arrives on the row broadcast as [COLS, SEG] -- this core takes
// column `core_col`. Both name the same global elements.
//
// The result is a plain [SEG] tile, which the tiling gathers to L2 and then
// spills to a DDR scratch buffer for phase 1 to read back.
template <typename dtype_a, typename dtype_b, typename dtype_ofm>
__attribute__((noinline)) void
add_kernel(adf::input_buffer_conf<dtype_a, adf::bpc_sync_0d> &__restrict a_in,
           adf::input_buffer_conf<dtype_b, adf::bpc_sync_0d> &__restrict b_in,
           adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict ofm,
           const uint32_t (&lp_params)[add_kernel_lp_size]) {
  const uint32_t SEG = lp_params[0];

  const uint16_t core_row = aie::tile::current().id().row;
  const uint16_t core_col = aie::tile::current().id().col;

  auto *a = (dtype_a *__restrict)a_in.data() + SEG * core_row;
  auto *b = (dtype_b *__restrict)b_in.data() + SEG * core_col;
  auto *out = (dtype_ofm *__restrict)ofm.data();

  for (unsigned i = 0; i < SEG; ++i)
    out[i] = (dtype_ofm)(a[i] + b[i]);
}

} // namespace custom_ops
