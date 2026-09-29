---
name: vai-flag-configuration
description: Iteratively improve the VAIML compilation configuration for an ONNX model.
  Analyzes the model and the flag catalog, proposes a non-default vitisai_config.json,
  compiles it, validates NPU offload (and board inference time when available), and
  loops until the user's stop condition is met. Use when the user wants to tune compiler
  flags, improve NPU offloading, or reduce inference time for a model.
license: Apache-2.0 WITH LLVM-exception
compatibility: Requires VitisAI 6.3+
metadata:
  version: 6.3.0
  stage: beta
---
<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->

# Flag-Configuration Skill

## Description

Orchestrates a three-phase loop to improve a model's VAIML configuration:
**(1) analyze & propose flags -> (2) compile -> (3) validate**, repeating until
the user is satisfied or no further improvement is possible. This skill is an
orchestrator: it reuses existing skills/scripts and owns only the loop, the flag
catalog, user interaction, and bookkeeping.

## Parse Arguments

This skill expects arguments in this form:

```
--model <onnx> [--t <activate>] [--device <ep-device-id>] [--vitisai-config <json>] [--board] [--work-dir <dir>] [--spill-workdir <dir>]
```

- `--model <path>`: **(MANDATORY)** ONNX model to optimize.
- `--t <path>`: **(OPTIONAL)** product virtualenv activate script. Omit when running
  inside the **official Vitis AI Docker image** or another pre-provisioned install
  where `flexml`, `onnxruntime`, and `dlanalyzer` already import. Do NOT search the
  filesystem for an activate script when `--t` is absent.
- `--device <value>`: **(OPTIONAL, no default)** target for `vaiml_config.device`
  when generating a config. If omitted, take `device` from `--vitisai-config` or
  ask the user — never assume `stx` or any other part. Use the **Supported Devices
  for Compilation** table in the public Vitis AI docs (values like
  `ve2-xc2ve3858`, `ve2-xc2ve3558`, …). Some installs also
-  accept short names (`t50`, `t20`); VAIP may normalize or override the
  value for the active product context — echo back what the compile actually used.
- `--vitisai-config <path>`: **(OPTIONAL)** starting `vitisai_config.json`. When
  given, **load it and treat every flag already set there as fixed** — carry
  them forward into every iteration and only add or tweak flags on top; never
  drop, reset, or silently omit a user-provided value unless the user explicitly
  asks to remove it. It must include top-level `target` and `targets`; a file
  containing only `passes` is incomplete and makes VAIP target discovery fail.
  When omitted, generate the complete baseline config shown below.
- `--board`: **(OPTIONAL)** a board run is available; enables the inference-time
  objective.
- `--work-dir <path>`: **(OPTIONAL, default: ./flag_opt_work)** where iteration
  artifacts and the ledger are stored.
- `--spill-workdir <path>`: **(OPTIONAL)** an existing compile output dir that
  already contains a `DetailedSpillingAnalysis.csv`. If given, the L3 spilling
  check reads it directly instead of relying on the loop's own compile.

Skill directory: the directory containing this SKILL.md (referred to as
`<skill_dir>`).

## Environment Setup Convention

Wherever a bash snippet shows `<ENV_SETUP>`: if `--t <path>` was given, replace
it with `source <path> 2>/dev/null &&`; otherwise omit the token. Never
auto-detect an activate script.

## Transparency: cite sources and explain choices (ALWAYS)

Every recommendation, proposal, or decision you present MUST include both:

1. **The source material, when available** -- a source the user can actually
   open. For flags, this is `FlagSpec.path` from `scripts/flag_catalog.py`, which
   resolves to whatever exists in the current installation: the shipped
   public flag catalog anchor (`fe-args.html#fe-flag-<name>`). For validation, cite
   the SDK timing JSON from `scripts/ai_analyzer_timing.py` and the
   `ai_extract.py` extract from the `vai-perf-analysis` skill (both
   call the AI Analyzer SDK — never raw timer JSON or on-disk operator_metrics);
   for spilling, the `DetailedSpillingAnalysis.csv` path; for the offload gap,
   the `*.chained_kernel.tosa.mlir` path. For handoffs, cite the agent or skill
   by name.
2. **The rationale for the AI's choice** -- why this flag/iteration/handoff, in
   plain language, grounded in the cited source (e.g. the flag's `description`,
   the offload/latency numbers, the spill driver).

If no source material exists for a claim, say so explicitly ("no source
available; this is a heuristic") rather than presenting it as authoritative. A
proposal without a citation and a reason is INCOMPLETE -- do not present it.

> **Cite only what the user can open.** Prefer, in order: the public Vitis AI
> documentation below; `fe-args.html#fe-flag-<name>` via `FlagSpec.path`; files
> in the user's own work/compile directory; and this skill's own `scripts/`.

### Authoritative documentation sources (cite these first)

For any `vaiml_config` / EP-config option, cite the **AMD Vitis AI EP
Configuration File** doc -- it is the authoritative public source for types,
supported values, and defaults -- with the specific anchor:

- Base URL:
  <https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/ep-config-file.html>
- `optimize_level` (1/2/3; default 2; O3 stacks on `tp_size`): `#optimize-level`
- `dp_size` (data parallelism; 1-6 for T20, 1-9 for T50;
  default 1): `#dp-size`
- `tp_size` (tensor parallelism; 0-6 / 0-9; default 0 = auto, resolves to 6 on
  T50): `#tp-size`
- `threshold_gops_percent` (0-100; default 20; ops above the threshold go to the
  NPU, below to the CPU): `#threshold-gops-percent`
- `keep_outputs` (bool; default false in the EP doc; **always set true** in this
  skill unless the user opts out; retains the vaiml dir for MLIR/spilling/partition
  artifacts): `#keep-outputs`
- `preferred_data_storage` (`auto` | `vectorized` | `unvectorized`; default
  `auto`): `#preferred-data-storage` — prefer **vectorized** for conv and stencil
  reductors (MaxPool, AveragePool, GlobalAveragePool); **unvectorized** for
  GEMM/MatMul-heavy graphs
- `device` (mandatory; EP doc string, e.g. `ve2-xc2ve3858` for XC2VE3858):
  `#device` — full list in
  [Supported Devices for Compilation](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/compiling.html#supported-devices-for-compilation)

When you propose one of these, cite the doc anchor AND state the value + default
from it.

## Board timing via the AI Analyzer SDK (never raw timer JSON)

All inference-time numbers come from the **AI Analyzer SDK**, not from hand-parsed
`record_timer_*.json`, `board_run.log` medians, or on-disk `operator_metrics`
exports. Raw files lie (schema-2.0 list timers read empty), double-count (summing
the timing dataframe), or inflate E2E (`run.py` perf_counter vs board VAIP).

**Primary script** (ships with this skill):

```bash
MPLBACKEND=Agg python3 <skill_dir>/scripts/ai_analyzer_timing.py \
  <board_work_dir> -o <work-dir>/iteration_<i>/sdk_timing.json
```

`measure()` / the CLI returns:

| Field | Meaning |
|---|---|
| `board_e2e_ms` | **Primary latency**: the SDK `Inference N` row (host + device), emitted only when `sdk_status` is `OK` |
| `board_e2e_usec` | The same E2E in microseconds (`board_e2e_ms * 1000`) — copy it into the ledger as-is |
| `npu_layer_ms` + `pm_load_ms` | Device timeline (kernels + program loads) |
| `npu_device_total_ms` | `npu_layer_ms` + `pm_load_ms` |
| `cpu_only_ms` | **E2E − `npu_device_total_ms`** — host + CPU-fallback ops |
| `cpu_host_rows` | Top CPU-timeline SDK rows — use before mapping an op-type flag |
| `vaip_median_ms`, `board_ms` | Cross-checks only (VAIP partition timers, `board.log` VART line) |

The cross-checks are **expected to disagree** with `board_e2e_ms`, and a lower
number there is not a reason to prefer it: `vaip_median_ms` and the EP-side
`vaiml_par_*` row time the partition call, not the inference, so both exclude the
host pre/post work that `Inference N` includes.

When `npu_device_total_ms` is a small fraction of `board_e2e_ms`, the model is
host-bound. A flat E2E across iterations then does **not** mean the flags did
nothing — it means they moved device time that E2E barely depends on. Judge such
runs on `npu_device_total_ms` and `cpu_host_rows` as well, and say so in the
verdict.

**Bottleneck op types** (NPU vs host phases): run the sibling
`ai_extract.py` from the scripts folder of the `vai-perf-analysis` skill on the
same directory. It also calls the
SDK (`get_performance_summary`, `get_performance_summaries`, `get_total_execution_time`, `get_timing_dataframe`). Read `operator_metrics`
from its JSON stdout — **do not open perf JSON already on disk without going
through `ai_extract.py`**.

Five traps the SDK frame punishes (`metrics_from_frame` in `ai_analyzer_timing.py`
handles all five):

1. Do not sum the timing dataframe (total + breakdown → ~N× inflation).
2. Do not parse `record_timer_*.json` by hand — use the SDK (+ `normalize_timer_json`
   when `record_timer_subgraph_cpu_ts.json` is a schema list).
3. Strip trailing spaces on `Name` (`"Inference 1 "`).
4. Mean per `inference_id`, not grand-total ÷ layer count.
5. Split `PM Load N` out of kernel time.

Pass the original full board work directory to `ai_analyzer_timing.py`; do not
hand-reconstruct a timing directory. The script reads the selected timer
session's `context_init[].subgraph_path`, keeps that compile cache, and builds a
lightweight SDK-readable shadow when needed. The shadow uses a real directory tree
whose payloads are file symlinks, not a symlink to the cache directory as a whole:
`dlanalyzer` discovers reports with a non-following recursive walk, so files
below directory symlinks are invisible and `get_timing_dataframe_batch` returns
`Failed to get reports`.

Treat `sdk_status != "OK"` as a timing failure to diagnose. In particular, do
not promote `vaip_median_ms`, raw VAIP timer medians, or `board.log` timing to
the primary SDK result when report discovery fails; fix the input layout and
rerun this script.

## Flag Relevance Policy (what is worth spending a compile on)

A `flexml-compile` run is slow, so `public: true` in the catalog is necessary but not
sufficient: a flag is only worth an iteration if it can plausibly change the
compiled result **for this model**. `scripts/flag_catalog.py` tags every flag
with a `relevance` and a cited `exclusion_reason`:

| Relevance | Meaning | How to use it |
|---|---|---|
| `proposable` | may change the compiled result | normal candidate |
| `needs-consent` | trades accuracy, or changes the deployment/runtime contract | never in autonomous mode; in human-in-the-loop, offer it WITH the trade-off stated |
| `excluded` | cannot change the compiled result or needs an artifact you cannot synthesize | never propose |

Use `proposable(catalog, objectives=<objectives>, autonomous=<bool>)` to get the
candidate set, never the raw catalog. It also drops flags whose
`goal_conflicts` include a selected objective (for example,
`small-tensor-threshold-unwrapping` works against an offload goal).

## Graph evidence (propose against the model, not from memory)

Every proposed flag needs **graph evidence**: something in this model, or in
this compile, that the flag acts on. The two sources are:

- **the ONNX graph** (pre-compile) -- `model_ops.op_type_counts`,
  `detect_custom_ops`, `input_shapes` / `output_shapes` / `large_layers`;
- **the compiled graph** (post-compile) -- `offload_gap.offload_gap`, i.e. what
  the compiler actually failed to place on the NPU.

A flag with no evidence in either is not a candidate, however promising its
description sounds. Say so rather than proposing it speculatively.

## Orchestrator-only handoffs (do not invoke leaf skills directly)

The mixed-precision and custom-op **leaf skills** are not entry points of this
orchestrator. **Do not invoke them directly.** Always go through the matching
orchestrator skill after printing the experimental-feature warning:

- **The only entry point to the mixed-precision skill is through the
  `vai-quantization-guide`**. That orchestrator may then run quantization /
  dequantization / patching. Never call `vai-quantization-guide`'s leaf skills
  (`vai-dequantize-model`, `vai-vaip-patching`, or similar) on their own from
  this loop. (`vai-fe-args` is island *analysis* only — it is not a
  mixed-precision skill entry point.)
- **The only entry point for the custom-op skill is through the
  `vai-custom-op`**. Never invoke `vai-custom-op-implementation` to create or
  integrate an op except by handing off to that orchestrator. Reusing
  `compile.py` from the `vai-custom-op-implementation` skill for a compile in
  Phase 2 is not a custom-op skill entry; it is a compile helper.

## Public configuration surface (the only flags this skill explores)

The shipped `fe-args.html` (the public flag catalog under `docs/`) is the
authoritative catalog. It lists every public frontend flag and the
vaiml_config fields below. **Do not propose flags outside this surface.**

### Config key aliases (logs and older configs)

When reading compiler logs or an existing `vitisai_config.json`, treat these
pairs as the same option:

| Preferred key (write in new configs) | Alias (still valid) |
|---|---|
| `fe_args` | `fe_experiment` |
| `group_args` | `experiments` |

A log line may say `fe_experiment args:` even when the JSON used `fe_args`.

### vaiml_config fields

| Field | Default | Role | Exploration |
|---|---|---|---|
| `device` | *(required — no skill default)* | Target part; EP doc ID or `--device` / existing config | Never — set once at startup |
| `keep_outputs` | false (EP doc) | Retain compile artifacts | **Always true** unless user opts out |
| `optimize_level` | 2 | O3 for aggressive latency after tp/dp tuning | Yes |
| `threshold_gops_percent` | 20 | Lower → more ops on NPU | Yes, for offload goal |
| `dp_size` | 1 | Data parallelism (throughput) | Yes, for latency goal |
| `tp_size` | 0 (auto) | Tensor parallelism (per-request latency) | Yes, for latency goal |
| `preferred_data_storage` | auto | Data layout: **vectorized** for conv/pool CNNs | Yes, when conv or pool ops dominate |

Also set `enable_f32_to_bf16_conversion` when mixed-precision island analysis
(from `vai-fe-args`) requires it — it is not a frontend (`fe_args`) flag.

### Baseline `vitisai_config.json`

The public [AMD Vitis AI EP configuration
reference](https://vitisai.docs.amd.com/projects/gen2/en/latest/docs/model_compilation/ep-config-file.html)
requires both the pass definitions and the top-level target graph. Every
generated iteration config must have top-level `target` and `targets`; never
write a passes-only file. The `targets[].pass` list names the entries in
`passes` in execution order.

Write each config to `<work-dir>/iteration_<i>/vitisai_config.json`. Compilation
runs with `<work-dir>/iteration_<i>` as its current directory and passes the
same file as `--vitisai-config vitisai_config.json`. When the user supplies
`--vitisai-config <path>`, copy that complete config into the iteration
directory, preserve its target graph and existing settings, then apply only the
approved iteration changes.

Minimal baseline for `ve2-xc2ve3858`:

```json
{
  "passes": [
    {
      "name": "init",
      "plugin": "vaip-pass_init"
    },
    {
      "name": "vaiml_partition",
      "plugin": "vaip-pass_vaiml_partition",
      "vaiml_config": {
        "device": "ve2-xc2ve3858",
        "keep_outputs": true
      }
    }
  ],
  "target": "VAIML",
  "targets": [
    {
      "name": "VAIML",
      "pass": ["init", "vaiml_partition"]
    }
  ]
}
```

For **frontend (`fe_args`) flags**, read the shipped `fe-args.html` public catalog.
Each flag's description, defaults, and optional **`use_case`** routing token live
there (YAML `use_case` → catalog `data-use-case` / **Use case** metadata).
Cite `fe-args.html#fe-flag-<name>` via `FlagSpec.path` for mechanism
details — do not duplicate descriptions from memory.

When choosing candidates, map the user's goal and compile evidence to a use
case, then list flags with:

```python
flags_for_use_case("<token>", load_catalog())
```

where `<token>` is one of `push-to-cpu`, `offloading`, `accuracy`, or
`coverage`. Read each returned flag's catalog entry before proposing it. Only
flags with `public: true` in their YAML appear in `fe-args.html` and
therefore in `load_catalog()`; if a flag is missing from the catalog, say it is
not on the public surface yet — do not propose it.

### Use-case routing (from the catalog)

| Use case | When to consider |
|---|---|
| **push-to-cpu** | Host-side small tensors / edge ops are cheaper on CPU; *conflicts* with max-offload goals |
| **offloading** | Close NPU gaps: quantize/dequant edges, conv→gemm rewrites |
| **accuracy** | User accepts slower kernels for listed ops (`use-accurate-mode` needs consent) |
| **coverage** | User guarantees Gather indices are non-negative (`indices-are-positive` needs consent; compiler name — not `gather-indices-are-positive`) |

**Push-to-CPU vs offload objective:** flags tagged `push-to-cpu` carry
`goal_conflicts` with `offload` in `flag_catalog.py` — legitimate for latency /
CPU-optimize flows, self-defeating when the objective is maximum NPU offload.

## Startup Interview (ask BEFORE the loop)

**Always settle this interview first -- even when the skill is invoked explicitly
(`/vai-flag-configuration`) part-way through a conversation.** Do not assume
goals/mode from *earlier* conversation; the objectives, mode, and stop metrics
must be settled for this run before Phase 0 or any compile.

### First, read the answers the user already gave you

Settled is not the same as asked. The invocation itself is an answer: treat
anything stated in it as decided, and ask only about what is genuinely missing.
Re-asking a question the user just answered is a bug, not diligence. For
example:

> `/vai-flag-configuration Generate for the model /my/dir/awesome.onnx, targeting`
> `t50, use a temporary dir. I don't want to have quantization. The target`
> `should be full offloading.`

resolves the model, `--device t50`, the work dir, "no quantization", and an
`offload` objective with a 100% target — leaving only mode (autonomous vs
human-in-the-loop) and the remaining stop metrics to ask about. Write
`ve2-xc2ve3858` (or the user's exact EP device string) into the
`vaiml_partition` pass's `vaiml_config.device` when generating the config. If
neither `--device` nor `--vitisai-config` supplies a device, **ask** — do not
default.

Do this in three steps:

1. **Resolve** every setting you can from the invocation and its flags: model,
   `--device`, `--board`, `--vitisai-config`, `--work-dir`, quantization
   yes/no, objectives, target offload, mode, stop metrics.
2. **Echo back** the resolved settings in one short block, marked as taken from
   the request, so a misreading is visible before any compile starts.
3. **Ask only the unresolved ones**, using AskUserQuestion.

Only the *earlier conversation* is off-limits as a source; the invocation is
not. If the invocation is ambiguous rather than silent (e.g. "make it fast"
without `--board`), treat it as unresolved and ask.

For any question that remains, ask the user, using AskUserQuestion, in this
order:

1. **Quantize the model first?** If you are about to offer or invoke
   quantization, **print this warning to the user first**, then ask:

   > **Warning:** Calling this sub-skill for modification of the input model or creating a custom-op is an experimental feature of the flag-configuration skill.

   If the user wants quantization, the only entry point to the mixed-precision
   skill is through the `vai-quantization-guide` (shipped alongside this skill in
   the ai_utils bundle). Hand the model to that orchestrator; do not invoke the
   mixed-precision leaf skills directly. The orchestrator runs the
   quantization/dequantization/patching sub-skills, then continue the loop with
   the returned quantized model. If declined, use the model as-is.

2. **Objective(s)** (multi-select; state expectations):
   - *Compile successfully* (default) -- the default config is expected to
     compile; use this as the baseline.
   - *Improve NPU offloading* -- maximize offloaded ops / minimize CPU fallback.
   - *Improve inference time* -- offered ONLY when `--board` is set.
   The selected objectives, in priority order, drive `Ledger.best(objectives)`
   with tokens `compile`, `offload`, `latency`.
   **Board pre-flight for `latency`:** a latency objective can only be measured
   by running the model on a board. Before iterating, establish that a board run
   actually works:
   - Use the `scripts/run_on_board.py` script provided alongside the skill.
   - If it is not there, or board access is not configured, STOP and ask the user
     how they run inference on their board (their own script, harness, or
     command) plus the connection/target details it needs. Only begin iterating
     once a board run succeeds. Never start or mark an iteration 0 "complete"
     for a latency goal you cannot measure this run.
   - If the user cannot provide board access, offer to switch to a
     compile/offload objective, which is measurable on host.
3. **Mode**:
   - *Fully autonomous exploration* -- iterate without stopping for approval.
   - *Human-in-the-loop* -- present each proposed flag set and wait for approval
     before compiling.
4. **Stop metrics** (suggest defaults, let the user confirm/edit):
   - NPU offload >= target %, OR
   - N iterations with no improvement (default N = 2), OR
   - no untried `public: true` flags remain, OR
   - user says stop.
5. **Compile depth** (ask unless the user already chose):
   - *Fast — get_capability* — FE/partition probe only (~minutes, no AIE binary).
     Use for early iterations, offload-gap discovery, and flag proposals that only
     affect partitioning/FE. Run `scripts/compile_probe.py --mode capability`.
   - *Complete — full compile* — ORT VitisAI compile through AIE (`--mode compile`,
     default). Required before board timing, spilling CSV, and final offload proof.

Default: **fast** while exploring flags; switch to **complete** before claiming
board latency or a final offload number.

## Compile depth and output types

### Two modes this skill runs

| Mode | Command | Answers | Does NOT produce |
|---|---|---|---|
| **Fast (`capability`)** | `compile_probe.py --mode capability` | Whole-model NPU capability, CpuBecause / unsupported ops, partition GOP split, `get_capability_v5.json` when `keep_outputs` | AIE binary, board-runnable xclbin, spilling CSV |
| **Complete (`compile`)** | `compile_probe.py --mode compile` (or `compile.py`) | Everything above plus per-partition AIE compile, `final-vaiml-pass-summary.txt`, spilling artifacts | Nothing — this is the validation compile |

#### How fast mode works in a shipped Vitis AI environment

The official **Vitis AI Docker image** and product install ship a Python environment
with the `flexml` package and its native **`pyflexmlcompile`** module already on
`PYTHONPATH`. In that environment:

- **`--t` is usually omitted** — same convention as the other Vitis AI skills:
  packages are pre-provisioned inside the container or product venv.
- **`compile_probe.py --mode capability`** imports `flexml.experimental.ext.dynamic`
  and calls **`get_capability_v4`** (frontend/partition probe, no AIE binary).
- Read **`compile_probe.json`**: when fast mode succeeded,
  `flexml_lite.available` is `true` and there is **no** `fallback` key.

If `flexml` cannot be imported (for example a minimal venv used only to run
`model_ops.py` with `onnx`), the script sets `fallback` in `compile_probe.json`
and runs a **full ORT VitisAI compile** instead — check that field; do not treat
the iteration as a fast probe.

**Device strings on the two paths.** The EP config doc uses hyphenated IDs
(`ve2-xc2ve3858`, …). ORT/VAIP accepts those on the **complete compile** path.
The flexml **`get_capability`** API expects flexml device tokens (`ve2`, `stx`, …);
`compile_probe.py` normalizes known EP IDs to `ve2` for capability-only calls.
After any compile, cite the `device` value that landed in the iteration's
`vitisai_config.json`.

Always write `compile_probe.json` beside the iteration config:

```bash
python3 <skill_dir>/scripts/compile_probe.py <model> \
  --vitisai-config <work-dir>/iteration_<i>/vitisai_config.json \
  --mode capability --cache-dir <work-dir>/iteration_<i>/<model_stem> \
  -o <work-dir>/iteration_<i>/compile_probe.json
```

### FlexML `output_type` stages (when driving flexml-compile directly)

The bundled `compile.py` path always targets a **board-ready** compile (≈ `aie-exe`).
When the user or a handoff uses **flexml-compile** / `flexml.compile()` instead,
`output_type` chooses how far down the pipeline to stop. Ordered roughly by depth:

| `output_type` | Stops after | Useful artifacts | Typical use |
|---|---|---|---|
| `tosa-mlir` | TOSA import/lowering | `*.tosa.mlir`, `unfused.viz.json` | Import/debug only |
| `frontend-mlir` / `mllib-kernel` / `incore-chaining` | FE fusion & chaining | `fused.viz.json`, `*.chained_kernel.tosa.mlir` | FE flags, CpuBecause — **same stage as get_capability** |
| `partition-mlir` | Fail-safe partitioner | `fs.fused.viz.json`, partition MLIR | Partition-cap debugging |
| `dse-mlir` | Tiling / DSE | `par.subgraph.dse.mlir`, `schedule.viz.json` | TSG / tiling handoffs |
| `aie-graph` / `aie-compiler` | Backend codegen | ADF / compiler IR | Backend-only issues |
| `aie-exe` | Full AIE binary | Runnable partition tree | **Complete compile** (default for validation) |

Do not quote a stage the run did not reach — read which files exist under
`cache/` and cite that path.

### Reading failures for the user (error-message usability)

On every compile (either mode), read `compile_probe.json` → `failure_report` and
**translate it**, do not paste raw logs first:

1. **`user_message`** — one paragraph in plain language (what broke, for whom it matters).
2. **`actionable`** — concrete next config/flag checks tied to evidence.
3. **`evidence`** — cited log lines / summary fields the paragraph rests on.

Source priority when building the story:

| Signal | Where | Usability note |
|---|---|---|
| Top-level exception | ORT / compile.py stderr | Often vague ("Failed") — always dig deeper |
| `preliminary-vaiml-pass-summary.txt` | `<cache>/cache/` | **Best for threshold/partition story** — GOP % per `vaiml_par_*` |
| `final-vaiml-pass-summary.txt` | same | Offloaded vs supported GOPs; subgraphs below threshold count |
| `get_capability_v5.json` | under cache when `keep_outputs: true` | Unsupported op list at original ONNX names |
| `aie_unsupported_original_ops_with_reasons.json` | FE partition tree | Per-op reason strings — cite verbatim |
| `AIECompiler.log` / `aiecompiler-flexml.log` | under `vaiml_par_*` | Backend failures after silent CPU fallback |
| `compile.log` tail | iteration dir | Last ERROR lines |

**Silent failure trap:** ORT can print "Compilation successful" while AIE errors
force CPU fallback. `compile_probe.py` runs `scan_errors()` from `compile.py` for
this reason — if `component_scan` evidence is non-empty, the compile **failed** for
loop purposes even when no exception was thrown.

Never invent a fix the compiler did not state; route `CpuBecause` / unsupported
reasons through `offload_gap.classify_message` when proposing the next flag.

## Phase 0 -- Pre-Compilation Analysis (HARD GATE before any compile)

This phase runs ONCE, before the loop, and **must complete and be presented to
the user before any compilation is started** -- including a default/baseline
compile. Do the analysis (Step A), then present the consolidated results
(Step C). **Do NOT run `compile.py`, suggest a default/baseline compile, or
enter the loop until the Step C results have been shown to the user** (and, in
human-in-the-loop mode, acknowledged). Only after Step C do you suggest the next
step (starting a compile).

### Step A: Operator-type census (pre-compilation)

Analyze the ONNX model(s) that will be compiled so the first proposal is grounded
in the model's actual contents:

- Census operator types with `model_ops.op_type_counts(<model>)` (see
  `scripts/model_ops.py`). Report the op-type histogram (e.g. how many Conv,
  Gemm, MatMul, Softmax, ...).
- Match those op types to catalog flags with
  `model_ops.match_flags_to_ops(op_counts, load_catalog())`. This
  returns `public: true` flags whose name/description target a present op type,
  each with a cited reason (the op type + the flag path). These are the seed
  candidates for Phase 1 iteration 0.
- Inspect shapes/sizes **before any compile** with `scripts/model_ops.py`:
  `input_shapes(<model>)` (declared graph-input shapes),
  `output_shapes(<model>)` (per-tensor shapes via `onnx.shape_inference`), and
  `large_layers(<model>, min_elements=...)` (nodes whose inferred output tensor
  is large, biggest first). This gives structured shape data for latency-
  bottleneck reasoning without needing a compile.
- **Detect custom ops** with `model_ops.detect_custom_ops(<model>)` (any node in
  a non-standard operator domain). If any are found, the generated
  `vitisai_config.json` MUST carry a `custom_ops` block with an `op_config` entry
  per custom op, e.g.:

  ```json
  "custom_ops": {
    "com.amd.quark:TestDummyOp": { "op_config": "<path>/dummy_op.yaml" }
  }
  ```

  For the Quark quantization custom ops (`is_quant`: MX6 BFP QDQ,
  Extended[De]QuantizeLinear) the `op_config` YAML comes from the quantization
  flow and must be included or the model will not compile. For a general custom
  op, the user must supply its `op_config` YAML; authoring that support is out
  of scope for this skill, but the config must still reference it.
- Present the op-type histogram, the shape summary (inputs + the largest layers),
  the matched flags (with source paths and reasons), and any detected custom ops
  per Transparency. When Conv or pool ops (MaxPool, AveragePool,
  GlobalAveragePool) dominate the histogram, include
  `preferred_data_storage=vectorized` as a candidate and cite
  `#preferred-data-storage`. If a model has an op type with no matching flag, say so; do
  not invent one.


### Step C: Present pre-compilation results, THEN suggest next steps (GATE)

Before any compilation, present a single consolidated **Pre-Compilation Analysis
report** to the user containing:

- the operator-type histogram (Step A) and the op-type -> flag matches, each with
  its cited source path and reason;
- the mixed-precision/topology findings (from `vai-fe-args`);
- the resulting **proposed starting configuration** (the candidate non-default
  flags for iteration 0) with per-flag source + rationale.

**This report is a gate.** Only AFTER it has been shown to the user do you
suggest the next step, e.g.:

> "Pre-compilation analysis complete (above). Next I can start a **default/
> baseline compile** (no non-default flags) to establish the baseline, or a
> **first optimized compile** using the proposed configuration. Which would you
> like?"

- In **human-in-the-loop** mode: wait for the user's choice before compiling.
- In **fully autonomous** mode: still print the report first, then state which
  compile you are starting and why, and proceed.

Never start `compile.py` before this report has been produced.

## The Loop

Enter the loop only after the Phase 0 gate (Step C) has been presented. Maintain a
`Ledger` at `<work-dir>/optimization_ledger.json` (see `scripts/ledger.py`). For
each iteration `i` (starting at 0):

### Phase 1 -- Analyze & propose (Skill 1)

- Build the legal flag catalog with `scripts/flag_catalog.py`
  (`load_catalog()`): the vaiml_config tuning fields plus every
  `fe_args` flag with `public: true`, read from the shipped
  `fe-args.html` public catalog. The catalog loader
  exposes each flag's `default`, so you only ever propose NON-default values.
  Then narrow it with `proposable(catalog, objectives=..., autonomous=...)`
  per the Flag Relevance Policy -- propose only from that set.
  **Every iteration config MUST set `keep_outputs: true`** unless the user
  explicitly opted out — offload-gap and spilling analysis depend on it.
- On iteration 0, seed the candidate set from the Phase 0 pre-compilation
  analysis (`model_ops.match_flags_to_ops`) so the op types present in the model
  drive the first proposal. Analyze the model's topology and reuse the
  `vai-fe-args` skill for mixed-precision/island reasoning.
- Choose a candidate set of non-default flags not already in the ledger
  (`Ledger.has_tried`). Write them into `<work-dir>/iteration_<i>/vitisai_config.json`.
- **Respect existing flags.** When `--vitisai-config` was supplied, iteration 0
  starts from that file — not from catalog defaults alone. Echo inherited
  non-default flags in the Phase 0 report; each later iteration must retain every
  flag the user (or a prior iteration) already set unless the user explicitly
  asks to remove it.
- **Exploration guardrail.** Do not change many flags at once. Propose at most
  **3 added/changed flags** relative to the current best config
  (`ledger.flag_delta_count(best_flags, candidate) <= 3`, starting from the
  user's `--vitisai-config` on iteration 0 when provided, otherwise catalog
  defaults). Widen beyond that budget only with explicit user
  consent (human-in-the-loop) or an explicit autonomous budget -- this keeps the
  search interpretable and avoids exploring the full combinatorial flag space.
- **Flags are typed, not all boolean.** Each `FlagSpec.type` is one of `bool`,
  `int`, `int64_t`, `list[str]`, or `str`. For a non-`bool` flag, propose a
  concrete valid *value* (not just on/off) and cite the allowed range / values
  from the flag's source doc (`FlagSpec.path`); never propose a bare toggle for a
  numeric or list/string flag.
- For EACH proposed flag, record a `FlagChange` (`scripts/ledger.py`): its cited
  source path (`FlagSpec.path`), the default it deviates from, the value you
  chose, and a one-line rationale drawn from the flag's `description` and the
  graph evidence (see Transparency). Attach the list to the iteration's
  `IterationRecord.proposal` -- this is what makes the iteration reconstructible
  later. A flag with no citation + reason must not be proposed.
- If human-in-the-loop: present the candidate set as a table (flag, source path,
  default -> chosen, rationale) and wait for approval; if autonomous: proceed,
  but still write the table to the iteration dir.

#### Goal-driven proposal strategy

Pick candidate flags based on the selected objective(s). Only propose flags that
are in the catalog (`public: true`); if the ideal flag for a goal is NOT in the
catalog, surface it to the user with its path and say it is outside the
proposable set rather than silently using it (Transparency).

- **100% NPU offloading (`offload`) -- close the gap, do not tune generally.**
  Maximizing offload is a *coverage* problem, not a performance problem: the
  only thing that matters is which operators the compiler failed to place on the
  NPU. Generic performance reasoning belongs to the `latency` goal, where board
  measurements can actually confirm it. So from iteration 1 onward, **start from
  the offload gap, not from the op histogram**:

  1. Locate the compiled graph with
     `offload_gap.find_chained_kernel_mlir(<compile dir>)` -- the
     `*.chained_kernel.tosa.mlir`, written when the compile ran with
     `keep_outputs: true`.
  2. Run `offload_gap.offload_gap(<mlir>)`. It returns, ranked by node count,
     every op type still on the CPU with the compiler's own `CpuBecause`
     `Message` for each (attribute contract:
     the compiler records the responsible operation in `Op` and its explanation
     in `Message`). This IS the list of what is missing;
     cite the MLIR path.
  3. For each gap entry, read `routes` from `offload_gap.classify_message` and
     follow the returned `action` guidance — cite the MLIR path and the compiler
     message verbatim. If the route is `unclassified`, report the message as-is
     and do not invent a cause or fix.
  4. `offload_gap.match_gap_to_flags(gap, proposable(catalog, ...))` turns the
     gap into candidate flags for the residual ops only, ranked so flags naming
     a missing op come before generic element-wise matches. Treat this as a
     **shortlist, not a recommendation**: it is keyword matching over flag
     descriptions, so read each candidate's `description` and discard any whose
     mechanism does not actually apply to the cited op. Say which you discarded
     and why.

  **When the offload target is still not met and CPU ops remain.** After at
  least one complete compile with gap evidence, check whether residual CPU work
  is beyond what the public flag catalog can fix — for example:
  - `offload_gap.offload_gap(<mlir>)` still lists ops with no applicable
    proposable flag, or iterations stall with no offload improvement;
  - `model_ops.detect_custom_ops(<model>)` finds non-standard-domain nodes or
    Quark quant custom ops without a working `custom_ops` entry;
  - a gap route points at custom-op support (e.g. frontend `[FE]` messages that
    no catalog flag addresses).

  **Do not start custom-op work without explicit user consent.** Before you
  offer a custom-op handoff, **print this warning to the user**:

  > **Warning:** Calling this sub-skill for modification of the input model or creating a custom-op is an experimental feature of the flag-configuration skill.

  The only entry point for the custom-op skill is through the
  `vai-custom-op` — do not invoke custom-op leaf skills directly. Then offer the
  orchestrator with AskUserQuestion, citing the remaining gap entries (op type,
  node count, compiler `Message` from the MLIR). Warn clearly that:
  - accepting opens a **separate agentic session** via the **`vai-custom-op`**
    skill (`vai-custom-op`, shipped alongside this skill in the ai_utils
    bundle);
  - custom-op development is a **complex, multi-phase workflow** (subgraph
    extraction, kernel implementation, compile/simulation) and **may take
    substantial time** — it is not another flag iteration;
  - the vai-flag-configuration loop pauses until the custom op agentic session returns
    an integrated model/config or the user declines.

  If the user consents, hand off the model, the best `vitisai_config.json` so
  far, and the cited gap list to `vai-custom-op`, then resume flag tuning on
  the returned artifact. If declined, continue reporting the gap and best
  achievable offload without implying a custom op is in progress.

  If no `*.chained_kernel.tosa.mlir` exists yet (before the first compile, or
  `keep_outputs` was off), fall back to the Phase-0 op census and custom-op
  detection, and label that as pre-compile evidence rather than gap evidence.

  Beyond the gap, the one broad lever is **`threshold_gops_percent`**
  (`vaiml_config`; default 20; range 0-100): ops above the GOPS threshold run on
  the NPU, below on the CPU, so **lowering it pushes more ops onto the NPU**.
  Cite the doc anchor `#threshold-gops-percent` (value + default) as the reason.
  To then enforce/verify the goal, add `full-offloading-required=true`
  (cite its catalog entry via `FlagSpec.path`) -- per
  its description it makes compilation fail in `get_capability` if the model is
  not fully offloaded, so it is the gate that proves 100% offload (it does not by
  itself increase offload).
  **Do not spend a separate iteration toggling `full-offloading-required` alone**
  -- it changes only the compile exit code, not the graph, so on its own it never
  improves offload and just wastes an iteration. Apply it alongside
  offload-increasing flags, or once as a final verification; exclude it from the
  exploration delta budget.
- **Reduce inference time (`latency`).** Run `ai_analyzer_timing.py` first for
  E2E / NPU / CPU split, then `ai_extract.py --compact` of the
  `vai-perf-analysis` skill
  for SDK `operator_metrics`. Read `cpu_host_rows` and the host_profiling rows
  from the extract before mapping an op type — if a host phase dominates, check
  *Host-side quantization* first. Then map the dominant **NPU** op type to a flag:
  - **Conv-dominated** -> propose converting convs to GEMM:
    `enable-conv-to-im2col-and-gemm=1`
    (cite its catalog entry via `FlagSpec.path`)
    and/or `prefer-conv-to-gemm-conversion=true`
    (likewise cited via `FlagSpec.path`),
    optionally tuning `conv-to-im2col-and-gemm-max-kernel-taps`.
    When the op census shows Conv or pool ops (MaxPool, AveragePool,
    GlobalAveragePool), also consider **`preferred_data_storage=vectorized`**
    (cite `#preferred-data-storage`) — vectorized layout suits conv and stencil
    reductors; leave at `auto` or try `unvectorized` only for GEMM/MatMul-heavy
    graphs.
  - **When `tp_size` tuning is exhausted:** add **`optimize_level=3`** (O3), which
    the doc says applies more aggressive latency optimizations *stacked on top of*
    any `tp_size`. Cite `#optimize-level` (EA option; use after standard paths).
  - Always cite the bottleneck source (`sdk_timing.json` + `ai_extract` host/NPU
    rows with numbers) as the reason for the mapping.
- **Compile successfully (`compile`).** Prefer conservative, broadly-safe
  `public: true` flags; on a failure, use the Phase 3 error to constrain the next
  proposal.

#### Host-side quantization (check before mapping an op type)

The NPU op-type breakdown ranks only the work the NPU did, so it cannot show a
`QuantizeLinear` that the compiler left on the CPU. A model can therefore look
conv-dominated while the host spends longer quantizing the input than the whole
NPU partition takes, and every flag in the list above would be aimed at the
wrong thing.

**Detect it from two independent signals**, never from GOP share or raw timer files:

- **SDK host timeline** — `ai_analyzer_timing.py` `cpu_host_rows`, or
  `ai_extract.py` `operator_metrics` with `category: host_profiling`, especially
  `Input Quantization and Transformation` as a large share of inference;
- the offload gap — `offload_gap.offload_gap(<mlir>)`, or the compiler's own
  `<cache>/cache/graph_partition_trace.csv`, still lists a `QuantizeLinear` on
  an input edge. Its GOP share is negligible, which is exactly why this gets
  overlooked.

**The lever is `match-power-of-two-quant-kernel=1`.** Per its catalog entry it
matches the QuantizeLinear kernel for a BF16/F16 -> int8 quantize, which lets
the compiler place that quantize on the NPU while keeping the same float input
contract.

**It is in the public catalog** (`public: true`), but only when the
input quantize meets the preconditions below is it worth a compile. Prefer it
when the host-quantization signals fire; cite
`fe-args.html#fe-flag-match-power-of-two-quant-kernel` and verify the scale/zero_point
before proposing it.

**Check its two preconditions before spending a compile.** The kernel matches
only a quantize whose **scale is a power of two** and whose **zero_point is
zero**. Both are readable off the `QuantizeLinear` initializers in the ONNX
graph, so verify them there first -- Quark symmetric-int8 models typically
satisfy both. If either fails the flag cannot fire, and saying so is more useful
than spending an iteration to discover it.

**Prove the arm actually did something.** An arm whose pattern matched nothing
is indistinguishable from the baseline, so confirm all three before crediting
the flag:

1. the compile resolved it -- the log shows `match-power-of-two-quant-kernel`
   as true, with no unknown-option warning naming it;
2. the quantize moved -- the partition trace places it in a partition instead of
   leaving it unsupported, and the offloaded-operator count rises;
3. the numerics did not move. The flag relocates the quantize rather than
   approximating it, so a changed output is a bug to report, not a trade-off to
   accept.

### Phase 2 -- Compile (Skill 2)

Run compilation in a SUB-AGENT. Use `compile_probe.py` so failures land in
`compile_probe.json` with a usable `failure_report`:

```
Use the Agent tool (subagent_type="general-purpose") with a prompt like:
"Run this and report compile_probe.json status + failure_report.user_message:
bash -c '<ENV_SETUP> cd <work-dir>/iteration_<i> && \
  python3 <skill_dir>/scripts/compile_probe.py <model> \
  --vitisai-config vitisai_config.json \
  --mode capability|compile \
  --cache-dir <model_stem> -o compile_probe.json'"
```

Use **`--mode capability`** for exploration iterations unless the user chose
complete-only or you need spilling/board artifacts. Switch to **`--mode compile`**
before the final validation iteration.

Capture: `compile_probe.json` (`status`, `failure_report`), and the cache directory.

On failure, quote `failure_report.user_message` and `actionable` to the user before
proposing the next flags — cite `evidence` lines, not undigested log walls.

### Phase 3 -- Validate & debug (Skill 3)

- **First, check whether the compile succeeded.** On compile failure, diagnose
  the error and derive a constraint for Phase 1 (e.g. avoid/adjust the offending
  flag). Do not blindly retry, and do not run timing extraction on a failed compile.
- On success, run `ai_extract.py --compact` of the `vai-perf-analysis` skill on the run
  dir for NPU offload % and SDK-backed `operator_metrics` (never read on-disk perf
  JSON without going through this script).
- If `--board`, run `scripts/ai_analyzer_timing.py` on the board work directory
  established in pre-flight (same tree as the compile cache + `record_timer_*.json`).
  Store `sdk_timing.json` beside the iteration. **Do not** use `run.py` wall-clock
  median or hand-parsed VAIP JSON as board E2E — the script applies the SDK + VAIP
  rules from above.
- Append an `IterationRecord` to the ledger -- carrying the `proposal` built in
  Phase 1 -- and `Ledger.save()`. The ledger scores `latency` on
  `IterationRecord.board_inference_usec` (microseconds). Copy it straight from
  the `board_e2e_usec` field of `sdk_timing.json`, which the script already
  converted: `board_inference_usec = sdk_timing["board_e2e_usec"]`. Do not
  convert by hand and do not store the millisecond `board_e2e_ms` value in the
  microsecond field.

- Evaluate termination NOW, so its metrics are stored with the iteration that
  produced them:
  `record.stop_check = Ledger.evaluate_stop(objectives, criteria).as_dict()`,
  where `criteria` is the `StopCriteria` agreed in the Startup Interview
  (`target_offload_pct`, `max_no_improvement`, `max_iterations`, and
  `untried_candidates` = how many proposable, untried flags are left).
- Write the iteration's **result next to its config** with
  `ledger.write_iteration_result("<work-dir>/iteration_<i>", record, objectives)`.
  The resulting `result.json` holds the full picture of the iteration in one
  place -- the change **proposal** (each `FlagChange` with its source path,
  default -> value and rationale), the **config** that was compiled, the
  **results** (offload %, npu/board time, verdict) and the **`stop_check`**
  metrics the termination decision was made on (`improvement`,
  `no_improvement_streak`, `target_gap`, `target_reached`, ...). This keeps each
  iteration dir self-describing, so a run can be re-evaluated later without
  replaying the agent log.

**L3 spilling check (Skill 3 extension).** Spill state is only visible from
compile artifacts, so run this whenever a `DetailedSpillingAnalysis.csv` is
available -- either from `--spill-workdir`, or from this iteration's compile
(which must run with `keep_outputs: true` and `enable_cache_file_io_in_mem: "0"`
so the report lands on disk):

- Locate the report with `spill_check.find_spilling_csv(<dir>)`.
- `spill_check.load_spilling_layers(csv)` lists layers with `Spill or not == Yes`
  (column D); `spill_check.fm_driven_spills(csv)` keeps the FM-driven ones -- those
  whose `Layer Spill Reason` (column W) is an FM-size L2 overflow (marker
  `l2FM Size Overflow`). This uses the backend report directly instead of
  reimplementing the L2-layout math, so no device/L2-capacity heuristic is needed.
- If FM-driven spilling layers exist, report them with their `Layer Spill Reason`
  and note that spatial tiling of the OFM may reduce L3 spill — cite the CSV path.
- Layers that spill for any other reason (weights-driven, successor-forced,
  concat, ...) are reported separately.
- **No `DetailedSpillingAnalysis.csv` available** (e.g. pre-compile, or the
  report was not emitted): fall back to the pre-compile proxy
  `model_ops.estimate_fm_spill_candidates(<model>)`, which flags output feature
  maps whose estimated footprint exceeds L2 (512 KiB/column) from ONNX shape
  inference alone. **Label any resulting recommendation clearly as a heuristic
  estimate, not a fact from the spilling report** — it ignores tiling/layout and
  can over- or under-report.
- A `DetailedSpillingAnalysis.csv` that lacks the `Layer Spill Reason` column
  still cannot be classified: state that FM-driven spill state is unknown.

### Decide

Compare against `Ledger.best(objectives)`. The stop metrics are applied by
`Ledger.evaluate_stop(objectives, criteria)` (already called in Phase 3): stop
when `should_stop` is true, and state which `reasons` fired
(`target-reached`, `no-improvement-limit`, `max-iterations`,
`no-untried-flags`, `all-failed`) together with the metrics behind them. Do not
re-derive "did this improve?" by eye -- quote the recorded numbers, so the
decision and the evidence for it cannot drift apart. In human-in-the-loop mode,
ask whether to continue. A ledger of tried flag combinations prevents repeats.

**Offload target not reached (CPU ops remain).** When `offload` is an objective
and `Ledger.evaluate_stop` shows the target is not met (or `target_gap` > 0)
while `offload_gap.offload_gap(<mlir>)` still lists CPU-resident ops, summarize
the residual gap before proposing more flag iterations. If the remaining ops are
not addressable by untried proposable flags, **offer the custom-op path** (see
the offload goal section): print the experimental-feature warning first, then
ask for consent, warn that `vai-custom-op` opens another agentic session and
may take substantial time, and only proceed on an explicit yes. Do not spawn
the custom ops worker autonomously.

**Bail-out (baseline not compileable).** If the baseline config and every
goal-relevant flag combination fail to compile -- `Ledger.all_failed()` once the
op/goal-relevant candidates are exhausted, an unsupported operator whose
unwrapping is broken, or every combination forces full CPU offload -- STOP and
report that **compilation fails under all relevant flag combinations**. Do not
churn through irrelevant combinations. For unsupported operators, report the
op name and compiler reason from the partition output or
`model_ops.detect_custom_ops`, and stop — do not prune the ONNX graph to force
a compile.
**Never reduce or prune the original ONNX to a compileable sub-network to force a
pass** -- that changes what is being compiled and hides the real gap.

## Final Report

When the loop stops (including on bail-out):
1. Print `Ledger.summary_table(objectives)` (its `delta` column is the
   per-iteration gain) and write the machine-readable run summary with
   `Ledger.write_summary("<work-dir>/optimization_summary.json", objectives)`.
   This is produced **even when no configuration compiled** (`status:
   compile-failed`, `best: null`), so a failed run still yields structured output
   of every attempt and its failure verdict for later re-evaluation or debugging.
2. If a config compiled, copy the best iteration's `vitisai_config.json` to
   `<work-dir>/best_vitisai_config.json` and report its flags + metrics, each
   flag cited to its source path with the rationale it was chosen (Transparency).
   On a `compile-failed` run there is no best config: state the bail-out verdict
   (compilation fails under all relevant flag combinations) and cite the failing
   iterations' verdicts from the summary instead.
3. **Evaluate whether each selected goal was achieved.** For every objective the
   user chose at startup, state ACHIEVED / PARTIALLY / NOT ACHIEVED with the
   concrete cited metric from the best iteration:
   - `offload` -> the NPU offload % (and, if `full-offloading-required=true` was
     used, whether the compile passed = 100% offload proven, or failed = not
     fully offloadable). Cite the perf/partition source. When NOT ACHIEVED or
     PARTIALLY and CPU ops remain, cite `offload_gap.offload_gap(<mlir>)` and
     state whether the user was offered (or accepted) a **`vai-custom-op`**
     handoff for the residual gap.
   - `latency` -> best `board_e2e_ms` from `sdk_timing.json` vs iteration-0
     baseline (absolute and %), plus dominant host/NPU row from the SDK extract.
     Cite `sdk_timing.json` and the `ai_extract` output path.
   - `compile` -> whether a compiling config was found. Cite the compile log.
4. State why the loop stopped, citing the concrete stop metric and the ledger
   evidence. Both are recorded: the summary's `final_stop_check` holds the
   `reasons` and the metrics they fired on, and `summary_table()`'s `delta`
   column shows which iterations improved or regressed and by how much. Quote
   those rather than recomputing them. If quantization was triggered during the
   run, cite the agent path and the source signal. If a custom-op handoff was
   triggered, cite the `vai-custom-op` skill and whether it completed.
5. If the user asks why a particular flag was never tried, answer from
   `excluded_flags(catalog)` -- its `relevance` and cited `exclusion_reason` --
   or from the graph evidence it lacked. "It was not in the candidate set" alone
   is not an answer.

## Non-goals

No functional/numeric correctness checking, no new compiler, no new perf
extractor, no environment variables, and nothing the Flag Relevance Policy marks
`excluded`. Because this skill does not check numerics, the accuracy-trading
flags are `needs-consent` rather than `proposable` -- never enable one on the
user's behalf in autonomous mode. Never reduce or prune the original ONNX to a
compileable sub-network to force a pass -- if nothing compiles, bail out and
report (see Decide -> Bail-out).

Do not infer a cause the compiler did not state. `offload_gap` routes only the
`CpuBecause` messages that can be cited to the code emitting them; anything else
is `unclassified` and must be reported verbatim.
