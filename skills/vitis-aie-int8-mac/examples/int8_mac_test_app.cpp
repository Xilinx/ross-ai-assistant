//
// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//

#include "int8_mac_test_graph.h"

Int8MacTestGraph dut;

#if defined(__AIESIM__) || defined(__X86SIM__)
int main(int argc, char* argv[])
{
    dut.init();
    dut.run(1);
    dut.end();
    return 0;
}
#endif
