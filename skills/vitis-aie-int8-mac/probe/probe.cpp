//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Measures the actual lane x column -> buffer-index maps of mul8 / mul16 in
// x86simulator, so the maps are measured rather than predicted. Output is
// parsed by scripts/compare_maps.py and diffed against the spreadsheets.
//
// x-map method
//   Hold z at a configuration whose map is the identity (zstart=0, zoffsets=0,
//   zstep=2, zsquare=0x3210 => zmap[r][c] == c), put a unit impulse on z[c],
//   and load xbuff[i] = i. Lane r then reads out exactly xmap[r][c].
//
// z-map method
//   The dual needs a way to excite one column on the x side. First measure the
//   canonical x map (xoffsets=0x03020100, xstep=4, xsquare=0x2110), which is
//   injective along each lane, then place a unit impulse at xmap_canon[r][c] so
//   that lane r sees column c alone, and read zbuff[j] = j+1 back out of it.
//   The +1 keeps index 0 distinguishable from "no contribution".
//

#include "probe.h"

#include <aie_api/aie.hpp>
#include <cstdio>

static constexpr int   CANON_XSTART  = 0;
static constexpr unsigned CANON_XOFFS = 0x03020100u;
static constexpr int   CANON_XSTEP   = 4;
static constexpr unsigned CANON_XSQ  = 0x2110u;

template <int NL>
static constexpr int cols() { return NL == 8 ? 16 : 8; }

// The address parameters are immediate fields of the instruction, so every
// setting has to be a template argument rather than a runtime value.
template <int NL, int XLEN,
          int XSTART, unsigned XOFFS, int XSTEP, unsigned XSQ,
          int ZSTART, unsigned ZOFFS, int ZSTEP, unsigned ZSQ>
static inline void one_mul(const int8* xd, const int8* zd, int* acc_out)
{
    const aie::vector<int8, XLEN> xv = aie::load_v<XLEN>(xd);
    const aie::vector<int8, 32>   zv = aie::load_v<32>(zd);

    if constexpr (NL == 16) {
        aie::accum<acc48, 16> a = mul16(xv, XSTART, XOFFS, XSTEP, XSQ,
                                        zv, ZSTART, ZOFFS, ZSTEP, ZSQ);
        const aie::vector<int32, 16> v = a.template to_vector<int32>(0);
        for (int n = 0; n < 16; n++) acc_out[n] = (int)v.get(n);
    } else {
        aie::accum<acc48, 8> a = mul8(xv, XSTART, XOFFS, XSTEP, XSQ,
                                      zv, ZSTART, ZOFFS, ZSTEP, ZSQ);
        const aie::vector<int32, 8> v = a.template to_vector<int32>(0);
        for (int n = 0; n < 8; n++) acc_out[n] = (int)v.get(n);
    }
}

static void emit(const char* kind, const char* name, int nl, int nc,
                 const int map[16][16])
{
    printf("BEGIN %s %s lanes=%d cols=%d\n", kind, name, nl, nc);
    for (int r = 0; r < nl; r++) {
        printf("ROW %2d :", r);
        for (int c = 0; c < nc; c++) printf(" %4d", map[r][c]);
        printf("\n");
    }
    printf("END\n");
}

template <int NL, int XLEN, int XSTART, unsigned XOFFS, int XSTEP, unsigned XSQ>
static int measure_xmap(const char* name)
{
    constexpr int NC = cols<NL>();
    // 64 compiles under x86sim but the hw compiler caps stack alignment at 32.
    alignas(32) int8 xd[XLEN];
    alignas(32) int8 zd[32];
    int acc[16];
    int map[16][16];

    for (int i = 0; i < XLEN; i++) xd[i] = (int8)i;

    for (int c = 0; c < NC; c++) {
        for (int i = 0; i < 32; i++) zd[i] = 0;
        zd[c] = 1;
        one_mul<NL, XLEN, XSTART, XOFFS, XSTEP, XSQ,
                0, 0x00000000u, 2, 0x3210u>(xd, zd, acc);
        for (int r = 0; r < NL; r++) map[r][c] = acc[r];
    }

    printf("\nPARAMS xstart=%d xoffsets=0x%08x xstep=%d xsquare=0x%04x xlen=%d\n",
           XSTART, XOFFS, XSTEP, XSQ, XLEN);
    emit("XMAP", name, NL, NC, map);

    int sum = 0;
    for (int r = 0; r < NL; r++)
        for (int c = 0; c < NC; c++) sum += map[r][c];
    return sum;
}

template <int NL, int ZSTART, unsigned ZOFFS, int ZSTEP, unsigned ZSQ>
static int measure_zmap(const char* name)
{
    constexpr int NC = cols<NL>();
    alignas(32) int8 xd[64];
    alignas(32) int8 zd[32];
    int acc[16];
    int xmap[16][16];
    int zmap[16][16];

    for (int i = 0; i < 64; i++) xd[i] = (int8)i;
    for (int c = 0; c < NC; c++) {
        for (int i = 0; i < 32; i++) zd[i] = 0;
        zd[c] = 1;
        one_mul<NL, 64, CANON_XSTART, CANON_XOFFS, CANON_XSTEP, CANON_XSQ,
                0, 0x00000000u, 2, 0x3210u>(xd, zd, acc);
        for (int r = 0; r < NL; r++) xmap[r][c] = acc[r];
    }

    // The impulse trick is only valid where a lane's x indices are distinct.
    for (int r = 0; r < NL; r++)
        for (int a = 0; a < NC; a++)
            for (int b = a + 1; b < NC; b++)
                if (xmap[r][a] == xmap[r][b])
                    printf("WARN %s: canonical x map repeats on lane %d "
                           "(col %d and %d both read x[%d])\n",
                           name, r, a, b, xmap[r][a]);

    for (int j = 0; j < 32; j++) zd[j] = (int8)(j + 1);
    for (int r = 0; r < NL; r++) {
        for (int c = 0; c < NC; c++) {
            for (int i = 0; i < 64; i++) xd[i] = 0;
            xd[xmap[r][c]] = 1;
            one_mul<NL, 64, CANON_XSTART, CANON_XOFFS, CANON_XSTEP, CANON_XSQ,
                    ZSTART, ZOFFS, ZSTEP, ZSQ>(xd, zd, acc);
            zmap[r][c] = acc[r] - 1;
        }
    }

    printf("\nPARAMS zstart=%d zoffsets=0x%08x zstep=%d zsquare=0x%04x\n",
           ZSTART, ZOFFS, ZSTEP, ZSQ);
    emit("ZMAP", name, NL, NC, zmap);

    int sum = 0;
    for (int r = 0; r < NL; r++)
        for (int c = 0; c < NC; c++) sum += zmap[r][c];
    return sum;
}

void probe(adf::output_buffer<int32, adf::extents<16>>& out)
{
    int acc = 0;

    acc += measure_xmap<16,  64, 0, 0x30303030u, 4, 0x3210u>("mac16_xbuff_64_stored");
    acc += measure_xmap<16,  64, 32, 0x30303030u, 4, 0x3210u>("mac16_xbuff_64_tc2_stepB");
    acc += measure_xmap<16, 128, 0, 0x06040200u, 4, 0x1010u>("mac16_xbuff_128_stored");
    acc += measure_xmap< 8,  64, 0, 0x30303030u, 4, 0x3210u>("mac8_xbuff_64_stored");
    acc += measure_xmap< 8,  64, 0, 0x00003030u, 4, 0x3210u>("mac8_v64_upper_dropped");
    acc += measure_xmap< 8, 128, 0, 0x30303030u, 4, 0x3210u>("mac8_xbuff_128_stored");
    acc += measure_xmap< 8, 128, 0, 0x03020100u, 4, 0x2110u>("mac8_xbuff_128_tc1_stepA");
    acc += measure_xmap< 8, 128, 0, 0x00000100u, 4, 0x2110u>("mac8_upper_nibbles_dropped");
    acc += measure_xmap< 8, 128, 32, 0x03020100u, 4, 0x2110u>("mac8_xbuff_128_tc1_stepB");
    // xstart=48 drives the pre-square index past 63: does the address wrap?
    acc += measure_xmap<16,  64, 48, 0x30303030u, 4, 0x3210u>("mac16_xbuff_64_wrap");

    acc += measure_zmap<16, 0, 0x43322110u, 2, 0x2110u>("mac16_zbuff_32_stored");
    acc += measure_zmap< 8, 0, 0x43322110u, 2, 0x2110u>("mac8_zbuff_32_stored");
    acc += measure_zmap< 8, 0, 0x00000000u, 2, 0x3210u>("mac8_zbuff_32_tc1_stepA");
    acc += measure_zmap< 8, 0, 0x88888888u, 2, 0x3210u>("mac8_zbuff_32_tc1_stepB");
    acc += measure_zmap<16, 0, 0xCC884400u, 2, 0x1010u>("mac16_zbuff_32_tc2");
    // Identity square, non-repeating nibbles: isolates the z lane -> nibble rule.
    acc += measure_zmap<16, 0, 0x43322110u, 2, 0x3210u>("mac16_zbuff_32_lanerule");

    printf("\nPROBE COMPLETE checksum=%d\n", acc);

    int32* po = out.data();
    for (int i = 0; i < 16; i++) po[i] = acc;
}
