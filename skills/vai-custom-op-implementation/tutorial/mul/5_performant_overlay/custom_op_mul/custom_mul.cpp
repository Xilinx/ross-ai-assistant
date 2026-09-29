// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

namespace custom_ops {

static constexpr int mul_kernel_lp_size = 1;

// Elementwise product on the 6x4x4 performant overlay.
//
// The performant overlay broadcasts each input to a *pair* of cores rather than
// a whole column/row.
template <typename dtype_a, typename dtype_b, typename dtype_ofm>
__attribute__((noinline)) void
mul_kernel(adf::input_buffer_conf<dtype_a, adf::bpc_sync_0d> &__restrict a_in,
           adf::input_buffer_conf<dtype_b, adf::bpc_sync_0d> &__restrict b_in,
           adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict ofm,
           const uint32_t (&lp_params)[mul_kernel_lp_size]) {
  const uint32_t SEG = lp_params[0];

  // Modular indices so the kernel is agnostic to any overlay row/col offset.
  const uint16_t core_row = aie::tile::current().id().row % 4;
  const uint16_t core_col = aie::tile::current().id().col % 4;

  auto *a = (dtype_a *__restrict)a_in.data() + SEG * (core_row % 2);
  auto *b = (dtype_b *__restrict)b_in.data() + SEG * (core_col % 2);
  auto *out = (dtype_ofm *__restrict)ofm.data();

  for (unsigned i = 0; i < SEG; ++i)
    out[i] = (dtype_ofm)(a[i] * b[i]);
}

} // namespace custom_ops
