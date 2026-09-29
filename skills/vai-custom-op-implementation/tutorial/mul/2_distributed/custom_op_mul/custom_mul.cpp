// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>
#include <aie_api/aie.hpp>

namespace custom_ops {

// Size of the lp_params that the tiling defines.
static constexpr int mul_kernel_lp_size = 1;

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) void
mul_kernel(adf::input_buffer_conf<dtype_ifm, adf::bpc_sync_0d> &__restrict a,
           adf::input_buffer_conf<dtype_wts, adf::bpc_sync_0d> &__restrict b,
           adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict out,
           const uint32_t (&lp_params)[mul_kernel_lp_size]) {

  auto *a_data = (dtype_ifm *__restrict)(a.data());
  auto *b_data = (dtype_wts *__restrict)(b.data());
  auto *out_data = (dtype_ofm *__restrict)(out.data());

  static uint16_t core_row = aie::tile::current().id().row % 4;
  static uint16_t core_col = aie::tile::current().id().col % 4;

  // Figure out where our data is.
  // The AIE is a rectangular grid of AIE cores, and the VAIML overlay we work
  // with imposes that data is broadcast along columns (for the a variable)
  // and along rows (for the b variable). Therefore, all cores along a column
  // have the same data but must process only one part of it; the same applies
  // to the columns.
  // It's fairly simple to determine where our data is: each core processes the
  // same amount of data, tile_size elements; so the 1st core in a column works
  // on A[0..tile_size-1], the 2nd on A[tile_size..2*tile_size-1], and so on.
  // The same mechanism is used for columns.
  const uint32_t tile_size = lp_params[0];
  const uint32_t a_offset = tile_size * core_row;
  const uint32_t b_offset = tile_size * core_col;

  for (unsigned i = 0; i < tile_size; ++i) {
    out_data[i] = a_data[a_offset + i] * b_data[b_offset + i];
  }
}

} // namespace custom_ops
