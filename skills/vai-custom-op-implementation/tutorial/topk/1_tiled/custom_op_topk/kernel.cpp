// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>

using namespace adf;

namespace custom_ops {
// Size of the lp_params that the tiling defines.
static constexpr int topk_kernel_lp_size = 3;

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void
topk_kernel(adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
            adf::output_buffer_conf<dtype_ofm, bpc_async_0d> &__restrict ofm,
            const uint32_t (&lp_params)[topk_kernel_lp_size]) {

  // The tiling hands the lp_params here:
  // lp_params[0] = tile size (elements per tile)
  // lp_params[1] = number of tiles per batch (how many IFM tiles we receive
  // before outputting) lp_params[2] = k (number of top elements to select per
  // batch)
  static int current_tile = 0;

  int tile_size = (int)lp_params[0];
  int tiles_per_batch = (int)lp_params[1];
  int k = (int)lp_params[2];

  // IFM is sync_0d, so it's automatically acquired/released each call
  dtype_ifm *in = ifm.data();

  // On first tile of a batch, acquire the OFM buffer and initialize to -inf
  if (current_tile == 0) {
    ofm.acquire();
  }
  dtype_ofm *out = ofm.data();
  if (current_tile == 0) {
    uint16_t neg_infinity = 0xff80; /*-inf for bfloat16*/
    auto bf_neg_infinity = *(bfloat16 *)&neg_infinity;
    for (int i = 0; i < k; i++) {
      out[i] = bf_neg_infinity;
    }
  }

  // out is sorted, so the minimum is at the end
  dtype_ofm min_val = out[k - 1];
  for (int i = 0; i < tile_size; i++) {
    dtype_ifm current_val = in[i];

    // Early continue if current_val is less than the minimum of top-k
    if (current_val <= min_val)
      continue;

    // Find insertion position and shift elements to maintain sorted order
    int insert_pos = k - 1;
    for (int j = k - 2; j >= 0; j--) {
      if (out[j] < current_val) {
        insert_pos = j;
      } else {
        break;
      }
    }

    // Shift elements down to make room for insertion
    for (int j = k - 1; j > insert_pos; j--) {
      out[j] = out[j - 1];
    }

    // Insert the new value
    out[insert_pos] = current_val;
    min_val = out[k - 1];
  }

  // After receiving all tiles for this batch, release OFM
  if (++current_tile == tiles_per_batch) {
    ofm.release();
    current_tile = 0;
  }
}
} // namespace custom_ops
