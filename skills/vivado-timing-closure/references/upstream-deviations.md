<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Deviations from upstream: the static flow

This is a review list. Each row is one place where this skill's static flow
differs from the upstream timing-closure framework. The owner marks each row
keep or revert.

- **Static flow:** the deterministic part of the flow, which needs no planner:
  Stage 1, the fixed Stage 2 recipes, Path B (R2), Stage 3 and the guardrails.
  Planner rounds (`stage2.mode: planner|both`) are left out. They appear only
  where a static setting changes them.
- **Upstream:** the framework at `3adebe7e` (PR #742, 2026-11-12), the commit
  last synced (see `reference/UPSTREAM_SYNC.md` in the repository). Rows 1-56
  were written against `b0cfa7a7`; PR #742 left their upstream lines in place
  except for the `chaithanya_skills` -> `apps_skills` rename and the
  `cd $run_dir` lines added after each `set run_dir`. Where the bsub engine
  (`core/agent.py`) and the MCP/LLM-mode orchestrator (`mcp_orchestrator/`)
  differ, both are given.
- **Citations:**
  - Ours use the shipped layout: `scripts/`, `config/`, `assets/` and
    `scripts/engine/`.
  - Upstream paths sit under `lib/src/agentic_timing_closure/`. In the table,
    `tca/` stands for `lib/src/agentic_timing_closure/timing_closure_agent/`
    and `mcp/` for `lib/src/agentic_timing_closure/mcp_orchestrator/`.
  - Our line numbers are from commit `8b73688` (2026-11-06), before the
    changes listed under "Already aligned in this change". Those changes move
    lines in `scripts/tcl_gen.py`, `scripts/orchestrate.py`,
    `scripts/engine/serve.py` and `config/agent_config.yaml`; search for the
    quoted names where a line number no longer matches.

**How to read a row.** "Class" says what kind of difference it is. "Impact"
says what changes in the runs. "Evidence" is the incident or measurement
behind our choice, taken from our config and code comments; a dash means none
is recorded. A "keep" recommendation always gives its reason. Every other row
is "decide" (or "revert" where the row is dead code). "How to revert" names the
cheapest way to get upstream's behaviour.

| Class | Meaning |
|---|---|
| config | One key in `config/agent_config.yaml` flips it. |
| code | Needs a code change in `scripts/` or `scripts/engine/`. |
| asset-behavioural | A Tcl or YAML asset under `assets/skills/` behaves differently. |
| asset-additive | An asset does more than upstream's; nothing upstream does is lost. |
| comment-or-shim | Wording only, or a file that sources its canonical copy. No behaviour change. |
| new-by-request | Added at the owner's request. Upstream has nothing like it. |

| Status | Meaning |
|---|---|
| aligned | Same as upstream by default. |
| aligned in this change | The owner decided to align; done on this branch. |
| deviation | Differs today and is waiting for a decision. |

## Already aligned in this change

The owner decided these four; all are done on this branch
(**aligned in this change**). Two additions by request follow them (rows 55, 56).

**(a) Stage 1 strategies**

- `stage1.runs` becomes upstream's `[default_placer, explore, aggressive_placer]`
  (`tca/config/agent_config.yaml:54-57`) instead of our seven
  (default_placer, explore, auto_1..auto_5).
- `stage1.include_baseline: true` (`tca/config/agent_config.yaml:52`).
- Ours: `config/agent_config.yaml:17-20` and `:41`.
- `auto_1..auto_5` stay defined and can still be listed in `stage1.runs`.

**(b) On timing closed: kill every sibling run**

- Upstream:
  - When a run meets timing, upstream kills every queued and running sibling.
    The baseline and methodology runs and the closing run itself are spared
    (`tca/core/agent.py:857-881`, `:959-975`; `tca/core/run_manager.py:113-122`).
  - It also kills them when it finishes (`tca/core/agent.py:1383-1402`).
- Ours today:
  - Stage 1 runs carry on to the end.
  - `stop_on_first_close` only skips runs that have not started
    (`scripts/orchestrate.py:613-617`, `:1408-1410`).
  - Leftover jobs are killed only when the flow completes
    (`scripts/engine/serve.py:542-571`, `:890`).
- New knob: `guardrails.on_timing_closed`, one of `kill_all` | `cancel_pending`
  | `off`, default `kill_all`:
  - `kill_all` cancels not-started runs and kills queued and running sibling
    runs of the design, sparing the closing run and a baseline or methodology
    run that is already running (one not yet started or still queued is
    cancelled: starting a multi-hour reference after the close would waste it).
  - `cancel_pending` cancels only the runs that have not started or are still
    queued.
  - After the close nothing new starts: no Path B, Stage 2, planner round,
    Stage 3 or retry (`orchestrate.py close-design`, `state.closed_by`).
  - `off` keeps today's behaviour.

**(c) Checkpoints (DCPs)**

- Upstream:
  - The MCP default writes no DCPs (`mcp_defaults.skip_dcp: true`,
    `tca/config/agent_config.yaml:26`; applied at `mcp/orchestrate.py:303-304`).
  - That works there because every replay starts from the input DCP and all
    detection runs inside the implementation run.
  - Its bsub path writes five DCPs per run (`tca/flows/tcl_generator.py:67-68`).
- Ours:
  - Ours keeps checkpoints (`output.skip_dcp: false`, `config/agent_config.yaml:234`).
  - Ranking, Stage 2 DETECT jobs, Stage 3 and the planner screen need a routed
    DCP, and the Path B detect job needs `postplace_physopt.dcp`.
- This change:
  - stops writing `postopt.dcp` and `postplace.dcp`;
  - deletes `postroute.dcp` in the run as soon as `postroute_physopt.dcp` is
    written; ranking accepts either routed checkpoint.
  - `output.intermediate_dcps: true` brings all five back for diagnosis.
  - `output.keep_dcps: winner` (default): after each collect, a ranked run
    that is not the overall best loses its DCPs (a Stage 1 flow's placed one
    is kept until its Path B detect has run); at finalize only the winner's
    remain. `all` keeps every run's.
- **The deviation remains:** while the flow runs we hold the current best's
  DCPs and those of runs in flight, and the winner's 2 at the end; upstream's
  MCP mode writes none.

**(d) Stage 2 fix runs: upstream's parallel fix runs**

- Upstream (`tca/flows/parallel_fix.py:39-205`, `:331-408`), per detected
  category of the Stage 1 winner:
  - `<Cat>_RQS` when the category has post-route phys_opt suggestions
    (`rqs_counts`), never for Partition unless `enable_partition_rqs`;
  - one run per actionable fix: an XDC skill whose detector wrote its
    artifact, or a directive skill whose detector wrote its flag;
  - `<Cat>_Combined` when `all_fixes and (use_rqs or len(all_fixes) > 1)`;
  - the fix runs replay the parent's own flow; no per-category cap.
- Ours before: a combined run only when the parent had a `.rqs` file (so a
  design with no QoR suggestions never got one), launched as "RQS only" when
  no fix was actionable, none for Partition; no per-category RQS-only runs;
  directive fixes gated on the classifier's condition instead of the
  detector's flag; skill runs capped per bucket; fix runs replayed `explore`.
- This change (rows 21, 22, 58):
  - `skill_resolver.py` plans `<cat>_rqs` from the per-category counts
    (`stage1_best.json` `rqs_counts`), every skill with no cap, and
    `<cat>_combined` whenever the category has a skill (Partition included).
  - `stage2-generate` keeps upstream's gates after detection: an XDC fix
    needs its artifact, a directive its flag; the combined run needs a fix
    and (RQS or 2+ fixes), and adds RQS only when the category has some.
  - The cross-category combined run (row 24, still off) takes its RQS from
    the same per-category counts.
  - Config: `stage2.inherit_parent_flow: true`, `stage2.enable_partition_rqs:
    false`, `stage2.cap_skill_runs: false` (true restores the caps).
  - A category is planned from the checker's detection alone; its
    `skills_config.yaml` trigger is recorded, not used as a gate (upstream's
    fix runs never read those triggers).
  - `structural_analysis` (upstream `apps_skills`) is a Stage 2 fix unless
    `early_fixes.structural_analysis` applied it in Stage 1 (default off).
  - A combined run with several directive fixes uses the last one, as
    upstream (each overwrites the subdirective).
- Unchanged and ours: planner rounds, Path B, Stage 3, the stop on close.
- Tests: `tests/test_static_parity.py`, and `tests/test_static_parity_diff.py`,
  which runs upstream's own `flows/parallel_fix.py` (from `reference/`) and
  ours over 70+ scenarios (every subset of fired fixes per category, with and
  without RQS) and requires the same runs. Known differences it maps: the
  `apps_skills` rename (row 32), the `control_set_remap` caught XDC, and
  run dedup (row 41: upstream's combined run of two directives is exactly its
  last directive's run, which ours does not launch twice).

## Deviations

| # | Area | Upstream (file:line) | Ours (file:line) | Class | Impact | Evidence / incident | Recommendation | How to revert |
|---|---|---|---|---|---|---|---|---|
| 1 | Stage 1 strategy set | `[default_placer, explore, aggressive_placer]`, `tca/config/agent_config.yaml:54-57`; flows `tca/flows/stage1.py:108-128` | `config/agent_config.yaml:17-20`; `scripts/tcl_gen.py:248-298` | config | 3 strategies + baseline instead of 7 | -- | aligned in this change (decision (a)) | -- |
| 2 | Baseline run | `include_baseline: true`, `tca/config/agent_config.yaml:52`; `tca/flows/stage1.py:100-107`, `:206-207` | `config/agent_config.yaml:41`; `scripts/tcl_gen.py:290-297`; never ranked: `scripts/orchestrate.py:1416-1418` | config | One more full run, reported as the plain-Vivado reference. Its own route directive and post-place step still differ (rows 7 and 10) | -- | aligned in this change (decision (a)) | -- |
| 3 | Extra and missing Stage 1 flows | Has `mixed` and `early_fix` (smart OC, GT-MRMAC pre-place), neither in its default sweep: `tca/flows/stage1.py:139-188` | Adds `spread_high`, `spread_balanced`, `reduce_cong_high` (opt-in) and keeps `auto_1..5` with an ML-directive fallback off Versal: `scripts/tcl_gen.py:218-277`. No `mixed` or `early_fix` | code | None by default; all of these are opt-in on both sides | Spreading flows: a routability-limited 2026.1 design met setup but could not route (Route 35-162, 252 signals unrouted) | keep: opt-in only, so the default sweep is unchanged | code: drop the entries from `STAGE1_FLOWS` |
| 4 | AUTO placer params | Auto_n flows set no `set_param` (dropped 2026-10-08): `tca/flows/stage1.py:146-180`, `tca/flows/tcl_generator.py:145-153` | `stage1.auto_placer_params: true`, `config/agent_config.yaml:49`; `scripts/tcl_gen.py:91-111`, `:437-440`, `:1375-1377` | config | Only runs that place with Auto_n sub-directives: opt-in `auto_*` flows, and category or planner fixes that have no resolved parent prediction | Our 2026.1 runs failed with Constraints 18-12534 without the params (planner fixes 1 and 4 on a suite design) | keep: without the params, place_design fails on 2026.1 | config: `stage1.auto_placer_params: false` |
| 5 | Stop duplicate placements | None | `stage1.stop_duplicate_placements: true`, `duplicate_wait_minutes: 30`: `config/agent_config.yaml:34`, `:37`; `scripts/tcl_gen.py:808-940` | code | Stops a Stage 1 run right after place_design when its placement signature matches an earlier strategy's. Rare with the 3-flow sweep | auto_1 == auto_2 and auto_3 == auto_4 on every design measured; about 3 of 7 Stage 1 runs | keep: Vivado is deterministic, so a stopped run would only have replayed its twin's result. No QoR effect | config: `stage1.stop_duplicate_placements: false` |
| 6 | Re-run opt_design | Always `opt_design -directive Explore`: `tca/flows/tcl_generator.py:71-78`, `:760` | `stage1.rerun_opt_design: true`: `config/agent_config.yaml:26`; `scripts/tcl_gen.py:1150-1156` | config | None by default. `false` skips the second opt pass | -- | keep: the default equals upstream | -- |
| 7 | Post-place phys_opt | Every run sources the shared `adaptive_physopt.tcl` skill: congestion-gated, over-constrains a met design, iterates. The baseline gets it too: `tca/flows/tcl_generator.py:810-813` | `flow.postplace_physopt: flow`, `stage1.adaptive_physopt: false`: `config/agent_config.yaml:22`, `:64`; `scripts/tcl_gen.py:1028-1039`. Stage 1 runs one `phys_opt_design -directive AggressiveExplore` (the baseline: Explore); Stage 2 variants use our adaptive loop | config | Different post-place optimisation in every run. Changes QoR | Default kept "so known-good results stay put until a validation run decides" (`config/agent_config.yaml:54-58`). No validation run recorded | decide | config: `flow.postplace_physopt: skill` |
| 8 | Shared skill iteration cap | `PHYSOPT_MAX_ITER` = `stage2.multi_physopt.max_iterations` (3) for Stage 1, RQS, R2 and PFR runs (`tca/flows/tcl_generator.py:736`, `:811`); 6 for OC, category-directive, partition and tcl-fix runs (`:481`, `:1079`, `:1509`, `:1632`, `:1744`) | `flow.postplace_physopt_max_iter: 6` for every run: `config/agent_config.yaml:65`; `scripts/tcl_gen.py:533` | config | Applies only with `postplace_physopt: skill`. Our comment "upstream: 6" is right only for the OC and fix runs; upstream's own Stage 1 Tcl sets 3 | -- | decide (and correct the comment either way) | code: take the cap per run kind (3 or 6) |
| 9 | Reset pre-place OC before post-place phys_opt | Always, in OC runs: `tca/flows/tcl_generator.py:1068-1076`; generic form `:799-808` | `flow.reset_preplace_oc: false`, used only with `postplace_physopt: skill`: `config/agent_config.yaml:69`; `scripts/tcl_gen.py:528-532` | config | See row 16: our OC runs keep the tightened target through post-place phys_opt | -- | decide | config: `flow.reset_preplace_oc: true` plus `flow.postplace_physopt: skill` |
| 10 | route_design directive | `route_directive_override` or else **AggressiveExplore** for every flow, the baseline included; the flow's switches are kept (`-tns_cleanup`): `tca/flows/tcl_generator.py:501-511`. Fixed Stage 2 variants: `AggressiveExplore -tns_cleanup` (`:1085`) | Each flow's own: `NoTimingRelaxation -tns_cleanup` for default, explore, auto and Stage 2 variants; AggressiveExplore for `aggressive_placer`; Explore for the baseline. `flow.route_directive` is unset: `config/agent_config.yaml:73`; `scripts/tcl_gen.py:233-237`, `:471-495` | config | Different router effort in every run except `aggressive_placer`. Changes QoR and runtime | -- | decide | config: `flow.route_directive: AggressiveExplore` |
| 11 | Post-route phys_opt | One `phys_opt_design -directive AggressiveExplore` in every run except the baseline: `tca/flows/tcl_generator.py:842-845`, `:1091` | `flow.postroute_physopt: flow`: `config/agent_config.yaml:76`; `scripts/tcl_gen.py:1066-1078`. Stage 1 is already one AggressiveExplore pass (same as upstream). Stage 2 and planner runs loop up to `multi_physopt.max_iterations` | config | Stage 2 and planner runs only | -- | decide | config: `flow.postroute_physopt: single` |
| 12 | Adaptive loop Tcl | `_prev_wns` seeded with -999, so at least 2 passes run unless timing is met: `tca/flows/tcl_generator.py:441-479` | Seeded with the WNS before the loop, so a first pass with no gain stops at once. TNS-aware continue for planner fixes: `scripts/tcl_gen.py:542-607` | code | Fewer identical phys_opt passes | The -999 seed forced a second, identical pass (`scripts/tcl_gen.py:546-549`) | keep: the extra pass repeats work and cannot change the result | code |
| 13 | Methodology, check_timing and report_cdc | report_methodology in the baseline only (`tca/flows/tcl_generator.py:899-904`); check_timing and report_cdc after opt_design in **every** run (`:769`, `:1056`, ...) | All three only in the design-checks host run: the baseline, else the first strategy that cannot stop as a duplicate. `scripts/orchestrate.py:680-691`; `scripts/tcl_gen.py:994-1011`, `:1207-1208` | code | Fewer reports per run; the design checks are not in every run dir | The post-opt design state is the same in every Stage 1 run, so extra copies add nothing (`reference/UPSTREAM_SYNC.md`) | keep: same information, written once | code: `include_check_timing`/`include_cdc` on in `flow_body` |
| 14 | Stage 2 recipe set | S2B runs **only** RQS (1) and the OC ladder (4): `tca/flows/stage2.py:162-212`. Multi-physopt, OC-at-post-place and OC-before-physopt are dropped (covered by the shared skill); Macro LOC is commented out (`:194-195`). S2A launches nothing (`:173-201`). Category fixes come from PFR (bsub, `tca/core/agent.py:245-257`) or `launch_category_runs` (MCP, `mcp/orchestrate.py:493-501`) | One Stage 2: the OC ladder, one RQS run and capped skill runs: `scripts/skill_resolver.py:712-735`; `scripts/orchestrate.py:1592-1751` | code | Same run kinds, one stage instead of S2B + PFR. Sub-differences are rows 15-23 | -- | keep: same recipe content. We never had the removed variants (`reference/UPSTREAM_SYNC.md`, sync 2) | -- |
| 15 | OC value and clocks | `oc = abs(WNS of the top failing group) x m`, on that group's clocks: `tca/flows/stage2.py:100-143`, `:249-259` | `oc = abs(best run WNS) x m`, on the top group's clocks, `*` if none parsed: `scripts/orchestrate.py:1624-1656` | code | Same in the usual case, where the worst group sets the WNS | -- | decide | code |
| 16 | OC reset point | Uncertainty reset right after place_design, before post-place phys_opt: `tca/flows/tcl_generator.py:1068-1076` | Kept through post-place phys_opt, reset before route (`oc_reset.tcl`): `scripts/tcl_gen.py:1334-1335`. Rows 7 and 9 together give upstream's order | code | Our post-place phys_opt works against the tightened target, close to upstream's removed OC-before-physopt variant. Changes QoR | -- | decide | config: rows 7 and 9 together; else code |
| 17 | OC verification | Plain `set_clock_uncertainty`: `tca/flows/tcl_generator.py:1033-1042` | Measures the user uncertainty before and after. If it did not move, re-applies the OC as a clock pair; if it still does not move, fails the run. Writes `oc_reset.tcl`: `scripts/tcl_gen.py:1247-1319` | code | An OC run whose OC is outranked stops instead of silently replaying its parent | design A (`-from * -to *` 0.000) and design B (clock-pair 0.000): the per-clock OC did nothing | keep: an OC that does not apply wastes a full run | code |
| 18 | OC multipliers and threshold | `[1.5, 2.0, 2.5, 3.0]`, -200 ps: `tca/config/agent_config.yaml:61-63` | Same: `config/agent_config.yaml:94-99` | config | -- | -- | aligned | -- |
| 19 | `multi_physopt.max_iterations` meaning | 3, used as the shared skill's `PHYSOPT_MAX_ITER` in full-flow runs (`tca/flows/tcl_generator.py:736`) | 3, used as the cap of our adaptive post-place and post-route loops in Stage 2 and planner runs: `config/agent_config.yaml:100-102`; `scripts/tcl_gen.py:992-993` | config | Same value, different consumer. Moot once rows 7 and 11 are reverted | -- | decide (follows rows 7 and 11) | -- |
| 20 | RQS run | Category filters on `postroute_physopt.rqs`: `tca/flows/tcl_generator.py:513-558` | Same filters. Adds a fallback to suggestions stamped `postroute_phys_opt` when a category has none at its usual step, stops the run when nothing is enabled (`scripts/tcl_gen.py:615-710`), and is not generated when nothing would be enabled (`scripts/orchestrate.py:303-315`) | code | No RQS run that only replays its parent | With intermediate RQS off (row 38), the filters matched nothing and 4 RQS runs replayed their parent (2026-09-28) | keep: needed while row 38 stays off | code |
| 21 | Stage 2 parent flow and early fixes | Every S2, PFR, R2 and S2R2 run replays the Stage 1 winner's own flow (placer, opt directive, early fixes): `tca/flows/stage2.py:42-92`; early fixes in every non-baseline run, `tca/flows/tcl_generator.py:739-756` | `stage2.inherit_parent_flow: true` (was `false`: OC and RQS replayed `explore` with a plain `opt_design` and no early fixes): `config/agent_config.yaml` | config | -- | -- | aligned in this change (d) | -- |
| 22 | PFR mapping (category fix runs) | Per detected category: one RQS-only run filtered by category (Partition maps to Timing, disabled by `enable_partition_rqs: false`), one run per skill fix, and one combined run when `all_fixes and (use_rqs or len(all_fixes) > 1)`. No caps: `tca/flows/parallel_fix.py:84-170`; `tca/config/agent_config.yaml:85-93` | Same run set and gates (change (d)): `<cat>_rqs`, one run per actionable fix, `<cat>_combined`; `stage2.enable_partition_rqs: false`; caps only with `stage2.cap_skill_runs: true`: `scripts/skill_resolver.py`, `scripts/orchestrate.py` `stage2-generate` | code | -- | Combined runs never launched: the old gate required a `.rqs` file, and a design with no QoR suggestions writes none (3 of 3 `tc_small_2p62` runs) | aligned in this change (d) | -- |
| 23 | Where detection runs | Inside every non-baseline run: Layer 1/2 detection at `postroute_physopt` (and Partition-only at `postplace_physopt`), in the same Vivado session: `tca/flows/tcl_generator.py:560-715`, `:817-819`, `:865-867` | Once, on the Stage 1 winner: classify its reports, then DETECT jobs open its checkpoints: `scripts/orchestrate.py:1509-1558` | code | Detection costs one job instead of minutes in every run, but needs the winner's DCPs (decision (c)) | `report_slr_crossing` alone took 17-47 min on a large SSI design suite | keep: detection runs once, not in every run | code |
| 24 | Cross-category combined run | `parallel_fix.combined_run` off: `tca/flows/parallel_fix.py:164` | `stage2.combined_run: false`: `config/agent_config.yaml:111-116` | config | -- | -- | aligned | -- |
| 25 | `stage2.mode` (note only) | No planner in the bsub engine; the MCP LLM mode differs | `stage2.mode: both`: fixed Stage 2, then planner rounds: `config/agent_config.yaml:93` | config | Not static flow. With `fixed`, our flow ends where upstream's does | Fixed menu plus planner beat either alone on the 2026.1 report | note only | config: `stage2.mode: fixed` |
| 26 | Path B detect job | XDC written inside each Stage 1 run, before route: `tca/flows/tcl_generator.py:600-629` | `path-b-scan` reads post-place reports (no Vivado); a separate R2D job opens `postplace_physopt.dcp` and writes the XDC. Runs write a `postplace_physopt.done` marker: `scripts/orchestrate.py:1054-1173`; `scripts/tcl_gen.py:1045` | code | Stage 1 runs are not slowed; needs `postplace_physopt.dcp` (decision (c)) | `report_slr_crossing` took 17-47 min; DETECT runs took about 2.5-3 h from submission | keep: inside every Stage 1 run it would delay all of Stage 2 | code |
| 27 | Path B scope | Every non-baseline Stage 1 run is polled until its fix or detection resolves; no cap: `tca/core/agent.py:200-209`, `:670-808` | `path_b.max_flows: 3`, first come first served; nothing new once Stage 1 has closed: `config/agent_config.yaml:155`; `scripts/orchestrate.py:1067-1100` | config | At most 3 R2 runs (with the 3-flow sweep, the cap can no longer bind) | Each flow costs one detect job plus one full run | decide | config: raise `path_b.max_flows` |
| 28 | Path B GT/MRMAC variant | `mrmac_slr_suggestions.xdc` triggers R2 runs of explore and default_placer: `tca/flows/fix_iteration.py:84-132`; `tca/core/agent.py:785-792` | Not ported | code | No GT/MRMAC R2 runs | Its input comes from a detection our Stage 1 never runs; `early_fixes.gt_mrmac_loc` covers the case (row 30) | decide (tied to row 30) | code |
| 29 | Stage 2 on R2 (S2R2) | `stages.s2r2: false`, so **off**: `tca/config/agent_config.yaml:47`. When on: OC + RQS + category runs on the best R2: `tca/core/agent.py:287-346` | `path_b.stage2_on_r2: true`: the OC ladder and RQS of the best R2, once, only if it beat Stage 1 without closing: `config/agent_config.yaml:158`; `scripts/orchestrate.py:1204-1273` | config | Up to 5 extra runs | -- | decide | config: `path_b.stage2_on_r2: false` |
| 30 | Early fix `gt_mrmac_loc` | `false` (opt-in): `tca/config/agent_config.yaml:105` | `true`: `config/agent_config.yaml:221` | config | GT/MRMAC SLR co-location applied in every Stage 1 run | One of the three Stage 1 slide fixes | decide | config: `early_fixes.gt_mrmac_loc: false` |
| 31 | Early fix `postopt_rqs` | `true`: applies RQS_CLOCK-1 and RQS_CLOCK-28 from the post-opt RQS before place: `tca/config/agent_config.yaml:108`; `tca/flows/tcl_generator.py:778-790` | `false`: `config/agent_config.yaml:225`. **No code reads this key**, and no post-opt RQS is written (row 38), so setting it to `true` changes nothing | code | No post-opt clocking suggestions applied | 14 of 14 segfaults on build 6511674 followed RQS commands | keep: avoids the RQS crash. Note the key is a no-op | code: add the apply block in `flow_body` and turn on post-opt RQS |
| 32 | Structural Analysis rename | Folder `timing/chaithanya_skills/`; key `early_fixes.chaithanya_skills`; env `EF_CHAITHANYA_SKILLS`; outputs `chaithanya_skills_fix.xdc` / `_detected.flag`: `tca/config/agent_config.yaml:107`, `tca/flows/tcl_generator.py:746-747` | `timing/structural_analysis/`; key `early_fixes.structural_analysis`; env `EF_STRUCTURAL_ANALYSIS`; `structural_analysis_fix.xdc`: `config/agent_config.yaml:224`; `scripts/tcl_gen.py:733-735`; `assets/skills/early_flow_fix/early_flow_fix.tcl:214`; `assets/skills/timing/structural_analysis/detect.tcl` | asset-behavioural | Same logic, off by default on both sides. An upstream config's key is ignored here. The detect flag line still says `CHAITHANYA_SKILLS_DETECTED` (`detect.tcl:81`) | -- | keep: descriptive name; the shipping rules allow no person names | code + asset rename |
| 33 | Hard-IP locality early fixes | `hardip_proximity_pblock` and `prevent_hardip_slr_crossing` are standalone skills; the flow never runs them | Opt-in inside `early_flow_fix.tcl` (`EF_<NAME>=1`; fix.tcl applied one caught command per line): `assets/skills/early_flow_fix/early_flow_fix.tcl:278-324`; `scripts/tcl_gen.py:736-738`; `config/agent_config.yaml:227-228` | asset-additive | None by default | -- | keep: off by default, which matches upstream | config: both `false` (the default) |
| 34 | Checkpoints | MCP: none (`skip_dcp: true`); bsub: 5 per run | 2 per finished run after this change (5 before): `config/agent_config.yaml:234`; `scripts/tcl_gen.py:364-365` | config | Disk; see decision (c) | Stage 2/3 replays, DETECT and R2D jobs need DCPs | keep: required by rows 23 and 26 (the reduction is aligned in this change) | config: `output.skip_dcp: true` breaks rows 23 and 26 |
| 35 | BRAM LOC dump | Every run after post-place phys_opt: `tca/flows/tcl_generator.py:821-833`. Its consumer, Macro LOC, is disabled (`tca/flows/stage2.py:194-195`) | `output.bram_loc_dump: false`: `config/agent_config.yaml:237`; `scripts/tcl_gen.py:747-766`, `:1048` | config | No `Block_Ram_locs.xdc` | All 5 segfaults on 2026.1 released happened inside this dump (design D, design B) | keep: nothing reads the file, and it is the crash site | config: `output.bram_loc_dump: true` |
| 36 | Intermediate RQS | report/write_qor_suggestions at postopt, postplace, postplace_physopt and postroute: `tca/flows/tcl_generator.py:769`, `:795-796`, `:812-813`, `:838` | `output.intermediate_rqs: false`; only the final one runs: `config/agent_config.yaml:242`; `scripts/tcl_gen.py:383-389` | config | No intermediate RQS files. Upstream's readers (`postopt_rqs`, S2A/PFR_A) are off or absent here | 14 of 14 segfaults on build 6511674 followed RQS commands | keep: nothing here reads them, and each is another crash chance | config: `output.intermediate_rqs: true` |
| 37 | RQS order in the report block | RQS written before `write_checkpoint`: `tca/flows/tcl_generator.py:49-53` vs `:67-68` | RQS after the checkpoint; the final RQS is the last command of the run: `scripts/tcl_gen.py:364-370`, `:392-406` | code | A crash in RQS leaves every result and the checkpoint on disk | The RQS heap corruption crashes in the next big allocation | keep: crash-safe ordering, same reports | code |
| 38 | `report_route_status` | After every route: `tca/flows/tcl_generator.py:838-839` | `output.route_status_report: true`: `config/agent_config.yaml:245`; `scripts/tcl_gen.py:959-960`, `:1061-1063` | config | -- | -- | aligned | -- |
| 39 | Run.tcl failure handling | Each opt/place/route/phys_opt wrapped in `catch` + `exit 1` (`_guard_cmd`); success is the LSF exit or a "completed successfully." line: `tca/flows/tcl_generator.py:80-104`, `:911-912` | One `catch` around the whole script: writes `run.failed` (JSON with the error) and exits 1; `run.done` on success; `TC_KEEP_SESSION` keeps a successful session open: `scripts/tcl_gen.py:1092-1147` | code | Status comes from sentinels, not LSF exit codes | An uncaught error left Vivado idle, holding its LSF slot for hours | keep: MCP and session runs have no LSF exit code to read | code |
| 40 | Stop on first close | No such knob | `stage1.stop_on_first_close: false`: `config/agent_config.yaml:30`; `scripts/orchestrate.py:613-617`, `:1408-1410` | code | None by default; mostly superseded by decision (b) | -- | decide (fold into `guardrails.on_timing_closed`?) | code: remove |
| 41 | Run dedup | None | `guardrails.skip_duplicate_runs: true`: no run whose run.tcl fingerprint matches a *completed* run. Also the empty-RQS skip (row 20): `config/agent_config.yaml:210`; `scripts/orchestrate.py:290-339`; `scripts/dedup.py:258-288` | code | Fewer replay runs | design B: Stage 3 `reroute` equalled planner fix 4 and `rqs` equalled fix 3, about 6 h of slot time | keep: Vivado is deterministic, so the skipped run would repeat a known result | config: `guardrails.skip_duplicate_runs: false` |
| 42 | Stale-run kill | Kills a run past 5x its siblings' median time whose `vivado.log` has not grown for 30 min: `tca/config/agent_config.yaml:75-76`; `tca/core/agent.py:1306-1373` | Not ported. `guardrails.stale_minutes` (`config/agent_config.yaml:213`) only marks a recently touched run dir as still running | code | A hung run holds its slot until a person or the wall limit stops it | Not ported on purpose: the user wants to watch and fix the MCP hang (`reference/UPSTREAM_SYNC.md`) | decide | code |
| 43 | `max_total_runs` | 40, counting every registered run: `tca/config/agent_config.yaml:72`; `tca/core/agent.py:1260-1267` | 40, counting started runs, retries and Path B runs: `config/agent_config.yaml:207`; `scripts/orchestrate.py:542-555` | code | Small difference in what counts toward the cap | -- | keep: retries spend slots too, so they count | code |
| 44 | Wall-clock limit | `guardrails.timeout_hours: 0` (bsub) and `mcp_defaults.timeout_hours: 0`: `tca/config/agent_config.yaml:36`, `:73` | No key in `agent_config.yaml`; engine `serve.max_wall_hours`, default 0: `scripts/engine/serve.py:878-879` | config | -- (both off) | -- | aligned | -- |
| 45 | Stage 2 min improvement | `stage2_min_improvement_ps: 5`, read only by `DecisionEngine.stage2_improved`, which nothing calls: `tca/core/decision_engine.py:14`, `:113-124` | `5`, and `min_tns_improvement_pct: 20`: a later stage "improved" only if it beats the previous best by 5 ps, or matches its WNS and cuts TNS by 20 %. `config/agent_config.yaml:208-209`; `scripts/decision_engine.py:407-425` | code | Affects which stage is reported as improving | -- | decide | code |
| 46 | Retries | bsub engine: none. Upstream MCP SKILL.md: retry a crash once | `guardrails.max_retries_per_run: 1`, `retry_crash: true`: `config/agent_config.yaml:211-212`; `scripts/engine/serve.py:573-600` | code | One retry for infrastructure failures and crashes | Segfaults on build 6511674 are non-deterministic (twins pass the same step) | keep: a retry usually passes | config: `max_retries_per_run: 0` |
| 47 | Run selection | Hold-met first, then the highest WNS, then TNS: `tca/core/decision_engine.py:16-60` | `selection.mode: wns`, the same rule; `closability` is optional: `config/agent_config.yaml:199-203` | config | -- by default | -- | keep: the default equals upstream | -- |
| 48 | Stage 3 / Final Touch | `stages.final_touch: false`. When on, on the best of R2/PFR/S2-non-OC: RQS (R2 only), OC x4 (-200..0 ps), route-twice, OC-before-physopt: `tca/config/agent_config.yaml:48`; `tca/flows/final_touch.py:29-114` | `stage3.enabled: false`. When on, on the overall winner within -150 ps: a post-route phys_opt sweep (`last_mile`), `reroute` with an untried directive, `rqs`: `config/agent_config.yaml:123-142`; `scripts/orchestrate.py:1936-1956`, `:2062-2160` | code | None by default (both off) | -- | decide (no effect while both are off) | code |
| 49 | `directives.yaml` non-Versal fallbacks | `place.subdirective` only | Adds `place.directive` used off Versal (AltSpreadLogic_high, SSI_SpreadLogic_high, SSI_BalanceSLRs): `assets/skills/congestion/congestion_only/directives.yaml:13-15`, `assets/skills/congestion/with_slr_imbalance/directives.yaml:13-15`, `assets/skills/partition/unbalanced_util/directives.yaml:13-15`; consumed by `scripts/orchestrate.py:1469-1475` | asset-additive | Only non-Versal parts. Dead once the flow is Versal-only (row 55) | Placer sub-directives fail on UltraScale+ (Vivado_Tcl 4-2310) | revert once row 55 lands (dead code then) | asset: delete the 3 lines from each file |
| 50 | `control_set_remap` fix | Deletes `control_set_remap_fix.xdc`; the PFR run sources `control_set_analysis/fix.tcl` directly, so one missing cell fails the run: `tca/skills/congestion/control_set_remap/detect.tcl:30-31` | Writes `control_set_remap_fix.xdc` with each fix.tcl line wrapped in `catch`: `assets/skills/congestion/control_set_remap/detect.tcl:30-48` | asset-behavioural | A missing cell skips one command instead of failing the run | The 2026.1 report's design A winner (`PFR_Congestion_control_set_remap`) came from the caught form | keep: it produced a winner | asset: take upstream's `detect.tcl` |
| 51 | `pcie_clock_root` `result.json` in early fixes | `tca/skills/early_flow_fix/pcie_clock_root.tcl` writes no result.json | Shim to the canonical `assets/skills/early_resolution/pcie_clock_root/detect.tcl`, which writes `pcie_clock_root_result.json` (caught): `:211-247` | asset-additive | One extra JSON file in each Stage 1 run dir | -- | keep: write-only, caught, does not touch the fix | asset: restore the non-shim copy |
| 52 | Early-fix flow body | `tca/skills/early_flow_fix/early_flow_fix.tcl` | `assets/skills/early_flow_fix/early_flow_fix.tcl`: rows 32 and 33 only (rename; hard-IP opt-in block at `:278-324`) | asset-behavioural | See rows 32 and 33 | -- | keep (per rows 32 and 33) | asset |
| 53 | Shimmed duplicates (12 files) | Full copies at 12 paths | One-line shims sourcing the canonical copy: 7 x `early_flow_fix/custom_qor/detect_*.tcl`, `early_flow_fix/gt_mrmac_early.tcl`, `early_flow_fix/pcie_clock_root.tcl`, `early_resolution/hardsync_ce/detect.tcl`, `early_resolution/mbufg_group/detect.tcl` and `fix.tcl` | comment-or-shim | A shimmed detect writes `"category": "clocking"` in result.json where upstream writes `"early_resolution"` (`assets/skills/clocking/hardsync_ce/detect.tcl:146`, `assets/skills/clocking/mbufg_group/detect.tcl:135`); nothing reads it. `pcie_clock_root`: row 51 | -- | keep: one copy to maintain | asset: replace shims with copies |
| 54 | Wording-only asset diffs | "PFR", "Risk Score" | "the Stage 2 run" / "fix-run discovery" / "the resolver"; "Fanout risk": `assets/skills/congestion/control_set_remap/fix.tcl:1`, `assets/skills/timing/bli_io/detect.tcl:11`, `assets/skills/timing/bli_io/fix.tcl:3`, `assets/skills/early_resolution/fanout_analysis/detect.tcl:27`, `assets/skills/early_resolution/custom_qor/detect_broadcast_control.tcl:27` | comment-or-shim | None | -- | keep: comments only | asset |
| 55 | Versal-only gate | No gate: any part runs; placer sub-directives fail off Versal | `init` refuses a non-Versal part read from the checkpoint's `dcp.xml` (exit 6); `probe-collect` refuses a non-Versal ARCHITECTURE (phase `unsupported_device`). `scripts/orchestrate.py` `checkpoint_part`, `cmd_init`, `cmd_probe_collect`; `scripts/tcl_gen.py` `device_family` | new-by-request | Non-Versal designs are refused up front. Makes dead: row 49, `AUTO_ML_DIRECTIVES`, `NON_VERSAL_TWINS` and the `off_versal` branches (`scripts/tcl_gen.py:218-231`, `:419-435`, `:1361-1368`) | Owner's request | new-by-request (done in this change) | code |
| 56 | Planner dedup against in-flight runs | No planner dedup | A planner fix whose run.tcl matches a done, generated, **queued or running** run (Path B included) is not run: `RunGenerator(role="planner")`, `dedup.find_duplicate(unfinished=True)`; planner-context `in_flight`; planner-validate `duplicates` / `--prune`; the rule planner's tried filter. Static stages still match completed runs only | new-by-request | No duplicate planner fix while its twin is still queued or running | The same OC fix in the early and r1 rounds cost 1.4-2.1 h each (`scripts/dedup.py:260-263`) | new-by-request (done in this change) | code |
| 57 | `gt_clock_pblocks` fix: when applied | Generated `fix.tcl` applied after opt_design, before place_design, in a PFR run: `tca/skills/timing/gt_clock_pblocks/skill.md`, `tca/flows/parallel_fix.py:400` | Sourced before opt_design in the Stage 2 skill run, like every skill fix: `scripts/skill_resolver.py` (`gt_clock_pblocks`), `scripts/tcl_gen.py` `_xdc_apply_lines` | code | opt_design runs with the pblocks already in place; a cell it removes or replicates is not in the pblock it was put in | -- | decide | code: an `apply_after: opt_design` catalog field honoured by `generate_stage2_xdc_fix` |
| 58 | Timing skill run cap | PFR launches a run for every skill whose detector fired | No cap by default (change (d)); `stage2.cap_skill_runs: true` restores `BUCKET_CAPS` | code | -- | -- | aligned in this change (d) | -- |
| 59 | Per-stage checks | report_methodology `-checks` and report_clock_interaction after all five Stage 1 steps, plus `<stage>_design_context.txt` for its HTML page: `tca/flows/tcl_generator.py` `_stage_check_reports` | Same reports and switches, Stage 1 only, but no clock-interaction report after route_design and no `design_context.txt`: `scripts/tcl_gen.py` `stage_check_lines` | code | Upstream's post-route copy is overwritten by the post-route extra reports anyway; the context file only feeds upstream's HTML page | -- | keep: no information lost | code: drop the `postroute` exception |
| 60 | `cd $run_dir` | `set run_dir [file dirname [info script]]` then `cd $run_dir` | `$run_dir` normalized to an absolute path before the `cd`, and `run.failed` written there: `scripts/tcl_gen.py` `RUN_DIR_TCL`, `_finish` | code | None when Vivado gets an absolute script path. With a relative one, upstream's later `$run_dir/...` paths point at the wrong folder after the cd | -- | keep | code |

Row count: 60.

## Upstream changes not incorporated

Upstream changes that were not ported, from `reference/UPSTREAM_SYNC.md`:

**Sync 1 (2026-10-08)**

- **Partition RQS exclusion (`enable_partition_rqs: false`).** Holds here
  already: our resolver has no Partition RQS category.
- **Stale-run guardrail.** It kills jobs automatically, and the user wants to
  watch and fix the MCP hang instead (row 42).
- **Monitor-loop robustness** (exception isolation, `lsf_output.log` fallback,
  "completed successfully." check, Path-B deadlock). These fix upstream's own
  daemon. Ours uses `run.done` / `run.failed` and a whole-run catch.
- **HTML reporting suite** (analysis, run summary, validation and executive
  reports; xdc_diff). A separate product on upstream's `run_status.json`. Ours
  is `final_summary.txt/json`; the inputs those reports need are now produced
  here.
- **`batch_launcher` / `run_tc_tests.py`.** Another product surface; ours is
  `tc` and the IDE agents. (The web run platform was later run in place from
  the upstream snapshot, but is not shipped.)
- **`mcp_orchestrator` LLM mode.** Our engine already runs one session per run,
  with supervisor, backends, failover and decisions.
- **`mcp_defaults`** (2026.2 daily, queue, memory, `skip_dcp: true`). Site
  settings live in each workspace's `.tc/engine.yaml`. `skip_dcp: true` would
  break our replays and detect jobs (decision (c)).
- **SKILL.md headless rules.** Already covered: decision deadlines, no
  reconnecting by URL, `guardrails.retry_crash`.
- **Path B GT/MRMAC variant.** Its input comes from a detection our Stage 1
  never runs (row 28).

**Sync 2 (2026-10-26)**

- **Agent-session token extras** (per-session breakdown, raw stream log,
  `--max-budget-usd`, `run_waiter`). They act on agent sessions the engine
  does not run.
- **Removal of the multi-physopt and OC-before-phys_opt Stage 2 variants.** We
  never had them (row 14).
- **`run_tc_tests.py`.** Upstream's own test harness.
- **Per-skill README.md and skill-card.md.** Kept in the snapshot only; they
  are not part of `assets/`.

**Sync 3 (2026-11-13, PR #742)**

- **`chaithanya_skills` -> `apps_skills` rename.** Ours has been
  `structural_analysis` since sync 1 (row 52).
- **Vivado `-log` / `-journal` into the run dir.** Our backends already start
  Vivado in the run dir.
- **The two per-stage HTML pages.** Part of the HTML reporting suite (sync 1);
  the `.rpt` / `.json` files they read are written (row 59).

**Kept as ours on purpose (all syncs):** `control_set_remap` (row 50),
`postopt_rqs` off (row 31), detection output folders (row 23) and the 12 shims
(row 53).

## Appendix: reproducing the comparison

Set `OURS` to this repository's root and `UPSTREAM` to an
`agentic-ai-driven-timing-closure-framework` checkout at `b0cfa7a7` or later.

```sh
O="$OURS/.claude/skills/timing-closure-agent"            # ships as scripts/ config/ assets/
U="$UPSTREAM/lib/src/agentic_timing_closure/timing_closure_agent"
tmp=$(mktemp -d)

# 1. Asset diff with our shims resolved: copy the assets, replace each shim by its
#    canonical file, then diff -r against upstream's skills/ (docs and Python excluded).
cp -r "$O/assets/skills" "$tmp/ours"
( cd "$tmp/ours" &&
  grep -rl '^# Shim -- canonical copy:' . | while read -r f; do
      c=$(sed -n 's|^# Shim -- canonical copy: assets/skills/||p' "$f")
      cp "$c" "$f"
  done )
( cd "$U/skills" &&
  diff -rq -x __pycache__ -x '*.md' -x '*.txt' -x '*.docx' -x '*.py' -x .gitkeep . "$tmp/ours" )
# One pair in full, e.g.:
( cd "$U/skills" && diff early_flow_fix/early_flow_fix.tcl "$tmp/ours/early_flow_fix/early_flow_fix.tcl" )
( cd "$U/skills" && diff timing/chaithanya_skills/detect.tcl "$tmp/ours/timing/structural_analysis/detect.tcl" )

# 2. Config diff (skills_config.yaml was identical on 2026-11-06).
diff "$U/config/agent_config.yaml"  "$O/config/agent_config.yaml"
diff "$U/config/skills_config.yaml" "$O/config/skills_config.yaml"

# 3. Generated Stage 1 Tcl for one flow, side by side.
touch "$tmp/in.dcp"
python3 "$O/scripts/tcl_gen.py" stage1 --flow explore --dcp "$tmp/in.dcp" \
    --run-dir "$tmp/ours_explore" --config "$O/config/agent_config.yaml" --dry-run > "$tmp/ours.tcl"
( cd "$U" && python3 - "$tmp/in.dcp" <<'EOF'
import sys; sys.path.insert(0, ".")
from core.yaml_parser import load_yaml
from flows.tcl_generator import TclGenerator
from flows.stage1 import STAGE1_FLOWS
cfg = load_yaml("config/agent_config.yaml")
print(TclGenerator(cfg).generate_stage1_tcl(sys.argv[1], STAGE1_FLOWS["explore"], "explore"))
EOF
) > "$tmp/upstream.tcl"
diff "$tmp/upstream.tcl" "$tmp/ours.tcl"
grep -n 'PHYSOPT_MAX_ITER\|route_design\|phys_opt_design\|write_checkpoint' "$tmp/upstream.tcl" "$tmp/ours.tcl"
```
