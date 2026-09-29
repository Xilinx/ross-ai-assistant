<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# vai-flag-configuration
:external_skills/flag_configuration/skills/flag-configuration/README.md

Orchestration skill that iterates propose-flags -> compile -> validate to improve
a model's VAIML configuration. It is its own entry point -- start it by name;
see `SKILL.md` for the procedure.

## Layout
- `SKILL.md` -- orchestrator instructions.
- `scripts/flag_catalog.py` -- enumerates legal non-default flags
  (vitisai_config fields + ``fe_args`` frontend flags with ``public: true``) and applies the
  relevance policy: flags that cannot change the compiled result, need an
  artifact the skill cannot synthesize, are deprecated, trade accuracy,
  or fight the selected objective are tagged (never dropped) with a cited
  reason. Use `proposable(...)` for candidates and `excluded_flags(...)` to
  explain a rule-out.
- `scripts/ledger.py` -- records iterations (change proposal + config + results
  + the termination metrics from `evaluate_stop`), prevents repeats, picks the
  best, and writes a self-describing `result.json` per iteration.
- `scripts/offload_gap.py` -- reads the `CpuBecause` subgraphs from a compile's
  `*.chained_kernel.tosa.mlir` to report which ops are still on the CPU and what
  the compiler said about each, routing only on citable markers. Turns "maximize
  offload" into "close this specific gap".
- `scripts/spill_check.py` -- flags FM-driven L3 spills (FM footprint > device L2,
  per column: STX/T50/T20 = 512 KB) from `DetailedSpillingAnalysis.csv`.
- `scripts/model_ops.py` -- censuses ONNX operator types and matches them to
  catalog flags (op-type-aware seed proposal); also inspects shapes/sizes
  pre-compile (`input_shapes`, `output_shapes` via shape inference,
  `large_layers`) for latency-bottleneck reasoning.
- `scripts/ai_analyzer_timing.py` -- board E2E / NPU / CPU split via the AI
  Analyzer SDK (`get_timing_dataframe_batch`); never parse raw `record_timer_*.json`.
- `scripts/normalize_timer_json.py` -- repairs schema-2.0 list timer files so the SDK reads them.

## Reused building blocks
Components are named, not linked: the harness resolves a skill or agent by its
name. Scripts of a sibling skill are addressed relative to this skill's
directory, where the skills are siblings:
- Optional quantization: `vai-quantization-guide` skill. **The only entry point
  to the mixed-precision skill is through `vai-quantization-guide`** — do not
  invoke leaf mixed-precision skills directly. **Experimental:** print a warning
  before offering it.
- Optional custom-op development when CPU-resident ops block offload after flag
  exploration: `vai-custom-op` skill — **the only entry point for the custom-op
  skill is through `vai-custom-op`**; **user consent required**; **experimental**
  (print the same warning before offering); opens a separate session and may
  take substantial time.
- Flag analysis: `vai-fe-args` skill.
- Compilation: `compile.py` in the scripts folder of the
  `vai-custom-op-implementation` skill.
- Validation: `vai-perf-analysis` skill for offload % and SDK-backed operator
  metrics; `scripts/ai_analyzer_timing.py` for board timing.

## Transparency
Every proposal/decision cites source material the user can actually open --
`FlagSpec.path` (the shipped `fe-args.html` public catalog anchor), the published
Vitis AI EP-configuration doc, `DetailedSpillingAnalysis.csv`, the
`*.chained_kernel.tosa.mlir`, or SDK timing JSON from `ai_analyzer_timing.py` /
`ai_extract.py` -- and explains the rationale for the choice.

## Setup (clean virtual environment)

Running the skill needs only `skill_requirements.txt` (`onnx`). Working on it in
a compiler source tree, and running the unit tests, additionally needs
`requirements-dev.txt` (`pyyaml`, `pytest`) — install that one below, since it
pulls in `skill_requirements.txt` too. A dedicated `.venv` is the cleanest,
isolated way, and it avoids PEP 668 "externally-managed-environment" errors on
system Python:

```bash
cd <this skill's directory>   # the one containing SKILL.md
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

When finished: `deactivate`.

> Running the actual **compile** and **profiling** steps (Phase 2/3) requires the
> **Vitis AI product environment** — the official Docker image or an equivalent
> install where `flexml`, `onnxruntime` with the `VitisAIExecutionProvider`, and
> `dlanalyzer` are already importable. Supply them via `--t <activate>` when using
> a product virtualenv, or run inside the Vitis AI container with no `--t`.
> The Vitis AI Docker image provides `onnx` but **does not ship pytest**; it is
> not where you run this skill's unit tests.

## Running the tests (compiler source tree only)

The **delivered skill bundle does not ship pytest or the test suite** — only
`skill_requirements.txt` (`onnx`) is installed with the skill.
`requirements-dev.txt` (`pytest`, `pyyaml`) and the tests under
`python/test/flag_configuration/` live in the **vitis_flexml source tree** for
skill development and CI, not in the ai_utils install.

From a vitis_flexml checkout, create the dev venv above, then from the
**repository root**:

```bash
python -m pytest --noconftest -o addopts="" \
  python/test/flag_configuration/ -v
```

The repository root's `pytest`/`conftest.py` config pulls in unrelated plugins
(e.g. torch, xdist), so `--noconftest -o addopts=""` keeps these tests
standalone. Do not expect this command to work inside the Vitis AI Docker image
unless you pip-install `requirements-dev.txt` there yourself (normally unnecessary).
