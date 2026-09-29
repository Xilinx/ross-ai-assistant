---
name: vai-custom-op
license: Apache-2.0 WITH LLVM-exception
compatibility: Requires VitisAI 6.3+
metadata:
  version: 6.3.0
  stage: beta
description: Orchestrate multi-agent parallel development of multiple custom AIE operators
  for VAIML. Handles workspace partitioning, spawning worker agents, relaying insights
  between workers, gating integration on verified results, and serializing the final
  model stitching. Use when developing 2+ custom ops in parallel, when coordinating
  multi-agent custom op work, or when integrating multiple validated custom ops into
  a single model.
---
<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->

# Custom Op Workflow Orchestrator

## Description

Coordinates parallel development of multiple custom AIE operators by spawning
worker agents, managing hermetic workspaces, and serializing the final
integration into one model. Each worker uses the `vai-custom-op` skill for
Phases 1-5; this orchestrator owns Phase 6 (stitching) and Phase 7
(end-to-end verification).

## Parse Arguments

This skill expects arguments in this form:

```
[--t <activate_path>] --model <onnx_model> --vitisai-config <config_json> --ops <op1_nodes:op1_name> [<op2_nodes:op2_name> ...]
```

Extract from `$ARGUMENTS`:
- `--t <path>`: **(OPTIONAL)** Path to a Ryzen AI / Vitis AI / product virtual environment activate script. Provide this only when the Python environment is NOT already on the shell. Omit it when the environment is pre-provisioned (e.g. inside the official Vitis AI Docker container, where `flexml`, `onnx`, `onnxruntime`, etc. are already importable from system Python). If `--t` is provided, forward it verbatim to every spawned worker; if it is absent, do NOT invent a path -- spawn workers without `--t`.
- `--model <path>`: **(MANDATORY)** Path to the full ONNX model
- `--vitisai-config <path>`: **(MANDATORY)** Path to vitisai_config.json
- `--ops <nodes:name> [...]`: **(MANDATORY)** One or more op specs, each in the
  form `node1,node2:op_name` (comma-separated nodes, colon, custom op name)

**Validation**: Verify all mandatory arguments are present before proceeding. `--t` is intentionally optional -- treat its absence as a deliberate signal to use the system-provisioned environment, not as missing input. Do NOT search the filesystem for an activate script to plug in.

## Role Split

| Role | Owns | Tools |
|------|------|-------|
| **Orchestrator** (you, the team lead) | Partition work, prepare hermetic workspaces, spawn workers, relay peer insights, gate integration, run Phases 6 and 7 serially. | `Task`/`Agent` tool to spawn workers; direct edits for integration. |
| **Worker** (one per custom op) | Phases 1-5 inside its own hermetic workspace. Must prove BOTH successful compilation AND numeric accuracy before reporting back. | The `vai-custom-op-implementation` skill, scoped to a single `--nodes` set. |

## Workspace Layout

Create one hermetic sub-directory per custom op plus a reserved directory for
integration:

```
<parent_dir>/
  <op_name_1>/          # worker 1 workspace (hermetic)
    <full_model>.onnx   # copy (or symlink) of the original model
    vitisai_config.json  # copy of the original config
    ...                  # worker creates model_a.onnx, model_b.onnx,
                         # custom_op_<name>/, reference_data/, etc.
  <op_name_2>/          # worker 2 workspace (hermetic)
    ...
  integration/           # reserved for orchestrator Phase 6-7
```

**Hermeticity rule:** each worker reads and writes **only** inside its own
`<parent_dir>/<op_name_N>/` directory. No worker touches another worker's
directory or the `integration/` directory.

## Orchestrator Workflow

The orchestrator is data-driven. Decisions must be taken based on real world data
and measurements. Certain decisions are not black and white and require
a full understand and proper bottleneck analysis to figure out the scope for the
implementation of a custom op. Auxiliary tools and skills (such as `vai-perf-analysis`)
have been developed to help you in this task.


1. **Partition the node list.** Perform model analysis using compilation logs
   or `vai-perf-analysis` - if runtime data is present - to figure out the optimal
   number of custom ops and which target nodes to include in one cluster.
   Each cluster becomes one worker and a custom op.
   1. **Sanity-check the high-level decomposition.** The orchestrator's
      job is to decide *how many* custom ops are worth building and
      *which nodes* land in each cluster. Custom ops have hard buffer
      limits (at most 2 inputs, 1 output -- see the `vai-custom-op-implementation` skill,
      "Design Constraints"), but the worker can pack extras into IFM/WTS
      via tiling and DMA, so a raw input/output count is **not** a strict
      partitioning constraint. Use this step to catch decomposition-level
      issues only:
      - The cluster is so large that it should clearly be **split into
        multiple custom ops** (e.g., distinct compute regions with little
        data sharing, or a long chain that exceeds reasonable kernel
        complexity).
      - The cluster is so trivial that it does **not warrant a custom op**
        at all (e.g., a single elementwise node already handled well by
        the default backend) and should be dropped.
      - Topology issues that no amount of packing can fix: e.g., a node
        in the middle of the cluster has an output consumed both inside
        and outside the cluster in a way that would require duplicating
        a large compute, or the cluster spans a control-flow boundary.

      Per-op concerns -- exact IFM/WTS packing layout, multi-output
      kernel concat + tiling split, attribute vs. `lp_params` choice --
      are deferred to the worker, which has full context on the kernel
      and tiling. Forward each surviving cluster to a worker as-is and
      let it negotiate the buffer constraints internally.
2. **Prepare hermetic workspaces.** For each cluster, create
   `<parent_dir>/<op_name>/` and copy (or symlink) the original model and
   `vitisai_config.json` into it.
3. **Spawn workers in parallel.** Use the `Agent` tool to spawn one subagent
   per custom op **simultaneously** (all Agent calls in a single message).
   Each worker's prompt MUST instruct it to invoke the `/vai-custom-op-implementation` skill
   with the appropriate arguments. Use `subagent_type="vai-custom-op-worker"`
   and `mode="bypassPermissions"`.

   **Worker prompt template** (one per custom op):
   ```
   You are a vai-custom-op-worker agent. Your ONLY task is to invoke the
   /vai-custom-op-implementation skill with the following arguments and report the result.

   Run this skill now:
   /vai-custom-op-implementation <operation_description> \
       [--t <activate_path>]    # include ONLY if the orchestrator was invoked with --t; otherwise omit this line entirely
       --model <parent_dir>/<op_name>/<full_model>.onnx \
       --vitisai-config <parent_dir>/<op_name>/vitisai_config.json \
       --nodes <nodes_for_this_op_only>

   Work ONLY inside <parent_dir>/<op_name>/. Execute Phases 1-5 of the
   /vai-custom-op-implementation skill in full -- Phase 5 (kernel optimization, including
   board-based measurement with /vai-perf-analysis when a board is
   reachable) is part of the skill's own contract and is mandatory.
   Apply techniques from BOTH /vai-aie-kernel-development AND
   /vai-aie-compiler-oriented-optimizations. Do NOT attempt Phase 6 or 7;
   those are owned by the orchestrator.

   When done, report back with: status, op_name, working_dir, yaml_path,
   compile_verdict, numeric_verdict, optimization_verdict, and any
   peer_insights.
   ```

   **IMPORTANT:** Launch ALL workers in a single message with multiple
   Agent tool calls -- do not wait for one worker to finish before spawning
   the next. The whole point of this orchestrator is parallel execution.
4. **Relay peer insights (optional).** Workers do **not** talk to each other.
   If a worker surfaces an insight likely to help peers (e.g., a tiling pattern
   for similar shapes, a debugging discovery), the orchestrator forwards it to
   the relevant peers.
5. **Gate integration on worker success.** For each worker, verify its report
   **before** integrating its op:
   - Phase 3: `"Compilation successful"` AND non-empty
     `<model_stem>/cache/vaiml_par_0/0/backend/gen_tg_adf/`
   - Phase 4b: `compare_npy.py` returns `SUCCESS`
   If either check fails, ask the worker to iterate, reassign the op, or drop
   it from the integration batch. **Never integrate an unverified custom op.**
6. **Serialize Phases 6 and 7.** For each validated custom op in turn:
   - Stitch it into the current integrated model (Phase 6b of `vai-custom-op-implementation` skill).
   - Add its `custom_ops` entry to `integration/vitisai_config.json` (Phase 6c)
     with `op_config` pointing at the worker's YAML (absolute path).
   - Recompile to confirm the incremental integration still succeeds.
   After all ops are integrated, run Phase 7 **once** on the final model.

## Worker Contract

Each worker agent MUST:

- Operate **only** inside its assigned `<parent_dir>/<op_name>/` directory.
- Execute Phases 1-5 of the `vai-custom-op-implementation` skill end-to-end, including a local
  git repo with milestone commits.
- **Not** attempt Phase 6 or Phase 7. Those belong to the orchestrator.
- Return a structured report to the orchestrator with:
  - `status`: `READY_FOR_INTEGRATION` or `FAILED`
  - `op_name` and `working_dir` (absolute path)
  - `yaml_path`: absolute path to `custom_op_<name>/<name>.yaml`
    (needed by the orchestrator for Phase 6c)
  - `compile_verdict`: success/failure plus `gen_tg_adf/` non-empty check
  - `numeric_verdict`: `compare_npy.py` max abs diff, mean abs diff, and
    SUCCESS/ERROR
  - `optimization_verdict`: which Phase 5 optimizations were applied,
    drawn from BOTH /vai-aie-kernel-development (vectorization at minimum,
    NOINLINE core pattern, loop splitting, etc.) AND
    /vai-aie-compiler-oriented-optimizations (restrict pointers, DM bank
    annotations, loop pragmas / hints, software pipelining, vector
    alignment, etc.); the Phase-4 re-validation result after each;
    board-measurement results from /vai-perf-analysis (per-op time,
    bottleneck class, before/after deltas) OR a documented reason no
    board was reachable; and the final kernel classification
    (vectorized / scalar-fallback-with-justification). A scalar kernel
    without a documented vectorization attempt, or a "board available
    but not measured" state, is a FAILED verdict.
  - `peer_insights` (optional): anything the orchestrator should forward

## Blocking Requirements

- **BLOCKING:** A custom op must pass BOTH the compilation and the numeric
  accuracy checks before being integrated. No exceptions.
- **BLOCKING:** Phase 6 (stitch + `vitisai_config.json` edits) and Phase 7
  (end-to-end verification) must be executed serially by the orchestrator.
  Parallel edits to the full model or its config will corrupt the integration.

## Systematic Debugging at Every Step

**IMPORTANT:** When any step fails -- worker compilation, x86sim, numeric comparison, or integration compile -- do not resort to trial and error. Debug systematically: read the full error output, trace backward to find the root cause, and apply a targeted fix. Check if there are other skills available that can help with debugging. When a worker reports FAILED, ask for the specific error message and root cause analysis before telling it to retry.

## Integration Phase (Orchestrator Only)

### Phase 6: Model Re-integration (Serial)

For each validated custom op, in order:

1. **Stitch** the custom op into the current integrated model using
   `onnx_stitch.py` from the `vai-custom-op-implementation` skill's scripts directory.
2. **Update** `integration/vitisai_config.json` to add the `custom_ops` entry
   with `op_config` pointing to the worker's YAML (absolute path).
3. **Recompile** the integrated model to confirm the incremental integration
   succeeds before adding the next custom op.

### Phase 7: End-to-End Verification (Once)

After all custom ops are integrated:

1. Compile the final integrated model.
2. Generate full-model CPU reference data.
3. Run x86sim on the integrated model.
4. Compare x86sim outputs against CPU reference.

This phase is **mandatory**. At minimum, the integrated model must compile
successfully.

## Final Report

After Phase 7 completes, generate a markdown report at
`<parent_dir>/report.md` summarizing the entire workflow. Use the following
structure:

```markdown
# Custom Op Development Report

## Summary
- **Model**: <original model path>
- **Custom ops developed**: <count>
- **Final status**: <all integrated / partial / failed>

## Per-Op Results

### <op_name_1>
- **Target nodes**: <node list>
- **Status**: READY_FOR_INTEGRATION | FAILED
- **Compilation**: success/failure
- **Numeric accuracy**: max abs diff, mean abs diff, SUCCESS/ERROR
- **Tiling strategy**: <Tiling_4x4 / Tiling_1x1, tile size, distribution>
- **Working directory**: <absolute path>
- **Issues encountered**: <brief description of failures and how they were resolved>

### <op_name_2>
...

## Integration
- **Integrated model**: <path to final model>
- **Integration compilation**: success/failure
- **End-to-end numeric accuracy**: max abs diff, mean abs diff (if run)

## Performance
- **Compilation time**: <per-op and total>
- **x86sim time**: <per-op>
- **Kernel configuration**: <vectorized / scalar, vector width, pipelining>

## Issues & Resolutions
| Issue | Root Cause | Resolution |
|-------|-----------|------------|
| ...   | ...       | ...        |
```

Include all attempts, not just the final successful run -- this makes the
report useful for future reference when developing similar ops.
