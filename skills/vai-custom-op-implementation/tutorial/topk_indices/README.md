<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# TopK Indices Custom Op Tutorial

This directory contains an example of implementing a TopK Indices custom operation for VitisAI, which is similar to the [ONNX TopK operation](https://onnx.ai/onnx/operators/onnx__TopK.html) but returns only the indices output.

## What is TopK Indices?

The TopK Indices operation identifies the K largest elements from a 1D input tensor and returns their **indices** (not the values). Given:
- An input tensor (values) of shape `[N]` containing numerical values
- A parameter K specifying how many top elements to find

It outputs a tensor of shape `[K]` containing the indices of the K largest elements, sorted in descending order by their corresponding values.

This operation is commonly used in feature detection, ranking, and selection tasks where you need to know the positions of the highest-scoring elements rather than the scores themselves.

## Overview

The implementation in `0_untiled` demonstrates:
- **Single-Core Processing**: Processes the entire input on a single AIE core
- **In-Memory Operation**: Fits the full input tensor in L1 memory (up to 10K elements for bfloat16)
- **Index Tracking**: Maintains a sorted list of top K indices using an insertion sort algorithm
- **Output Encoding**: Returns indices as int16 values.

Note: The runtime zero-pads the 16-bit results by the right to 32 bits before returning them to the user. The kernel's results are therefore the upper 16 bits of each 32-bit result value.
