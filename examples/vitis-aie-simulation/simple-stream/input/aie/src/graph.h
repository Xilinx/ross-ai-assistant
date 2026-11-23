// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <adf.h>
#include "kernel.h"

using namespace adf;

class simpleStream : public graph {
    private : 
    kernel first;
    public :
    input_plio in,in1;
    output_plio out,out1;

    simpleStream(){
        in = input_plio::create("din1",plio_32_bits,"data/input.txt");
        
        out = output_plio::create("dout1",plio_32_bits,"data/output.txt");
       

        first = kernel::create(simple_stream);

        connect(in.out[0],first.in[0]);
        
        connect(first.out[0],out.in[0]);
        
       //1 
        source(first)="aie/src/kernel.cpp";


        runtime<ratio>(first)=0.1;
    }
};
