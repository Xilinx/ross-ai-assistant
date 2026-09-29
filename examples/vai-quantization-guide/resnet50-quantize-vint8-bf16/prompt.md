<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# ResNet50 — Quantize to VINT8+BF16 vs. the BF16 Design

Quick-start prompt for the **mixed-precision** skill (`/vai-quantization-guide`):
**quantize** a ResNet50 straight to a mixed **VINT8+BF16** design and show it
**matches the accuracy of the all-BF16 design** while being faster, and is
**clearly more accurate than the full-VINT8 design**.

The all-BF16 model — from the Vitis-AI
[`resnet50_bf16_cifar10` tutorial](https://github.com/amd/Vitis-AI/blob/release/6.3/versal_2ve/examples/tutorials/resnet50_bf16_cifar10)
— is the **accuracy reference** (a BF16 model is numerically close to the
non-quantized FP32 model).

## The prompt

```
Quantize the ResNet50 model to a mixed VINT8+BF16 design with the mixed-precision
skill, starting from the FP32/BF16 model. The all-BF16 design from the
Vitis-AI resnet50_bf16_cifar10 tutorial is the accuracy reference.
Goal: match the BF16 design's top-1 accuracy while running faster than all-BF16,
and be clearly more accurate than the full-VINT8 design.

Quantize to INT8 but keep the accuracy-critical layers in floating point (excluded
from INT8) so the VAIML compiler converts those islands to BF16 via
enable_f32_to_bf16_conversion. Ensure that the design is fully offloaded, that is, it achieves a single NPU partition.

/vai-quantization-guide --model input/models/resnet_trained_for_cifar10.onnx \
    --device ve2 --target-accuracy "top1>=bf16-1%" \
    --calibration-data input/calib/ifm_0.npy input/calib/ifm_1.npy input/calib/ifm_2.npy
```

After quantization, compile all three designs (BF16 reference, full VINT8, and
VINT8+BF16) for the NPU, confirm full offload, and run on the board to compare
latency and accuracy.

## Notes

- **Model**: a ResNet50 fine-tuned on CIFAR-10 (input `1x3x32x32`, 10 classes) —
  the model shipped with the Vitis-AI `resnet50_bf16_cifar10` tutorial.
  `input/fetch_model.py` downloads its weights and exports the FP32 ONNX.
- **Reference (BF16)**: the FP32 ONNX compiled with
  `enable_f32_to_bf16_conversion` (no Quark) — this *is* the
  `resnet50_bf16_cifar10` design. Its accuracy equals the FP32 model's.
- **Device**: `ve2`.
- **Accuracy metric**: **top-1** on the CIFAR-10 test set.
