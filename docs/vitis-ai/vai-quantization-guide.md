# Model quantization workflow

Entry point: `/vai-quantization-guide`

Quantizes a model, assigning precision per layer where mixed precision is
warranted. Choosing that precision by hand is error-prone and
topology-dependent, and tuning it takes many experiment cycles. The workflow
automates the cycle: it analyzes the model, selects a strategy, quantizes,
compiles, evaluates accuracy against an FP32 baseline, and iterates until your
targets are met.

The points in [Board access](index.md#board-access) apply to this workflow. For the manual procedure
this workflow automates, see the [Model Quantization](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_quantization/model_quantization.html) and [Mixed Precision Compilation](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/mixed_precision_compilation.html) sections of the Vitis AI documentation.

## When to use

* Uniform low-precision quantization has cost you more accuracy than you can
  accept.
* You want to find the smallest set of layers that must stay at higher
  precision.
* You want to know whether mixed precision is worth pursuing at all, before
  committing to it.

You provide the model, calibration data, and explicit accuracy and performance
objectives. The workflow has no body of knowledge about what counts as a good
quantized model on your device, so it cannot infer targets you have not stated.
You get a quantized or requantized model, the per-layer precision assignment
that produced it, and the measured accuracy for that assignment.

## Prerequisites

Follow the installation procedure in
[the skill installation guide](../../README.md#quick-start), so that
your assistant has model access and the workflow files installed. Quark must be
available for any run that quantizes a model; the workflow checks for it before
proceeding.

Configure board access as described in [Board access](index.md#board-access).

> **Note**
>
> Ideally keep a board available for the whole session. The workflow measures
> accuracy and performance on hardware to steer its iterations. Without a
> board, it is limited to compile-stage results, and cannot verify what a
> precision assignment costs in practice.

## Quick start

You can be as brief or as specific as you like. At its simplest, point the
workflow at a model:

```text
Use the quantization workflow for ./my_model.onnx
```

The workflow then asks which device you target, checks whether the model is
already quantized, proposes a strategy based on its structure, confirms that you
want to proceed, and guides you through the rest.

For full control, give the options explicitly:

```text
/vai-quantization-guide --model ./resnet50.onnx --config full_vint8.json \
  --device ve2 --stage exec --explore
```

See [Command reference](#command-reference) for the full argument list.

## How it works

The workflow proceeds through six phases:

1. **Model analysis.** Determines whether the model is already quantized, and
   inspects operator types and structure.
2. **Baseline.** Runs the FP32 model on the CPU, as the accuracy reference.
3. **Quantization.** Applies the specified configuration with Quark, whether
   full VINT8, full BF16, or mixed BF16 and VINT8.
4. **Accuracy validation.** Compares the quantized model's outputs against the
   FP32 baseline.
5. **Exploration.** With `--explore`, iterates over configurations, such as
   BF16 head and tail or mixed BF16 and VINT8, to find the best accuracy and
   performance tradeoff. Specialized formats can be applied through optional
   plugin skills.
6. **Board execution.** Compiles the final model and runs it on the NPU.

Throughout, the workflow selects compiler options deterministically, by
classification rather than by guess, and uses seed-based calibration data so
that results are reproducible.

## Modes

`--mode full`
   The default. Runs the complete workflow described above, from analysis
   through board execution.

`--mode dry-run`
   Runs the model on the CPU at FP32, full BF16, and full VINT8, and reports the
   accuracy of each without modifying the model. FP32 is your reference, and the
   BF16 and VINT8 figures bound what each single-precision strategy can achieve.
   The workflow prints a comparison table and recommends a strategy.

   Run this first. It tells you whether mixed precision is worth pursuing at
   all, and it is the quickest way to gauge what a longer run will cost on your
   setup.

`--mode requantize`
   Strips the existing Q/DQ nodes from a quantized model, applies the new
   configuration given by `--config`, validates accuracy against the FP32
   baseline, and reports whether accuracy improved. Use this when an earlier
   attempt failed its accuracy checks and you want to try a different strategy,
   such as moving some layers to BF16.

`--explore`
   Searches for a mixed-precision assignment automatically. It scores each layer
   by activation dynamic range, ranks them, then moves the highest-ranked layers
   from VINT8 to BF16 one at a time, measuring accuracy after every promotion.

   Each iteration is a full requantize and evaluate, so the cost grows linearly
   with the number of promotions.

   The search keeps itself bounded. It skips a promotion that does not improve
   accuracy, and rolls back one that makes accuracy worse. It stops when any of
   the following is true:

   * The pass rate reaches 99.9%.
   * PSNR improves by less than 0.5 dB across three consecutive promotions.
   * BF16 reaches 30% of compute layers.

   > **Note**
   >
   > Activation dynamic range is a proxy. It correlates with INT8 error, but
   > does not capture layer interactions, accumulation effects, or
   > post-processing sensitivity. A layer that ranks low can still be the one
   > costing you accuracy.

## Command reference

```text
/vai-quantization-guide \
  --model <path_to_onnx_model> \
  [--config <path_to_quark_config.json>] \
  [--device <device>] \
  [--mode <full|dry-run|requantize>] \
  [--stage <compile|exec>] \
  [--explore]
```

| Argument | Required | Description |
| --- | --- | --- |
| `--model <path>` | Yes | Path to the ONNX model, either `.onnx` or `.onnxtxt`. |
| `--config <path>` | No | Path to a Quark JSON configuration preset. |
| `--device <device>` | No | Target device. Defaults to `ve2`. |
| `--mode <full|dry-run|requantize>` | No | Execution mode. Defaults to `full`. See [Modes](#modes). |
| `--stage <compile|exec>` | No | `compile` compiles only; `exec` compiles and runs on the NPU. Defaults to `compile`. |
| `--explore` | No | Enables exploration mode, to search for an optimal mixed-precision assignment. |
| `--target-accuracy <metric>` | No | Accuracy objective, as a quoted expression, for example `"psnr>30"`. |
| `--atol <float>` | No | Absolute tolerance. Defaults to `0.015`. |
| `--rtol <float>` | No | Relative tolerance. Defaults to `0.01`. |
| `--calibration-data <path>` | No | Calibration data, as `.npy` or `.npz` files. |
| `--work-dir <path>` | No | Where the run stores its iterations and ledger. |
| `-t <path>` | No | Path to the Vitis AI environment activation script, needed for the compile and board stages when you are not already in a provisioned environment. Omit inside the Vitis AI Docker container. |

## Examples

The workflow accepts structured invocations, free-form prompts, and a mix of the
two.

### Explore against reference data

A structured invocation. Explores mixed precision against explicit absolute and
relative tolerances, using the supplied calibration data:

```text
/vai-quantization-guide --model /path/to/model_fp32.onnx --explore
--atol .15 --rtol 0.1 --calibration-data ifm_0.npy
```

### Requantize for higher accuracy

A mixed prompt: the intent in prose, followed by the invocation that carries it
out. Use this shape when the reason for a request matters and you want the
workflow to take it into account:

```text
Requantize a vint8 model with a vint8+bf16 strategy. The full VINT8 model has
lower accuracy than required.

/vai-quantization-guide --model /path/to/model_vint8.onnx --mode requantize
--device ve2-xc2ve3858 --target-accuracy "psnr>40" --calibration-data ifm_0.npy
ifm_1.npy ifm_2.npy
```

### Quantize and verify offload

A free-form prompt covering quantization, compilation, partition analysis, and a
board run in a single request:

```text
/vai-quantization-guide Apply quantization to the model:
/path/to/vit_model_fp32.onnx. After quantization you should compile the
model. Attempt to achieve full offloading. Perform partition analysis and
report in detail what was achieved. If the compilation validates full
offloading, run on the board to identify the inference time speedup.
```

## Iterate

Once the basic flow works, refine the result with follow-up prompts in the same
session:

```text
Dequantize the model and requantize with the backbone in VINT8 and head and
tail in BF16.
```

```text
Keep the accuracy-sensitive layers in BF16 and quantize the rest to VINT8.
```

## Session length

Session length is dominated by compile and board iterations, not by the
assistant's reasoning, so it depends on your host machine, your board setup, and
the size of the model. The list below is relative rather than absolute, ordered
from fastest to slowest:

* **A dry run**, or a narrowly specified quantization request, is the quickest
  path. It does not compile, so it is bounded by evaluation alone.
* **Compilation** with the Vitis AI compiler dominates any run that reaches it.
* **Board time** splits into setup and inference, both highly dependent on
  context and data.
* **Exploration** calls Quark in a loop, and is substantially slower than a
  guided quantization or a dry run. Its cost grows with the number of layer
  promotions.
* **ViT models** go through a rigid pipeline, and are normally much faster than
  exploration.

Measure a dry run on your own setup first, and use it to gauge what a full
exploration will cost.

## Configuration ownership

Do not hand-write Quark configurations. The workflow derives them from model
analysis, with target node names and regular expressions for the layers it
promotes, exclude lists for shape and indexing operations that do not need
quantizing, and the extra options that matter for VINT8+BF16 and VINT8+FP16.

Do not hand-write `vitisai_config.json` either. The workflow determines the
compiler options your quantized model needs, and is aware of
[Compiler options workflow](vai-flag-configuration.md).

By policy, the workflow does not perform ONNX graph surgery. It produces a new
quantized or requantized model from a new Quark configuration.

## Supported data types

* BF16, FP16, and VINT8 single precision, plus VINT8+BF16 and VINT8+FP16 mixed
  precision.
* BF16 models cannot run under ONNX Runtime, because there is no CPU reference
  for BF16.
* Extended quantization, as defined by Quark, is not supported in this release.
* Data types beyond those listed are available only through experimental
  methods.

## Partition analysis

Partition analysis aims for full offload: a single partition carrying 100% of
GOPS on the NPU. A model that fits one partition maps onto a single NPU block;
models that do not are spread across several. Where a single partition is not
achievable, the workflow falls back to secondary targets that favor high offload
and a low partition count. It adjusts compiler options first, and changes the
quantization only if that is not enough.

Support for edge quantization in the runtime is limited.

## Limitations

* The workflow has only a partial view of the kernels and graph patterns the
  compiler provides, and less of their constraints. `CpuBecause` can therefore
  appear even with a valid quantization. The workflow recognizes the most common
  `CpuBecause` cases, but not all of them.
* The workflow does not account for `dp_size`, `tp_size`, `overlay`, or
  `device`, and assumes a VE2 device. Follow up with
  [Compiler options workflow](vai-flag-configuration.md) if you need those set.
* The exploration algorithm is deliberately simple, designed for fast loop
  iterations rather than thorough search.
* Warnings are not suppressed, by design.
* The orchestrator and worker split is fully supported only in Claude Code.

## Implementation

You do not need this to use the workflow. It is here for reference when
troubleshooting an installation.

The quantization workflow consists of five skills and one agent, plus optional
plugin skills discovered at runtime:

| Component | Type | Purpose |
| --- | --- | --- |
| `vai-quantization-guide` | Skill | Entry point. Orchestrates end-to-end mixed-precision quantization. |
| `vai-fe-args` | Skill | Determines the compiler options a mixed-precision model needs. |
| `vai-dequantize-model` | Skill | Strips quantization from a model, for requantization. |
| `vai-partition-analysis` | Skill | Analyzes how a model is partitioned across the NPU and CPU, and reports the offloading impact of each quantization and compilation iteration. |
| `vai-vaip-patching` | Skill | Patches models for compatibility, covering shapes and data types. |
| `vai-ffn-quantization` | Skill | Detects and quantizes FFN layers, that is MLP and MHA projections. |
| `vai-mixed-precision-worker` | Agent | Constrained worker spawned by the orchestrator. |

```text
ai_utils/
  skills/
    vai-quantization-guide/            # Entry point, /vai-quantization-guide
      SKILL.md                         # Orchestrator workflow definition
      README.md                        # Mixed-precision reference
      scripts/                         # Quantization, dequantization,
                                       # and accuracy comparison
    vai-fe-args/                       # Compiler option determination
      SKILL.md
    vai-dequantize-model/              # Model dequantization
      SKILL.md
    vai-vaip-patching/                 # Compatibility patching
      SKILL.md
    vai-ffn-quantization/              # FFN detection and quantization
      SKILL.md
    experimental-methods/              # Optional plugins, found at runtime
      <plugin-name>/
        SKILL.md
  agents/
    vai-mixed-precision-worker.md      # Worker agent definition
```

Only `/vai-quantization-guide` is a user entry point. The supporting skills and
the worker agent are invoked on your behalf and should not be called directly.

Plugin skills are optional and are discovered from the filesystem at runtime. If
one is installed, the workflow offers it where relevant, and asks for your
consent before applying it.
