# Compiler options workflow

Entry point: `/vai-flag-configuration`

Iteratively improves the compilation configuration for an ONNX model. The
workflow analyzes your model, proposes a set of compiler options,
compiles with them, measures how many of the model's operators were placed on
the NPU rather than the CPU, measures inference time when a board is available,
and repeats until your stop condition is met. It never modifies the model.

The points in [Board access](index.md#board-access) apply to this workflow.
For the manual procedure this workflow automates, see
[the Vitis AI documentation](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/ep-options.html).

## When to use

* You want more of the model's operators offloaded to the NPU, rather than
  falling back to the CPU.
* You want better inference time from compilation changes alone.
* A model fails to compile and you want it to compile successfully. This counts as
  success even if part of the model stays on the CPU; compiling and reaching
  full offload are separate goals.

You provide an ONNX model and your goals. You get a `vitisai_config.json`,
along with the measured offload and, where a board was used, the board inference
time for every configuration tried.

## Prerequisites

Follow the installation procedure in
[the skill installation guide](../../README.md#quick-start), so that
your assistant has model access and the workflow files installed.

Board access is needed only if inference time is one of your goals. Configure it
as described in [Board access](index.md#board-access). Without a board, the workflow scores
compile success and offload from a host compile, and will not offer to make the
model faster, because it has no measured inference time to work from.

## Quick start

State the model, your goal, and how much autonomy you want. The workflow asks
about anything you leave out:

```text
/vai-flag-configuration Run autonomously on ./model.onnx targeting
ve2-xc2ve3858. Maximize NPU offload. Stop after 2 iterations without
improvement.
```

See [Examples](#examples) for fuller requests, and [Command reference](#command-reference) for the arguments.

## How it works

### Startup interview

Before any analysis, the workflow settles five questions. It resolves what it
can from your request, echoes back what it understood, and asks only about what
is genuinely missing.

**Quantize the model first?** If you say yes, the workflow hands your model to
[Model quantization workflow](vai-quantization-guide.md) and continues once that returns. See
[Handoffs](#handoffs).

**Objectives.** One or more of: compile successfully, improve NPU offload,
improve inference time. You give them in priority order. Inference time is
offered only when a board is available.

**Mode.** Autonomous or human-in-the-loop. See [Operating modes](#operating-modes).

**Stop conditions.** See [Stop conditions](#stop-conditions).

**Compile depth.** See [Compile depth](#compile-depth).

If inference time is one of your objectives, the workflow verifies that a board
run actually works before it begins iterating. If board access is not
configured, it stops and asks how you run inference on your board. It will not
begin a latency-goal session it cannot measure. If you cannot provide board
access, it offers to switch to a compile or offload goal instead.

### Pre-compilation analysis

The workflow then analyzes your model and presents a report before compiling
anything. The report covers:

* The operator-type census, that is how many Conv, Gemm, MatMul, Softmax, and
  other operators the model contains.
* Input and output shapes, and the largest layers by tensor size.
* Any custom operators the model uses.
* The compiler options it proposes for the first iteration, each with a source
  reference and the reason it was chosen.

This report is a gate. Nothing compiles until it has been shown to you. In
human-in-the-loop mode, the workflow waits for you to choose between a baseline
compile and a first optimized compile. In autonomous mode, it prints the report,
states which compile it is starting and why, and proceeds.

### The iteration loop

Each iteration runs three phases:

1. **Propose.** Selects non-default options not already tried, grounded in
   evidence from your model or the previous compile. Proposals are limited to
   three added or changed options relative to the current best configuration,
   which keeps the search interpretable. Widening that budget requires your
   consent.
2. **Compile.** Compiles with the proposed configuration, at the chosen depth.
3. **Validate.** Measures offload and, where a board is available, inference
   time. Records the result and checks the stop conditions.

Every proposal cites a source you can open and states why that option was chosen
for your model. If you ask why a particular option was never tried, the workflow
answers from its own records rather than saying it was not considered.

### Operating modes

**Autonomous** proceeds without asking which options to keep, and runs until a
stop condition is met. Ask for this with a phrase such as "run autonomously" or
"compile the model fully automatically."

**Human-in-the-loop** presents each proposed set as a table, showing each option,
its source, the default it deviates from, the chosen value, and the rationale,
then waits for your approval before compiling. Ask for this with a phrase such
as "run with human-in-the-loop."

Options that trade accuracy, or that change the deployment or runtime contract,
are never applied in autonomous mode. In human-in-the-loop mode they are offered
with the trade-off stated.

### Compile depth

Each iteration compiles at one of two depths.

**Fast** probes the front end and partitioning only. It answers whether offload
improves with a given set of options, and reports unsupported operators and the
partition split. It produces no NPU binary, no board-runnable artifacts, and no
memory analysis.

**Complete** runs the full compile. It is significantly slower, and it is
required before any board timing, before the memory analysis, and for a final
offload figure you can rely on.

The workflow uses fast depth while exploring and switches to complete before
claiming a board latency result or a final offload number. You can override
this, in the interview or in your prompt.

### Stop conditions

The workflow stops when any of the following is true:

* Offload reaches the target percentage you set.
* A number of consecutive iterations bring no improvement. The default is two.
* No untried compiler options remain.
* You tell it to stop.
* Nothing compiles. See [No viable configuration](#no-viable-configuration)

When it stops, it states which condition fired and the numbers behind it.

## Command reference

```text
/vai-flag-configuration [prompt] \
  --model <path_to_onnx_model> \
  [--device <device-id>] \
  [--vitisai-config <path>] \
  [--board] \
  [--work-dir <path>] \
  [--spill-workdir <path>]
```

| Argument | Required | Default | Description |
| --- | --- | --- | --- |
| `--model <path>` | Yes | none | ONNX model to optimize. |
| `--device <device-id>` | No | none | Target device, such as `ve2-xc2ve3858`. If omitted, the workflow takes it from `--vitisai-config`, or asks. It never assumes a device. See [Model Compilation](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/compiling.html). |
| `--vitisai-config <path>` | No | generated | An existing configuration to start from. See [Existing configuration](#existing-configuration). |
| `--board` | No | off | Declares that a board run is available, which enables the inference time objective. Takes no value; configure the board connection as described in [Board access](index.md#board-access). |
| `--work-dir <path>` | No | `./flag_opt_work` | Where iteration artifacts and the run record are written. |
| `--spill-workdir <path>` | No | none | Output directory from an earlier complete compile. The workflow reads the memory analysis in it, rather than producing its own, to judge whether an option change is likely to help latency. |

> **Note**
>
> Objectives, operating mode, stop conditions, and compile depth are not
> command-line arguments. The workflow settles them in the startup interview,
> and you can state any of them in your prompt.

> **Tip**
>
> You do not have to work out these paths yourself. Point the workflow at the
> working directory of an earlier compilation and it locates what it needs.

### Existing configuration

When you supply `--vitisai-config`, every option already set in that file is
treated as fixed. The workflow carries them forward into every iteration and
only adds or adjusts options on top. It never drops, resets, or silently omits a
value you provided unless you explicitly ask it to.

Your original file is left untouched. Each iteration writes a new configuration
into its own directory under the working directory.

## Scope

Only a defined set of supported compiler options are explored.

These configuration fields are tuned:

| Field | Default | Explored |
| --- | --- | --- |
| `device` | none | Never. Set once at startup and held fixed. |
| `keep_outputs` | false | Never. Forced to `true` unless you opt out, because the offload and memory analysis depend on the artifacts it retains. |
| `optimize_level` | 2 | Yes. Level 3 applies more aggressive latency optimizations, after parallelism tuning. |
| `threshold_gops_percent` | 20 | Yes, for an offload goal. Lowering it places more operators on the NPU. |
| `dp_size` | 1 | Yes, for a latency goal. |
| `tp_size` | 0 | Yes, for a latency goal. |
| `preferred_data_storage` | auto | Yes, when convolution or pooling operators dominate the model. |

For the meaning and permitted values of each, see
[Vitis AI EP Configuration File](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/ep-config-file.html).

Alongside these, the workflow explores front-end compiler options(fe_args). These are a
larger set than the configuration fields above, and the skill selects from them based
on your model and the compile evidence. For each option it
proposes, it states the option, the default it deviates from, the value chosen,
and why that option applies to your model.

## Examples

The first three examples run autonomously. To review each iteration instead,
replace "run autonomously" with "run with human-in-the-loop."

### Maximize offload and minimize inference time

```text
/vai-flag-configuration Run autonomously. Maximize NPU offload, then minimize
inference time on board. If the board is unreachable, report the compile
results and say so. Post the path to the report containing the compile and 
board results. Write the winning flags to vitisai_config.json.
```

### Compile for maximum offload

Compile only, with no board run:

```text
/vai-flag-configuration Run autonomously. Maximize NPU offload. Compile only, 
using capability mode for faster iteration, validate with full compilation. 
Write the winning flags to vitisai_config.json. Post the path to the report, 
containing the compilation results.
```

### Compile, and measure on board

```text
/vai-flag-configuration Run autonomously. Maximize NPU offload and minimize 
inference time on board. If the board is unreachable, report the compile results 
and say so. Write the winning options to vitisai_config.json.
```

### Reviewing each iteration

The same request, pausing for your approval before each compile:

```text
/vai-flag-configuration Run with human-in-the-loop. Maximize NPU offload, then
minimize inference time on board. If the board is unreachable, report the compile 
results and say so. Post the path to the report containing the compile and board results. 
```

## Output

Everything is written under the working directory:

| Path | Contents |
| --- | --- |
| `best_vitisai_config.json` | The best configuration found. Absent if nothing compiled. |
| `optimization_summary.json` | Machine-readable summary of the run, written even when no configuration compiled, so a failed run still yields structured output of every attempt. |
| `optimization_ledger.json` | The full record of every configuration tried, which also prevents repeats. |
| `iteration_<n>/` | One directory per iteration, holding the configuration that was compiled, the compile result, and a self-describing `result.json` covering the proposal, the measurements, and the stop metrics the decision rested on. |

The final report gives a per-objective verdict of achieved, partially achieved,
or not achieved, each with the measured figure behind it, along with the reason
the loop stopped.

## Handoffs

> **Warning**
>
> The handoffs below are experimental. Each requires your explicit consent,
> opens a separate and longer session, and is not a normal iteration. The
> workflow tells you when it is suggesting one, and you decide.

**Quantization.** Offered at the start of a session, and again if compiler
options alone cannot reach your goal. Control passes to
[Model quantization workflow](vai-quantization-guide.md), and the loop resumes on the model it
returns.

**Custom operators.** Offered when operators remain on the CPU that no available
option can move, citing each remaining operator, its node count, and the
compiler's own explanation. Control passes to [Custom operator workflow](vai-custom-op.md), which is
a multi-phase workflow that may take substantial time. This loop pauses until it
returns an integrated model, or you decline.

## Session length

Session length is driven by how many iterations run and how deep each one
compiles. Ordered from cheapest to most expensive:

* **Proposing a set of options** is near-instant.
* **A fast compile** is the quick path, but still the dominant cost of an
  iteration.
* **A complete compile** is considerably slower, and a board run adds to it.

A large model explored at complete depth can therefore run for a long time.
Keep it bounded with an explicit stop condition in your prompt:

```text
Stop after 2 iterations without improvement.
```

## Limitations

* No functional or numeric correctness checking. Options that trade accuracy are
  used only with your consent, and never in autonomous mode.
* Only a defined set of supported options are explored.
* 100% offload cannot be guaranteed. Whether an operator can run on the NPU
  depends on compiler support for it.
* Inference time figures come from a real board run, never from a host-only
  compile.
* The workflow proposes against evidence from your model or your compile. An
  option with no supporting evidence is not proposed, however promising it
  sounds.

### No viable configuration

If the baseline configuration and every relevant combination fail to compile,
the workflow stops and reports that compilation fails under all relevant
combinations. It does not churn through irrelevant combinations, and it never
reduces or rewrites your ONNX model to force a compile. Where an unsupported
operator is the cause, it reports the operator and the compiler's reason.
