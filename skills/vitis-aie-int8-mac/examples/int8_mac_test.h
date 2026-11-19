//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//

#pragma once

#include <adf.h>
#include <aie_api/aie.hpp>

using namespace adf;

// Output buffer byte 0: 0 = all pass, non-zero = fail count.
static constexpr int RESULT_SAMPLES = 8;  // one 64-bit PLIO word

class Int8MacTest {
public:
    void run(output_buffer<int8, extents<RESULT_SAMPLES>>& __restrict result);

    static void registerKernelClass() {
        REGISTER_FUNCTION(Int8MacTest::run);
    }
};
