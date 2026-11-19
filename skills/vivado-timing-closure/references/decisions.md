<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Answering triage and planner calls

Read this when a `triage` or `planner` decision is waiting (SKILL.md, section 4).
The gates (`postplace`, `stage2`, `stage3`) need only SKILL.md.

## A triage call

1. Read the failed run's `run.failed`, `vivado_console.log` (or the session
   logs beside it), `lsf.log` and any `hs_err*` file.
2. Quote the line that explains the failure.
3. Answer `{"answer": <retry?>, "why": "<that line>"}`. Infrastructure failures
   — killed, out of memory, licence, crash, host — are worth one retry; a
   design or Tcl error is not.
4. Never restart, kill or edit anything yourself.

## The planner call

This is the one that earns its place. Fixed recipes re-sample the same kind of
placement; a planner round finds the *cause*.

There can be several planner calls in one run:

- **`planner.early`:** an early round, opened while Stage 2 is still running
  when Stage 1 is far off. It plans from the Stage 1 winner and its fixes join
  Stage 2. Aim at what the fixed menu cannot reach: placement structure, clock
  roots, SLR assignment, fanout. Its default applies after 1 h.
- **`planner.r1`, `planner.r2`:** rounds after Stage 2. Another round follows
  only when the previous one improved the best result.

`tc planner-context --output-dir <dir>` (add `--early` for the early round)
lists what a plan may use. Its `winner_xdc_chain` is the set of fixes the
winner already carries; flow, over-constraint and RQS fixes replay them
automatically, so build on them and never copy them into `xdc_lines`. Check a
plan with `tc planner-validate --output-dir <dir> --plan <plan.json>` (again
`--early` for the early round) before answering.

A fix whose run would repeat one already done, or one generated, queued or
running anywhere in the flow (`already_tried`, `in_flight` in planner-context),
is not run: Vivado is deterministic, so it would only reproduce that result.
planner-validate lists such fixes under `duplicates`; propose something else.

1. **Read the evidence.** `decision` points at an `evidence.json` from the
   winner's routed checkpoint: per failing path — slack, logic levels, logic vs
   net delay, skew, source and destination cells with LOC, SLR and clock
   region; `failing_clusters`, which counts failing endpoints by hierarchy
   prefix and is where a wide slack wall shows up, invisible in the worst 50
   paths; plus hold violators, congestion, clocks, utilization.

2. **Ask the design what the reports do not say.** This is what most often
   finds the real cause:

   ```sh
   tc query --run-dir <winner> --dcp <winner>/postroute.dcp \
            --tcl 'get_property SLR [get_cells -hier -filter {REF_NAME =~ GT*}]' \
            --tcl 'foreach c [get_clocks] { puts "$c [get_property PERIOD $c]" }'
   ```

   Writes, implementation commands, constraint changes and Tcl escapes are
   refused before Vivado sees them. `--check-only` validates without running.
   It runs on the farm like the flow's runs: as a Vivado LSF session when the
   site uses the session backend, so it is listed with them; never on this
   machine unless the site allows it. Each answer records the host and LSF job
   in `queries.jsonl`, also under the open planner round's folder.

3. **Ground the fix (optional).** `tc docs search '<topic>'` for the UltraFast
   guidance (UG949, UG906, UG1387). It returns 5 passages; `--limit 20` (up to
   50) for more, `--family 'Versal Premium'` to narrow them. Or use a
   documentation search tool if you have one — the same service. The query
   leaves this machine: name the concept ("USER_CLOCK_ROOT Versal clock
   region"), never a design name, path or netlist content. If the service
   fails, go on without it. Check the property or option actually applies to
   this device family — several plausible ones do not.

4. **Write `plan.json`**: a diagnosis plus up to four ranked fixes, each with a
   justification and the evidence IDs it rests on. Validate it, then answer
   with its absolute path. The engine validates again before accepting; a
   rejection comes back with its reason and you may revise once.

### What a fix may be

| Kind | What it changes | When it is right |
|---|---|---|
| `flow` | Re-place and re-route with new opt/place/phys_opt/route directives, placer sub-directives, and/or physical XDC | Wrong SLR, bad clock root, logic straddling clock-region rows |
| `over_constrain` | Extra setup uncertainty on the failing clock during placement, reset before routing | A near miss where the placer should push harder |
| `rqs` | A chosen subset of the tool's own QoR suggestion categories | The tool has relevant suggestions |
| `incremental` | From the winner's **routed** checkpoint: `group_path`, `phys_opt_design -path_groups`, directive passes, optional re-route | A narrow wall of endpoints just short of closure (1–3 h, no re-place) |

**Physical XDC only.** `create_pblock`, `resize_pblock`, `add_cells_to_pblock`,
`remove_cells_from_pblock`, `set_property`, `group_path`; properties such as
`USER_SLR_ASSIGNMENT`, `USER_CLOCK_ROOT`, `LOC`, `BEL`, `MAX_FANOUT`,
`CLOCK_DELAY_GROUP`, `IS_SOFT`, `CONTAIN_ROUTING`, `PHYS_OPT_SKIP`.

**Never**: timing exceptions (`set_false_path`, `set_multicycle_path`,
`set_max_delay`, `set_min_delay`), clock definitions or uncertainty in XDC, or
any Tcl escape. A plan must never "close" timing by relaxing what is measured;
these are rejected, so proposing them only costs a round.

Two rules learned the hard way:

- **No `-hierarchical` in a `group_path` for a `flow` fix.** Groups of
  155k–187k cells raised a tool error and wrecked a placement (-0.38 → -1.08
  post-place). List endpoint pins instead.
- **Check your cell-name patterns match something.** An `add_cells_to_pblock`
  with an empty cell list is an error that aborts the whole run. Query the
  pattern first; the engine re-counts every query after applying and reports
  the empty ones back to you.

### What good looks like

On one design the evidence showed every failure on one 390 MHz clock, worst
paths with 0–1 logic levels and 88–95% net delay, all crossing an SLR, and
failing endpoints clustered in two converter modules. Queries then showed the
transceiver quads sat in adjacent SLRs, and the RTL's own 195 MHz crossing
pipeline had +2.2 ns of slack while the 390 MHz FIFOs sat on the far side. The
fix was a pblock keeping each FIFO on its transceiver's SLR: -0.875 ns to
closed.

No directive sweep would have found that. Every lever a fixed menu has works
*within* a placement that has the FIFOs on the wrong side.
