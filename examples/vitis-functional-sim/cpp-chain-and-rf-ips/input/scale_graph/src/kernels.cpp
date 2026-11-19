// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "kernels.h"

// Writes 2 * sample - 5.
void scale_sub(input_stream<int32> *in, output_stream<int32> *out)
{
    writeincr(out, 2 * readincr(in) - 5);
}
