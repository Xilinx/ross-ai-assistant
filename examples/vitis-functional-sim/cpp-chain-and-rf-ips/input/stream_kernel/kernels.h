// Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#pragma once

#include "hls_stream.h"
#include <stdint.h>

void scale_offset(hls::stream<int32_t> &in, hls::stream<int32_t> &out);
