// Copyright (C) 2025 - 2026 Advanced Micro Devices, Inc. All rights reserved.
// Alternative optimized insertion sort implementation

#include <adf.h>
#include <cassert>
#include <cstring>
using namespace adf;

namespace custom_ops {

constexpr int cse_topk_indices_lp_size = 2;

template <typename dtype_ifm1, typename dtype_ofm>
__attribute__((noinline)) void cse_topk_indices_kernel(
    adf::input_buffer_conf<dtype_ifm1, bpc_sync_0d> &__restrict ifm1,
    adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[cse_topk_indices_lp_size]) {

  const int ifm_num_elems = (int)lp_params[0];
  const int k = (int)lp_params[1];

  dtype_ifm1 *in = ifm1.data();
  int16_t *out = reinterpret_cast<int16_t *>(ofm.data());

  // Initialize with invalid indices
  for (int i = 0; i < k; i++) {
    out[i] = -1;
  }

  int valid_count = 0;

  for (int i = 0; i < ifm_num_elems; i++) {
    dtype_ifm1 current_val = in[i];

    if (valid_count < k) {
      // Still filling the topk array
      int pos = valid_count;
      // Find insertion position (binary search for better cache performance)
      int left = 0, right = valid_count;
      while (left < right) {
        int mid = (left + right) / 2;
        if (in[out[mid]] >= current_val) {
          left = mid + 1;
        } else {
          right = mid;
        }
      }
      pos = left;

      // Shift elements to make room
      for (int j = valid_count; j > pos; j--) {
        out[j] = out[j - 1];
      }
      out[pos] = i;
      valid_count++;
    } else {
      // Array is full, check if current element should replace the smallest
      if (current_val > in[out[k - 1]]) {
        // Find insertion position using binary search
        int left = 0, right = k - 1;
        while (left < right) {
          int mid = (left + right) / 2;
          if (in[out[mid]] >= current_val) {
            left = mid + 1;
          } else {
            right = mid;
          }
        }

        // Shift elements and insert
        for (int j = k - 1; j > left; j--) {
          out[j] = out[j - 1];
        }
        out[left] = i;
      }
    }
  }

  // Fill any remaining slots with zeros if we had fewer than k elements
  for (int i = valid_count; i < k; i++) {
    out[i] = 0;
  }
}

} // namespace custom_ops
