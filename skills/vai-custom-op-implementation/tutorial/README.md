<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# Tutorials and examples

This directory contains examples and tutorials to demonstrate various aspects of the custom ops
flow, and serve as a basis to implement your own custom ops.

| Example             | Description                                                                                           |
|---------------------|-------------------------------------------------------------------------------------------------------|
| [negate/0_untiled](/docs/30_custom_ops/tutorial/negate/0_untiled)  | Simplest way to implement a unary negate operation. Runs on a single AIE core and only supports tensors that fit in L1.                                                  |
| [negate/1_tiled](/docs/30_custom_ops/tutorial/negate/1_tiled)    | Expands on the previous example by using tiling to support tensor sizes that don't fit in L1 memory. |
| [negate/2_distributed](/docs/30_custom_ops/tutorial/negate/2_distributed) | Extends tiling to make full use of the 4x4 AIE overlay, distributing the negate operation across all 16 cores.    |
| [negate/3_distributed_and_vectorized](/docs/30_custom_ops/tutorial/negate/3_distributed_and_vectorized)    | Expands on the previous example by optimizing the kernel using pipelining, aliasing information and vectorization. |
| [negate/4_distributed_vectorized_2stamp](/docs/30_custom_ops/tutorial/negate/4_distributed_vectorized_2stamp)    | Expands on the previous example by increasing parallelism by using 2 stamps. |
| [negate/5_distributed_vectorized_6stamp](/docs/30_custom_ops/tutorial/negate/5_distributed_vectorized_6stamp)    | Expands on the previous example by increasing parallelism with 6 stamps. |
| [negate/6_split_channel](/docs/30_custom_ops/tutorial/negate/6_split_channel)    | Reads the input from DDR in two halves, each over a distinct shim DMA channel. |
| [negate/7_reuse_4channel](/docs/30_custom_ops/tutorial/negate/7_reuse_4channel)    | Reads the input from DDR in four parts, each over a distinct shim DMA channel. |
| [mul/0_untiled](/docs/30_custom_ops/tutorial/mul/0_untiled)    | Simple binary operation: computes the element-wise product of two vectors. Runs on a single AIE core and only supports tensors that fit in L1. |
| [mul/1_tiled](/docs/30_custom_ops/tutorial/mul/1_tiled)    | Expands on the previous example by using tiling to support tensor sizes that don't fit in L1 memory. Runs on a single AIE core. |
| [mul/2_distributed](/docs/30_custom_ops/tutorial/mul/2_distributed)    | Expands on the previous example by parallelizing ("distributing") the computations across all AIE cores. |
| [mul/3_distributed_2stamp](/docs/30_custom_ops/tutorial/mul/3_distributed_2stamp)    | Expand on the previous example by parallelizing across both AIE cores and 2 stamps. |
| [mul/4_distributed_6stamp](/docs/30_custom_ops/tutorial/mul/4_distributed_6stamp)    | Expand on the previous example by parallelizing across 6 stamps |
| [mul/5_performant_overlay](/docs/30_custom_ops/tutorial/mul/5_performant_overlay)     | Demonstrates how to tile the `2_distributed` example on the "performant" overlay |
| [conv1d/0_distributed](/docs/30_custom_ops/tutorial/conv1d/0_distributed)    | 1-D single-channel convolution distributed over the full 4x4 AIE overlay, with column-broadcast IFM, row-broadcast WTS, and an asynchronous (long-lived) WTS buffer. Worked example for the [tiling guide](/docs/30_custom_ops/tiling.md). |
| [gemm](/docs/30_custom_ops/tutorial/gemm)    | **GEMM + bias** on the 8x8x8 `aie::mmul` -- the matmul / linear / pointwise-conv archetype (a 1x1 conv *is* a GEMM: out-ch x in-ch x spatial, no halo, no sliding window). Demonstrates pre-blocking both operands into 8x8 mmul tiles, **per-row weight distribution** (`SpatialDistribute`), **bias folded into the weights** (one extra reduction tile, no separate bias DMA), multi-memtile L3->L2 staging with a wide DMA burst, and **weight streaming** from L2 when weights don't fit in L1. |
| [conv2d/conv3x3_int8_2stamp](/docs/30_custom_ops/tutorial/conv2d/conv3x3_int8_2stamp)    | **Spatial 3x3 stride-1 INT8 convolution** on the 4x8x8 `aie::mmul` (`int8 x int8 -> int32`). The full-conv regime: **halo rows** + an in-register **sliding window** (`shuffle_down_fill`), a **multi-call partial-sum reduction over input-channel depth** (OFM async ratio), and **per-output-channel int8 requantization** with a folded pre-scaled int32 bias. Reproduces a native VAIML conv layer bit-exactly. |
| [topk/0_untiled](/docs/30_custom_ops/tutorial/topk/0_untiled)    | Simple TopK operation that selects the largest K elements. Runs on a single AIE core and only supports tensors that fit in L1. |
| [topk/1_tiled](/docs/30_custom_ops/tutorial/topk/1_tiled)    | A tiled TopK implementation that supports tensor sizes that don't fit in L1 memory and demonstrates asynchronous kernel outputs. |
| [affinegrid2d/0_nonvectorized_1x1](/docs/30_custom_ops/tutorial/affinegrid2d/0_nonvectorized_1x1)    | A non-vectorized AffineGrid2D implementation running on a single AIE core. Simplest starting point for AffineGrid2D.|
| [affinegrid2d/1_vectorized_1x1](/docs/30_custom_ops/tutorial/affinegrid2d/1_vectorized_1x1)    | A tiled and vectorized AffineGrid2D implementation that supports tensor sizes that don't fit in L1 memory.|
| [affinegrid2d/2_distributed_4x4](/docs/30_custom_ops/tutorial/affinegrid2d/2_distributed_4x4)    | Expands on the previous example by distributing the AffineGrid2D operation across all 16 AIE cores.|
| [topk_indices/0_untiled](/docs/30_custom_ops/tutorial/topk_indices/0_untiled)    | TopK operation that returns the indices of the K largest elements from a 1D input tensor. Runs on a single AIE core and fits the entire input in L1 memory. |
| [reducemax](reducemax/README.md)           | Custom implementation of ONNX ReduceMax. Two variants show how the reduction axis drives the tiling: a strided outer-axis max, and a de-interleaved inner-most-axis max. |
| [gridsample2d/0_scalar_1x1](/docs/30_custom_ops/tutorial/gridsample2d/0_scalar_1x1)           | Non-vectorized scalar GridSample2D implementation running on a single AIE core. Simplest starting point for GridSample2D. |
| [gridsample2d/1_tiled_1x1](/docs/30_custom_ops/tutorial/gridsample2d/1_tiled_1x1)           | Single-core tiled GridSample2D implementation. Supports tensor sizes that don't fit in L1 memory. |
| [gridsample2d/2_tiled_1x4](/docs/30_custom_ops/tutorial/gridsample2d/2_tiled_1x4)           | Vectorized GridSample2D implementation running on 4 AIE cores. |
| [gridsample2d/3_tiled_4x4](/docs/30_custom_ops/tutorial/gridsample2d/3_tiled_4x4)           | Expands on the previous example by distributing the grid sampling operation across all 16 AIE cores. |
| [spacetodepth](/docs/30_custom_ops/tutorial/spacetodepth)           | INT8 SpaceToDepth (`blocksize=2`), showing a custom op that changes the DDR data layout (`auto_pad`, `ddr_tensor_layout`). Also demonstrates **PDLL matching**: the input model is a stock, unmodified ONNX `SpaceToDepth`, and the custom op is created by the compiler from a `match_on` pattern instead of by hand-editing the model. |
| [addmul](addmul/README.md)           | Custom implementation of `out = (A + B) * C` as a **two-phase** op whose phase boundary lives in DDR. Shows `create_l3_scratch()` / `create_phase()`, two separate L1 input buffers reached over the column- and row-broadcast families (one kernel argument each, no packed pointer arithmetic), and `setTemporalIterations()` to stream a tensor through chunk-sized memory tiles. |

## Setup (Linux)
This description assumes that we are running on Ubuntu 24.04 or later

Source the virtual environment you want to test, e.g.:
```
source <ryzen_ai_dir>/lnx64/bin/activate
```

If you have a Strix machine, source the XRT installation. Skip
this step if you only want to compile but not run the model.
```
source /opt/xilinx/xrt/setup.sh
```

Not strictly required, but highly recommended are:
```
export DEBUG_VAIML_PARTITION=1
export FLEXML_PRINT_VITISTOOLS_OUTPUT=1
```

This will make sure error messages from VAIML are shown on the console.

## Run an example

Copy all example files to some location, we call it `<mydir>` here.

To execute it, use:
```
cd <mydir>
python run.py
```

This will compile the ONNX model, run it on the AIE and output results.
For details, see the python script that is called.
