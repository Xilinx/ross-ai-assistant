// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#pragma once

#include <adf.h>
#include "src/kernels.h"

class ScaleGraph : public adf::graph {
public:
    adf::input_plio in;
    adf::output_plio out;
    adf::kernel scale;

    ScaleGraph() {
        in = adf::input_plio::create("in", adf::plio_32_bits, "in.txt");
        out = adf::output_plio::create("out", adf::plio_32_bits, "out.txt");

        scale = adf::kernel::create(scale_sub);
        adf::source(scale) = "src/kernels.cpp";
        adf::runtime<ratio>(scale) = 0.9;

        adf::connect<adf::stream>(in.out[0], scale.in[0]);
        adf::connect<adf::stream>(scale.out[0], out.in[0]);
    }
};
