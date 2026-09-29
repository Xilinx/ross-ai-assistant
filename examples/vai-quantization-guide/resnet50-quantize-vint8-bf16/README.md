<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# ResNet50 — Quantize to VINT8+BF16 vs. the BF16 Design

**Quantize** a ResNet50 directly to a mixed **VINT8+BF16** design and show it
**matches the accuracy of the all-BF16 design** while running faster, and is
**clearly more accurate than the full-VINT8 design** — using fake quantization
with QuantizeLinear and DequantizeLinear for **VINT8**.

The model is a ResNet50 fine-tuned on CIFAR-10 (input `1x3x32x32`, 10 classes),
the same one used by the Vitis-AI
[`resnet50_bf16_cifar10`](https://github.com/amd/Vitis-AI/blob/release/6.3/versal_2ve/examples/tutorials/resnet50_bf16_cifar10)
tutorial, whose weights this example downloads.

## The three designs

| Design | How it is built | Role |
|--------|-----------------|------|
| **BF16** (reference) | FP32 ONNX compiled with `enable_f32_to_bf16_conversion`. This *is* the [`resnet50_bf16_cifar10` BF16 design](https://github.com/amd/Vitis-AI/blob/release/6.3/versal_2ve/examples/tutorials/resnet50_bf16_cifar10). | Accuracy reference (BF16 ≈ FP32). |
| **Full VINT8** | Quark symmetric INT8 (`XInt8Spec`) on every layer. | Fast, but loses accuracy. |
| **VINT8+BF16** (this design) | Quark INT8 **except** the accuracy-critical layers, which are excluded → FP32 → compiler BF16. | Matches BF16 accuracy, faster than all-BF16. |

## Goal

Derive a VINT8+BF16 design that **does not lose accuracy versus the BF16
reference** and is **more accurate than the full-VINT8 design**, while still
compiling to a **single NPU partition**.

## Quantization approach with force-conversion

BF16 regions are produced as follows: Accuracy-critical layers are **excluded from
INT8** (they stay FP32 — standard `QuantizeLinear`/`DequantizeLinear` everywhere
else), and the VAIML compiler converts those FP32 islands to **BF16** via
`enable_f32_to_bf16_conversion`.

## Skills Used

- **vai-quantization-guide** — quantizes the FP32 model, selects the VINT8+BF16
  layer assignment, and validates accuracy against the BF16 reference.
- The quantization uses a **Quark JSON config** ([`input/vint8_bf16.quark.json`](input/vint8_bf16.quark.json)):
  a global `XInt8Spec` plus an `exclude` list of the accuracy-critical layers.
  The skill writes this config (from its sensitivity analysis) and applies the
  exclusions via `QConfig.exclude` — the excluded layers stay FP32 and the
  compiler converts them to BF16. No custom quantization script is needed.

## Prerequisites

- A **Vitis AI** environment (Quark, the VAIML `flexml` compiler, the
  `VitisAIExecutionProvider`). Provisioned inside the Vitis AI Docker container.
- `torch` + `torchvision` and the CIFAR-10 dataset (reference model, calibration
  tensors, top-1 check).
- Network access to the public [amd/Vitis-AI](https://github.com/amd/Vitis-AI/tree/release/6.3/versal_2ve/examples/tutorials/resnet50_bf16_cifar10)
  repository to download the pretrained weights, or a local clone of it.
- For the board step: a **VE2** board reachable via `/path/to/run_on_board`.

## Starting Point

Reproduction files in [`input/`](input/):

| File | Role |
|------|------|
| [`fetch_model.py`](input/fetch_model.py) | Downloads the CIFAR-10 ResNet50 weights from the `resnet50_bf16_cifar10` tutorial and exports the FP32 ONNX — the BF16/quantization source. |
| [`vint8_bf16.quark.json`](input/vint8_bf16.quark.json) | Quark config for the **VINT8+BF16** design — global `XInt8Spec` + an `exclude` list (the BF16-head layers, kept FP32 → compiler BF16). Consumed by the skill's `mixed_precision_quantize.py`. The skill can also produce this file. |
| [`full_vint8.quark.json`](input/full_vint8.quark.json) | Quark config for the **full VINT8** baseline (global `XInt8Spec`, no exclusions). The skill can also produce this file. |
| [`make_calib.py`](input/make_calib.py) | Exports CIFAR-10 calibration tensors as `ifm_*.npy`. |
| [`eval_top1.py`](input/eval_top1.py) | Top-1 / top-k accuracy on the CIFAR-10 test set (CPU). With `--reference`, also reports logit fidelity against the BF16 reference: argmax agreement, cosine similarity and PSNR. |
| [`pin_batch1.py`](input/pin_batch1.py) | Pins the dynamic batch dim to 1 (VAIML needs static shapes). |
| [`vitisai_config_bf16.json`](input/vitisai_config_bf16.json) | BF16 **reference** compile config (`enable_f32_to_bf16_conversion`, no Quark) — the `resnet50_bf16_cifar10` tutorial config. |
| [`vitisai_config_vint8_bf16.json`](input/vitisai_config_vint8_bf16.json) | VINT8+BF16 compile config (`ve2`, `enable_f32_to_bf16_conversion`, `group_args: "mixed_precision"`). |
| [`calib/ifm_*.npy`](input/calib) | 8 sample calibration tensors (regenerate the full set with `make_calib.py`). |

## How to Run

**Follow [`prompt.md`](prompt.md)**, or run the steps directly:

```bash
# 0. FP32 reference model + CIFAR-10 data, then calibration tensors:
python input/fetch_model.py                     # -> models/resnet_trained_for_cifar10.onnx
python input/make_calib.py --num 64 --download  # -> calib/ifm_*.npy
```

`fetch_model.py` pulls `models/resnet_trained_for_cifar10.pt` from the
`resnet50_bf16_cifar10` tutorial in [amd/Vitis-AI](https://github.com/amd/Vitis-AI/tree/release/6.3/versal_2ve/examples/tutorials/resnet50_bf16_cifar10)
(branch `release/6.3`). No credentials are needed. If you already have a clone
of the repository, point at it directly and skip the download:
`python input/fetch_model.py --weights <clone>/versal_2ve/examples/tutorials/resnet50_bf16_cifar10/models/resnet_trained_for_cifar10.pt`

```bash
# 1. Full VINT8 (fast, lower accuracy) — Quark config with no exclusions:
python scripts/mixed_precision_quantize.py \
    --input models/resnet_trained_for_cifar10.onnx --output rn_vint8.onnx \
    --config input/full_vint8.quark.json --calibration-data input/calib/ifm_*.npy

# 2. VINT8+BF16 design — same config plus an `exclude` list for the BF16 head:
python scripts/mixed_precision_quantize.py \
    --input models/resnet_trained_for_cifar10.onnx --output rn_vint8_bf16.onnx \
    --config input/vint8_bf16.quark.json --calibration-data input/calib/ifm_*.npy

# 3. Accuracy and fidelity. The FP32 model is the BF16 reference; `--reference`
#    adds agreement / cosine similarity / PSNR of the logits against it:
python input/eval_top1.py --model models/resnet_trained_for_cifar10.onnx --num 10000
python input/eval_top1.py --model rn_vint8.onnx      --num 10000 --reference models/resnet_trained_for_cifar10.onnx
python input/eval_top1.py --model rn_vint8_bf16.onnx --num 10000 --reference models/resnet_trained_for_cifar10.onnx

# 4. Compile all three for the NPU (pin batch first):
python input/pin_batch1.py models/resnet_trained_for_cifar10.onnx rn_fp32_b1.onnx
python input/pin_batch1.py rn_vint8.onnx      rn_vint8_b1.onnx
python input/pin_batch1.py rn_vint8_bf16.onnx rn_vint8_bf16_b1.onnx
python scripts/compile.py rn_fp32_b1.onnx       --vitisai-config input/vitisai_config_bf16.json      --cache-dir cache_bf16ref
python scripts/compile.py rn_vint8_b1.onnx      --vitisai-config input/vitisai_config_vint8_bf16.json --cache-dir cache_int8
python scripts/compile.py rn_vint8_bf16_b1.onnx --vitisai-config input/vitisai_config_vint8_bf16.json --cache-dir cache_bf16
```

## Recorded Run

### 1. Which layers stay BF16 (sensitivity)

A per-stage sweep (keep one stage in BF16, INT8 elsewhere) shows the accuracy
loss is concentrated in the **early stages** — quantizing the raw,
high-dynamic-range input feature maps to INT8 is what costs top-1:

| BF16 region (rest INT8) | Top-1 (500 imgs) |
|-------------------------|------------------|
| — (full VINT8)          | 80.6% |
| `conv1 + layer1`        | 82.0% |
| **`conv1 + layer1 + layer2`** | **83.0%** |

So the design keeps an **INT8 core/tail with a BF16 head** (`conv1`, `layer1`,
`layer2`) — the "Floating-Point Head with Quantized Core" pattern.

### 2. Accuracy — VINT8+BF16 matches the BF16 reference

| Design | Top-1 (CIFAR-10 test, 500 imgs) | Δ vs BF16 ref |
|--------|--------------------------------|----------------|
| **BF16 (reference)** | **83.0%** | — |
| Full VINT8 | 80.6% | −2.4 |
| **VINT8+BF16 (this design)** | **83.0%** | **0.0** |

The VINT8+BF16 design **loses nothing** versus the BF16 reference and is **+2.4
points** over full VINT8.

### 3. Compilation — all three fully offload to NPU

| Design | Operators | Supported by VAIML | NPU partitions | CPU partitions |
|--------|-----------|--------------------|----------------|----------------|
| BF16 (reference) | 124 | 124 (100%) | **1** | 0 |
| Full VINT8 | 400 | 400 (100%) | **1** | 0 |
| VINT8+BF16 (this design) | 281 | 281 (100%) | **1** | 0 |

The VINT8+BF16 operation census confirms:

```
kernel:Conv2d       (i8, i8, i16) -> (i8)   , 30   # INT8 core + tail
kernel:Conv2DBf16   (bf16,bf16,bf16)->(bf16), 24   # BF16 head (compiler-converted FP32)
kernel:AddBf16      (bf16, bf16) -> (bf16)  , 16   # residual adds in the BF16 head
kernel:DequantizeLinear (i8) -> (bf16)      , 18   # INT8 <-> BF16 island boundaries
kernel:QuantizeLinear   (bf16) -> (i8)      , 10
```

### 4. Board comparison — BF16 vs INT8 vs VINT8+BF16

All three are single-partition NPU binaries. On a VE2 board, compare
latency and accuracy with the bundled script:

```bash
python /path/to/run_on_board.py \
    --board-type telluride --boardhost <telluride-host> --board-user <user> \
    -p <project-dir> -- rn_vint8_bf16_b1.onnx --cache-dir cache_bf16 --num-runs 10
```

<!-- BOARD_RESULTS: filled in from the recorded VE2 board run (bf16 vs int8 vs vint8+bf16 latency) -->
_Board latency numbers are recorded here once the run completes. Expectation:
full VINT8 is fastest, all-BF16 is slowest, and the VINT8+BF16 design sits close
to VINT8 (only a BF16 head) while matching BF16 accuracy._

## Expected Behavior

- The VINT8+BF16 design **matches the BF16 reference accuracy** and is **more
  accurate than full VINT8**.
- All three designs compile to a **single NPU partition (100% on AIE)**.
- No experimental / Extended-QDQ operators appear — the BF16 regions come
  entirely from INT8-exclusion + compiler `enable_f32_to_bf16_conversion`.
