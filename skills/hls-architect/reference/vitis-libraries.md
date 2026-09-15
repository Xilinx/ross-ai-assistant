---
description: Vitis Libraries study guide — browse L1 source code to learn HLS coding patterns. DO NOT include library headers in user designs. Study, then write your own.
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Pattern References — Study, Then Write Your Own Code

**Rule for all references**: DO NOT include or link any headers or source files in user designs. Use them as **pattern references only** — read the implementation, understand the HLS coding style, then write equivalent code from scratch in the user's own kernel.

---

## Vitis Libraries (Production AMD/Xilinx HLS Code)

**Repository**: https://github.com/Xilinx/Vitis_Libraries/tree/2025.1

Production-quality HLS implementations covering vision, DSP, linear algebra, data compression, and more. Study L1 source code to learn streaming interfaces, pragma usage, AXI/stream bridging, and memory optimization patterns.

---

## How to Look Up Library Code

1. **Browse the L1 include directory** for your domain (see Library Domains table below):
   `https://github.com/Xilinx/Vitis_Libraries/tree/2025.1/<domain>/L1/include/`

2. **Read the raw source** to study the implementation:
   `https://raw.githubusercontent.com/Xilinx/Vitis_Libraries/2025.1/<domain>/L1/include/hw/<file>.hpp`

3. **Extract the pattern** and write your own kernel from scratch following that style

---

**Focus on L1** (individual HLS kernels for C-sim/csynth/cosim). L2/L3 are for complete XCLBIN/multi-kernel apps.

---

## Library Domains

| Domain | Path | Key Primitives |
|---|---|---|
| **DSP** | `dsp/L1/include/hw/` | FFT (1D, 2D), FIR filters, DDS, matrix multiply (GeMM) |
| **Vision** | `vision/L1/include/` | filter2D, resize, threshold, morphology, Harris, FAST, line buffer, window buffer |
| **Utils / Streams** | `utils/L1/include/xf_utils_hw/` | AXI↔stream bridges, stream split/combine/dup/reorder/sync |
| **Data Mover** | `data_mover/L1/include/xf_data_mover/` | Load master→stream, store stream→master, 4D data movers |
| **BLAS** | `blas/L1/include/` | GEMM, GEMV, DOT, AXPY — matrix/vector ops |
| **Solver** | `solver/L1/include/` | QR, SVD, Cholesky, linear system solver |
| **Data Compression** | `data_compression/L1/include/` | LZ4, Snappy, Zlib, Zstd |
| **Security** | `security/L1/include/` | AES, SHA, RSA, ECC |
| **HPC** | `hpc/L1/include/` | GEMM variants, systolic arrays |


