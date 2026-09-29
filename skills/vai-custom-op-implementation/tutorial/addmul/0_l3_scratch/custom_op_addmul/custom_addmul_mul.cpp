// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

namespace custom_ops {

static constexpr int mul_kernel_lp_size = 1;

// Phase 1 of the two-phase addmul op: out = t * C.
//
// Same shape as phase 0, only the operands differ: the column broadcast carries
// the intermediate t that phase 0 left in the DDR scratch buffer, and the row
// broadcast carries the third operand C, read straight from DDR.
template <typename dtype_t, typename dtype_c, typename dtype_ofm>
__attribute__((noinline)) void
mul_kernel(adf::input_buffer_conf<dtype_t, adf::bpc_sync_0d> &__restrict t_in,
           adf::input_buffer_conf<dtype_c, adf::bpc_sync_0d> &__restrict c_in,
           adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict ofm,
           const uint32_t (&lp_params)[mul_kernel_lp_size]) {
  const uint32_t SEG = lp_params[0];

  const uint16_t core_row = aie::tile::current().id().row;
  const uint16_t core_col = aie::tile::current().id().col;

  auto *t = (dtype_t *__restrict)t_in.data() + SEG * core_row;
  auto *c = (dtype_c *__restrict)c_in.data() + SEG * core_col;
  auto *out = (dtype_ofm *__restrict)ofm.data();

  for (unsigned i = 0; i < SEG; ++i)
    out[i] = (dtype_ofm)(t[i] * c[i]);
}

} // namespace custom_ops
