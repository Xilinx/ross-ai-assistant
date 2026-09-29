---
name: vai-ffn-quantization
license: Apache-2.0 WITH LLVM-exception
compatibility: Requires VitisAI 6.3+
metadata:
  version: 6.3.0
  stage: beta
description: 'Identify and quantize Feed-Forward Network (FFN) layers in ONNX models.
  An FFN is any MatMul+Add (weight+bias) linear layer: MLP projections, attention
  QKV projections, attention output projections, and classifier heads. This skill
  detects all FFN patterns, groups them into blocks, and applies per-layer INT8 quantization.'
---
<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->

# FFN Quantization Sub-Skill

## Access command

This skill must always be accessed through the worker, never in a different way.
Allowed worker: `vai-mixed-precision-worker`.

## Parse Arguments

This skill expects arguments in this form:

```
--model <onnx_model> [--precision int8|bf16|fp32] [--output <quantized.onnx>] [--list-only] [--layer-filter <regex>]
```

- `--model <path>`: **(MANDATORY)** Path to the input ONNX model (.onnx or .onnxtxt)
- `--precision <int8|bf16|fp32>`: **(OPTIONAL, default: int8)** Target precision for the detected FFN weights and biases
- `--output <path>`: **(OPTIONAL)** Path for the quantized output model. Required unless `--list-only` is given.
- `--list-only`: **(OPTIONAL)** Only detect and report FFN layers/blocks; do not modify the model
- `--layer-filter <regex>`: **(OPTIONAL)** Restrict quantization to FFN layers whose MatMul node name matches this regular expression

## Scope

This skill identifies **all Feed-Forward Network (FFN) layers** in ONNX models
and applies quantization to their weights and biases. An FFN is any linear
layer represented as a `MatMul` with a 2D weight initializer followed by an
`Add` with a 1D bias initializer. This includes:

- MLP projections (up-projection, down-projection)
- Attention QKV projections (fused or separate)
- Attention output projections
- Classifier/head linear layers

An FFN layer consists of:
- A `MatMul` node where one input is a 2D graph initializer (weight)
- An `Add` node immediately downstream whose other input is a 1D initializer (bias)
- Optionally followed by an activation function (GELU, ReLU, SiLU, etc.)

FFNs are grouped into **blocks** based on their structural role in the model:

| Block Type | FFNs per Block | Description |
|------------|----------------|-------------|
| **MLP block** | 2 | Up-projection (with activation) + down-projection (no activation) |
| **Attention block** | 2 | QKV in-projection (no activation) + output projection (no activation) |
| **Tail** | 1–2 | Standalone linear layers (classifiers, heads) at model start/end |

## What This Skill Does

1. **Pattern Detection**: Scans an ONNX graph for **all** MatMul+Add patterns
   where the MatMul has a 2D weight initializer and the downstream Add has a
   1D bias initializer. Does NOT filter by dimension ratio or structural role —
   every such pattern is an FFN.

2. **Activation Detection**: For each FFN, checks whether the Add output feeds
   directly into an activation function (Gelu, Relu, Silu, etc.).

3. **Block Grouping**: Groups FFNs into blocks (MLP blocks, attention blocks,
   tail) based on weight shapes and activation presence.

4. **Weight/Bias Extraction**: Reports the weight and bias initializer names,
   shapes, and current quantization state for each detected FFN layer.

5. **Quantization Application**: Inserts QuantizeLinear/DequantizeLinear nodes
   around FFN weights and biases to quantize them to INT8 (symmetric,
   per-tensor). VINT8 on AMD NPU is always per-tensor quantization.

## Supported FFN Patterns

The detection handles these structural variants:

| Pattern | Description |
|---------|-------------|
| `MatMul → Add` | Basic FFN: weight matmul followed by bias add |
| `MatMul → Add → Activation` | FFN followed by an activation (GELU, ReLU, SiLU, etc.) |
| `MatMul → Q → DQ → Add` | Already-quantized FFN (INT8 Q/DQ between matmul and bias) |
| `MatMul → BFPQuantizeDequantize → Add` | MX/BFP-quantized FFN |
| `Gemm` (with bias) | Fused MatMul+Add as a single Gemm node |
| `Gemm → Q → DQ → Reshape → Q → DQ → Activation` | Quantized FFN with activation behind Q/
DQ/Reshape chain |
| `Add → Q → DQ → Reshape → Q → DQ → Activation` | Quantized bias Add with activation behind pass-through ops |

### Activation Detection Through Q/DQ Chains

In quantized (QDQ) models, activation functions are not the direct consumer
of the bias Add/Gemm output. Instead, there are intermediate pass-through
nodes (QuantizeLinear, DequantizeLinear, Reshape, Cast, BFPQuantizeDequantize)
between the FFN output and the activation. The detection traces forward through
single-consumer chains of these pass-through ops (up to 8 hops) to find
activations that logically belong to the FFN layer.

## Detection Algorithm

An FFN is identified by three criteria (all must hold):

1. **Has a 2D weight initializer**: One input to the MatMul is a graph
   initializer with exactly 2 dimensions `[in_features, out_features]`.
   This excludes attention score MatMuls (Q×K^T, attn×V) where both
   inputs are runtime tensors.
2. **Has a bias Add immediately downstream**: The MatMul output (possibly
   through Q/DQ) feeds into an Add whose other input is a 1D initializer.
3. **Weight is 2D**: Not 3D/4D (which would indicate a batched matmul).

No dimension-ratio filtering is applied. All MatMul+Add patterns meeting
these criteria are FFNs regardless of their structural role (MLP, attention
projection, classifier head, etc.).

### Previous error (corrected)

Earlier versions of this skill used dimension-ratio heuristics (e.g.,
`out_features / in_features ∈ {2, 3, 4, 8}`) and structural context to
exclude attention projections from FFN detection. This was wrong — attention
QKV projections (`dim → 3*dim`) and output projections (`dim → dim`) are
equally valid FFNs. The ratio heuristic also failed for fused QKV projections
(ratio 3) and square projections (ratio 1).

## Usage Examples

### FFN detection and quantization

```bash
# List all detected FFN layers without modifying the model
python ffn_quantize.py --input model.onnx --list-only

# Quantize all FFN weights and biases to INT8
python ffn_quantize.py --input model.onnx --output model_ffn_int8.onnx --precision int8

# Quantize only specific layers (by regex on node name)
python ffn_quantize.py --input model.onnx --output model_ffn_int8.onnx \
    --precision int8 --layer-filter "mlp|ffn"

# Dequantize FFN layers back to float (for re-quantization experiments)
python ffn_quantize.py --input model_quant.onnx --output model_ffn_fp32.onnx --precision fp32
```

### FFN block extraction and reporting

```bash
# Report all FFN blocks in a model
python extract_ffns.py --input model.onnx

# JSON output
python extract_ffns.py --input model.onnx --format json

# Filter by node name pattern
python extract_ffns.py --input model.onnx --layer-filter "mlp"
```

## Example: ViT Encoder (vit_encoder_b1_s256_d512_m2048_h16_l12)

This model has **48 FFNs** organized into 24 blocks:

| Block Type | Count | FFNs/Block | FFN 1 | FFN 2 | Total FFNs |
|------------|-------|------------|-------|-------|------------|
| MLP block | 12 | 2 | up-proj [512→2048] + Gelu | down-proj [2048→512] | 24 |
| Attention block | 12 | 2 | QKV in-proj [512→1536] | out-proj [512→512] | 24 |

- **MLP blocks**: One FFN with activation (Gelu), one without
- **Attention blocks**: Both FFNs without activation
- **Tail**: None (model ends with LayerNormalization)
- **Attention score MatMuls** (Q×K^T, attn×V): 24 nodes excluded — both
  inputs are runtime tensors, no weight initializer

## Integration with Mixed-Precision Workflow

This skill is a building block for the broader mixed-precision strategy:

1. Run pattern detection to identify FFN layers
2. Quantize FFN weights/biases to INT8 (they are typically robust to quantization)
3. If accuracy degrades, selectively promote specific FFN layers to BF16
4. Combine with the precision choices selected for attention layers (INT8 for FFN)

## Residual-Add Exclusion (mandatory for ViT encoders)

From the ViT encoder quantization sweep (12 shapes, d512–d2048,
s256–s1024): **baseline per-tensor INT8 on the full encoder collapses to
0.10% top-1**. No INT8 scheme recovers it — asymmetric, float-scale,
SmoothQuant+BiasCorrection all stay ≤0.20%. Only more bits (INT16) or
**excluding the deep residual-stream Adds** from INT8 does.

The cause is entirely the 13 residual-stream Add outputs
(`add_12`..`add_24`), which carry outlier activations (max |x| ≈ 277–281).
These interleave into two kinds — attention residual Adds (odd indices) and
MLP residual Adds (even indices, deep) — and **both branches must be
excluded**: dropping only one branch collapses accuracy back to ≈0%.

| Excluded → BF16 set             | count | top-1 (2k) |
|----------------------------------|------:|-----------:|
| 13 Adds (`add_12..24`)           | 13    |   68.30 %  |
| only 12 attention Adds (odd)     | 12    |   0.05 %   |
| only 7 MLP Adds (even, deep)    | 7     |   0.15 %   |

Late residual Adds (20–23) are the most sensitive; early ones tolerate INT8
better — consistent with residual magnitude growing with depth.

### BF16 casting pattern for excluded Adds

FP32, INT16, and BF16 all recover equally for the excluded Adds. Use BF16
(via cast-style QDQ) for deployment efficiency:

**Detection** — for each residual Add, inspect what consumes its output:
```python
cons_optypes = {c.op_type for c in consumers.get(add_node.output[0], [])}
# {'QuantizeLinear'} → still INT8, needs conversion
# {'Cast'}           → already BF16, skip
```

**Fix** — replace each `QuantizeLinear` with `Cast(to=BFLOAT16)` (single
input: the Add output; drop scale/zero_point inputs) and each downstream
`DequantizeLinear` with `Cast(to=FLOAT)`. Delete the now-unused
`_scale`/`_zero_point` initializers. Fix stale `value_info` dtype entries.

### Dual-branch nuance: LayerNorm branch stays INT8

Each residual Add output feeds two branches: one into `LayerNormalization`
(the next FFN/MHA sublayer input) and one carrying the residual stream
forward to the next Add. **Only the residual-carry branch should be BF16.**
The LayerNorm-feeding branch should remain INT8, matching the model's general
precision recipe. If step 1 of graph surgery over-converts both branches,
step 6 (`restore_int8_layernorm_branch.py`) must revert just the
LayerNorm-feeding branch by recovering the original scale/zero_point from
pre-surgery model state and replacing the Cast pair back to
QuantizeLinear/DequantizeLinear.

### Quark configuration for BF16 Add exclusion

Set `BF16QDQToCast = True` in `extra_options` and pass the 13 Add output
tensor names under `ExtendedQuantType.QBFloat16` in `MixedPrecisionTensor`.
Always set `DedicatedQDQPair = True` explicitly (AIESW-30603) — block
floating-point presets don't enable it by default, and without it the VAIML
frontend over-reports unsupported ops in fused clusters.

## >2GB Model Handling

Models exceeding ~2GB (e.g. d2048 shapes) trigger two separate crashes in
Quark 0.12's `quantize_static`:

1. **Auto-detect crash**: `ByteSize()` is called before the external-data
   flag can be set, itself triggering the protobuf 2GB overflow.
2. **Re-inline crash**: `check_onnx_model` → `create_infer_session_for_onnx_model`
   re-inlines the cached model without forwarding `use_external_data_format`.

**Fix**: auto-detect when external tensor bytes exceed ~90% of protobuf's 2GB
limit (`--external-data auto|on|off`), pre-set `use_external_data_format=True`,
and monkeypatch `create_infer_session_for_onnx_model` to force the
external-data branch for any in-memory `ModelProto`.

For >2GB shapes, Quark never fuses `MatMul+bias-Add → Gemm`, so the unfused
boundary fix (`fix_mha_boundary_unfused.py`) is needed instead of the
fused-case fix.

## Key Insight from Production Quantization

From ViT encoder quantization experience: all FFN layers (MLP projections
AND attention projections) are reliably quantizable to INT8 without accuracy
loss. The accuracy-sensitive components are the residual-stream Adds (which
**must** be excluded to BF16 — see above) and the attention score
computations (runtime×runtime MatMuls) — not the weight MatMuls. This makes
INT8 FFN quantization a safe default that delivers performance without
sacrificing accuracy, provided the residual-Add exclusion is applied.
