---
name: vai-quantization-guide
license: MIT
compatibility: Requires VitisAI 6.3+
metadata:
  version: 6.3.0
  stage: beta
description: Master orchestrator for mixed-precision ONNX model quantization targeting
  AMD NPU (Strix/Telluride). Guides users through quantization strategy selection,
  accuracy validation, compiler flag configuration, and board execution. Supports
  full bf16, full vint8, hybrid BF16/VINT8 configurations, and edge quantization in
  runtime. Specialized quantization methods are provided as optional plugin skills
  discovered at runtime.
---
<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->

# Mixed-Precision Quantization Orchestrator

## Scope

This skill helps you **quantize ONNX models** for deployment on AMD NPU
hardware (Strix or Telluride). It covers the full journey from an unquantized
FP32 model to a hardware-optimized mixed-precision model. Specifically, it:

- Analyzes an input model (quantized or not)
- Selects a quantization strategy (VINT8, BF16, or a mix)
- Applies quantization via the AMD Quark tool
- Validates accuracy against a reference
- Determines the correct compiler flags
- Compiles and optionally executes on NPU hardware
- Iterates until accuracy and performance targets are met

**What this skill does NOT do**: It does not train models, alter model
architecture (layer count, shapes), or generate training data. It operates
solely on the quantization and precision-mapping of an existing ONNX model.

---

## Model Modification Policy (Mandatory)

> **A model is quantized ONLY through Quark configurations.** This is the
> governing rule of this skill. When something needs to change about how a model
> is quantized — accuracy, precision mapping, unsupported ops, dtype mismatches,
> partitioning problems — the response is to **write or adjust a Quark JSON
> configuration and (re)quantize**, never to hand-edit the ONNX graph.

**NEVER perform ad hoc ONNX graph surgery** to work around a problem. This
includes (but is not limited to) inserting/removing/rewiring nodes, editing
initializers, rewriting `value_info`, or patching tensor dtypes/shapes directly
on the model. If a problem *seems* like it would benefit from ad hoc rewriting
of the model, that is a signal to express the fix as a Quark configuration
instead (e.g., `--mode requantize` with a new config), not to edit the graph.

There are exactly **two** permitted exceptions where direct modification of the
ONNX model is allowed:

1. **The `vai-vaip-patching` skill — and only for the specific edits it supports.**
   Namely: retagging `com.microsoft` Q/DQ to the default ONNX domain, adding
   `shape`/`data_type` attributes to custom ops (`ExtendedQuantizeLinear`,
   `ExtendedDequantizeLinear`, BFP QDQ), and fixing zero-point `bf16`/`fp16`
   dtypes. No other edits may be made under this exception. Invoke it via
   `/vai-vaip-patch`; do not reimplement its edits by hand.

2. **ViT models — the MHA-MX9 quantization pipeline.** ViT-style encoders
   require graph surgery *by construction* (fused-QKV split, BFP wrapper
   insertion on MHA blocks, attention-path QDQ stripping, and bf16 residual
   `Cast` conversion). This is the **only** quantization pathway permitted to
   modify the graph directly, and only through the dedicated ViT/MHA-MX9
   scripts — never as improvised one-off edits. If graph surgery appears
   necessary and the model is **not** a ViT going through this pipeline, do
   **not** do it: write a Quark configuration instead.

In every other situation, changing the model means changing the Quark config and
requantizing — full stop.

---

## Terminology: Quantization Formats

Before you begin, here is what the key terms mean:

| Term | What it is | Accuracy | Speed | Hardware |
|------|-----------|----------|-------|----------|
| **BF16** (BFloat16) | 16-bit floating point with 8-bit exponent. All ops run in reduced-precision float. | Highest (near FP32) | Baseline (1x) | STX + VE2 |
| **FP16** (Float16) | 16-bit floating point with 5-bit exponent. Similar to BF16 but native on Telluride. | High | ~1x | **VE2 only** |
| **VINT8** | 8-bit integer with fake-quantization (DQ→op→Q pattern). Scale/zero-point per tensor. | Lower (depends on model) | 2–4x faster | STX + VE2 |
| **Mixed BF16/VINT8** | Some layers in BF16 (accuracy-sensitive), others in VINT8 (performance). | Tunable | 1.2–2x faster | STX + VE2 |

> Additional specialized quantization formats (e.g. block floating-point or
> extended per-layer precision) may be available as **optional plugin skills**.
> See [Plugin Skills](#plugin-skills).

### Hardware Compatibility

| Device | Supported formats |
|--------|-------------------|
| **STX** (Strix) | BF16, VINT8, Mixed BF16/VINT8 |
| **VE2** (Telluride) | All of the above **plus** FP16 |

> **Important**: FP16 is only supported on the VE2 (Telluride) device.
> If you target STX (Strix), you can only use BF16 and VINT8 combinations.
> Optional plugin skills may add formats with their own hardware constraints.

---

## User Stories: Where to Start

Pick the scenario that best matches your situation:

### Story 1: "I have an FP32 model and want maximum performance"

**Goal**: Deploy on NPU as fast as possible, accepting some accuracy loss.

**Recommended approach**:
1. Try full VINT8 first (`--config full_vint8.json`)
2. Check accuracy against FP32 reference
3. If accuracy is OK → done, this is the fastest option
4. If accuracy fails → use `--explore` to find which layers need BF16

**Command**:
```
/vai-quantization-guide --model my_model.onnx --device ve2 --explore
```

### Story 2: "I have a quantized model and want to deploy it"

**Goal**: Compile and run an already-quantized model on NPU.

**Recommended approach**:
1. Run the skill to analyze quantization patterns and determine flags
2. Compile and validate on hardware

**Command**:
```
/vai-quantization-guide --model my_quantized_model.onnx --device ve2 --stage exec
```

### Story 3: "I need high accuracy AND good performance"

**Goal**: Find the optimal accuracy/performance trade-off using mixed precision.

**Recommended approach**:
1. Run in exploration mode — the skill will iteratively move layers between
   VINT8 and BF16 until accuracy targets are met
2. This produces a Quark JSON config you can reuse

**Command**:
```
/vai-quantization-guide --model my_model.onnx --device ve2 --explore \
  --atol 0.01 --calibration-data calib_data.npy
```

---

## Execution Modes Explained

| Mode | What happens step by step |
|------|---------------------------|
| `--mode full` | (1) Analyze model → (2) Run FP32 baseline on CPU → (3) Quantize with Quark → (4) Determine compiler flags → (5) Compile for NPU → (6) Compare accuracy → (7) Report results |
| `--mode dry-run` | (1) Analyze model → (2) Run FP32 baseline → (3) Run BF16 baseline → (4) Run VINT8 baseline → (5) Report accuracy of each WITHOUT modifying the model → (6) Recommend a strategy |
| `--mode requantize` | (1) Strip existing quantization → (2) Apply new quantization from `--config` → (3) Validate accuracy → (4) Report improvement |

### `--mode dry-run` in detail

Use this when you want to **understand your model's quantization behavior
before committing** to a specific strategy. No files are modified.

Steps executed:
1. **Analyze** — inspect the model for existing quantization nodes, op types,
   input/output shapes.
2. **FP32 baseline** — run the model on CPU with `CPUExecutionProvider` to
   produce golden reference outputs.
3. **BF16 baseline** — quantize a temporary copy to full BF16, run on CPU,
   compare against FP32 (reports PSNR and element-wise pass rate). This shows
   the accuracy ceiling for NPU deployment.
4. **VINT8 baseline** — quantize a temporary copy to full VINT8, run on CPU,
   compare against FP32. This shows the performance-optimum accuracy.
5. **Report** — print a summary table comparing BF16 and VINT8 accuracy, and
   recommend a strategy:
   - If VINT8 passes tolerances → "Use full VINT8 (fastest)"
   - If VINT8 fails but BF16 passes → "Use mixed BF16/VINT8 with exploration"
   - If both fail → "Model may need architectural changes or looser tolerances"

**Example output (regression model)**:
```
┌────────────────┬──────────┬────────────┬────────────────┐
│ Configuration  │ PSNR(dB) │ Pass Rate  │ Recommendation │
├────────────────┼──────────┼────────────┼────────────────┤
│ Full BF16      │   46.2   │   100.0%   │ Accuracy ref   │
│ Full VINT8     │   22.1   │    68.4%   │ ✗ fails atol   │
└────────────────┴──────────┴────────────┴────────────────┘
→ Recommendation: Use --explore to find optimal mixed BF16/VINT8 config
```

**Example output (classification model)**:
```
┌────────────────┬──────────┬────────────┬──────────┬────────────────┐
│ Configuration  │ PSNR(dB) │ Pass Rate  │ Top-1    │ Recommendation │
├────────────────┼──────────┼────────────┼──────────┼────────────────┤
│ Full BF16      │   46.2   │   100.0%   │ ✓ match  │ Accuracy ref   │
│ Full VINT8     │   16.1   │     7.1%   │ ✓ match  │ ✓ USE THIS     │
└────────────────┴──────────┴────────────┴──────────┴────────────────┘
→ Recommendation: Use full VINT8 (top-1 preserved, fastest configuration).
  Low PSNR is expected for classification logits and does not indicate a problem.
```

**When to use**: Before spending time on quantization. Helps decide whether
full VINT8 is sufficient or mixed-precision is needed.

### `--mode requantize` in detail

Use this when you have an **already-quantized model** whose quantization you
want to replace (e.g., switch from full VINT8 to a mixed config, or update
scale factors with better calibration data).

Steps executed:
1. **Strip quantization** — invoke the `/vai-dequantize` sub-skill to remove all
   existing QDQ nodes (QuantizeLinear, DequantizeLinear) and any `com.amd.quark`
   quantization nodes. This produces a clean FP32 model.
2. **Apply new quantization** — quantize the stripped model using the config
   specified by `--config`. If `--calibration-data` is provided, use it for
   calibration; otherwise Quark uses random data.
3. **Validate accuracy** — compare the newly quantized model against the FP32
   baseline (the stripped model). Report PSNR, pass rate, and per-output metrics.
4. **Report improvement** — if previous accuracy metrics are available (from a
   prior dry-run or full run), show the delta.

**Example**:
```bash
# Original model was quantized with full VINT8 and accuracy was poor.
# Requantize with backbone in VINT8, head in BF16:
/vai-quantization-guide --model model_vint8.onnx --mode requantize \
  --config backbone_int8_head_bf16.json --calibration-data calib.npy
```

**When to use**:
- You got a quantized model from a colleague and want to try a different config
- Your first quantization attempt failed accuracy checks
- You have better calibration data and want to re-calibrate
- You want to switch between quantization strategies (e.g., full VINT8 → mixed BF16/VINT8)

---

## Quark Availability

**AMD Quark ships with the Vitis AI installation**, alongside `flexml` and the
`VitisAIExecutionProvider`, so quantization, compilation and board execution all
run in the **same** environment. Before quantization can proceed, the skill
simply confirms Quark is importable there:

```bash
python -c "import quark" 2>/dev/null
```

If the import fails, the environment is not a complete Vitis AI environment.
**STOP and ask the user** how their Vitis AI environment should be set up (or
for the correct `--t` activate script) instead of searching the filesystem for
another interpreter. Only if the user explicitly asks to install it, install it
into that same environment:

```bash
pip install amd-quark
# or, for a specific version:
pip install amd-quark==0.12
```

> **Note**: Never go hunting elsewhere on the filesystem for Quark, and never
> create a side `.venv` for it — mixing a separate Quark environment with the
> Vitis AI one is what this skill deliberately avoids.

---

## Parse Arguments

This skill expects arguments in this form:

```
--model <onnx_model> [--t <activate_path>] [--config <quark_config.json>] [--device stx|ve2] [--target-accuracy <metric>] [--atol 0.015] [--rtol 0.01] [--mode full|dry-run|requantize] [--stage compile|exec] [--explore] [--calibration-data <path>] [--edge-quant none|input|output|both] [--edge-quant-auto] [--plugin <name> [plugin-args...]]
```

Extract from `$ARGUMENTS`:
- `--model <path>`: **(MANDATORY)** Path to input ONNX model (.onnx or .onnxtxt)
- `--t <path>`: **(OPTIONAL)** Path to a Vitis AI environment activate script, used for the compile / board steps. Provide this only when the Vitis AI Python environment is NOT already on the shell. Omit it when the environment is pre-provisioned (e.g. inside the Vitis AI Docker container, where `flexml` and the `VitisAIExecutionProvider` are already importable). See [Environment Setup Convention](#environment-setup-convention).
- `--config <path>`: **(OPTIONAL)** Path to Quark JSON configuration preset
- `--device <stx|ve2>`: **(OPTIONAL, default: ve2)** Target device (stx=Strix/BF16, ve2=Telluride/FP16)
- `--target-accuracy <metric>`: **(OPTIONAL)** Target accuracy metric (e.g., "psnr>30")
- `--atol <float>`: **(OPTIONAL, default: 0.015)** Absolute tolerance for output comparison
- `--rtol <float>`: **(OPTIONAL, default: 0.01)** Relative tolerance (percent) for output comparison
- `--mode <full|dry-run|requantize>`: **(OPTIONAL, default: full)** Execution mode
- `--stage <compile|exec>`: **(OPTIONAL, default: compile)** Stage to execute
- `--explore`: **(OPTIONAL)** Enable exploration mode to find optimal configuration
- `--calibration-data <path>`: **(OPTIONAL)** Calibration data (.npy or .npz files)
- `--edge-quant <none|input|output|both>`: **(OPTIONAL, default: none)** Control edge quantization in runtime:
  - `none`: No edge quantization (Q/DQ at boundaries become NPU kernels)
  - `input`: Enable input-side quantization in RT (`input-quantization-in-rt=1`)
  - `output`: Enable output-side dequantization in RT (`output-dequantization-in-rt=1`)
  - `both`: Enable both input and output (`edge-quantization-in-rt=1`)
- `--edge-quant-auto`: **(OPTIONAL)** Automatically detect whether edge quantization is beneficial by compiling twice (with and without) and comparing kernel counts
- `--plugin <name> [plugin-args...]`: **(OPTIONAL)** Invoke an optional plugin skill by name (see [Plugin Skills](#plugin-skills)). The plugin is discovered at runtime; any arguments after the name are passed through to the plugin's own sub-commands/options.

## Environment Setup Convention

The **whole workflow runs in a single environment**: a Vitis AI installation
provides `quark` for quantization together with `flexml` and the
`VitisAIExecutionProvider` for compilation and board execution. There is no
separate quantization venv and no environment hand-off between phases.

This skill supports two deployment models for that environment:

1. **Sourced environment**: the user passes `--t <path>` and every shell the
   skill runs must `source <path>` first.
2. **Pre-provisioned environment** (e.g. the Vitis AI Docker container): the
   Python packages and binaries are already on `PATH` / `PYTHONPATH`. `--t` is
   NOT provided; no sourcing is needed.

Wherever this document shows `<ENV_SETUP>` inside a bash snippet, expand it as
follows:
- If `--t <path>` was provided: replace the `<ENV_SETUP>` line with `source <path>`.
- Otherwise: delete the `<ENV_SETUP>` line entirely.

**Do NOT auto-detect or guess an activate script.** If `--t` is absent, trust
that the calling shell already has `python3 -c "import flexml, quark"` working.
Searching the filesystem for an environment to source is a bug, not a fallback.

Before the first compile, verify the toolchain is usable:

```bash
<ENV_SETUP>
python3 -c "import onnxruntime as ort; \
  assert 'VitisAIExecutionProvider' in ort.get_available_providers(); print('VitisAI EP OK')"
```

If that fails, **STOP and ask the user** how the Vitis AI environment should be
set up (or for the correct `--t` activate script) rather than hunting for one.

The VAIML frontend/partitioner also expects these knobs, so export them in the
compile shell:

```bash
export DEBUG_VAIML_PARTITION=2
export DEBUG_LOG_LEVEL=info
export BF16_SELECT=True
export ADD_TARGET_TO_CFG=True
```

## Board Execution: Ask First (Mandatory)

**Before running any part of the workflow, establish whether the user wants to
execute on a board and how.** Do not defer this to the end of Phase 6 — the
answer determines whether accuracy can be confirmed on real hardware or only
estimated on CPU. Ask:

> "Do you want to run the quantized model on a board?
>  1. **No** — compile only; accuracy is validated on CPU against the FP32 reference.
>  2. **Yes, using the bundled board-run script** — please provide board address, user name,
>     password, key file or SSH_AUTH_SOCK that I need to connect.
>  3. **Yes** — tell me how to reach and run on your board (host, credentials/SSH
>     config, board type, and the script/harness/command you use), and I will use that."


Resolution rules:
- If the user picks option 3, capture the exact command/harness now and use it
  verbatim in Phase 6 — do not guess a board command.
- If the user picks option 1, say plainly that accuracy claims will rest on CPU
  reference comparisons only, and that on-hardware behavior is unverified.
- **Headless (no interactive user):** don't block — proceed compile-only and
  report that board execution was not performed.

## Static Shape Preservation (Mandatory)

The VAIML compiler requires all tensor shapes to be fully static (no dynamic
dimensions). When the input model has only static shapes, the quantized output
model **must** also have only static shapes.

> The graph-surgery steps described below apply **only** within the two permitted
> exceptions of the [Model Modification Policy](#model-modification-policy-mandatory)
> (the `vai-vaip-patching` skill and the ViT MHA-MX9 pipeline). They are **not** a
> license to hand-edit models in the general case — outside those exceptions,
> shape/precision problems are fixed by adjusting the Quark config and
> requantizing, not by editing the graph.

> **RULE**: Never introduce dynamic or unranked tensor dimensions during
> quantization. If the input model has shape `[1, 1024, 1536]`, every
> intermediate and output tensor in the quantized model must also have fully
> resolved static shapes. Violating this causes the VAIML frontend to emit
> `WARNING: [VAIML-FRONTEND 2018] Operation/s with NON-STATIC SHAPE have been
> detected in MODEL` and crash during ONNX-to-TOSA lowering.

**Checks to enforce**:
1. After every graph surgery step (node insertion, removal, rewiring), run
   `onnx.shape_inference.infer_shapes(model, data_prop=True)` to propagate
   shapes through the modified graph.
2. After saving the final model, verify no tensor has unknown dimensions:
   ```python
   for vi in model.graph.value_info:
       shape = vi.type.tensor_type.shape
       for dim in shape.dim:
           assert dim.dim_value > 0, f"Dynamic dim in {vi.name}"
   ```
3. Custom ops (`com.amd.quark` domain) are not handled by
   `onnx.shape_inference`. After inserting any `com.amd.quark` quantization
   nodes, manually propagate shapes: output shape = first input shape.
4. When splitting, slicing, or reshaping weight tensors (e.g., QKV split),
   compute the output shapes explicitly from the input tensor dimensions —
   never use `-1` or symbolic dimensions.

## Default Tolerances (XOAH Defaults)

These tolerances are the standard acceptance criteria for **regression-type**
models (where output values are directly consumed):
- **atol**: 0.015 (absolute tolerance)
- **rtol**: 0.01 (1% relative tolerance)
- **PSNR threshold**: 30 dB (for models whose output values are consumed directly, e.g. segmentation masks, super-resolution pixels, embeddings)

Users may override these, but the defaults represent industry-standard acceptance
criteria for quantized model deployment on NPU.

**These tolerances do NOT apply to classification models.** For classification,
the pass/fail criterion is argmax preservation (top-1 or top-k match). A
classification model with PSNR=10 dB but correct top-1 is a PASS; a regression
model with PSNR=25 dB is a FAIL regardless of argmax. Always check the task
type first (Phase 0, step 3) before applying any threshold.

## Quantization Pipeline: Native Quark vs. Manual FE Pipeline

This skill supports two approaches. Choose the one appropriate for your model:

### Approach A: Native Quark Quantization (Preferred)

Use Quark directly on the ONNX model to apply quantization and compare
numerical accuracy. Quark natively supports mixed-precision quantization with
per-layer configuration, sensitivity analysis, and accuracy comparison against
the FP32 baseline — all in one step, without manual patching.

**When to use**: This is the default and preferred approach. Quark operates
directly on the ONNX graph and produces a quantized model ready for
compilation. Use this whenever FE (Front-End compiler) transformations are
**not** required before quantization.

### Approach B: FE-Transformed Pipeline

Apply FE transformations first, then quantize the transformed model. This
pipeline is only necessary when FE performs quantization-sensitive
transformations that alter the graph structure (e.g., op fusions, layout
changes, constant folding) in ways that affect how quantization should be
applied.

**When to use**: Only when the model requires FE transformations that:
- Fuse operations (e.g., Conv+BN, Conv+Relu) before quantization boundaries
- Change tensor layouts or shapes in ways that affect quantization ranges
- Apply optimizations that Quark cannot replicate on the original ONNX

**Risk**: If FE transformations introduce graph changes not representable back
in ONNX, then the quantization applied in this pipeline comes from the
**original** (pre-transformation) model, not the transformed one. This
mismatch can introduce additional error. Monitor for this by comparing the
native Quark quantization output with the FE-pipeline output — if they
diverge significantly, the FE transformations are affecting quantization
ranges and the FE-transformed pipeline is warranted.

**Recommendation**: Start with Approach A (native Quark). Only fall back to
Approach B if accuracy results on the compiled model diverge from what Quark
predicts, indicating that FE transformations are changing quantization
behavior.

## Workflow Phases

### Phase 0: Model Analysis

1. **Load and inspect the model**:
   ```python
   import onnx
   model = onnx.load(model_path)
   ```

2. **Determine model state**:
   - Check for QuantizeLinear/DequantizeLinear nodes → quantized (legacy QDQ)
   - Check for `com.amd.quark` domain quantization ops → quantized by a
     specialized method. If a matching plugin skill is available under
     `skills/experimental-methods/`, delegate its analysis to that plugin
     (see [Plugin Skills](#plugin-skills)); otherwise report that the model
     uses a specialized quantization not handled by the core pipeline.
   - No quantization nodes → non-quantized (float model)

3. **Classify the model's task type** (determines which accuracy metric to use):

   | Task type | How to identify | Primary accuracy metric |
   |-----------|----------------|------------------------|
   | **Classification** | Output is a single tensor of shape `[N, num_classes]` (logits or probabilities). Typical indicators: final compute ops include Softmax, Gemm, MatMul, or GlobalAveragePool feeding a dense layer; output has no spatial dimensions. | **Top-k accuracy**: does the argmax (top-1) or top-5 set match FP32? Element-wise logit differences are irrelevant. |
   | **Detection** | Multiple outputs (boxes, scores, classes); ops like NonMaxSuppression, ScatterND, TopK near output | **mAP / IoU**: do bounding boxes and class predictions match FP32 within standard detection tolerances? |
   | **Regression / Generation** | Output values are directly consumed (pixel values, embeddings, coordinates, waveforms) | **Element-wise PSNR + atol/rtol**: the numeric value itself matters. |

   **Determine the task type before running any accuracy check.** All
   subsequent pass/fail decisions use the metric that matches the task type.
   Do NOT reject a classification model because its element-wise PSNR or
   atol/rtol is poor; it passes if argmax is preserved, regardless of logit
   magnitude differences. Do NOT accept a regression model without verifying
   element-wise tolerance; argmax preservation is meaningless when the output
   values themselves are consumed.

   When the task type is ambiguous, infer from output shape and context:
   - Output `[N, C]` where C is large (100-10000) and no spatial dims → classification
   - Output `[N, C, H, W]` where H, W > 1 → regression (segmentation, generation)
   - Multiple outputs with mixed shapes → detection or multi-task (check each output independently)

4. **Report model characteristics**:
   - Total nodes, op types present
   - Input/output shapes and dtypes
   - **Task type** (classification / detection / regression)
   - Quantization coverage (% of nodes quantized)
   - Island structure (contiguous quantized/float regions)

### Phase 1: Baseline Establishment

#### Sample count requirements

**Calibration**: Use at least 4 representative samples (more is better with
real data; with random data, more samples can widen the MinMax range and hurt
VINT8 precision). If `--calibration-data` is not provided, use
`UseRandomData: True` with a fixed seed for reproducibility.

**Evaluation**: Use at least 100 samples for initial validation, 1000 for
final reporting. A single sample is never sufficient; classification models
on random inputs have small logit margins, so single-sample results are
dominated by noise. Always evaluate on a fixed seed for reproducibility.

For **non-quantized models**, establish accuracy baselines:

1. **FP32 CPU Reference**: Run model on CPU with ONNXRuntime CPUExecutionProvider
2. **Full BF16 Baseline** (or FP16 for ve2): Quantize entire model to BF16/FP16
   - This is the accuracy ceiling for NPU execution
   - Use config: `full_bf16.json`
   - Compare to FP32: should have PSNR > 40dB typically

3. **Full VINT8 Baseline**: Quantize entire model to INT8
   - This is the performance ceiling
   - Use config: `full_vint8.json`
   - Compare to FP32: check against atol/rtol thresholds

**Decision Point** (apply the metric determined by task type in Phase 0):

- **Classification models**: If top-1 prediction matches FP32 → **DONE**.
  Do NOT proceed to exploration based on element-wise PSNR or atol/rtol
  failures alone. Logit magnitudes can differ substantially without affecting
  the decision boundary. Only explore if the argmax changes.
- **Detection models**: If bounding box IoU and class predictions match FP32
  (compute via model-specific post-processing, e.g. NMS, then compare box
  coordinates with IoU ≥ 0.5 and class IDs matching). If detection accuracy
  cannot be evaluated in this environment, report it as requiring external
  validation and proceed to compilation without exploration. → **DONE**.
- **Regression models**: If element-wise metrics meet thresholds (PSNR ≥ 30 dB
  AND pass rate ≥ 99.9% at configured atol/rtol) → **DONE**.
- Otherwise → proceed to Phase 2 (mixed-precision exploration).

### Phase 2: Mixed-Precision Strategy Selection

Based on accuracy analysis, select a quantization strategy:

#### Strategy A: Legacy Mixed-Precision (Multi-Island)

Use when: Floating-point operations form contiguous regions (heads/tails/middles).

Patterns supported:
- `vint8 → bf16 tail` (quantized core, floating output)
- `bf16 head → vint8` (floating input, quantized core)
- `bf16 → vint8 → bf16` (floating head+tail, quantized middle)
- `vint8 → bf16 → vint8` (quantized head+tail, floating middle)
- Four-island+ models (alternating regions)

Configuration: Uses `group_args: "mixed_precision"` flag which enables:
- `match-power-of-two-quant-kernel=1` — QDQ-aware FP conversion

#### Strategy B: Specialized Quantization via Plugins

Some models benefit from specialized quantization formats or per-layer
precision schemes that are not part of the core BF16/VINT8 pipeline (for
example, block floating-point formats or extended per-layer precision for
frequently-interleaved floating-point layers).

These methods are provided as **optional plugin skills** discovered at runtime.
When a model's accuracy or structure calls for such a method, check whether a
matching plugin is available and, if so, delegate to it via the plugin facility
(see [Plugin Skills](#plugin-skills)). Each plugin defines its own scope,
hardware constraints, experimental-feature consent banners, version checks,
and sub-commands/options — the core orchestrator does not hard-code any of
these.

### Phase 3: Quantization Execution

1. **If model is already quantized and mode=requantize**:
   - Invoke `/vai-dequantize` to strip existing quantization
   - Proceed with fresh quantization

2. **Apply selected strategy**:
   ```bash
   python mixed_precision_quantize.py \
       --input <model.onnx> \
       --output <model_quantized.onnx> \
       --config <selected_config.json> \
       [--calibration-data <data.npz>] \
       [--fp-nodes "node1,node2"] \
       [--fp-precision bf16|fp16]
   ```

   Note: `--fp-nodes` accepts exact ONNX node names (as shown in the model
   graph, e.g., `/layer4/layer4.1/conv2/Conv`). These are passed as exact
   string matches to Quark's `specific_layer_config`. For regex patterns
   (e.g., `^/layer4/.*`), use the JSON config file's `target_node_names`
   field instead.

3. **If exploration mode is enabled**:
   - Iteratively try different layer assignments
   - Use sensitivity analysis to identify accuracy-critical layers
   - Move the most sensitive layers to higher precision first

### Phase 4: Model Patching (if needed)

For models with `com.amd.quark` custom ops targeting VAIP (not VAIML directly):
- Invoke `/vai-vaip-patch` to add shape/data_type attributes
- Fix zero-point dtypes (bf16/fp16 not representable in onnxtxt)

### Phase 5: Compiler Flag Determination

Invoke `/vai-fe-dtype --model <quantized_model.onnx>` to determine:
- Whether `enable_f32_to_bf16_conversion=true` is needed for BF16.
- Whether `enable_f32_to_bf16_conversion=false` in combination with `enable_f32_to_f16_conversion=true` is needed (necessary for F16).
- Whether `group_args: "mixed_precision"` or `match-power-of-two-quant-kernel=1` is needed (the choices are equivalent).
- Device-specific flags (ve2 for fp16)
- Whether edge quantization in RT should be enabled based on `--edge-quant`
  setting or auto-detection (`--edge-quant-auto`)

`fe_args` and `fe_experiment` are aliases, as are `group_args` and
`experiments`; compiler logs may print either spelling. Write `fe_args` and
`group_args` in generated configs.

If `--edge-quant` was specified:
- `input` → append `input-quantization-in-rt=1` to `fe_args`
- `output` → append `output-dequantization-in-rt=1` to `fe_args`
- `both` → append `edge-quantization-in-rt=1` to `fe_args`

If `--edge-quant-auto` was specified:
- Analyze the model for standalone Q at inputs and DQ at outputs
- If found, compile once without and once with `edge-quantization-in-rt=1`
- Compare kernel counts and recommend the better configuration

Generate the appropriate `vitisai_config.json`.

### Phase 6: Compilation and Execution

This phase turns a quantized model into an NPU-runnable artifact. It is
**interactive**: after quantization succeeds, offer compilation as an explicit
choice, and only after a successful compilation proceed with the board decision
already taken in [Board Execution: Ask First](#board-execution-ask-first-mandatory).
Never silently compile or run — and never skip the offer either. The compile /
board scripts referenced here are deployed in the `ai_utils` bundle as siblings
of this skill, in the scripts folder of the `vai-custom-op-implementation`
skill. Before running the snippets below, set
`VAI_CUSTOM_OP_IMPLEMENTATION_SCRIPTS` to that scripts folder, and
`VAI_FLAG_CONFIGURATION_SCRIPTS` to the script directory of the
`vai-flag-configuration` skill.

#### Step 6.0 — Activate the Vitis AI environment

Compilation and board execution use the **same** environment as quantization: a
Vitis AI installation, which provides the **VAIML compiler toolchain** — the
`flexml` package and, crucially, the `VitisAIExecutionProvider` in ONNXRuntime —
next to Quark.

Follow the [Environment Setup Convention](#environment-setup-convention): in the
Vitis AI Docker container the environment is already provisioned and nothing
needs sourcing; otherwise source the activate script passed via `--t`. Export
the VAIML knobs in the same shell:

```bash
<ENV_SETUP>
export DEBUG_VAIML_PARTITION=2
export DEBUG_LOG_LEVEL=info
export BF16_SELECT=True
export ADD_TARGET_TO_CFG=True
```

**Verify the toolchain is usable** before compiling:

```bash
python -c "import onnxruntime as ort; \
  assert 'VitisAIExecutionProvider' in ort.get_available_providers(); print('VitisAI EP OK')"
```

- If the VitisAI EP is unavailable after the setup above, **STOP and ask the
  user** for the correct Vitis AI environment (or the `--t` activate script) —
  do not assume, hardcode, or search for one.

#### Step 6.1 — Prompt: compile the mixed-precision model with VAIML (a choice)

Once the quantized model exists (and its accuracy is validated), **ask the user
whether to compile it with the VAIML compiler**:

> "Quantization is complete. Do you want to compile the mixed-precision model
> with the VAIML compiler now? [compile / skip]"

- If the user **skips**, report the quantized model path and stop here — do not
  compile or run.
- If the user **compiles**:
  1. Determine the compiler flags first by invoking
     `/vai-fe-dtype --model <quantized_model.onnx> --device <stx|ve2> --output-config vitisai_config.json`
     (Phase 5). Ensure the generated `vitisai_config.json` sets
     `keep_outputs: true` so the partition/CpuBecause artifacts are written for
     Step 6.3.
  2. Compile in two steps. First **patch the model for VAIP** so `com.amd.quark`
     EQDQ/BFP nodes carry the `shape`/`data_type` XIR attributes and bf16
     zero-points (a no-op for plain QDQ VINT8/BF16 models):
     ```bash
     python3 scripts/patch_model_for_vaip.py \
         --input <quantized_model.onnx> --output <quantized_model_patched.onnx>
     ```
     Then **compile the patched model** with the shared VAIML compile driver,
     which registers any `com.amd.quark` custom ops found in the graph before
     creating the session:
     ```bash
     python3 ../custom-op-implementation/scripts/compile.py \
         <quantized_model_patched.onnx> \
         --vitisai-config vitisai_config.json --cache-dir <model_stem>
     ```
     For plain QDQ (VINT8/BF16) models the patch step is optional and you can
     compile the quantized model directly with `compile.py`.
     For a fast frontend/partition-only probe (no AIE binary — useful to check
     partitioning before a full compile) use the `vai-flag-configuration`
     probe instead:
     ```bash
     python3 "$VAI_FLAG_CONFIGURATION_SCRIPTS/compile_probe.py" <quantized_model.onnx> \
         --vitisai-config vitisai_config.json --mode capability \
         --cache-dir <model_stem> -o compile_probe.json
     ```
  3. **Verify the compile actually succeeded.** ORT can print "Compilation
     successful" while AIE errors force CPU fallback — check the log for errors
     and confirm the cache dir is populated, as the compile scripts' own error
     scan does.

#### Step 6.2 — Inspect the partitioning (recommended after every compile)

A successful compile does not mean the model reached a single NPU partition.
Invoke the `vai-partition-analysis` sub-skill on the compile output to report the
operation statistics, the partition count, and the AIE/CPU split, and — when
more than one partition was produced — to propose remediation (frontend flags
first, Quark-config changes when the break is quantization-caused):

```
/vai-partition-analysis --compile-dir <model_stem> --model <quantized_model.onnx> \
    --config vitisai_config.json --device <stx|ve2>
```

The goal is **1 partition / 100% on AIE**. If a single partition with 100% offloading
is not possible, then the next best goals would combine the smallest possible number
of partitions with the highest possible offloading percentage. For example:
- "1 partition and greater than 98% offloading"
- "less than 3 partitions and close to 100% offloading".

If `vai-partition-analysis` proposes a
change, apply it and re-compile (Step 6.1) to evaluate — `vai-partition-analysis`
always closes with a re-compilation.

#### Step 6.3 — Run the compiled model on the board

**Only after a successful compilation**, act on the board decision taken up
front in [Board Execution: Ask First](#board-execution-ask-first-mandatory). If
that question was never asked, ask it now before going any further.

- **Board execution declined** — report the compiled artifacts and stop.
- **Bundled board-run script** — Use the `run_on_board.py` the `scripts` folder of the
  `vai-custom-op-implementation` skill. It reuses the Phase 6.1 compile cache (no recompile)
  and handles SSH, file sync, the `com.amd.quark` custom-op registration, and Telluride
  board locking:
  ```bash
  # Remote Strix board via SSH
  python3 "$VAI_CUSTOM_OP_IMPLEMENTATION_SCRIPTS/run_on_board.py" \
      --boardhost <BOARDHOST> [--board-user <BOARD_USER>] \
      [--board-password <PASSWORD>] [--board-key <BOARD_SSH_KEY>] \
      -p . -- <quantized_model.onnx> \
      --input-dir <inputs_dir> --output-dir board_outputs
  ```
  Note that user, password, SSH key are optional.
- **User's own board setup** — run the exact command / harness the user gave
  you, against the Phase 6.1 compile cache. Do not invent board commands, hosts,
  or flags.

After a successful board run, compare the board outputs against the
full-precision CPU reference and report accuracy metrics and performance.

#### Step 6.4 — Report

Report the quantized model path, the compiler flags used, the partition count
and AIE/CPU split (from Step 6.2), and — if the board was run — the on-board
accuracy and performance versus the FP32 reference.


### Phase 7: Iteration (if accuracy/performance not met)

If results don't meet criteria:
1. **Accuracy too low**: Move more layers to higher precision
   - Identify failing output elements
   - Use sensitivity analysis to find problematic layers
   - Promote the most sensitive layers to BF16
2. **Performance too slow**: Move more layers to lower precision
   - VINT8 should be faster than BF16 (flag if not)
3. **Loop back** to Phase 2 with updated configuration

## Performance Expectations

| Configuration | Relative Speed | Relative Accuracy |
|---------------|----------------|-------------------|
| Full BF16 | 1.0x (baseline) | Highest |
| Full VINT8 | 2-4x faster | Lower (may fail tolerance) |
| BF16+VINT8 mixed | 1.2-2x faster | Between BF16 and VINT8 |

## Plugin Skills

The orchestrator supports **optional plugin skills** located under
`skills/experimental-methods/`.  Plugins extend the quantization workflow with
specialized capabilities (e.g. block floating-point formats, extended per-layer
precision) that are not part of the core BF16/VINT8 pipeline.  The orchestrator
provides a generic facility for calling a plugin: it never hard-codes which
plugins exist, validates availability from the filesystem, and only when the
plugin is present does it execute the plugin and its own sub-commands/options.

### Invoking a Plugin

```
/vai-quantization-guide --model <path.onnx> --plugin <plugin-name> [plugin-specific args...]
```

or, equivalently, the shorthand:

```
/vai-plugin <plugin-name> --model <path.onnx> [plugin-specific args...]
```

### Plugin-Calling Facility (Discovery + Validation + Delegation)

Follow this protocol whenever a plugin is requested (or when model analysis
indicates a specialized quantization the core pipeline does not handle):

1. **Resolve** the plugin path: `skills/experimental-methods/<plugin-name>/SKILL.md`.
2. **Validate availability** by checking whether that file exists. Do this
   dynamically — do **not** maintain a hard-coded list of plugin names.
3. **If unavailable**: inform the user the plugin is not installed, and list
   what **is** available by enumerating the directories under
   `skills/experimental-methods/`. Stop — do not attempt the specialized method
   in the core pipeline.
4. **If available**: read the plugin's `SKILL.md` to discover its scope,
   hardware constraints, experimental-feature consent banner, version checks,
   and its own sub-commands/options. Display any consent banner and obtain
   user consent before proceeding.
5. **Delegate**: execute the plugin's protocol, passing through any
   plugin-specific arguments to its sub-commands/options. The core orchestrator
   does not interpret plugin-specific flags itself.
6. **Resume**: when the plugin returns its quantized model, resume the core
   workflow at Phase 5 (Compiler Flag Determination) for compilation and
   execution. The plugin's `SKILL.md` supplies any additional compiler flags
   its format requires.

### Available Plugins

Plugins ship under `experimental-methods/` and are deployed by the installer
when present.  They are not referenced by any core skill or agent definition —
enumerate the `experimental-methods/` directory at runtime to see which are
installed.

**Red flags**:
- If VINT8 is slower than BF16 → report compilation/mapping issue
- If mixed-precision is slower than full BF16 → too many precision transitions

## Dry-Run Mode

When `--mode dry-run`:
- Run FP32 CPU reference
- Run BF16 baseline (compile-only or exec)
- Run VINT8 baseline (compile-only or exec)
- Report accuracy metrics WITHOUT modifying the model
- Recommend a strategy based on results

## Requantize Mode

When `--mode requantize`:
- Strip existing quantization (`/vai-dequantize`)
- Apply new quantization based on `--config`
- Validate accuracy against baselines
- Report improvement over previous quantization

## Exploration Mode

When `--explore`:
1. Start with full VINT8
2. Run sensitivity analysis on each layer
3. Move most sensitive layers to BF16, one at a time
4. After each change, measure accuracy delta
5. Stop when accuracy threshold is met
6. Report the minimal set of BF16 layers needed

This produces an optimal Quark JSON config file that can be reused.

### Pre-exploration Gate

**Before entering exploration, verify that exploration is warranted:**

1. If task type is **classification** and top-1 matches FP32 → do NOT explore.
   Report success immediately with full VINT8.
2. If task type is **detection** and IoU/mAP pass → do NOT explore.
3. Only enter exploration for regression models that fail element-wise
   thresholds, or classification/detection models where the functional metric
   (argmax, IoU) itself fails.

Skipping this gate check caused the observed failure where a classification
model (ResNet50) was sent into exploration despite correct top-1. Check it
before any exploration logic runs.

### Convergence Guardrails

The exploration mode uses the following guardrails to ensure the search
converges efficiently and avoids wasting iterations:

| Guardrail | Rule | Rationale |
|-----------|------|-----------|
| **Sensitivity ranking** | Layers are sorted by dynamic range (max |activation|) before exploration | High dynamic range → high quantization error → try BF16 first |
| **One-at-a-time promotion** | Move exactly one layer from INT8 → BF16 per iteration | Isolates the accuracy impact of each layer |
| **Monotonic accuracy** | If promoting a layer does not improve accuracy, skip it and try the next | Avoids wasting BF16 budget on insensitive layers |
| **Performance budget** | Stop if the number of BF16 layers exceeds 30% of total compute layers | Beyond this, the model is mostly BF16 and mixed-precision offers little speed benefit |
| **PSNR plateau** | Stop when PSNR improves by less than 0.5 dB across 3 consecutive promotions | Diminishing returns — further promotions won't meaningfully help |
| **Pass rate target** | Stop when element-wise pass rate ≥ 99.9% at configured atol/rtol | Accuracy threshold met |
| **Max iterations** | Cap at min(50, total_compute_layers) iterations | Prevents runaway exploration |
| **Rollback on regression** | If promoting a layer degrades accuracy (pass rate drops), revert it | Guards against interactions where a BF16 layer hurts accuracy |

### Exploration Output

The exploration produces:
1. **Sensitivity report**: Ranked list of layers with their sensitivity scores
2. **Promotion log**: Each iteration's accuracy delta and decision
3. **Optimal config**: A Quark JSON config with the minimal BF16 set
4. **Summary**: Number of BF16 layers, expected speedup vs. full BF16

## Identifying Layers to Exclude from Quantization: Step-by-Step Guide

The key question in mixed-precision quantization is: **which layers should
remain in higher precision (BF16) to preserve accuracy?** This section
provides a systematic approach with a worked example.

### Step 1: Establish Baselines

Run the model in three configurations and record accuracy metrics:

```bash
# FP32 reference (golden output)
python compare_accuracy.py --reference model_fp32.onnx --model model_fp32.onnx

# Full BF16 baseline (accuracy ceiling for NPU)
python mixed_precision_quantize.py -i model_fp32.onnx -o model_bf16.onnx -c full_bf16.json
python compare_accuracy.py --reference model_fp32.onnx --model model_bf16.onnx

# Full VINT8 baseline (performance ceiling)
python mixed_precision_quantize.py -i model_fp32.onnx -o model_vint8.onnx -c full_vint8.json
python compare_accuracy.py --reference model_fp32.onnx --model model_vint8.onnx
```

**Decision**: If VINT8 meets your tolerance (e.g., PSNR > 30 dB, pass rate > 99.9%), stop here — use full VINT8.

### Step 2: Run Per-Layer Sensitivity Analysis

Identify which layers contribute most to quantization error:

```bash
python mixed_precision_quantize.py -i model_fp32.onnx -o /dev/null \
    -c full_vint8.json --explore --explore-num-samples 50
```

This outputs a ranked list:
```
Layer sensitivity (highest = most accuracy-critical):
  /model/head/Softmax:        42.1503
  /model/head/MatMul_1:       38.7291
  /model/neck/Add_3:          21.4820
  /model/backbone/Conv_47:    18.3102
  /model/backbone/Conv_46:    15.2710
  /model/backbone/Relu_44:     9.0031
  ...
```

Layers with high dynamic range (large activation magnitudes) lose the most
information when quantized to INT8 and are prime candidates for BF16.

### Step 3: Iterative Layer Promotion

Starting from full VINT8, promote the most sensitive layers to BF16 one at a
time:

```bash
# Iteration 1: Promote the most sensitive layer
python mixed_precision_quantize.py -i model_fp32.onnx -o model_iter1.onnx \
    -c full_vint8.json --fp-nodes "/model/head/Softmax" --fp-precision bf16
python compare_accuracy.py --reference model_fp32.onnx --model model_iter1.onnx

# Iteration 2: Add the next most sensitive layer
python mixed_precision_quantize.py -i model_fp32.onnx -o model_iter2.onnx \
    -c full_vint8.json \
    --fp-nodes "/model/head/Softmax,/model/head/MatMul_1" --fp-precision bf16
python compare_accuracy.py --reference model_fp32.onnx --model model_iter2.onnx
```

Track the accuracy improvement at each iteration:
```
Iteration 0 (full VINT8):     PSNR=18.2 dB, pass_rate=72.1%  ← fails
Iteration 1 (+Softmax):       PSNR=24.7 dB, pass_rate=88.3%  ← improving
Iteration 2 (+MatMul_1):      PSNR=31.4 dB, pass_rate=97.5%  ← improving
Iteration 3 (+Add_3):         PSNR=34.8 dB, pass_rate=99.8%  ← close
Iteration 4 (+Conv_47):       PSNR=36.2 dB, pass_rate=99.95% ← PASS
```

Stop when the pass rate threshold is met (≥ 99.9%).

### Step 4: Validate Patterns Across the Model

Often, sensitivity follows structural patterns. In the example above, the
head layers (Softmax, MatMul) are the most sensitive. Check if a structural
rule captures the pattern:

```json
{
  "specific_layer_config": [
    {
      "target_node_names": ["^/model/head/.*"],
      "layer_config": {
        "input_tensors": { "spec": "BFloat16Spec", "params": {} },
        "weight": { "spec": "BFloat16Spec", "params": {} },
        "bias": { "spec": "BFloat16Spec", "params": {} }
      }
    }
  ]
}
```

This is more maintainable than listing individual nodes and generalizes
better to model variants.

**IMPORTANT — JSON Format vs. Quark `QConfig.from_dict` Format:**

The JSON format shown above (list of `{"target_node_names": [...], "layer_config": {...}}`
objects) is our custom format, consumed by `mixed_precision_quantize.py`. It is NOT the
format that Quark's `QConfig.from_dict` expects. The two formats for `specific_layer_config`
are:

- **Our JSON** (used in config files and throughout this guide):
  ```json
  "specific_layer_config": [
    {"target_node_names": ["^/head/.*"], "layer_config": {"input_tensors": {"spec": "BFloat16Spec", "params": {}}, ...}}
  ]
  ```

- **Quark `QConfig.from_dict`** (paired lists of `[layer_config_dict, node_names_list]`):
  ```json
  "specific_layer_config": [
    [{"input_tensors": {"spec": "BFloat16Spec", "params": {}}, ...}, ["^/head/.*"]]
  ]
  ```

`mixed_precision_quantize.py` bridges the two: it reads our JSON, builds
`dict[QLayerConfig, list[str]]`, and passes that to the `QConfig()` constructor
directly (not through `from_dict`). If you need to call `QConfig.from_dict`
yourself, use the paired-list format.

`QLayerConfig` accepts keyword arguments `input_tensors`, `activation`, `weight`,
`bias`, and `output_tensors`, each a `QTensorConfig` (or `None`). Its `from_dict`
parses the same keys, each containing `{"spec": "<SpecName>", "params": {...}}`.

**IMPORTANT — Pattern Syntax Rules for `target_node_names`:**

Quark's `get_all_target_nodes` interprets patterns as follows:
- **Regex patterns**: MUST start with `^` AND contain `.*` (e.g., `"^/layer4/.*"`)
- **Exact node names**: Plain strings without `^` prefix (e.g., `"/layer4/layer4.1/conv2/Conv"`)
- Patterns starting with `^` but missing `.*` will trigger a Quark warning and may not match

Examples:
```json
"target_node_names": [
    "^/layer4/.*",
    "^/model/(head|neck)/.*",
    "/conv1/Conv"
]
```

**CPU Evaluation Limitation:**

Mixed-precision models (containing BF16 Cast ops or `com.amd.quark` custom ops)
**cannot be evaluated on CPU** with ONNXRuntime's CPUExecutionProvider. The
BF16 Cast ops and `com.amd.quark` domain ops are only supported
on NPU hardware. When running accuracy evaluation during exploration, use the
FP32 model for reference and compare against the full VINT8 model (which IS
CPU-evaluable). The mixed-precision model's accuracy must be validated on-device
after compilation.

### Step 5: Compile and Verify on CPU or NPU

```bash
/vai-fe-dtype --model model_mixed.onnx --analyze
```

Use the generated `vaiml_config.json` to compile and execute the model on the
NPU, e.g., using the VitisAI execution provider.

### Worked Example: BEVFormer Detection Head

A BEVFormer model with ~800 nodes shows poor accuracy in full VINT8:
- Full BF16 baseline: PSNR=48.2 dB, pass_rate=100%
- Full VINT8: PSNR=12.4 dB, pass_rate=41.3%

**Sensitivity analysis** reveals:
1. `pts_bbox_head` (detection head): Contains Softmax, MatMul, and
   ScatterND with very high dynamic ranges (>100). These are numerical
   operations sensitive to precision loss.
2. `img_neck` (feature pyramid): Add operations that aggregate multi-scale
   features are moderately sensitive (dynamic range 15–25).
3. `img_backbone` (ResNet/Swin): Conv+Relu chains are relatively robust
   to INT8 quantization (dynamic range < 10).

**Resulting configuration**:
- Backbone (Conv, Relu): **VINT8** — robust to quantization, fastest
- Neck (Add, Concat): **VINT8** — moderate sensitivity but tolerable
- Detection head: **BF16** — high sensitivity, needs full precision
- Excluded nodes: Gather, Unsqueeze, ScatterND at model boundaries — these
  are shape/indexing ops that don't benefit from quantization

This corresponds to the `backbone_int8_all_layers_other_bf16.json` preset,
which achieves PSNR=35.1 dB at ~2x the speed of full BF16.

## Listings of Quark configurations

### full_vint8.json

```json
{
    "global_config": {
        "input_tensors": { "spec": "XInt8Spec", "params": {} },
        "weight": { "spec": "XInt8Spec", "params": {} },
        "bias": { "spec": "XInt8Spec", "params": {} }
    },
    "extra_options": {
        "OpTypesToQuantize": [
            "Conv", "Mul"
        ],
        "OptimizeModel": false,
        "UseRandomData": false,
        "ConvertBNToConv": true,
        "ConvertSigmoidToHardSigmoid": false,
        "ConvertClipToRelu": true,
        "SplitLargeKernelPool": false,
        "ReplaceClip6Relu": true,
        "ConvertReduceMeanToGlobalAvgPool": false,
        "RemoveQDQConvClip": false,
        "RemoveQDQConvPRelu": false,
        "RemoveQDQConvRelu": false,
        "RemoveQDQConvLeakyRelu": false
    }
}
```

### full_bf16.json

```json
{
    "global_config": {
        "input_tensors": { "spec": "BFloat16Spec", "params": {} },
        "weight": { "spec": "BFloat16Spec", "params": {} },
        "bias": { "spec": "BFloat16Spec", "params": {} }
    },
    "extra_options": {
        "UseRandomData": false,
        "IgnoreWarnings": false,
        "SimplifyModel": false,
        "OptimizeModel": false,
        "BF16QDQToCast": true
    }
}
```

### Sample of vint8+bf16 mixed config: backbone_int8_all_layers_other_bf16.json

```json
{
  "global_config": {
    "input_tensors": { "spec": "XInt8Spec", "params": {} },
    "weight": { "spec": "XInt8Spec", "params": {} },
    "bias": { "spec": "XInt8Spec", "params": {} }
  },
  "specific_layer_config": [
    {
      "target_node_names": ["^(?!/model/module/(img_backbone|img_neck)/).*"],
      "layer_config": {
        "input_tensors": { "spec": "BFloat16Spec", "params": {} },
        "weight": { "spec": "BFloat16Spec", "params": {} },
        "bias": { "spec": "BFloat16Spec", "params": {} }
      }
    }
  ],
  "exclude": {
    "node_names": ["^(/Gather|/model/module/pts_bbox_head/(Unsqueeze_64|Concat_36|Reshape_2[0-4]|Tile_1|MatMul|Gather_33|Slice_(70|72|76|80)|Sub|Sub_[1-3]|Div_[2-5]|ScatterND_1[3-5]|Transpose_1|Clip|Clip_[12]|Log))$"],
    "subgraphs": []
  },
  "extra_options": {
    "OpTypesToQuantize": [],
    "ActivationSymmetric": true,
    "UseRandomData": false,
    "ConvertBNToConv": true,
    "ConvertSigmoidToHardSigmoid": false,
    "ConvertClipToRelu": false,
    "ConvertSplitToSlice": true,
    "SplitLargeKernelPool": false,
    "ReplaceClip6Relu": true,
    "ConvertReduceMeanToGlobalAvgPool": false,
    "RemoveQDQConvClip": false,
    "RemoveQDQConvPRelu": false,
    "RemoveQDQConvRelu": false,
    "RemoveQDQConvLeakyRelu": false,
    "Int32Bias": false,
    "IgnoreWarnings": false,
    "SimplifyModel": false,
    "OptimizeModel": false,
    "BF16QDQToCast": true,
    "EnableDualQuantNodePairs": true
  }
}
```

---

## Edge Quantization in Runtime

### Overview

Edge quantization controls **where** the data type conversions at model
boundaries are performed — on the host (by the runtime) or on the NPU (as
a kernel). This is an orthogonal optimization to the precision-assignment
choices (vint8/bf16) above.

### The `--edge-quant` Option

```
/vai-quantization-guide --model model.onnx --device ve2 --edge-quant both
```

| Value | FE Flag Generated | Effect |
|-------|-------------------|--------|
| `none` | (no flag) | Default. Boundary Q/DQ handled by NPU kernels |
| `input` | `input-quantization-in-rt=1` | Input Q handled by runtime |
| `output` | `output-dequantization-in-rt=1` | Output DQ handled by runtime |
| `both` | `edge-quantization-in-rt=1` | Both handled by runtime |

### When to Use `--edge-quant`

Use edge quantization when your model has:

1. **Float inputs → int8 core**: The model receives fp32 data but the
   backbone runs in int8. Instead of dedicating an NPU QuantizeLinear
   kernel to the input conversion, offload it to the runtime.

2. **Int8 core → float outputs**: The model produces int8 results that
   must be dequantized to fp32 for the application. Offload the
   DequantizeLinear to the runtime.

3. **Mixed-precision models where the input/output precision differs
   from the compute precision**: Common in YOLO models with int8 backbone
   but float detection heads.

**Do NOT use** when:
- Model inputs and outputs are already int8 (no conversion needed)
- Full BF16 models (no quantization at all)
- The Q/DQ at the boundary is part of a fake QDQ pair (these are handled
  differently by the QDQ detection algorithm)

### The `--edge-quant-auto` Option (Stretch Goal: Auto Mode)

When `--edge-quant-auto` is specified, the skill automatically evaluates
whether edge quantization is beneficial:

```
/vai-quantization-guide --model model.onnx --device ve2 --edge-quant-auto
```

**Algorithm**:

1. **Detect boundary Q/DQ ops**: Scan the model for QuantizeLinear ops
   that consume model inputs and DequantizeLinear ops that produce model
   outputs. If none exist, report "edge quantization not applicable."

2. **Compile without edge quant**: Run the compiler without edge-quant
   flags and record:
   - NPU kernel count
   - Number of `QuantizeLinear` / `DequantizeLinear` kernels at edges
   - Scale factors used at boundaries

3. **Compile with edge quant**: Run the compiler with
   `edge-quantization-in-rt=1` and record:
   - NPU kernel count (should be lower by the number of edge Q/DQ ops)
   - Whether any new CpuBecause messages appeared
   - Scale factors in `flexmlrt-hsi.json` `rt_transformations`

4. **Compare and recommend**:
   - If kernel count decreased AND no new CpuBecause → **recommend enabling**
   - If CpuBecause appeared → **recommend disabling** (edge quant broke
     something downstream)
   - If kernel count unchanged → edge Q/DQ were already handled differently

5. **Report tradeoffs**:
   ```
   ┌──────────────────────┬────────────┬────────────────┐
   │ Configuration        │ NPU Kernels│ Edge Q/DQ in RT│
   ├──────────────────────┼────────────┼────────────────┤
   │ Without edge quant   │    247     │      0         │
   │ With edge quant      │    243     │      4         │
   └──────────────────────┴────────────┴────────────────┘
   → Recommendation: Enable edge-quantization-in-rt=1
     Saves 4 NPU kernels by offloading boundary Q/DQ to runtime.
     Scale factors propagated to flexmlrt-hsi.json.
   ```

6. **Performance estimation** (when board is available):
   - If `--stage exec` is also specified, run both configurations on
     hardware and report the latency difference
   - Typical expectation: edge quant reduces latency by 1-5% depending
     on model size (bigger models see smaller relative benefit)

### How It Works Internally (Domain Knowledge)

The FPConversionPass in the compiler uses edge quantization flags to decide
which Q/DQ operations at function boundaries should be preserved (for
runtime handling) vs. converted (into NPU kernels):

```
With input-quantization-in-rt=1:
  ┌─────┐    ┌───┐    ┌─────────┐
  │ arg │ -> │ Q │ -> │ NPU ops │ -> ...
  └─────┘    └───┘    └─────────┘
                ↑
     This Q is PRESERVED in the compile graph.
     Runtime performs: float_input * (1/scale) → int8
     Scale factor recorded in flexmlrt-hsi.json.

Without input-quantization-in-rt=1:
  ┌─────┐    ┌───────────────────┐    ┌─────────┐
  │ arg │ -> │ NPU QuantizeLinear│ -> │ NPU ops │ -> ...
  └─────┘    └───────────────────┘    └─────────┘
                       ↑
     This Q becomes an NPU kernel (QuantizeLinear3D or similar).
```

The MoveCastDown pass then moves any residual cast operations (from
`fp-conversion-keep-function-signature=1`) closer to the function boundary,
so they combine with the edge quantization annotation rather than creating
separate cast kernels mid-graph.

### Example: YOLO Model with Edge Quantization

A YOLO model with float inputs, int8 backbone, and mixed detection head:

```bash
# Without edge quant: 3 partitions due to input/output Q/DQ kernels
/vai-quantization-guide --model yolov8m_int8.onnx --device ve2 \
  --edge-quant none

# With edge quant: single partition, boundary Q/DQ offloaded to RT
/vai-quantization-guide --model yolov8m_int8.onnx --device ve2 \
  --edge-quant both

# Auto-detect: compiles both ways and recommends
/vai-quantization-guide --model yolov8m_int8.onnx --device ve2 \
  --edge-quant-auto
```

### Integration with Other Flags

Edge quantization flags compose with other FE experiment flags:

```json
{
  "enable_f32_to_bf16_conversion": true,
  "fe_args": "match-power-of-two-quant-kernel=1 edge-quantization-in-rt=1"
}
```

The full flag precedence for a mixed-precision model with edge quant:
1. `enable_f32_to_bf16_conversion=true` — force-converts naked fp ops to bf16
2. `match-power-of-two-quant-kernel=1` — matches Q/DQ as kernels at island boundaries
3. `edge-quantization-in-rt=1` — offloads boundary Q/DQ to runtime

These three flags together represent the maximum mixed-precision configuration
with runtime-handled edges. The edge quant flag does NOT conflict with any
other flag; it changes where certain Q/DQ operations are executed.
