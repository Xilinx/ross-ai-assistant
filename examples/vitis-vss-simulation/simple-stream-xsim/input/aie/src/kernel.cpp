// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <adf.h>
#include "kernel.h"
#include "adf/stream/types.h"
#include <aie_api/aie.hpp>
#include <aie_api/aie_adf.hpp>
#include <aie_api/utils.hpp>
#include <aie_api/operators.hpp>

void simple_stream(input_stream_int32 *in ,output_stream_int32 *out)
{
 int32 val;
 for(int i=0;i<8;i++)
 {

     val=readincr(in);
     writeincr(out,val);
 }
 
}
 
