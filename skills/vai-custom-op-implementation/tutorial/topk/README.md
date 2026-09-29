<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# TopK Custom Op Tutorial

This directory contains examples of implementing a TopK custom operation for VitisAI.

## What is TopK?

The TopK operation selects the K largest elements from the input tensor. Given an input tensor of shape `[batch, n]`, it outputs a tensor of shape `[batch, k]` containing the top K values from each batch.
