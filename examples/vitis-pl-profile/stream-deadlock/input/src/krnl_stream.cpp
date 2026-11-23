// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Kernel-to-kernel streaming pair (HLS). The log was captured from -t hw_emu on xilinx_vck190_base_202610_1.
// This directory has no platform, host program, or xclbin. Do not build this file.
#include <ap_axi_sdata.h>
#include <hls_stream.h>

typedef ap_axiu<32, 0, 0, 0> pkt;

extern "C" void krnl_mm2s(const int* in, hls::stream<pkt>& out, int n) {
#pragma HLS INTERFACE m_axi port=in offset=slave bundle=gmem0
#pragma HLS INTERFACE axis port=out
    for (int i = 0; i < n; ++i) {
#pragma HLS PIPELINE II=1
        pkt p;
        p.data = in[i];
        p.last = (i == n - 1);
        out.write(p);
    }
}

extern "C" void krnl_s2mm(hls::stream<pkt>& in, int* out, int n) {
#pragma HLS INTERFACE axis port=in
#pragma HLS INTERFACE m_axi port=out offset=slave bundle=gmem1
    for (int i = 0; i < n; ++i) {
#pragma HLS PIPELINE II=2
        pkt a = in.read();
        pkt b = in.read();
        out[i] = a.data + b.data;
    }
}
