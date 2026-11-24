//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
#include <adf.h>
#include <aie_api/aie.hpp>

// Six int8 MAC calls from a larger design. Each comment states what the call is meant to read.
void review(adf::input_buffer<int8, adf::extents<256>>& in, adf::output_buffer<int32, adf::extents<80>>& out)
{
    const int8* p = in.data();
    const aie::vector<int8, 64> xw = aie::load_v<64>(p);
    const aie::vector<int8, 128> xb = aie::load_v<128>(p + 64);
    const aie::vector<int8, 32> zx = aie::load_v<32>(p + 192);
    const aie::vector<int8, 32> zt = aie::load_v<32>(p + 224);
    int32* q = out.data();

    // C1: lane groups of 4 share the weights in x[0..31]; group g reads the samples z[8g .. 8g+7].
    aie::accum<acc48, 16> c1 = mul16(xw, 0, 0x30303030, 4, 0x3210, zx, 0, 0xCC884400, 2, 0x1010);

    // C2: lane r starts at x[r]; column pairs are 32 bytes apart and wrap around the 128-byte buffer.
    //     Every lane reads the taps z[0..15].
    aie::accum<acc48, 8> c2 = mul8(xb, 0, 0x03020100, 32, 0x2110, zt, 0, 0x00000000, 2, 0x3210);

    // C3: second half of a 32-tap filter: the samples advanced by 16, and the taps z[16..31].
    aie::accum<acc48, 8> c3 = mul8(xb, 32, 0x03020100, 4, 0x2110, zt, 16, 0x00000000, 2, 0x3210);

    // C4: as C1, with a different mini-permute of the weights.
    aie::accum<acc48, 16> c4 = mul16(xw, 0, 0x30303030, 4, 0x3214, zx, 0, 0xCC884400, 2, 0x1010);

    // C5: as C1, but every lane reads the same samples z[0..7].
    aie::accum<acc48, 16> c5 = mul16(xw, 0, 0x30303030, 4, 0x3210, zx, 0, 0x00000000, 34, 0x3210);

    // C6: subtract the cross term: weights from x[32..63], samples starting at z[6].
    c1 = msc16(c1, xw, 32, 0x30303030, 4, 0x3210, zx, 6, 0x00000000, 2, 0x0123);

    aie::store_v(q, c1.to_vector<int32>(0));
    aie::store_v(q + 16, c4.to_vector<int32>(0));
    aie::store_v(q + 32, c5.to_vector<int32>(0));
    aie::store_v(q + 48, c2.to_vector<int32>(0));
    aie::store_v(q + 56, c3.to_vector<int32>(0));
    for (int i = 64; i < 80; i++) q[i] = 0;
}
