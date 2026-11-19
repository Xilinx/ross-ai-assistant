//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//

#include "legality_graph.h"

LegalityGraph dut;

#if defined(__AIESIM__) || defined(__X86SIM__)
int main()
{
    dut.init();
    dut.run(1);
    dut.end();
    return 0;
}
#endif
