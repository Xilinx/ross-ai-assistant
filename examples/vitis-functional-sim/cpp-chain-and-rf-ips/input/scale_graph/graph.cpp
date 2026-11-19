// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "graph.h"

ScaleGraph g;

#if defined(__AIESIM__) || defined(__X86SIM__)
int main() {
    g.init();
    g.run();
    g.end();
    return 0;
}
#endif
