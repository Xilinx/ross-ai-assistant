// Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
#include <adf.h>

using namespace adf;

namespace custom_ops {
// Size of the lp_params that the tiling defines.
static constexpr int topk_kernel_lp_size = 3;

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void
topk_kernel(adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
            adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
            const uint32_t (&lp_params)[topk_kernel_lp_size]) {

  // The tiling hands the lp_params here:
  // lp_params[0] is the total number of elements in the input
  // lp_params[1] is the K value (number of top elements to select per batch)
  // lp_params[2] is the batch size
  int total_elements = (int)lp_params[0];
  int k = (int)lp_params[1];
  int batch_size = (int)lp_params[2];

  // Handles to input and output.
  dtype_ifm *in = ifm.data();
  dtype_ofm *out = ofm.data();

  // Calculate width per batch
  int width = total_elements / batch_size;

  // Process each batch separately
  for (int batch = 0; batch < batch_size; batch++) {
    dtype_ifm *batch_in = in + (batch * width);
    dtype_ofm *batch_out = out + (batch * k);

    // Selection sort to find top K elements in this batch.
    for (int i = 0; i < k && i < width; i++) {
      // Find the maximum element in the remaining unsorted portion
      int max_idx = i;
      dtype_ifm max_val = batch_in[i];

      for (int j = i + 1; j < width; j++) {
        if (batch_in[j] > max_val) {
          max_val = batch_in[j];
          max_idx = j;
        }
      }

      // Swap the found maximum element with the current position
      if (max_idx != i) {
        dtype_ifm temp = batch_in[i];
        batch_in[i] = batch_in[max_idx];
        batch_in[max_idx] = temp;
      }

      // Write the top-k element to output
      batch_out[i] = batch_in[i];
    }
  }
}
} // namespace custom_ops
