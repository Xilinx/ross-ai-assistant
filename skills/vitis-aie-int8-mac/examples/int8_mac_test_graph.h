//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//

#pragma once

#include <adf.h>
#include "int8_mac_test.h"

using namespace adf;

class Int8MacTestGraph : public graph {
public:
    kernel k;
    output_plio out_port;

    Int8MacTestGraph()
    {
        k = kernel::create_object<Int8MacTest>();

        out_port = output_plio::create("out_port",
                                       plio_64_bits,
                                       "data/output.txt");

        connect<>(k.out[0], out_port.in[0]);
        dimensions(k.out[0]) = {RESULT_SAMPLES};

        source(k)          = "int8_mac_test.cpp";
        runtime<ratio>(k) = 0.9;
    }
};
