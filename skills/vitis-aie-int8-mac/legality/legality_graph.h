//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//

#ifndef LEGALITY_GRAPH_H
#define LEGALITY_GRAPH_H

#include <adf.h>
#include "legality.h"

using namespace adf;

class LegalityGraph : public graph
{
public:
    kernel      k;
    output_plio o;

    LegalityGraph()
    {
        k = kernel::create(legality);
        o = output_plio::create("out", plio_64_bits, "data/out.txt");

        connect(k.out[0], o.in[0]);
        dimensions(k.out[0]) = { 16 };

        source(k)         = "legality.cpp";
        runtime<ratio>(k) = 0.9;
    }
};

#endif // LEGALITY_GRAPH_H
