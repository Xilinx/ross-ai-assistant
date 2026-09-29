---
name: vai-dequantize-model
license: Apache-2.0 WITH LLVM-exception
compatibility: Requires VitisAI 6.3+
metadata:
  version: 6.3.0
  stage: beta
description: Strip quantization from an ONNX model to produce a non-quantized (float)
  version suitable for re-quantization. Removes QDQ pairs, Extended QDQ operators,
  and BFP quantization nodes while preserving the computational graph structure. Supports
  legacy QDQ (vint8), Extended QDQ (EQDQ/bf16/fp16), and MX6/BFP quantization.
---
<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->

# Dequantize Model Skill

## Access command

This skill must always be accessed through the worker, never in a different way.
Allowed worker: `vai-mixed-precision-worker`.

## Description

Takes an already-quantized ONNX model and produces a non-quantized (float32)
version. This is useful for:
- Re-quantization with a different configuration
- Switching from legacy quantization (multi-island fp op flags) to extended
  quantization (EQDQ)
- Switching from extended to legacy quantization
- Model analysis and debugging

## Parse Arguments

This skill expects arguments in this form:

```
--input <quantized.onnx> --output <float_model.onnx> [--keep-weights] [--preserve-structure]
```

- `--input <path>`: **(MANDATORY)** Path to the quantized ONNX model (.onnx or .onnxtxt)
- `--output <path>`: **(MANDATORY)** Path for the output non-quantized model
- `--keep-weights`: **(OPTIONAL)** Preserve weight values as float32 (default: true)
- `--preserve-structure`: **(OPTIONAL)** Keep graph topology unchanged except for Q/DQ removal

## Quantization Patterns to Remove

### 1. Standard QDQ (QuantizeLinear / DequantizeLinear)

**Input pattern**:
```
tensor → QuantizeLinear(scale, zero_point) → DequantizeLinear(scale, zero_point) → consumer
```

**Output (dequantized)**:
```
tensor → consumer
```

**Steps**:
1. Identify all QuantizeLinear → DequantizeLinear pairs
2. For activation Q/DQ: rewire the DQ output consumers to use the Q input directly
3. For weight Q/DQ: dequantize weight values back to float32:
   ```python
   float_weight = (int8_weight - zero_point) * scale
   ```
4. Remove the Q and DQ nodes
5. Update initializer dtypes from int8 to float32

### 2. Extended QDQ (ExtendedQuantizeLinear / ExtendedDequantizeLinear)

**Input pattern** (com.amd.quark domain):
```
tensor → ExtendedQuantizeLinear(scale, zp) → ExtendedDequantizeLinear(scale, zp) → consumer
```

**Output (dequantized)**:
```
tensor → consumer
```

**Steps**:
1. Identify all ExtendedQuantizeLinear → ExtendedDequantizeLinear pairs
2. Since EQDQ represents bf16/fp16 precision, the values are already essentially
   float — simply remove the EQ/EDQ wrapper nodes
3. Rewire: EQ input tensor → directly to EDQ consumers
4. Remove unused scale/zero_point initializers
5. Remove `com.amd.quark` from opset_import if no quark ops remain

### 3. BFP / MX6 Quantization

**Input pattern**:
```
tensor → BFPQuantizeLinear(params) → BFPDequantizeLinear(params) → consumer
```

**Output (dequantized)**:
```
tensor → consumer
```

**Steps**:
1. Identify BFP Q/DQ pairs (look for ops with BFP attributes or in quark domain)
2. Remove BFP quantization nodes
3. Rewire inputs directly to consumers
4. Remove BFP-specific initializers and attributes

### 4. Standalone Q or DQ (Boundary Nodes)

At model input/output boundaries, there may be standalone Q or DQ:

**Input boundary** (standalone DQ at beginning):
```
input(int8) → DequantizeLinear → float_ops
```
→ Change input type to float32, remove DQ

**Output boundary** (standalone Q at end):
```
float_ops → QuantizeLinear → output(int8)
```
→ Remove Q, change output type to float32

## Edge Cases

1. **Shared scales**: Multiple Q/DQ nodes may share scale/zp initializers.
   Only remove an initializer when ALL referencing nodes are removed.

2. **Subgraph Q/DQ**: Q/DQ nodes may appear inside subgraphs (If, Loop).
   Process all subgraphs recursively.

3. **Fused ops**: Some quantized models have fused ops (e.g., QLinearConv).
   These must be replaced with their float equivalents (Conv).

4. **Mixed domains**: A model may have both standard QDQ and EQDQ.
   Process both types.

5. **Dynamic quantization**: Externally-quantized models may use per-channel
   quantization. Handle both per-tensor and per-channel QDQ correctly during
   stripping. Note: VINT8 applied by this toolchain is always per-tensor.

## Output Verification

After dequantization:
1. Run ONNX shape inference on the output model
2. Verify all tensors are float32
3. Run the model on CPUExecutionProvider to confirm it produces valid outputs
4. Compare with the quantized model's CPU output (should be very close for
   properly dequantized weights)

## Usage in Re-quantization Workflow

```bash
# Step 1: Dequantize existing model
/vai-dequantize --input model_vint8.onnx --output model_float.onnx

# Step 2: Re-quantize with different config (e.g., move some layers to bf16)
/vai-quantize --input model_float.onnx --config backbone_int8_other_bf16.json

# Step 3: Or re-quantize with extended quantization for interleaved precision
/vai-quantize --input model_float.onnx --config eqdq_config.json
```
