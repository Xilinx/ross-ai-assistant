<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# Custom Op example: AffineGrid2D

The examples in this directory implement the 2D version of [ONNX AffineGrid](https://onnx.ai/onnx/operators/onnx__AffineGrid.html) operation for VitisAI. The AffineGrid operation generates a sampling grid of normalized coordinates (`[N, H, W, 2]`) from affine transformation matrices (`theta` with shape `[N, 2, 3]`), which can be used for spatial transformer operations. The examples progress from a simple scalar single-core implementation to a vectorized 16-core distributed version.

| Example | Cores | Description |
|---------|-------|-------------|
| `0_nonvectorized_1x1` | 1 (1x1) | Non-vectorized scalar implementation. Processes one grid point at a time on a single AIE core. Simplest starting point. |
| `1_vectorized_1x1` | 1 (1x1) | Vectorized implementation on a single AIE core. Supports tensor sizes that don't fit in L1 memory via tiling. |
| `2_distributed_4x4` | 16 (4x4) | Vectorized implementation distributed across all 16 AIE cores. |

## What is important to know?

All folders share the same structure, with differences in the kernel (scalar vs vectorized) and the tiling configuration (1x1 vs 4x4).

The distribution in `2_distributed_4x4` is **not** along the batch dimension. Instead, the output grid's H×W spatial points are split evenly across all 16 cores — each core computes `(H_OUT * W_OUT) / 16` grid points. The theta matrix is broadcast to every core so that all cores work on the same batch sample simultaneously, and the batch dimension is iterated externally. This approach allows distributing work across all cores even when the batch size is 1.

When executing `run.py`, statistics will be printed out. These consist of the main statistics cossim (cosine similarity) and medAE (median absolute error), as well as max absolute error and mean absolute error.
