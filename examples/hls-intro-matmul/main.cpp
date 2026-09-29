/*
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/
#include "kernel.hpp"

#include <cassert>
#include <ap_float.h>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <random>
#include <vector>

constexpr int ERROR_LIMIT = 10;

// ULP-based float comparison.
// Reinterprets bits as int32 and measures the number of representable floats
// between the two values. Returns true when the values are within tolerance.
// NaN inputs always return false; infinities compare as expected.
static bool ulp_close(float a, float b, ap_int<32> max_ulp = 8) {
    if (std::isnan(a) || std::isnan(b)) return false;
    // bits_ref() returns ap_int<32> — the raw IEEE 754 bit pattern, no UB
    ap_int<32> ia = ap_float_single(a).bits_ref();
    ap_int<32> ib = ap_float_single(b).bits_ref();
    // Convert sign-magnitude to two's complement so subtraction gives ULP distance
    if (ia < 0) ia = ap_int<32>(0x80000000u) - ia;
    if (ib < 0) ib = ap_int<32>(0x80000000u) - ib;
    ap_int<33> diff = ia - ib;
    return std::max<ap_int<33>>(diff, -diff) <= max_ulp;
}

// Reference Implementation of Matrix Multiplication
void matrix_multiply(float C[], const float A[], const float B[], int M, int N, int K) {
    // Initialize C to 0
    for (int i = 0; i < M; i++) {
        for (int j = 0; j < N; j++) {
            C[i * N + j] = 0;
        }
    }

    // Matrix Multiplication
    for (int i = 0; i < M; i++) {
        for (int j = 0; j < N; j++) {
            for (int k = 0; k < K; k++) {
                C[i * N + j] += A[i * K + k] * B[k * N + j];
            }
        }
    }
}

int main(int argc, char *argv[]) {
    if (argc != 4) {
        std::cerr << "Usage: " << argv[0] << " <M> <N> <K>" << std::endl;
        return 1;
    }

    int M = std::atoi(argv[1]);
    int N = std::atoi(argv[2]);
    int K = std::atoi(argv[3]);

    assert(M > 0 && N > 0 && K > 0);
    assert(M * N <= MAX_SIZE && N * K <= MAX_SIZE && M * K <= MAX_SIZE);

    std::default_random_engine gen(42);
    std::uniform_real_distribution<float> dis(-1.0f, 1.0f);

    std::vector<float> A(MAX_SIZE); // M * K
    std::vector<float> B(MAX_SIZE); // K * N
    std::vector<float> C(MAX_SIZE); // M * N
    std::vector<float> C_ref(MAX_SIZE); // M * N

    // Initialize A, B, C, C_ref
    for (int i = 0; i < M; i++)
        for (int j = 0; j < K; j++)
            A[i * K + j] = dis(gen);
    for (int i = 0; i < K; i++)
        for (int j = 0; j < N; j++)
            B[i * N + j] = dis(gen);
    for (int i = 0; i < M; i++) {
        for (int j = 0; j < N; j++) {
            C[i * N + j] = dis(gen);
            C_ref[i * N + j] = 0;
        }
    }

    // Run kernel
    kernel(C.data(), A.data(), B.data(), M, N, K);

    // Run reference implementation
    matrix_multiply(C_ref.data(), A.data(), B.data(), M, N, K);

    // Check result
    int error = 0;
    for (int i = 0; i < M; i++) {
        for (int j = 0; j < N; j++) {
            if (!ulp_close(C[i * N + j], C_ref[i * N + j])) {
                error++;
                if (error < ERROR_LIMIT) {
                    std::cerr << "Mismatch: C[" << i << "][" << j << "] = "
                              << C[i * N + j] << " (expected: " << C_ref[i * N + j] << ")" << std::endl;
                } else if (error == ERROR_LIMIT) {
                    std::cerr << "..." << std::endl;
                }
            }
        }
    }

    if (error > 0) {
        std::cerr << error << " errors found" << std::endl;
        std::cerr << "FAIL" << std::endl;
        return EXIT_FAILURE;
    }

    std::cout << "PASS" << std::endl;
    return EXIT_SUCCESS;
}