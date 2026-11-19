<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Trying more on a run that exists

Read this when the user wants to try something on a checkpoint that already has a run.

When the user wants to try something on a checkpoint that already has a run —
a pblock, a fanout limit, an early-fix switch, another placer, one more planner
round — `extend` that run. Do **not** `start` a new flow for it: a new flow
repeats the probe, all of Stage 1, detection and Stage 2 (about 25 jobs) to
test one idea, while `extend` adds one run per idea (per placer) to the run you
have, from its overall best, and its report shows them against everything
before. Start a new flow only for a different checkpoint.

`extend` with `output_dir` and one of:
- `ideas`: a JSON file listing what to try, each
  `{"name", "xdc": "<file>" | "xdc_lines": [...], "placers": [...], "early_fixes": {...}}`.
  XDC is physical only (pblocks, `MAX_FANOUT`, `LOC`, ...), checked like a
  planner fix. `placers` are Stage 1 flows to try the idea on (one run each);
  `early_fixes` switches the early-fix bundle (for example
  `{"structural_analysis": true}`). An idea may be only an early-fix switch.
- `plan`: a full planner plan, if you wrote one;
- neither: one more planner round (an evidence run, then a `planner` decision
  you answer as in §4).

`from_run` starts from another run than the best (for example the second-best
Stage 1 flow); `max_runs` raises the run's guardrail, only when the user asks.
`extend` refuses while the supervisor is running (wait for it to finish or
reach a stage boundary). Then wait and report exactly as for `start`.
