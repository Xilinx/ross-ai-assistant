// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "kernels.h"

// Reads one TLAST-delimited packet of int32 samples and writes its maximum.
void packet_max(hls::stream<ap_axis<32, 0, 0, 0>> &in, hls::stream<int32_t> &out)
{
#pragma HLS INTERFACE mode=axis port=in
#pragma HLS INTERFACE mode=ap_none port=out

    ap_axis<32, 0, 0, 0> beat = in.read();
    int32_t best = beat.data.to_int();
    while (!beat.last) {
#pragma HLS PIPELINE II=1
        beat = in.read();
        int32_t v = beat.data.to_int();
        if (v > best) {
            best = v;
        }
    }
    out.write(best);
}
