<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# Custom ReduceMax Implementation

This directory contains a custom implementation of the [ONNX ReduceMax](https://onnx.ai/onnx/operators/onnx__ReduceMax.html) operation. The implementation demonstrates how to create custom operators with specialized tiling strategies for different reduction axes, including the case where the reduced axis is the inner-most (contiguous) dimension.

## Overview

The custom ReduceMax operation takes the maximum along a given axis of a bfloat16 tensor. Two reduction layouts are shown, each in its own subdirectory:
- **`reduce_max_axis_0` (Outer Dimension)**: Reduces along the outer axis of a `[4, 2, 31360]` tensor, taking the max across the strided outer planes.
- **`reduce_max_axis_2` (Inner-most Dimension)**: Reduces along the inner-most axis (axis 2) of a `[31360, 2, 4]` tensor, taking the max over the contiguous run of elements per output.

Both layouts reduce to the same number of independent outputs (`N = 62720`) and distribute that work across the 16 cores of the 4x4 AIE overlay.

## Architecture

### Core Components

**Kernel Implementation** (`custom_reducemax*.cpp`)
   - Kernel implementation tailored to the reduction-axis layout (strided max vs. contiguous de-interleaved max)

**Tiling Strategy** (`custom_reducemax_tiling.py`)
   - Memory hierarchy management (L3 -> L2 -> L1)
   - Core distribution across 4x4 AIE overlay

**Configuration** (`custom_reducemax.yaml`)
   - Links kernel and tiling implementations
   - Defines the ADF wrapper signature

## Implementation Details

### Axis 0 Reduction (Outer Dimension)

**Input Shape**: [4, 2, 31360] -> **Output Shape**: [1, 2, 31360]

- **Strategy**: The reduced axis (extent `R = 4`) is the strided outer dimension; the `N = 2 x 31360 = 62720` output positions are independent. Distribute `N` across the cores and keep the `R`-way max inside each core.
- **Memory Layout**:
  - L2: View the input as plane-major `[R, N]`; stream `16 x T` output positions per rotation.
  - L1: Broadcast each column's `[R, ROWS*T]` slice to its 4 cores; each core selects its own `[R, T]` slice via its row index.
- **Core Distribution**: Each core takes the max of `R` strided planes for its `T` output positions; the OFM is collected per-core with `SpatialDistribute2D`.

### Axis 2 Reduction (Inner-most Dimension)

**Input Shape**: [31360, 2, 4] -> **Output Shape**: [31360, 2, 1]

- **Strategy**: The reduced axis (extent `K = 4`) is the contiguous inner-most dimension. Flattened, this is `N = 31360 x 2 = 62720` independent reductions, each over `K = 4` contiguous elements (`out[g] = max_k in[K*g + k]`). Distribute the `N` reductions across the cores.
- **Memory Layout**:
  - L2: Stream the flat tensor in 16-core slabs (`16 x T x K` input elements -> `16 x T` outputs per rotation).
  - L1: Broadcast each column's `[ROWS, T*K]` slice to its 4 cores; each core selects its own `[T*K]` slice via its row index.
- **Core Distribution**: Each core handles `T` reductions per call. The kernel loads a wide contiguous bf16 vector, de-interleaves it into the `K` components, combines them with a pairwise `aie::max` tree, and stores the result, avoiding a scalar tail.
