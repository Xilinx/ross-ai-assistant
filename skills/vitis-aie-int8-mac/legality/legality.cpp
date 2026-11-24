//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Parameter legality gate. Issues one mul per candidate parameter set and is
// only ever compiled with --target hw, where the chess compiler checks the
// address parameters. The Makefile fails on any chess_error or chess_warning.
//
// Add a CHECK_* line per parameter set under test and run `make`. A violation
// is reported by the chess compiler as e.g.
//   "Argument number 2 of this operation must be a multiple of 4"
//
// This deliberately contains no loops or printf: it must stay small enough to
// fit in one tile's program memory.
//

#include "legality.h"

#include <aie_api/aie.hpp>

#define CHECK_MUL16(XLEN, XSTART, XOFFS, XSTEP, XSQ, ZSTART, ZOFFS, ZSTEP, ZSQ) \
    do {                                                                        \
        const aie::vector<int8, XLEN> xv = aie::load_v<XLEN>(xd);               \
        const aie::vector<int8, 32>   zv = aie::load_v<32>(zd);                 \
        aie::accum<acc48, 16> a = mul16(xv, XSTART, XOFFS, XSTEP, XSQ,          \
                                        zv, ZSTART, ZOFFS, ZSTEP, ZSQ);         \
        sink += (int)a.to_vector<int32>(0).get(0);                              \
    } while (0)

#define CHECK_MUL8(XLEN, XSTART, XOFFS, XSTEP, XSQ, ZSTART, ZOFFS, ZSTEP, ZSQ)  \
    do {                                                                        \
        const aie::vector<int8, XLEN> xv = aie::load_v<XLEN>(xd);               \
        const aie::vector<int8, 32>   zv = aie::load_v<32>(zd);                 \
        aie::accum<acc48, 8> a = mul8(xv, XSTART, XOFFS, XSTEP, XSQ,            \
                                      zv, ZSTART, ZOFFS, ZSTEP, ZSQ);           \
        sink += (int)a.to_vector<int32>(0).get(0);                              \
    } while (0)

void legality(adf::output_buffer<int32, adf::extents<16>>& out)
{
    alignas(32) int8 xd[128];
    alignas(32) int8 zd[32];
    for (int i = 0; i < 128; i++) xd[i] = (int8)i;
    for (int i = 0; i < 32; i++) zd[i] = (int8)i;

    int sink = 0;

    // ---- parameter sets under test -------------------------------------
    //          xlen xstart xoffsets     xstep xsq     zstart zoffsets     zstep zsq
    CHECK_MUL16( 64,  0, 0x30303030u, 4, 0x3210u,  0, 0x43322110u, 2, 0x2110u);
    CHECK_MUL16( 64, 32, 0x30303030u, 4, 0x3210u,  0, 0xCC884400u, 2, 0x1010u);
    CHECK_MUL16(128,  0, 0x06040200u, 4, 0x1010u,  0, 0x43322110u, 2, 0x2110u);
    CHECK_MUL8 ( 64,  0, 0x30303030u, 4, 0x3210u,  0, 0x43322110u, 2, 0x2110u);
    CHECK_MUL8 (128,  0, 0x30303030u, 4, 0x3210u,  0, 0x43322110u, 2, 0x2110u);
    CHECK_MUL8 (128,  0, 0x03020100u, 4, 0x2110u,  0, 0x00000000u, 2, 0x3210u);
    CHECK_MUL8 (128, 32, 0x03020100u, 4, 0x2110u,  0, 0x88888888u, 2, 0x3210u);
    // --------------------------------------------------------------------

    int32* po = out.data();
    for (int i = 0; i < 16; i++) po[i] = sink;
}
