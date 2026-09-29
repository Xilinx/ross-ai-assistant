<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# Custom Op example: 1-D convolution

The examples in this directory demonstrate a 1-D, single-channel convolution
implemented as a custom op. The operator computes, for each output index `o`:

```
out[o] = sum_{k=0..K-1} in[o + k] * kernel[k]
```

This is the same operation as a stride-1, non-padded 1-D convolution with a
single input/output channel, equivalent to ONNX `Conv` with a 1-D kernel.

The shape convention used in the examples is `out[L_out] = in[L_in] *
kernel[K]` with `L_out = L_in - K + 1`.

This tutorial is used as an example in the [tiling guide](../../tiling.md).
