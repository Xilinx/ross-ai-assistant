<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# Mixed Precision Support

## Table of Contents

- [Introduction](#introduction)
- [Supported Patterns](#supported-patterns)
  - [Non-quantized (int8)](#non-quantized-int8)
  - [Pure Quantized (int8)](#pure-quantized-int8)
  - [Quantized Core with Floating-Point Tail](#quantized-core-with-floating-point-tail)
  - [Floating-Point Head with Quantized Core](#floating-point-head-with-quantized-core)
  - [Floating-Point Head and Tail with Quantized Core](#floating-point-head-and-tail-with-quantized-core)
  - [Quantized Head and Tail with Floating-Point Middle](#quantized-head-and-tail-with-floating-point-middle)
  - [Four-Island Models](#four-island-models)
  - [MX6 Models](#mx6-models)
  - [Extended Quantization (EQDQ) Models](#extended-quantization-eqdq-models)
- [Configuration](#configuration)
  - [Compiler Flags](#compiler-flags)
  - [VitisAI Execution Provider Config](#vitisai-execution-provider-config)
- [FP16 Support (AIE2ps Only)](#fp16-support-aie2ps-only)
- [BF16 and ONNXRuntime](#bf16-and-onnxruntime)
- [Examples](#examples)
  - [Running the Examples](#running-the-examples)
  - [Running on a Board](#running-on-a-board)
  - [Example Reference](#example-reference)
- [Limitations and Known Issues](#limitations-and-known-issues)

---

## Introduction

Mixed precision support enables VAIML to compile and execute models that combine
different numeric data types within a single graph. A typical mixed-precision
model contains quantized (int8) subgraphs — expressed via `QuantizeLinear` /
`DequantizeLinear` (QDQ) patterns — alongside floating-point subgraphs in
`float32`, `bfloat16`, or `float16`.

This is useful for models where:
- Only part of the graph is quantized (e.g. a quantized backbone with a
  floating-point detection head).
- Certain operations require higher precision for numerical accuracy.
- Different layers have been independently quantized to int8 with
  floating-point transitions between them.

The VAIML compiler recognizes QDQ patterns and preserves them as quantized
int8 operators on AIE, while converting the surrounding floating-point
operations to the target AIE data type (typically bfloat16).

---

## Supported Patterns

The following sections describe the precision patterns that the compiler
supports, grouped by how the floating-point and quantized regions are
arranged. Each pattern has a corresponding ONNX example model in the
[`models/`](/docs/10_onnx_support/mixed_precision/models) directory.

### Non Quantized (int8)

Operations that support the int8 tensor element type are not quantized with QDQ patterns. Instead, they are represented as pure/raw int8 ops. All operations run as int8 on AIE.

In the example illustrated below, op1 is a raw int8 op but op2 is a QDQ op.

```
input(int8) → op1 → DQ → op2 → Q → output(int8)
```

| Model | Inputs | Outputs |
|-------|--------|---------|
| [`raw_int8.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/raw_int8/raw_int8.onnxtxt) | `int8[16,32,64]`, `int8[32,16,64]` | `int8[16,32,64]` |

### Pure Quantized (int8)

The entire graph is quantized with QDQ patterns. All operations run as int8
on AIE.

```
input(int8) → DQ → op → Q → DQ → op → Q → output(int8)
```

| Model | Inputs | Outputs |
|-------|--------|---------|
| [`vint8.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8/vint8.onnxtxt) | `int8[16,32,64]`, `int8[32,16,64]` | `int8[16,32,64]` |

### Quantized Core with Floating-Point Tail

The model starts quantized and transitions to floating-point at the output.
The quantized subgraph runs as int8 on AIE; the floating-point tail runs at
the target FP precision.

```
input(int8) → DQ → [quantized ops] → Q → DQ → [fp ops] → output(fp)
```

| Model | Outputs | Device | Description |
|-------|---------|--------|-------------|
| [`vint8_fp32_tobf16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_fp32_tobf16/vint8_fp32_tobf16.onnxtxt) | `float[16,32,128]` | all | FP32 tail |
| [`vint8_fp32_tofp16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_fp32_tofp16/vint8_fp32_tofp16.onnxtxt) | `float[16,32,128]` | **ve2** | FP32 tail |
| [`vint8_bf16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_bf16/vint8_bf16.onnxtxt) | `bfloat16[16,32,128]` | all | BF16 tail |
| [`vint8_fp16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_fp16/vint8_fp16.onnxtxt) | `float16[16,32,128]` | **ve2** | FP16 tail (AIE2ps only) |

### Floating-Point Head with Quantized Core

The model starts with floating-point operations and transitions to quantized
int8 for the output.

```
input(fp) → [fp ops] → Q → DQ → [quantized ops] → Q → output(int8)
```

| Model | Inputs | Device | Description |
|-------|--------|--------|-------------|
| [`fp32_vint8_tobf16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/fp32_vint8_tobf16/fp32_vint8_tobf16.onnxtxt) | `float[16,32,64]` | all | FP32 head |
| [`fp32_vint8_tofp16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/fp32_vint8_tofp16/fp32_vint8_tofp16.onnxtxt) | `float[16,32,64]` | **ve2** | FP32 head |
| [`bf16_vint8.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/bf16_vint8/bf16_vint8.onnxtxt) | `bfloat16[16,32,64]` | all | BF16 head |
| [`fp16_vint8.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/fp16_vint8/fp16_vint8.onnxtxt) | `float16[16,32,64]` | **ve2** | FP16 head (AIE2ps only) |

### Floating-Point Head and Tail with Quantized Core

The model has floating-point regions at both ends with a quantized int8 core
in the middle.

```
input(fp) → [fp ops] → Q → DQ → [quantized ops] → Q → DQ → [fp ops] → output(fp)
```

| Model | Inputs | Outputs | Device | Description |
|-------|--------|---------|--------|-------------|
| [`fp32_vint8_fp32_tobf16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/fp32_vint8_fp32_tobf16/fp32_vint8_fp32_tobf16.onnxtxt) | fp32 | fp32 | all | FP32 head + tail |
| [`fp32_vint8_fp32_tofp16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/fp32_vint8_fp32_tofp16/fp32_vint8_fp32_tofp16.onnxtxt) | fp32 | fp32 | **ve2** | FP32 head + tail |
| [`bf16_vint8_bf16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/bf16_vint8_bf16/bf16_vint8_bf16.onnxtxt) | bf16 | bf16 | all | BF16 head + tail |
| [`fp16_vint8_fp16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/fp16_vint8_fp16/fp16_vint8_fp16.onnxtxt) | fp16 | fp16 | **ve2** | FP16 head + tail (AIE2ps only) |

### Quantized Head and Tail with Floating-Point Middle

The model has quantized int8 regions at the input and output with
floating-point operations in between.

```
input(fp/int8) → Q → DQ → [quantized ops] → Q → DQ → [fp ops] → Q → DQ → [quantized ops] → Q → output(int8)
```

| Model | Middle Type | Device | Description |
|-------|-------------|--------|-------------|
| [`vint8_fp32_vint8_tobf16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_fp32_vint8_tobf16/vint8_fp32_vint8_tobf16.onnxtxt) | fp32 | all | FP32 middle |
| [`vint8_fp32_vint8_tofp16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_fp32_vint8_tofp16/vint8_fp32_vint8_tofp16.onnxtxt) | fp32 | **ve2** | FP32 middle |
| [`vint8_bf16_vint8.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_bf16_vint8/vint8_bf16_vint8.onnxtxt) | bf16 | all | BF16 middle |
| [`vint8_fp16_vint8.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_fp16_vint8/vint8_fp16_vint8.onnxtxt) | fp16 | **ve2** | FP16 middle (AIE2ps only) |

### Four-Island Models

Models with four or more alternating quantized/floating-point regions. These
require `match-power-of-two-quant-kernel=1` in the compiler configuration.

```
input → [region1] → [region2] → [region3] → [region4] → output
```

| Model | Device | Description |
|-------|--------|-------------|
| [`fp32_vint8_four_islands_tobf16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/fp32_vint8_four_islands_tobf16/fp32_vint8_four_islands_tobf16.onnxtxt) | all | FP32/int8 four-island (to-bf16 conversion) |
| [`fp32_vint8_four_islands_tofp16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/fp32_vint8_four_islands_tofp16/fp32_vint8_four_islands_tofp16.onnxtxt) | **ve2** | FP32/int8 four-island (to-fp16 conversion) |
| [`vint8_fp32_four_islands_tobf16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_fp32_four_islands_tobf16/vint8_fp32_four_islands_tobf16.onnxtxt) | all | Int8/fp32 four-island (to-bf16 conversion) |
| [`vint8_fp32_four_islands_tofp16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_fp32_four_islands_tofp16/vint8_fp32_four_islands_tofp16.onnxtxt) | **ve2** | Int8/fp32 four-island (to-fp16 conversion) |

### MX6 Models

Models with MX6 convolution and vint8 fake quantized operations or floating-point
regions. These require `match-conv-mx6=1` (enabled by default) and depending on the model `match-gemm-mx6=1`. `fp16` is not supported with these models as the force-conversion floating-point type.

| Model | Device | Description |
|-------|--------|-------------|
| [`mx6_vint8.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/mx6_vint8/mx6_vint8.onnxtxt) | **ve2** | MX6/int8 multi-island (to-bf16 conversion) |
| [`mx6_fp32_tobf16.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/mx6_fp32_tobf16/mx6_fp32_tobf16.onnxtxt) | **ve2** | MX6/FP32 multi-island (to-fp16 conversion) |

### Extended Quantization (EQDQ) Models

Models using `com.amd.quark` Extended QuantizeLinear / DequantizeLinear (EQDQ)
operators. These operators represent quantization from the AMD Quark quantizer
and are compiled to bf16/f16 kernels on AIE (they are NOT matched as int8
operators). EQDQ models do not require `enable_f32_to_bf16_conversion` since
the EQDQ operators already signal the target precision.

```
input(fp) → EQ → EDQ → [bf16/f16 ops] → EQ → EDQ → output(fp)
```

| Model | Device | Description |
|-------|--------|-------------|
| [`eqdq.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/eqdq/eqdq.onnxtxt) | all | Pure EQDQ — Conv + Relu both wrapped in Extended QDQ |
| [`vint8_eqdq.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/vint8_eqdq/vint8_eqdq.onnxtxt) | all | Mixed vint8/EQDQ — 2 vint8 islands (Conv) + 2 EQDQ islands (Sigmoid, Add) |
| [`conv_vint8_eqdq.onnxtxt`](/docs/10_onnx_support/mixed_precision/models/conv_vint8_eqdq/conv_vint8_eqdq.onnxtxt) | all | QDQ Conv+Relu (vint8) followed by EQDQ Conv+Relu, output cast via Q/DQ |

---

## Configuration

### Compiler Flags

The following flags control mixed precision behavior in the VAIML compiler:

| Flag | Description |
|------|-------------|
| `enable_f32_to_bf16_conversion` | Converts floating-point (fp32) operations to bfloat16 on AIE. Required for bf16 and fp32 models where the AIE executes in bfloat16. |
| `enable_f32_to_f16_conversion` | Converts floating-point (fp32) operations to float16 on AIE. Required for fp16 models on AIE2ps. |
| `group_args: "mixed_precision"` | Enables the consolidated mixed-precision flow, which includes QDQ-aware FP conversion (the compiler preserves quantized subgraphs and only converts the floating-point portions). |
| `device: "ve2"` | Targets the AIE2ps platform, which is required for FP16 support. |

Internally, the `group_args: "mixed_precision"` flag enables:
- `match-power-of-two-quant-kernel=1` — matches MLLIB QuantizeLinear kernel for a BF16/F16 -> Int8 xten_nn.quantize operator when
  operation is a power-of-two scale and has zero zero_point.

### VitisAI Execution Provider Config

Standard configuration for mixed precision models:

```json
{
  "passes": [
    {
      "name": "vaiml_partition",
      "plugin": "vaip-pass_vaiml_partition",
      "vaiml_config": {
        "keep_outputs": true,
        "optimize_level": 2,
        "enable_f32_to_bf16_conversion": true,
        "logging_level": "info",
        "group_args": "mixed_precision"
      }
    }
  ],
  "target": "VAIML",
  "targets": [
    { "name": "VAIML", "pass": ["vaiml_partition"] }
  ]
}
```

For **AIE2ps (ve2)** targets, add `"device": "ve2"` to the
`vaiml_config` section. See [`vitisai_config_ve2.json`](/docs/10_onnx_support/mixed_precision/vitisai_config_ve2.json).

---

## FP16 Support (AIE2ps Only)

Float16 (FP16) operations are natively supported only on the AIE2ps architecture. This corresponds to device `ve2` in the VAIML
configuration.

**NOTE**: All `*_tofp16` or fp16-including models require a `ve2` target.

When running the test suite, FP16 tests are **automatically skipped** unless
the `--device ve2` flag is passed:

```bash
# Skip fp16 tests (default):
python scripts/run_tests.py --stage compile

# Include fp16 tests (ve2 board required):
python scripts/run_tests.py --device ve2 --stage exec
```

---

## BF16 and ONNXRuntime

**ONNXRuntime does not natively support bfloat16.** When executing mixed
precision models through the VitisAI Execution Provider in ONNXRuntime, the
**fp32 variant** of the model is used in place of the bf16 variant. The VAIML
compiler performs the fp32-to-bf16 conversion automatically when
`enable_f32_to_bf16_conversion` is enabled.

The mapping of bf16 models to their fp32 ORT substitutes:

| BF16 Model | ORT FP32 Substitute |
|------------|---------------------|
| `vint8_bf16.onnx` | `vint8_fp32_tobf16.onnx` |
| `bf16_vint8.onnx` | `fp32_vint8_tobf16.onnx` |
| `bf16_vint8_bf16.onnx` | `fp32_vint8_fp32_tobf16.onnx` |
| `vint8_bf16_vint8.onnx` | `vint8_fp32_vint8_tobf16.onnx` |

---

## Examples

All example models are located in the [`models/`](/docs/10_onnx_support/mixed_precision/models) directory with both
`.onnx` (binary) and `.onnxtxt` (human-readable) formats.

### Running the Examples

#### Prerequisites

Activate the Vitis AI environment. Inside the Vitis AI Docker container it is
already provisioned and nothing needs sourcing; otherwise source its activate
script:
```bash
source <path-to-vitis-ai-activate-script>
```

If you have a board available, also source XRT:
```bash
source /opt/xilinx/xrt/setup.sh
```

Recommended environment variables for verbose error output:
```bash
export DEBUG_VAIML_PARTITION=1
export FLEXML_PRINT_VITISTOOLS_OUTPUT=1
```

#### List available tests

```bash
python scripts/run_tests.py --list
```

#### Compile only (no board required)

```bash
# All stx-compatible tests:
python scripts/run_tests.py --stage compile

# A specific test:
python scripts/run_tests.py --test vint8_fp32_tobf16 --stage compile

# Include ve2/fp16 tests:
python scripts/run_tests.py --device ve2 --stage compile
```

#### Compile and execute (board required)

```bash
python scripts/run_tests.py --stage exec

# With custom tolerances:
python scripts/run_tests.py --stage exec --rtol 0.02 --atol 0.02
```

### Running on a Board

By default, use the `run_on_board.py` script provided by the `vai-custom-op-implementation` skill. It handles SSH, file syncing, environment setup, custom-op registration, and board locking for Telluride targets, and can run locally or remotely.
If the user tells you to do board runs another way, follow their instructions and use their commands.

### Example Reference

| Test Name | Category | Model Structure | Device | What It Demonstrates |
|-----------|----------|-----------------|--------|----------------------|
| `raw_int8` | int8 only | `int8 → int8` | all | Non-quantized int8 model — supported ops in raw int8 |
| `vint8` | int8 only | `int8 → int8` | all | Fully quantized baseline — all ops in QDQ patterns run as int8 on AIE |
| `vint8_fp32_tobf16` | int8 → fp | `int8 → fp32` | all | Quantized core with fp32 output tail (force-converted bf16 operations) |
| `vint8_fp32_tofp16` | int8 → fp | `int8 → fp32` | **ve2** | Quantized core with fp32 output tail (force-converted fp16 operations) |
| `vint8_bf16` | int8 → fp | `int8 → bf16` | all | Same as above but output in bf16; ORT uses fp32 substitute |
| `vint8_fp16` | int8 → fp | `int8 → fp16` | **ve2** | Same as above but output in fp16; requires AIE2ps |
| `fp32_vint8_tobf16` | fp → int8 | `fp32 → int8` | all | FP32 input head feeding into quantized core (force-converted bf16 operations)|
| `fp32_vint8_tofp16` | fp → int8 | `fp32 → int8` | **ve2** | FP32 input head feeding into quantized core (force-converted fp16 operations) |
| `bf16_vint8` | fp → int8 | `bf16 → int8` | all | BF16 input head; ORT uses fp32 substitute |
| `fp16_vint8` | fp → int8 | `fp16 → int8` | **ve2** | FP16 input head; requires AIE2ps |
| `fp32_vint8_fp32_tobf16` | fp → int8 → fp | `fp32 → int8 → fp32` | all | FP32 head + tail with quantized middle (multi-input model) |
| `fp32_vint8_fp32_tofp16` | fp → int8 → fp | `fp32 → int8 → fp32` | **ve2** | FP32 head + tail with quantized middle (multi-input model) |
| `bf16_vint8_bf16` | fp → int8 → fp | `bf16 → int8 → bf16` | all | BF16 head + tail; ORT uses fp32 substitute |
| `fp16_vint8_fp16` | fp → int8 → fp | `fp16 → int8 → fp16` | **ve2** | FP16 head + tail; requires AIE2ps |
| `mx6_vint8` | multi-island | `... int8 → mx6 → int8 ...` | **ve2** | MX6 operators + int8 quantized operators |
| `mx6_fp32_tobf16` | multi-island | `... fp32 → mx6 → fp32 ...` | **ve2** | MX6 operators + fp32 operators (force-converted bf16 operations) |
| `vint8_fp32_vint8_tobf16` | int8 → fp → int8 | `int8 → fp32 → int8` | all | Quantized head and tail with fp32 middle (multi-input model) |
| `vint8_fp32_vint8_tofp16` | int8 → fp → int8 | `int8 → fp32 → int8` | **ve2** | Quantized head and tail with fp32 middle (multi-input model) |
| `vint8_bf16_vint8` | int8 → fp → int8 | `int8 → bf16 → int8` | all | Same with bf16 middle; ORT uses fp32 substitute |
| `vint8_fp16_vint8` | int8 → fp → int8 | `int8 → fp16 → int8` | **ve2** | Same with fp16 middle; requires AIE2ps |
| `fp32_vint8_four_islands_tobf16` | 4 islands | `fp32 → int8 → fp32 → int8` | all | FP32/int8 four-island (to-bf16 conversion) |
| `fp32_vint8_four_islands_tofp16` | 4 islands | `fp32 → int8 → fp32 → int8` | **ve2** | FP32/int8 four-island (to-fp16 conversion) |
| `vint8_fp32_four_islands_tobf16` | 4 islands | `int8 → fp32 → int8 → fp32` | all | Int8/fp32 four-island (to-bf16 conversion) |
| `vint8_fp32_four_islands_tofp16` | 4 islands | `int8 → fp32 → int8 → fp32` | **ve2** | Int8/fp32 four-island (to-fp16 conversion) |
| `eqdq` | eqdq only | `fp → eqdq → fp` | all | Pure EQDQ — Conv + Relu wrapped in Extended QDQ (bf16 kernels) |
| `vint8_eqdq` | multi-island | `int8 → eqdq → int8 → eqdq → int8` | all | 2 vint8 Conv islands + 2 EQDQ islands (Sigmoid, Add) |
| `conv_vint8_eqdq` | int8 → eqdq | `int8(conv+relu) → eqdq(conv+relu)` | all | QDQ Conv+Relu (vint8) then EQDQ Conv+Relu |

---

## Limitations and Known Issues

- **BF16 not supported in ONNXRuntime**: BF16 models cannot be loaded directly
  by ORT. Use the fp32 variant with `enable_f32_to_bf16_conversion` instead.
- **FP16 requires AIE2ps**: Float16 operations are only supported on the
  `ve2` device. Tests are skipped automatically on other platforms.
- **Tolerance considerations**: Comparisons between fp32 CPU reference and
  bf16/fp16 AIE execution use relaxed tolerances (default: 1% relative, 0.015
  absolute) due to reduced floating-point precision.
