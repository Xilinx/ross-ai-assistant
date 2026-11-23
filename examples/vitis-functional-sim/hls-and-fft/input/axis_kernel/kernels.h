// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#pragma once

#include "hls_stream.h"
#include "ap_axi_sdata.h"
#include <stdint.h>

void packet_max(hls::stream<ap_axis<32, 0, 0, 0>> &in, hls::stream<int32_t> &out);
