// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Two-stage FIR chain. PL feeds DataIn1 and reads DataOut1.
// This file shows the graph and PLIO names only. The kernel source is not included.
// Do not compile this file.
#pragma once
#include <adf.h>

using namespace adf;

class fir_graph : public graph {
public:
    input_plio  in;
    output_plio out;
    kernel      fir_stage0;
    kernel      fir_stage1;

    fir_graph() {
        in  = input_plio::create("DataIn1", plio_64_bits, "data/input.txt", 312.5);
        out = output_plio::create("DataOut1", plio_64_bits, "data/output.txt", 312.5);

        // fir_sym_16tap names the kernel. This file does not declare it.
        fir_stage0 = kernel::create(fir_sym_16tap);
        fir_stage1 = kernel::create(fir_sym_16tap);

        connect(in.out[0], fir_stage0.in[0]);
        connect(fir_stage0.out[0], fir_stage1.in[0]);
        connect(fir_stage1.out[0], out.in[0]);

        dimensions(fir_stage0.in[0])  = {256};
        dimensions(fir_stage0.out[0]) = {256};
        dimensions(fir_stage1.in[0])  = {256};
        dimensions(fir_stage1.out[0]) = {256};

        runtime<ratio>(fir_stage0) = 0.8;
        runtime<ratio>(fir_stage1) = 0.8;
    }
};
