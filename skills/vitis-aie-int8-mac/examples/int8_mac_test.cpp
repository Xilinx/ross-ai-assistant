//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//

// =============================================================================
// int8_mac_test.cpp
//
// Five test cases exercising the AIE int8×int8 MAC intrinsics:
//   mul8 / mul16 / mac8 / mac16 / msc8 / msc16
//
// Each test case calls an intrinsic with chosen parameters, prints the
// accumulator result via aie::print(), and checks each lane against a
// reference value computed offline (see companion Python script / spreadsheets).
//
// Data values used in all test cases (initialised inline):
//   xbuff_128[i] = i + 1      (1 .. 128)   v128int8
//   xbuff_64[i]  = i + 1      (1 ..  64)   v64int8   — used by TC2/TC5
//   zbuff_32[i]  = i + 1      (1 ..  32)   v32int8   — used by TC1/TC3/TC4/TC5
//                                                        (TC2 uses its own local zbuff_32_I/zbuff_32_Q)
//
// Test cases
// ──────────
//  TC1  mul8+mac8,       v128int8 xbuff  – 32-tap FIR, complex int8 samples (real-imag interleaved), real int8 taps
//  TC2  mul16+msc16/mac16, v64int8  xbuff – 8-antenna 4-layer MIMO receiver, complex samples × complex weights
//  TC3  mul16,           v128int8 xbuff  – all defaults (xstep=0, offsets=0, square=0x3210)
//  TC4  mul8,            v128int8 xbuff  – non-default square permute (xsquare=0x2130, zsquare=0x1032)
//  TC5  mul8+msc8,       v64int8  xbuff  – exercises msc8 with v64int8 xbuff; net result is zero by construction
//
//  Variable names: xbuff_128, xbuff_64, zbuff_32 (+ zbuff_32_I / zbuff_32_Q for TC2)
// =============================================================================

#include "int8_mac_test.h"
#include <adf.h>
#include <aie_api/aie.hpp>
#include <aie_api/utils.hpp>
#include <stdio.h>

// ---------------------------------------------------------------------------
// Helper: check one acc48 lane value against an expected int32 and report.
// Returns 1 on failure, 0 on pass.
// ---------------------------------------------------------------------------
static inline int check_lane(const char* tc, int lane, int32_t got, int32_t exp)
{
    if (got == exp) {
        printf("  [PASS] %s lane %d = %d\n", tc, lane, got);
        return 0;
    } else {
        printf("  [FAIL] %s lane %d : got %d, expected %d\n", tc, lane, got, exp);
        return 1;
    }
}

// Extract a single acc48 lane to int32 via .to_vector (shift=0).
// acc48 LSBs fit in int32 for the value ranges used here.
static inline int32_t lane_i32(aie::accum<acc48,8> acc, int lane) {
    return acc.to_vector<int32>(0)[lane];
}
static inline int32_t lane_i32_16(aie::accum<acc48,16> acc, int lane) {
    return acc.to_vector<int32>(0)[lane];
}

void Int8MacTest::run(output_buffer<int8, extents<RESULT_SAMPLES>>& __restrict result)
{
    // -----------------------------------------------------------------------
    // Initialise test buffers inline.
    //   xbuff_128[i] = i+1  (1..128, last value wraps to -128 as int8)
    //   xbuff_64[i]  = i+1  (1..64)   — used by TC2/TC5 (v64int8 xbuff)
    //   zbuff_32[i]  = i+1  (1..32)   — used by TC1/TC3/TC4/TC5; TC2 uses local buffers
    // -----------------------------------------------------------------------
    static const int8 xdata_128[128] = {
          1,   2,   3,   4,   5,   6,   7,   8,
          9,  10,  11,  12,  13,  14,  15,  16,
         17,  18,  19,  20,  21,  22,  23,  24,
         25,  26,  27,  28,  29,  30,  31,  32,
         33,  34,  35,  36,  37,  38,  39,  40,
         41,  42,  43,  44,  45,  46,  47,  48,
         49,  50,  51,  52,  53,  54,  55,  56,
         57,  58,  59,  60,  61,  62,  63,  64,
         65,  66,  67,  68,  69,  70,  71,  72,
         73,  74,  75,  76,  77,  78,  79,  80,
         81,  82,  83,  84,  85,  86,  87,  88,
         89,  90,  91,  92,  93,  94,  95,  96,
         97,  98,  99, 100, 101, 102, 103, 104,
        105, 106, 107, 108, 109, 110, 111, 112,
        113, 114, 115, 116, 117, 118, 119, 120,
        121, 122, 123, 124, 125, 126, 127,-128
    };
    static const int8 xdata_64[64] = {
          1,   2,   3,   4,   5,   6,   7,   8,
          9,  10,  11,  12,  13,  14,  15,  16,
         17,  18,  19,  20,  21,  22,  23,  24,
         25,  26,  27,  28,  29,  30,  31,  32,
         33,  34,  35,  36,  37,  38,  39,  40,
         41,  42,  43,  44,  45,  46,  47,  48,
         49,  50,  51,  52,  53,  54,  55,  56,
         57,  58,  59,  60,  61,  62,  63,  64
    };
    static const int8 zdata_32[32] = {
          1,   2,   3,   4,   5,   6,   7,   8,
          9,  10,  11,  12,  13,  14,  15,  16,
         17,  18,  19,  20,  21,  22,  23,  24,
         25,  26,  27,  28,  29,  30,  31,  32
    };

    aie::vector<int8,128> xbuff_128 = *(v128int8*)xdata_128;
    aie::vector<int8,64>  xbuff_64  = *(v64int8*)xdata_64;
    aie::vector<int8,32>  zbuff_32  = *(v32int8*)zdata_32;

    int fails = 0;

    // -----------------------------------------------------------------------
    // TC1: mul8 + mac8, v128int8 xbuff
    //      32-tap real FIR on complex int8 samples stored real-imag interleaved:
    //        xbuff_128[2k]   = xr[k]  (real part of sample k)
    //        xbuff_128[2k+1] = xi[k]  (imaginary part of sample k)
    //      Real filter taps: zbuff_32[0..31] = z[0..31]  (single v32int8 register)
    //
    //      Step A: mul8(xbuff_128, xstart=0,  ..., zbuff_32, zstart=0, zoffsets=0x00000000, ...)  — taps z[0..15]
    //      Step B: mac8(acc, xbuff_128, xstart=32, ..., zbuff_32, zstart=0, zoffsets=0x88888888, ...) — taps z[16..31]
    //              zoffsets nibbles are in units of 2 elements; nibble=8 → offset 8×2=16 into zbuff_32
    //              (zstart is 4-bit so max=15; zoffsets enables reaching z[16..31] in the same register)
    //
    //      Common parameters: xoffsets=0x03020100, xstep=4, xsquare=0x2110
    //                         zoffsets=0x00000000, zstep=2, zsquare=0x3210
    //
    //  mul8/mac8: 8 acc48 lanes, 16 multiplications per call (32 per lane total).
    //
    //  Sample mapping per lane:
    //    Lane n, column c:  x-index = n + 2c     (c = 0..15),  z-index = c
    //    Step A uses xstart=0, zoffsets=0  → x[n + 2c], z[c]              (c = 0..15)
    //    Step B uses xstart=32, zoffsets=8 → x[n + 2c + 32], z[c + 16]   (c = 0..15)
    //
    //    Even lanes (n=2j)  → real FIR output j
    //    Odd  lanes (n=2j+1) → imag FIR output j
    //
    //  Combined: acc[2j]   = Σ_{t=0}^{31} z[t] · xr[j+t]    (real output j)
    //            acc[2j+1] = Σ_{t=0}^{31} z[t] · xi[j+t]    (imag output j)
    //  for j = 0..3,  where xr[k] = xbuff_128[2k], xi[k] = xbuff_128[2k+1], z[t] = zbuff_32[t].
    // -----------------------------------------------------------------------
    printf("\n=== TC1: mul8+mac8 / v128int8 / 32-tap complex FIR (real taps) ===\n");
    {
        aie::accum<acc48,8> acc = mul8(xbuff_128,
                                        /*xstart=*/0,  /*xoffsets=*/0x03020100,
                                        /*xstep=*/4,   /*xsquare=*/0x2110,
                                        zbuff_32,
                                        /*zstart=*/0,  /*zoffsets=*/0x00000000,
                                        /*zstep=*/2,   /*zsquare=*/0x3210);
        acc = mac8(acc,
                   xbuff_128,
                   /*xstart=*/32, /*xoffsets=*/0x03020100,
                   /*xstep=*/4,   /*xsquare=*/0x2110,
                   zbuff_32,
                   /*zstart=*/0,  /*zoffsets=*/0x88888888,
                   /*zstep=*/2,   /*zsquare=*/0x3210);

        printf("TC1 accumulator:\n");
        aie::print(acc, true, "  acc = ");

        static const int32_t exp[8] = {22352,22880,23408,23936,24464,24992,25520,26048};
        for (int i = 0; i < 8; i++)
            fails += check_lane("TC1", i, lane_i32(acc, i), exp[i]);
    }

    // -----------------------------------------------------------------------
    // TC2: mul16 + msc16 / mac16
    //      8-antenna 4-layer MIMO receiver: 4 time-consecutive antenna snapshots,
    //      each snapshot combined against 4 complex weight vectors → 16 complex outputs
    //
    //  Buffer roles are SWAPPED relative to TC1:
    //    zbuff_32  (v32int8)  — antenna samples  (z-operand, 32 real values per call)
    //    xbuff_64  (v64int8)  — receive weights  (x-operand)
    //
    //  ── Sample layout ────────────────────────────────────────────────────────────
    //  Two v32int8 registers hold one full I/Q snapshot set:
    //    zbuff_32_I[8t+k] = xI[t][k]  — I-component of antenna k at time t  (t=0..3, k=0..7)
    //    zbuff_32_Q[8t+k] = xQ[t][k]  — Q-component of antenna k at time t
    //  With zbuff_32_I[i]=2i+1 and zbuff_32_Q[i]=2i+2:
    //
    //  Alternative packing: store I/Q interleaved in a v64int8, then extract before the MAC calls:
    //    zbuff_32_I = aie::filter_even(zbuff_64, 1)  — picks every even-indexed element (all I-components)
    //    zbuff_32_Q = aie::filter_odd (zbuff_64, 1)  — picks every odd-indexed element  (all Q-components)
    //  This matches the natural complex sample layout (I₀,Q₀, I₁,Q₁, ...) but costs two
    //  extra filter operations per kernel invocation.
    //
    //  ── Weight layout (xbuff_64) ───────────────────────────────────────────────────
    //  Four I-weight vectors (indices 0..31) followed by four Q-weight vectors
    //  (indices 32..63), each stored with stride-2 interleaving so the hardware
    //  x-address pattern reads them cleanly.  xoffsets=0x30303030 separates
    //  layers {L0,L1} from {L2,L3}; the square permutation then separates the
    //  even/odd lanes within each pair.
    //
    //    wI[L][k] — real weight for layer L, antenna k:  x-index = col_l(k)
    //    wQ[L][k] — imag weight for layer L, antenna k:  x-index = col_l(k) + 32
    //      col_0(k) = 2k       col_1(k) = 2k + 1
    //      col_2(k) = 2k + 16  col_3(k) = 2k + 17
    //    for k = 0..7
    //
    //  ── Accumulator output layout ─────────────────────────────────────────────
    //  acc[4t+l] = complex output for time-slot t, layer l  (t=0..3, l=0..3)
    //
    //  ── z operand — lane-to-sample mapping ────────────────────────────────
    //  zstart=0, zoffsets=0xCC884400, zstep=2, zsquare=0x1010
    //    Lanes 4t..4t+3 → z-index = 8t + k,  k = 0..7  (8 antennas for time slot t)
    //    (zoffsets nibbles 0x00, 0x44, 0x88, 0xCC shift by 0, 8, 16, 24 elements)
    //
    //  ── x operand — lane-to-weight mapping ────────────────────────────────
    //  xstart=0, xoffsets=0x30303030, xstep=4, xsquare=0x3210
    //    Layer l, column k → x-index = col_l(k)  (same formulas as above)
    //    (xoffsets nibble 3 shifts L2/L3 by 3×4=12 bytes; all 4 time groups
    //     share the same x-pattern)
    //
    //  ── Computation scheme — 4 intrinsic calls, 2 accumulators ───────────────
    //    acc_yI:
    //      Step A  mul16(xstart= 0, zbuf=zbuff_32_I):  acc[4t+l] += xI[t][k]·wI[l][k]
    //      Step B  msc16(xstart=32, zbuf=zbuff_32_Q):  acc[4t+l] -= xQ[t][k]·wQ[l][k]
    //      → acc_yI[4t+l] = Σ_k xI[t][k]·wI[l][k] − xQ[t][k]·wQ[l][k]
    //    acc_yQ:
    //      Step C  mul16(xstart=32, zbuf=zbuff_32_I):  acc[4t+l] += xI[t][k]·wQ[l][k]
    //      Step D  mac16(xstart= 0, zbuf=zbuff_32_Q):  acc[4t+l] += xQ[t][k]·wI[l][k]
    //      → acc_yQ[4t+l] = Σ_k xI[t][k]·wQ[l][k] + xQ[t][k]·wI[l][k]
    //
    //  ── Expected results ───────────────────────────────────────────────────────
    //    acc_yI[4t+l] = Σ_{k=0}^{7} zbuff_32_I[8t+k]·x[col_l(k)] − zbuff_32_Q[8t+k]·x[col_l(k)+32]
    //    acc_yQ[4t+l] = Σ_{k=0}^{7} zbuff_32_I[8t+k]·x[col_l(k)+32] + zbuff_32_Q[8t+k]·x[col_l(k)]
    //    (see exp_yI/exp_yQ arrays below for the concrete values with xbuff_64[i]=i+1)
    // -----------------------------------------------------------------------
    printf("\n=== TC2: mul16+msc16/mac16 / v64int8 / 8-antenna 4-layer MIMO receiver ===\n");
    {
        // Separate I/Q sample buffers: zbuff_32_I[8t+k]=xI[t][k], zbuff_32_Q[8t+k]=xQ[t][k]
        // xI uses odd values 1,3,...,63; xQ uses even values 2,4,...,64 (both fit int8)
        static const int8 zI_data[32] = {
              1,   3,   5,   7,   9,  11,  13,  15,
             17,  19,  21,  23,  25,  27,  29,  31,
             33,  35,  37,  39,  41,  43,  45,  47,
             49,  51,  53,  55,  57,  59,  61,  63
        };
        static const int8 zQ_data[32] = {
              2,   4,   6,   8,  10,  12,  14,  16,
             18,  20,  22,  24,  26,  28,  30,  32,
             34,  36,  38,  40,  42,  44,  46,  48,
             50,  52,  54,  56,  58,  60,  62,  64
        };
        aie::vector<int8,32> zbuff_32_I = *(v32int8*)zI_data;
        aie::vector<int8,32> zbuff_32_Q = *(v32int8*)zQ_data;

        // acc_yI = Σ_k xI·wI (Step A) − Σ_k xQ·wQ (Step B)
        aie::accum<acc48,16> acc_yI = mul16(xbuff_64,
                                            /*xstart=*/0,  /*xoffsets=*/0x30303030,
                                            /*xstep=*/4,   /*xsquare=*/0x3210,
                                            zbuff_32_I,
                                            /*zstart=*/0,  /*zoffsets=*/0xCC884400,
                                            /*zstep=*/2,   /*zsquare=*/0x1010);
        acc_yI = msc16(acc_yI,
                       xbuff_64,
                       /*xstart=*/32, /*xoffsets=*/0x30303030,
                       /*xstep=*/4,   /*xsquare=*/0x3210,
                       zbuff_32_Q,
                       /*zstart=*/0,  /*zoffsets=*/0xCC884400,
                       /*zstep=*/2,   /*zsquare=*/0x1010);

        // acc_yQ = Σ_k xI·wQ (Step C) + Σ_k xQ·wI (Step D)
        aie::accum<acc48,16> acc_yQ = mul16(xbuff_64,
                                            /*xstart=*/32, /*xoffsets=*/0x30303030,
                                            /*xstep=*/4,   /*xsquare=*/0x3210,
                                            zbuff_32_I,
                                            /*zstart=*/0,  /*zoffsets=*/0xCC884400,
                                            /*zstep=*/2,   /*zsquare=*/0x1010);
        acc_yQ = mac16(acc_yQ,
                       xbuff_64,
                       /*xstart=*/0,  /*xoffsets=*/0x30303030,
                       /*xstep=*/4,   /*xsquare=*/0x3210,
                       zbuff_32_Q,
                       /*zstart=*/0,  /*zoffsets=*/0xCC884400,
                       /*zstep=*/2,   /*zsquare=*/0x1010);

        printf("TC2 acc_yI:\n");
        aie::print(acc_yI, true, "  acc_yI = ");
        printf("TC2 acc_yQ:\n");
        aie::print(acc_yQ, true, "  acc_yQ = ");

        // acc[4t+l]: t = time slot (0..3), l = output layer (0..3)
        static const int32_t exp_yI[16] = {
             -2368,  -2376,  -2496,  -2504,   // t=0, L0..L3
             -6464,  -6472,  -6592,  -6600,   // t=1
            -10560, -10568, -10688, -10696,   // t=2
            -14656, -14664, -14784, -14792    // t=3
        };
        static const int32_t exp_yQ[16] = {
              3472,  3608,  5648,  5784,   // t=0, L0..L3
              9616, 10008, 15888, 16280,   // t=1
             15760, 16408, 26128, 26776,   // t=2
             21904, 22808, 36368, 37272    // t=3
        };
        for (int i = 0; i < 16; i++)
            fails += check_lane("TC2-yI", i, lane_i32_16(acc_yI, i), exp_yI[i]);
        for (int i = 0; i < 16; i++)
            fails += check_lane("TC2-yQ", i, lane_i32_16(acc_yQ, i), exp_yQ[i]);
    }

    // -----------------------------------------------------------------------
    // TC3: mul16, v128int8 xbuff
    //      xstart=0, xoffsets=0x00000000, xstep=0, xsquare=0x3210
    //      zstart=0, zoffsets=0x00000000, zstep=0, zsquare=0x3210
    //
    //  mul16: 16 acc48 lanes, 8 multiplications each (128 total).
    //
    //  x-table (xstep=0 and xoffsets=0 freeze the window; xsquare=0x3210 gives the 4-element rotation):
    //    lanes [ 0, 4, 8,12]  x0  x2  x0  x2  x0  x2  x0  x2
    //    lanes [ 1, 5, 9,13]  x1  x3  x1  x3  x1  x3  x1  x3
    //    lanes [ 2, 6,10,14]  x4  x6  x4  x6  x4  x6  x4  x6
    //    lanes [ 3, 7,11,15]  x5  x7  x5  x7  x5  x7  x5  x7
    //
    //  z-table (zstep=0 and zoffsets=0 keep the same 2-element window for all lanes):
    //    all 16 lanes  z0  z1  z0  z1  z0  z1  z0  z1  (4 repeats)
    //
    //  Expected sums (xbuff_128[i]=i+1, zbuff_32[i]=i+1, each product pair repeats 4x):
    //    acc[ 0, 4, 8,12]: 4*(xbuff_128[0]*zbuff_32[0] + xbuff_128[2]*zbuff_32[1]) = 4*(1*1 + 3*2) =  28
    //    acc[ 1, 5, 9,13]: 4*(xbuff_128[1]*zbuff_32[0] + xbuff_128[3]*zbuff_32[1]) = 4*(2*1 + 4*2) =  40
    //    acc[ 2, 6,10,14]: 4*(xbuff_128[4]*zbuff_32[0] + xbuff_128[6]*zbuff_32[1]) = 4*(5*1 + 7*2) =  76
    //    acc[ 3, 7,11,15]: 4*(xbuff_128[5]*zbuff_32[0] + xbuff_128[7]*zbuff_32[1]) = 4*(6*1 + 8*2) =  88
    // -----------------------------------------------------------------------
    printf("\n=== TC3: mul16 / v128int8 / all-default params ===\n");
    {
        aie::accum<acc48,16> acc = mul16(xbuff_128,
                                         /*xstart=*/0,  /*xoffsets=*/0x00000000,
                                         /*xstep=*/0,   /*xsquare=*/0x3210,
                                         zbuff_32,
                                         /*zstart=*/0,  /*zoffsets=*/0x00000000,
                                         /*zstep=*/0,   /*zsquare=*/0x3210);

        printf("TC3 accumulator:\n");
        aie::print(acc, true, "  acc = ");

        static const int32_t exp[16] = {28,40,76,88, 28,40,76,88, 28,40,76,88, 28,40,76,88};
        for (int i = 0; i < 16; i++)
            fails += check_lane("TC3", i, lane_i32_16(acc, i), exp[i]);
    }

    // -----------------------------------------------------------------------
    // TC4: mul8, v128int8 xbuff
    //      xstart=0, xoffsets=0x03020100, xstep=4, xsquare=0x2130
    //      zstart=0, zoffsets=0x00000000, zstep=2, zsquare=0x1032
    //
    //  mul8: 8 acc48 lanes, 16 multiplications each (128 total).
    //
    //  From mac8_int8xint8.xlsx, xbuff_128 after-xsquare table (xN = xbuff_128[N]):
    //    acc[0]   x0   x6   x4  x10   x8  x14  x12  x18  x16  x22  x20  x26  x24  x30  x28  x34
    //    acc[1]   x1   x7   x5  x11   x9  x15  x13  x19  x17  x23  x21  x27  x25  x31  x29  x35
    //    acc[2]   x2   x4   x6   x8  x10  x12  x14  x16  x18  x20  x22  x24  x26  x28  x30  x32
    //    acc[3]   x3   x5   x7   x9  x11  x13  x15  x17  x19  x21  x23  x25  x27  x29  x31  x33
    //    acc[4]   x4  x10   x8  x14  x12  x18  x16  x22  x20  x26  x24  x30  x28  x34  x32  x38
    //    acc[5]   x5  x11   x9  x15  x13  x19  x17  x23  x21  x27  x25  x31  x29  x35  x33  x39
    //    acc[6]   x6   x8  x10  x12  x14  x16  x18  x20  x22  x24  x26  x28  x30  x32  x34  x36
    //    acc[7]   x7   x9  x11  x13  x15  x17  x19  x21  x23  x25  x27  x29  x31  x33  x35  x37
    //
    //  From mac8_int8xint8.xlsx, zbuff_32 after-zsquare table (zN = zbuff_32[N]):
    //    acc[0]  z0  z1  z2  z3  z4  z5  z6  z7  z8  z9  z10  z11  z12  z13  z14  z15
    //    acc[1]  z0  z1  z2  z3  z4  z5  z6  z7  z8  z9  z10  z11  z12  z13  z14  z15
    //    acc[2]  z0  z1  z2  z3  z4  z5  z6  z7  z8  z9  z10  z11  z12  z13  z14  z15
    //    acc[3]  z0  z1  z2  z3  z4  z5  z6  z7  z8  z9  z10  z11  z12  z13  z14  z15
    //    acc[4]  z0  z1  z2  z3  z4  z5  z6  z7  z8  z9  z10  z11  z12  z13  z14  z15
    //    acc[5]  z0  z1  z2  z3  z4  z5  z6  z7  z8  z9  z10  z11  z12  z13  z14  z15
    //    acc[6]  z0  z1  z2  z3  z4  z5  z6  z7  z8  z9  z10  z11  z12  z13  z14  z15
    //    acc[7]  z0  z1  z2  z3  z4  z5  z6  z7  z8  z9  z10  z11  z12  z13  z14  z15
    //
    //  Expected sums (xbuff_128[i]=i+1, zbuff_32[i]=i+1):
    //    acc[0]:  1*1+ 7*2+ 5*3+11*4+ 9*5+15*6+13*7+19*8+17*9+23*10+21*11+27*12+25*13+31*14+29*15+35*16 = 3144
    //    acc[1]:  2*1+ 8*2+ 6*3+12*4+10*5+16*6+14*7+20*8+18*9+24*10+22*11+28*12+26*13+32*14+30*15+36*16 = 3280
    //    acc[2]:  3*1+ 5*2+ 7*3+ 9*4+11*5+13*6+15*7+17*8+19*9+21*10+23*11+25*12+27*13+29*14+31*15+33*16 = 3128
    //    acc[3]:  4*1+ 6*2+ 8*3+10*4+12*5+14*6+16*7+18*8+20*9+22*10+24*11+26*12+28*13+30*14+32*15+34*16 = 3264
    //    acc[4]:  5*1+11*2+ 9*3+15*4+13*5+19*6+17*7+23*8+21*9+27*10+25*11+31*12+29*13+35*14+33*15+39*16 = 3688
    //    acc[5]:  6*1+12*2+10*3+16*4+14*5+20*6+18*7+24*8+22*9+28*10+26*11+32*12+30*13+36*14+34*15+40*16 = 3824
    //    acc[6]:  7*1+ 9*2+11*3+13*4+15*5+17*6+19*7+21*8+23*9+25*10+27*11+29*12+31*13+33*14+35*15+37*16 = 3672
    //    acc[7]:  8*1+10*2+12*3+14*4+16*5+18*6+20*7+22*8+24*9+26*10+28*11+30*12+32*13+34*14+36*15+38*16 = 3808
    // -----------------------------------------------------------------------
    printf("\n=== TC4: mul8 / v128int8 / non-default square permute ===\n");
    {
        aie::accum<acc48,8> acc = mul8(xbuff_128,
                                        /*xstart=*/0,  /*xoffsets=*/0x03020100,
                                        /*xstep=*/4,   /*xsquare=*/0x2130,
                                        zbuff_32,
                                        /*zstart=*/0,  /*zoffsets=*/0x00000000,
                                        /*zstep=*/2,   /*zsquare=*/0x1032);

        printf("TC4 accumulator:\n");
        aie::print(acc, true, "  acc = ");

        static const int32_t exp[8] = {3144,3280,3128,3264,3688,3824,3672,3808};
        for (int i = 0; i < 8; i++)
            fails += check_lane("TC4", i, lane_i32(acc, i), exp[i]);
    }

    // -----------------------------------------------------------------------
    // TC5: mul8 + msc8, v64int8 xbuff
    //      Exercises msc8 with a v64int8 xbuff.
    //      Uses the same z parameters as TC1 Step A so the subtracted products are
    //      identical to the Step A products, giving a provably correct zero result.
    //
    //    Parameters: xstart=0, xoffsets=0x03020100, xstep=4, xsquare=0x2110
    //                zstart=0, zoffsets=0x00000000, zstep=2, zsquare=0x3210
    //    Step A  mul8(xbuff_64, zbuff_32)            — products
    //    Step B  msc8(acc,      xbuff_64, zbuff_32)  — subtract the same products → 0
    //  Expected: all 8 lanes → 0
    // -----------------------------------------------------------------------
    printf("\n=== TC5: mul8/msc8 / v64int8 / multiply then subtract ===\n");
    {
        aie::accum<acc48,8> acc = mul8(xbuff_64,
                           /*xstart=*/0,  /*xoffsets=*/0x03020100,
                           /*xstep=*/4,   /*xsquare=*/0x2110,
                           zbuff_32,
                           /*zstart=*/0,  /*zoffsets=*/0x00000000,
                           /*zstep=*/2,   /*zsquare=*/0x3210);

        printf("TC5 after mul8 (v64int8 xbuff products):\n");
        aie::print(acc, true, "  acc_mid = ");

        acc = msc8(acc,
                   xbuff_64,
                   /*xstart=*/0,  /*xoffsets=*/0x03020100,
                   /*xstep=*/4,   /*xsquare=*/0x2110,
                   zbuff_32,
                   /*zstart=*/0,  /*zoffsets=*/0x00000000,
                   /*zstep=*/2,   /*zsquare=*/0x3210);

        printf("TC5 after msc8 (expected = 0):\n");
        aie::print(acc, true, "  acc_final = ");

        static const int32_t exp[8] = {0,0,0,0,0,0,0,0};
        for (int i = 0; i < 8; i++)
            fails += check_lane("TC5", i, lane_i32(acc, i), exp[i]);
    }

    // -----------------------------------------------------------------------
    // Write result summary to output port: 0 = all pass, else fail count.
    // -----------------------------------------------------------------------
    printf("\nSummary: %d failure(s)\n", fails);
    auto* out = result.data();
    out[0] = (int8)fails;
    for (int i = 1; i < RESULT_SAMPLES; i++) out[i] = 0;
}
