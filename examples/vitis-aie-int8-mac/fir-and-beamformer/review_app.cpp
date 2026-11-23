//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
#include <adf.h>

void review(adf::input_buffer<int8, adf::extents<256>>& in, adf::output_buffer<int32, adf::extents<80>>& out);

using namespace adf;

class ReviewGraph : public graph {
public:
    kernel k;
    input_plio pin;
    output_plio pout;
    ReviewGraph()
    {
        k = kernel::create(review);
        pin = input_plio::create("in", plio_64_bits, "data/input.txt");
        pout = output_plio::create("out", plio_64_bits, "data/output.txt");
        connect(pin.out[0], k.in[0]);
        connect(k.out[0], pout.in[0]);
        source(k) = "review_kernel.cpp";
        runtime<ratio>(k) = 0.9;
    }
};

ReviewGraph g;

#if defined(__AIESIM__) || defined(__X86SIM__)
int main()
{
    g.init();
    g.run(1);
    g.end();
    return 0;
}
#endif
