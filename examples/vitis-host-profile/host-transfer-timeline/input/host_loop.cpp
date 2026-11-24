// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Excerpt of the host loop that produced this run (native XRT, Alveo U55C, -t hw).
// This is not a complete program. Do not compile this file.
// xclbin was linked with --profile.data all:all:all, so device_trace has CU data.
for (int it = 0; it < 4; ++it) {
    fill_inputs(a, b, it);
    bo_a.sync(XCL_BO_SYNC_BO_TO_DEVICE);        // 16 MiB
    bo_b.sync(XCL_BO_SYNC_BO_TO_DEVICE);        // 16 MiB
    auto run = krnl(bo_a, bo_b, bo_c, N);
    run.wait();
    bo_c.sync(XCL_BO_SYNC_BO_FROM_DEVICE);      // 16 MiB
    check_outputs(a, b, c);
}
