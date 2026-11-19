// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#pragma once

#include <adf.h>
#include "src/kernels.h"

// One 32-bit PLIO carries packets for two kernels: pktsplit routes each packet
// by its ID, and pktmerge combines the results on one output PLIO.
class PacketGraph : public adf::graph {
public:
    adf::input_plio in;
    adf::output_plio out;
    adf::kernel offset;
    adf::kernel neg;
    adf::pktsplit<2> split;
    adf::pktmerge<2> merge;

    PacketGraph()
    {
        in = adf::input_plio::create("in", adf::plio_32_bits, "in.txt");
        out = adf::output_plio::create("out", adf::plio_32_bits, "out.txt");

        offset = adf::kernel::create(::add_offset);
        neg = adf::kernel::create(::negate);
        adf::source(offset) = "src/kernels.cpp";
        adf::source(neg) = "src/kernels.cpp";
        adf::runtime<adf::ratio>(offset) = 0.9;
        adf::runtime<adf::ratio>(neg) = 0.9;

        split = adf::pktsplit<2>::create();
        merge = adf::pktmerge<2>::create();
        adf::connect<adf::pktstream>(in.out[0], split.in[0]);
        adf::connect<adf::pktstream>(split.out[0], offset.in[0]);
        adf::connect<adf::pktstream>(split.out[1], neg.in[0]);
        adf::connect<adf::pktstream>(offset.out[0], merge.in[0]);
        adf::connect<adf::pktstream>(neg.out[0], merge.in[1]);
        adf::connect<adf::pktstream>(merge.out[0], out.in[0]);
    }
};
