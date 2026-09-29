<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# Custom Op example: gridsample2d

The examples in this directory compute 2D grid sampling of inputs, e.g., like the [ONNX GridSample op](https://onnx.ai/onnx/operators/onnx__GridSample.html). The examples progress from a simple scalar single-core implementation to a vectorized 16-core distributed version.

| Example | Cores doing distinct work | Description |
|---------|-------|-------------|
| `0_scalar_1x1` | 1 | Non-vectorized scalar implementation. Processes one pixel at a time. Simplest starting point. |
| `1_tiled_1x1` | 1 | Single-core vectorized implementation. Supports tensor sizes that don't fit in L1 memory via tiling. |
| `2_tiled_1x4` | 4 (1x4) | Vectorized implementation distributed across 4 AIE cores. |
| `3_tiled_4x4` | 16 (4x4) | Vectorized implementation distributed across all 16 AIE cores. |

Note that the `1x1` in the first two names refers to the *programming model* -- one core's worth of work per kernel call, with no spatial split -- not to how many cores the design occupies. `AieConfig` always describes the full core array and requires every core's input and output ports to be connected, so these two examples broadcast the same tiles to **all** cores, which then all compute the **same** result. Only one core's output is meaningful; the rest are redundant copies.

## What is important to know?
All folders share the same structure, with differences in the kernel (scalar vs vectorized) and the tiling configuration.

When executing `run.py`, statistics will be printed out. These consist of the main statistics cossim (cosine similarity) and medAE (median absolute error), as well as max absolute error and mean absolute error.

There is an `images_input` folder, which contains images to be used for processing. Outputs will be under `images_output_temp`. There are 16 different grids, so output naming will be `images_output_temp/outputs_cop_<grid_no>_<image_no>.png` for the custom op implementation, and `images_output_temp/outputs_ref_<grid_no>_<image_no>.png` for the reference implementation.

Also note the extra attribute `H_tile` in `.onnxtxt` files. This slices grid/output images into smaller pieces so that larger outputs fit in memory. For example, `H_tile=5` for a `100x100` grid will create 5 tiles of `20x100` grids to process sequentially.
