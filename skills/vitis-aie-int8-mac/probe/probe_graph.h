//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//

#ifndef PROBE_GRAPH_H
#define PROBE_GRAPH_H

#include <adf.h>
#include "probe.h"

using namespace adf;

class ProbeGraph : public graph
{
public:
    kernel      k;
    output_plio o;

    ProbeGraph()
    {
        k = kernel::create(probe);
        o = output_plio::create("out", plio_64_bits, "data/out.txt");

        connect(k.out[0], o.in[0]);
        dimensions(k.out[0]) = { 16 };

        source(k)         = "probe.cpp";
        runtime<ratio>(k) = 0.9;
    }
};

#endif // PROBE_GRAPH_H
