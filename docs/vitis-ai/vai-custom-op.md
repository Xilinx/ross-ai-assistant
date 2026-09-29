# Custom operator workflow

Entry point: `/vai-custom-op`

Implements a custom operator end to end and integrates it into your build. The
workflow writes the three artifacts that make up a custom operator: the AIE C++
kernel, the Python tiler, and the YAML configuration. It then wires them into
your compilation and runtime configurations, and validates the result in
simulation and, when a board is available, on hardware.

The points in [Board access](index.md#board-access) apply to this workflow. For the manual procedure
this workflow automates, see
[the Vitis AI documentation](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/additional_information/custom_ops/custom_ops_introduction.html).

## When to use

* Your model contains an operator the NPU does not support natively.
* You want several ONNX-level operations fused into a single custom operator.
* You are porting an operator you have already implemented elsewhere and want
  the surrounding integration written for you.

You provide a working Vitis AI environment in which compilation and simulation
succeed, together with either the model or a specification of the operator you
want implemented. You get an AIE C++ kernel, a Python tiler, a YAML
configuration, and the edits to your compilation and runtime configurations
needed to use them.

## Prerequisites

Follow the installation procedure in
[the skill installation guide](../../README.md#quick-start), so that
your assistant has model access and the workflow files installed. Configure
board access as described in [Board access](index.md#board-access).

> **Note**
>
> Ideally keep a board available for the whole session. The workflow runs on
> hardware to validate the operator once it is integrated, and to drive its
> optimization loop. Without a board it falls back on simulation, which is not
> a one-to-one match for the hardware, so numeric mismatches are more likely
> and the operator is not tuned for performance. If the board becomes
> unavailable partway through, the session continues, but everything after that
> point is unvalidated.

### Verify

Launch your assistant:

```shell
~/.local/bin/claude
```

Run `claude` directly if `~/.local/bin` is on your `PATH`. Type
`/vai-custom-op` and confirm the workflow appears as available.

## Quick start

Invoke the workflow with the model, the compilation configuration, and the node
or nodes you want converted:

```text
/vai-custom-op "Create an element-wise multiply custom op" \
  --model ./model.onnx \
  --vitisai-config ./vitisai_config.json \
  --ops Mul_1
```

See [Command reference](#command-reference) for the full argument list, and [Examples](#examples) for other
ways to specify what to replace.

## How it works

The workflow proceeds through eight phases:

1. **Subgraph extraction.** Extracts the target node into an isolated subgraph,
   Model A, and generates CPU reference outputs for it.
2. **File creation.** Generates the kernel C++ code, the tiling Python script,
   the YAML configuration, and a new ONNX model, Model B, that wraps the
   subgraph as a single custom operator node.
3. **Compilation.** Compiles the custom operator model with Vitis AI.
4. **Simulation and board validation.** Runs the compiled model on the x86
   simulator and compares the output against the CPU reference. A board run
   follows, to obtain correctness and performance figures.
5. **Optimization.** Tunes the operator for performance, using further board
   runs as feedback.
6. **Re-integration.** Stitches the validated operator back into the original
   model, replacing the nodes it was built from.
7. **End-to-end verification.** Compiles and runs the integrated model on the
   board.
8. **Reporting.** Writes a structured report to `report.md`.

When you name several operators, each is developed in parallel by its own worker
agent. Once all of them pass compilation and numeric validation, the
orchestrator stitches them into the model one at a time and verifies the result.

## Command reference

```text
/vai-custom-op [prompt] \
  --model <path_to_onnx_model> \
  --vitisai-config <path_to_vitisai_config.json> \
  --ops <nodes:op_name> [<nodes:op_name> ...]
```

| Argument | Required | Description |
| --- | --- | --- |
| `--model <path>` | Yes | Path to the full ONNX model. |
| `--vitisai-config <path>` | Yes | Path to the `vitisai_config.json` holding your compilation and runtime options. |
| `--ops <nodes:name> [...]` | Yes | One or more operator specifications, in `node1,node2:op_name` format: a comma-separated list of ONNX node names, a colon, then the name to give the resulting custom operator. Separate multiple specifications with spaces. |

You can give a free-form prompt instead of `--ops`, or alongside it, when the
nodes you want replaced are easier to describe than to list.

## Examples

### One node

Convert the ONNX node `/layer_0/Mul_1` into a custom operator named `mymul`:

```text
/vai-custom-op "Create an element-wise multiply custom op" \
  --model ./model.onnx \
  --vitisai-config ./vitisai_config.json \
  --ops /layer_0/Mul_1:mymul
```

### Several nodes

Convert `/layer_0/Mul_1` and `/layer_2/AffineGrid` into custom operators
named `mymul` and `myaffinegrid`. Each is developed in parallel by its own
worker agent:

```text
/vai-custom-op \
  --model ./model.onnx \
  --vitisai-config ./vitisai_config.json \
  --ops /layer_0/Mul_1:mymul /layer_2/AffineGrid:myaffinegrid
```

### Every instance of an operator

If the nodes are easier to describe than to list, use a free-form prompt:

```text
/vai-custom-op \
  --model ./model.onnx \
  --vitisai-config ./vitisai_config.json \
  Replace all instances of `onnx.AffineGrid` by a custom op named
  `mydomain.myaffinegrid`.
```

### A subgraph

Name the boundaries and state whether each is included:

```text
/vai-custom-op \
  --model ./model.onnx \
  --vitisai-config ./vitisai_config.json \
  Replace all ops between `Conv_7` (excluded) and `Softmax_2` (included) by a
  custom op.
```

### A specific optimization

The workflow optimizes for performance by default, using a tuning loop driven by
board measurements. To direct that effort, say what you want. Absorbing the data
layout transformations caused by transposes around an operator is a common case:

```text
/vai-custom-op \
  --model ./model.onnx \
  --vitisai-config ./vitisai_config.json \
  Replace `Softmax_2` by a custom op that absorbs the transpositions that
  surround it.
```

## Iterate

Once the basic flow works, refine the operator with follow-up prompts in the
same session:

```text
Add tiling so this custom op supports tensors larger than L1 memory.
```

```text
Distribute the computation across all 16 AIE cores using the 4x4 overlay.
```

```text
Optimize the kernel with vectorization using aie::vector intrinsics.
```

Each of these has a worked example bundled with the product, under the
`vai-custom-op-implementation` tutorial directory: `mul/1_tiled` for tiling,
`mul/2_distributed` for distribution, and
`negate/3_distributed_and_vectorized` for vectorization. See the 
[Bundled examples and utilities](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/additional_information/custom_ops/custom_ops_introduction.html#bundled-examples-and-utilities)
section of the Vitis AI documentation for more details.

## Board runs

The workflow runs models on the board as part of developing an operator, but you
can also ask it to run one directly, without creating a custom operator. With
board access configured as described in [Board access](index.md#board-access):

```text
Run the model ./model.onnx on my board using configuration
./vitisai_config.json using the /vai-custom-op skill.
```

Add input files, if you have them:

```text
The inputs are located in ./model_inputs.
```

And point at an existing build, if the model is already compiled:

```text
The model is already compiled, the compiled artifacts are in ./mymodel_cache.
```

## Session length

Sessions can be long, and the time is dominated by compilation, simulation, and
board iterations rather than by the assistant's reasoning. A single optimized
element-wise operator, such as a multiply or a divide, sits at the short end.
Fusing several ONNX-level operations into one custom operator takes considerably
longer. Start with a simple operator to calibrate what your setup costs before
attempting a fused one.

## Limitations

### Data layout

The workflow knows about the input and output data layout requirements that
compilation imposes on the operator, but does not always account for them. The
result is extra transpose or pad operations that can outweigh any gain from the
operator itself. Check the generated graph. If you see them:

```text
Absorb the Transposes around the generated custom op.
```

### Repeated operators

Modifying several instances of the same custom operator can fail. The workflow
usually recovers by renaming them, but not always. If it does not:

```text
Rename each instance of the custom op with its own name.
```

### Accuracy bias

The workflow prefers a functionally correct operator to a faster one that
changes the behavior of the original model. If you can accept a looser
tolerance, say so:

```text
Accept inaccurate outputs with a relative tolerance of 5%.
```

### Complex operators

Aggressively fused operators, such as a fully fused multi-head attention, may
not converge. These need several sub-kernels, or round trips through scratch
buffers at different memory levels, and the decomposition is beyond what the
workflow will find on its own. Agree on a decomposition first, then ask it to
implement that:

```text
Implement the GroupQueryAttention_1 op as a custom op. Please look at
the mathematical expression of the GQA op and suggest a decomposition of this
op; conceive the custom op combining each of the individual ops you
decomposed.
```

### Other

* Tiling implementations can fail unexpectedly in combination with aggressive
  optimizations, such as optimization level O3.
* The workflow does not have precise instructions for building MX6 or MX9 custom
  operators.
* Without a board, numeric mismatches and reduced performance are considerably
  more likely, because the result is validated only in simulation.

## Implementation

You do not need this to use the workflow. It is here for reference when
troubleshooting an installation.

The custom operator workflow consists of two skills and one agent:

| Component | Type | Purpose |
| --- | --- | --- |
| `vai-custom-op` | Skill | Entry point. Orchestrates several custom operators in parallel, using worker agents. |
| `vai-custom-op-implementation` | Skill | End-to-end development of a single custom AIE operator. Invoked by `vai-custom-op`. |
| `vai-custom-op-worker` | Agent | Constrained worker spawned by the orchestrator, one per operator. |

```text
ai_utils/
  skills/
    vai-custom-op/                # Entry point, invoked as /vai-custom-op
      SKILL.md                    # Orchestrator workflow definition
    vai-custom-op-implementation/ # Single-operator implementation skill
      README.md                   # AIE architecture reference
      tiling.md                   # Tiling technical reference
      scripts/                    # Compile, simulate, cut and stitch ONNX
      tutorial/                   # mul, negate, topk, softmax, and others
  agents/
    vai-custom-op-worker.md       # Worker agent definition
```

Only `/vai-custom-op` is a user entry point. The implementation skill and the
worker agent are invoked on your behalf and should not be called directly.
