<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# hls-intro-matmul — Matrix Multiplication C++ → HLS

Introductory example for the **C++ → HLS** skill flow. This design starts with a simple triple-nested loop matrix multiplication and demonstrates how the skills transform it into an optimized HLS implementation.

- **Environment:** Claude Code + `claude-opus-4.8` model, Vitis HLS 2026.1 / Vivado 2026.1

> **Audience:** Users starting with existing C++ algorithms who want to accelerate them with HLS. This design exercises the HLS-architect skill flow without requiring MATLAB.

---

## What matrix multiplication does

Matrix multiplication computes `C = A × B` where:
- **A** is an `M × K` matrix
- **B** is a `K × N` matrix  
- **C** is an `M × N` matrix (output)

```
  ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ A (M×K)  │ × │ B (K×N)  │ = │ C (M×N)  │
  └──────────┘   └──────────┘   └──────────┘

  C[i][j] = Σ A[i][k] × B[k][j]  (for k = 0 to K-1)
```

The baseline implementation uses three nested loops — a classic compute pattern that HLS can heavily optimize through tiling, pipelining, and array partitioning.

---

## Files

| File | Role |
|---|---|
| [`kernel.cpp`](kernel.cpp) | Baseline kernel — simple triple-nested loop implementation |
| [`kernel.hpp`](kernel.hpp) | Header file defining `MAX_SIZE=4096` and the `kernel()` function signature |
| [`main.cpp`](main.cpp) | Testbench — generates random matrices, runs the kernel, verifies against reference using ULP-based float comparison |

---

## How to run the design

**Design targets:**

| Parameter | Value |
|---|---|
| Throughput goal | **15 GOPS** |
| FPGA part | xczu9eg-ffvb1156-2-e |
| Clock period | 3.3 ns |
| Matrix size | **64×64×64** (ops per call = 2×64³ = 524 288 MAC ops) |

In Claude Code, from this design directory:

/hls-architect throughput=15 GOPS part=xczu9eg-ffvb1156-2-e clock=3.3 matrix size(M,N,K) = 64,64,64


The skill chain will:

1. [`/hls-architect`](../../skills/hls-architect/SKILL.md) — generate HLS DATAFLOW architecture (load → compute → store)
2. [`/hls-optimize`](../../skills/hls-optimize/SKILL.md) — iterate pragmas until 15 GOPS throughput target is met

