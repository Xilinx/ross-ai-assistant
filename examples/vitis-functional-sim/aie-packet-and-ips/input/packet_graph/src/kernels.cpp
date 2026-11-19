// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "kernels.h"

// Each kernel consumes one packet: it drops the input header, writes an output
// header with its own packet ID, then processes samples until TLAST.

void add_offset(input_pktstream *in, output_pktstream *out)
{
    readincr(in);
    writeHeader(out, 0, getPacketid(out, 0));
    bool last = false;
    do {
        writeincr(out, readincr(in, last) + 100, last);
    } while (!last);
}

void negate(input_pktstream *in, output_pktstream *out)
{
    readincr(in);
    writeHeader(out, 0, getPacketid(out, 0));
    bool last = false;
    do {
        writeincr(out, -readincr(in, last), last);
    } while (!last);
}
