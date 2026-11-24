// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "kernels.h"

// Reads one sample and writes 3 * sample + 1.
void scale_offset(hls::stream<int32_t> &in, hls::stream<int32_t> &out)
{
    int32_t x = in.read();
    out.write(3 * x + 1);
}
