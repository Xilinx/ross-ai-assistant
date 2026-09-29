/*
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/
#ifndef _KERNEL_H_
#define _KERNEL_H_

#define MAX_SIZE 4096

extern "C"
void kernel(float C[], const float A[], const float B[], int M, int N, int K);

#endif // _KERNEL_H_