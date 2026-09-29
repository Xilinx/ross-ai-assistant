---
name: vai-fe-args
license: Apache-2.0 WITH LLVM-exception
compatibility: Requires VitisAI 6.3+
metadata:
  version: 6.3.0
  stage: beta
description: Determine the correct fe_args and vaiml_config flags for a mixed-precision
  ONNX model based on its quantization pattern. Analyzes model structure to identify
  island topology, quantization types, and required compiler features. Targets single
  NPU partition (embedded Telluride scenario).
---
<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->

# FE Args Skill

## Access command

This skill must always be accessed through the worker, never in a different way.
Allowed workers: `vai-mixed-precision-worker`, or the `vai-flag-configuration`
skill, which is its own entry point.

## Description

Determines the correct `fe_args` frontend flags and `vaiml_config` settings for a
mixed-precision ONNX model. Analyzes the model's quantization topology to select
appropriate compiler flags for single-NPU-partition compilation.

`fe_args` and `fe_experiment` are aliases, as are `group_args` and
`experiments`; compiler logs may use either spelling.
Prefer `fe_args` and `group_args` in new configs.

## Parse Arguments

This skill expects arguments in this form:

```
--model <onnx_model> [--analyze] [--device stx|ve2] [--output-config <vitisai_config.json>]
```

- `--model <path>`: **(MANDATORY)** Path to the quantized ONNX model
- `--analyze`: **(OPTIONAL)** Print detailed analysis without generating config
- `--device <stx|ve2>`: **(OPTIONAL, default: ve2)** Target device
- `--output-config <path>`: **(OPTIONAL)** Path to write generated vitisai_config.json

## Model Classification

Analyze the model and classify into one of these categories:

### Category 1: Pure VINT8

**Pattern**: Entire graph is wrapped in QuantizeLinear/DequantizeLinear (QDQ)
```
input(int8) → DQ → op → Q → DQ → op → Q → output(int8)
```

**Required flags**:
```json
{
  "enable_f32_to_bf16_conversion": false,
  "fe_args": ""
}
```

No special flags needed. The compiler handles pure QDQ models natively.

### Category 2: Two-Island Models (BF16/FP16 + VINT8)

**Pattern**: One floating-point region and one quantized region.

Sub-patterns:
- **Float head → VINT8 core**: `input(fp) → [fp ops] → Q → DQ → [int8 ops] → output`
- **VINT8 core → Float tail**: `input(int8) → [int8 ops] → Q → DQ → [fp ops] → output`
- **Float head + tail, VINT8 middle**: `input(fp) → [fp] → Q/DQ → [int8] → Q/DQ → [fp] → output`
- **VINT8 head + tail, Float middle**: `input(int8) → [int8] → Q/DQ → [fp] → Q/DQ → [int8] → output`

**Required flags**:
```json
{
  "enable_f32_to_bf16_conversion": true,
  "fe_args": "match-power-of-two-quant-kernel=1"
}
```

For **ve2** (FP16 instead of BF16):
```json
{
  "enable_f32_to_f16_conversion": true,
  "fe_args": "match-power-of-two-quant-kernel=1",
  "device": "ve2"
}
```

**Explanation**: `match-power-of-two-quant-kernel=1` enables the MLLIB
QuantizeLinear kernel matcher for BF16/FP16 → Int8 transitions at island
boundaries. Without this, the quantize operation between floating-point and
int8 regions won't be recognized as an NPU kernel.

### Category 3: Multi-Island Models (4+ alternating regions)

**Pattern**: Multiple alternating quantized and floating-point regions.
```
[int8] → [fp] → [int8] → [fp] → [int8] → ...
```

**Required flags**:
```json
{
  "enable_f32_to_bf16_conversion": true,
  "fe_args": "match-power-of-two-quant-kernel=1"
}
```

**Explanation**:
- `match-power-of-two-quant-kernel=1` — same as Category 2

**The shorthand** `group_args: "mixed_precision"` is equivalent to both flags:
```json
{
  "enable_f32_to_bf16_conversion": true,
  "group_args": "mixed_precision"
}
```
This expands internally to: `match-power-of-two-quant-kernel=1`

### Category 4: Pure BF16/FP16 (Non-Quantized, Force-Converted)

**Pattern**: Entire model is floating-point, to be force-converted to bf16/fp16.

**Required flags**:
```json
{
  "enable_f32_to_bf16_conversion": true,
  "fe_args": ""
}
```

For FP16 (ve2 only):
```json
{
  "enable_f32_to_f16_conversion": true,
  "device": "ve2"
}
```

## Analysis Algorithm

```python
def classify_model(model):
    """Classify model quantization topology."""
    has_qdq = any(n.op_type in ("QuantizeLinear", "DequantizeLinear")
                  for n in model.graph.node)
    # com.amd.quark quantization ops indicate a specialized method (e.g.
    # block floating-point or extended per-layer precision) handled by an
    # optional plugin skill, not by the core pipeline.
    has_specialized = any(n.domain == "com.amd.quark" and "Quantize" in n.op_type
                          for n in model.graph.node)
    has_naked_fp = has_floating_point_ops_without_qdq(model)

    # Count islands (contiguous regions of same precision)
    islands = count_precision_islands(model)

    if has_specialized:
        # Delegate flag determination to the matching plugin skill under
        # skills/experimental-methods/. If none is installed, report that the
        # model uses a specialized quantization the core pipeline cannot flag.
        return "SPECIALIZED"
    if has_qdq:
        if not has_naked_fp:
            return "PURE_VINT8"
        if islands <= 3:
            return "TWO_ISLAND"
        return "MULTI_ISLAND"
    return "PURE_FLOAT"
```

## Generated vitisai_config.json Template

```json
{
  "passes": [
    {
      "name": "vaiml_partition",
      "plugin": "vaip-pass_vaiml_partition",
      "vaiml_config": {
        "keep_outputs": true,
        "optimize_level": 2,
        "enable_f32_to_bf16_conversion": <DETERMINED>,
        "logging_level": "info",
        "fe_args": "<DETERMINED>",
        "device": "<DEVICE>"
      }
    }
  ],
  "target": "VAIML",
  "targets": [
    { "name": "VAIML", "pass": ["vaiml_partition"] }
  ]
}
```

## Single NPU Partition (Embedded Scenario)

For Telluride architecture targeting a single NPU partition:
- All operations must be mapped to NPU (no CPU fallback)
- The entire model must be quantized in some way (no fp32 operations)
- This means every operation is either:
  - Standard QDQ (vint8)
  - Force-converted from fp32 to bf16/fp16
  - Quantized by a specialized method handled by an optional plugin skill
    (e.g. block floating-point or extended per-layer precision)

If `CpuBecause` messages appear in compilation logs, it indicates operations
that could not be mapped to NPU. Common causes:
- Missing force-conversion flag for naked fp32 ops
- YAML constraint failures (dtype mismatch in kernels)
- Unsupported operations

## Flag Interaction Matrix

| Model Type | f32_to_bf16 | f32_to_f16 | match-pow2-quant |
|------------|:-----------:|:----------:|:----------------:|
| Pure VINT8 | - | - | - |
| 2-island (bf16) | ✓ | - | ✓ |
| 2-island (fp16) | - | ✓ | ✓ |
| Multi-island (bf16) | ✓ | - | ✓ |
| Multi-island (fp16) | - | ✓ | ✓ |
| Pure BF16 | ✓ | - | - |
| Pure FP16 | - | ✓ | - |

> Models quantized by a specialized method (block floating-point, extended
> per-layer precision, etc.) are flagged by the corresponding **plugin skill**
> under `skills/experimental-methods/`, which supplies its own flag rows. The
> core skill does not determine flags for those formats.

---

## Edge Quantization in Runtime (RT)

Edge quantization allows the runtime (FLEXMLRT/VART) to handle data type
conversions at model boundaries — inputs and outputs — rather than having the
NPU kernel graph perform them. This is relevant for mixed-precision models
where the model's input/output types differ from the NPU compute types.

### What Edge Quantization Does

When enabled, edge quantization **preserves** standalone Quantize (at input)
or Dequantize (at output) operations in the compiled graph metadata
(`flexmlrt-hsi.json`) rather than converting them into NPU kernels. The
runtime then handles these conversions as host-side data transformations.

Concretely:
- **Input edge quantization**: A standalone `QuantizeLinear` at the model input
  (arg → Q → NPU ops) is annotated for runtime processing. The runtime
  performs the float→int8 conversion on the host before feeding data to NPU.
- **Output edge dequantization**: A standalone `DequantizeLinear` at the model
  output (NPU ops → DQ → return) is annotated for runtime processing. The
  runtime performs the int8→float conversion on the host after NPU computation.

### FE Flags for Edge Quantization

| Flag | Description | Default |
|------|-------------|---------|
| `edge-quantization-in-rt=1` | Enable both input quantization and output dequantization in RT | `false` |
| `input-quantization-in-rt=1` | Enable only input-side quantization in RT | `false` |
| `output-dequantization-in-rt=1` | Enable only output-side dequantization in RT | `false` |

**Relationship**: `edge-quantization-in-rt=1` is the **union** of
`input-quantization-in-rt=1` and `output-dequantization-in-rt=1`. Setting the
umbrella flag is equivalent to setting both individual flags.

### How Edge Quantization Interacts with FPConversionPass

The FPConversionPass QDQ detection algorithm uses these flags to decide
whether standalone Q/DQ operations at function boundaries should be preserved
or converted:

1. **With `input-quantization-in-rt=1` or `edge-quantization-in-rt=1`**:
   Standalone Quantize ops that consume a block argument (function input)
   directly AND are not part of a fake QDQ pair are added to the
   `keepTypeSet`. Their types are preserved so the runtime can handle the
   conversion. The scale factor from the QuantizeLinear is propagated to the
   `rt_transformations` annotation in the HSI JSON.

2. **With `output-dequantization-in-rt=1` or `edge-quantization-in-rt=1`**:
   Standalone Dequantize ops that feed a function return AND are not part of
   a fake QDQ pair are added to the `keepTypeSet`. Their types are preserved
   so the runtime can handle the output conversion. The scale factor from the
   DequantizeLinear is propagated to the output `rt_transformations`.

3. **Without these flags**: Edge Q/DQ operations are treated like any other
   operation — they may be force-converted to the target type (bf16/fp16) and
   implemented as NPU DequantizeLinear/QuantizeLinear kernels.

### Interaction with MoveCastDown Pass

The `MoveCastDown` pass attempts to move `tosa.cast` operations closer to
function boundaries (block arguments or return ops), past layout
transformation ops (reshape, transpose). This matters for edge quantization
because:

- When `fp-conversion-keep-function-signature=1` is active, casts are inserted
  at function edges to convert between the original signature type and the
  internal NPU type.
- MoveCastDown moves these casts past reshape/transpose chains so they sit
  directly at the boundary, making them candidates for RT handling.
- If edge quantization in RT is ALSO enabled, the combined effect is that the
  runtime handles both the type cast AND the quantization at the boundary,
  reducing NPU kernel count.

### When to Enable Edge Quantization in RT

| Scenario | Recommendation | Rationale |
|----------|---------------|-----------|
| Model inputs are float, NPU works in int8 | `input-quantization-in-rt=1` | Runtime quantizes inputs on host — avoids an NPU Q kernel |
| Model outputs are float, NPU produces int8 | `output-dequantization-in-rt=1` | Runtime dequantizes outputs on host — avoids an NPU DQ kernel |
| Both of the above | `edge-quantization-in-rt=1` | Enables both directions |
| Pure int8-in/int8-out model | Not needed | No float↔int8 conversion at edges |
| Full BF16 model | Not needed | No quantization at all |
| Models with floating-point boundaries | Evaluate case-by-case | If the outermost boundary op feeds directly into return, edge quant may help |

### Tradeoffs

| Aspect | Edge Quant Enabled | Edge Quant Disabled |
|--------|-------------------|---------------------|
| **NPU kernel count** | Fewer (Q/DQ handled by RT) | More (Q/DQ are NPU kernels) |
| **Host CPU work** | More (float↔int8 on host) | Less (NPU handles everything) |
| **Latency** | May increase if host is bottleneck | May increase if NPU is kernel-count limited |
| **Data movement** | Int8 crosses PCIe/AXI (smaller) | Float crosses PCIe/AXI (larger) |
| **Accuracy** | Identical (same math, different executor) | Identical |
| **Compilation** | Scale factors in `flexmlrt-hsi.json` | Scale factors baked into NPU graph |

**General guidance**:
- For **embedded** (single NPU partition, Telluride): edge quantization in RT
  is beneficial when the model has float inputs but an int8 core. The runtime
  handles the quantization efficiently on the host NPU interface.
- For **performance-sensitive** deployments: measure both configurations.
  Edge quant reduces NPU kernel pressure but adds host-side work.
- For **VAIP** flow: edge quantization flags may be needed to produce
  correct `flexmlrt-hsi.json` annotations that the runtime stack expects.

### Estimating Impact

To estimate whether edge quantization helps:

1. **Compile without edge quant** and note the partition report's kernel count
2. **Compile with edge quant** (`edge-quantization-in-rt=1`) and compare
3. If kernel count decreases AND no new CpuBecause messages appear, the edge
   quant configuration is viable
4. For performance measurement, run both configurations on the board and
   compare latency

### Example Configuration with Edge Quantization

```json
{
  "enable_f32_to_bf16_conversion": true,
  "fe_args": "match-power-of-two-quant-kernel=1 edge-quantization-in-rt=1"
}
```

Or individually controlled:
```json
{
  "enable_f32_to_bf16_conversion": true,
  "fe_args": "match-power-of-two-quant-kernel=1 input-quantization-in-rt=1 output-dequantization-in-rt=1"
}
```
