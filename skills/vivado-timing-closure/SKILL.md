---
name: vivado-timing-closure
description: Close timing on an AMD Versal FPGA design. Start and supervise a Vivado implementation sweep, diagnose why a design misses timing from measured evidence, propose targeted fixes, and report the verdict. Use when asked to close timing on a design or a checkpoint (.dcp), or when a timing-closure run has decisions waiting.
license: MIT
compatibility: AMD Versal designs only; Vivado 2026.1+ (vivado on PATH or configured); python3 3.8+; LSF bsub for farm runs (optional, else local); Vivado MCP with vivado_workflow (optional; the engine also runs from its own command line)
metadata:
  version: "1.1.0"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Timing closure

You drive a timing-closure engine. It owns everything long-running — it submits
the Vivado jobs, tracks their state, retries what is worth retrying and picks
the winner. You start it, answer the judgement calls it raises, and report what
happened.

> The engine decides; you advise. Every pass/fail, budget and "closed" verdict
> is computed deterministically. Your answers improve the outcome — they cannot
> override a verdict, and a run completes without you if you are unavailable.

**You are the orchestrator, from the first command to the verdict.** The
supervisor keeps the jobs moving, but nothing tells you when something happens
— if you end your turn, nobody answers the judgement calls and nobody reports
the result. So you stay in a loop until the run is finished. A run takes hours;
that is expected.

## 0. Reaching the engine

There are two ways in. They drive the same engine and the same files, so you
can mix them.

- **The `vivado_workflow` action**, when your host offers it. Pass
  `workflow: "timing-closure"`, an `action`, `workspace` (the absolute path of
  the folder you are working in) and the action's `args`. The engine's own
  JSON comes back under `result`.
- **The `tc` command** otherwise, and for the steps the action does not cover.
  `tc` sits beside this file (`<skill-dir>/tc`; `vivado_workflow` action `list`
  reports the skill's `dir`).

| Step | `vivado_workflow` action and `args` | `tc` command |
|---|---|---|
| Check the workspace | `doctor` | `tc doctor --json` |
| Configure the workspace | `setup` (`vivado`, `queue`, `os_type`, `memory_mb`, `slots`, `env_script`, `force`) | `tc setup --queue long --os-type ws8 ...` |
| Can runs start? | `check` | `tc setup --check --json` |
| Start or resume | `start` (`dcp`) | `tc close --dcp <dcp> --json --attach` |
| Start a list of designs | `start_batch` (`designs`) | `tc batch --designs designs.cfg --json` |
| Wait on one run | `wait` (`output_dir`, `timeout`) | `tc wait --output-dir <dir> --compact` |
| Wait on every run | `wait_all` (`timeout`) | `tc wait --all --compact` |
| Watch a large batch | `watch` (`timeout`, opt-in flags below) | `tc watch --compact [--auto-triage ...]` |
| List runs | `runs` | `tc runs --json` |
| One run in full | `status` (`output_dir`) | `tc snapshot --output-dir <dir>` |
| Pending decisions | `decisions` (`output_dir`) | `tc decisions list --output-dir <dir> --pending --json` |
| One decision | `decision` (`output_dir`, `id`) | `tc decisions get --output-dir <dir> --id <id>` |
| Answer it | `answer` (`output_dir`, `id`, `answer`) | `tc decisions answer --output-dir <dir> --id <id> --answer-json '<json>'` |
| Release your watch | `detach` (`output_dir`, `user_asked`) | `tc detach --output-dir <dir>` (`--user-asked`) |
| Stop the supervisor | `stop` (`output_dir`) | `tc serve --stop --output-dir <dir>` |

Planner context and validation, read-only design queries, documentation search
and extra Tcl hooks are `tc` only (sections 2 and 4).

**Approval.** Actions that change something (`setup`, `start`, `stop`) are put
to the user first. If the reply says `needs_confirmation`, nothing ran: show the
user the command it lists, ask, and only if they agree call again with the same
arguments plus the `confirm_token` the reply gave. Never pass a token the user
did not approve. For an unattended batch the user can pre-approve the workflow
(`vivadoAI.workflows.autoApprove`, or `VIVADO_WORKFLOW_AUTO_APPROVE`).

**In a terminal.** Give each `tc wait` call a timeout of at least 10 minutes.
If the call comes back before the command printed its JSON line, it is still
running: read that terminal's output again until the line appears, and start
nothing else in it meanwhile. If a terminal keeps returning nothing at all, use
the `vivado_workflow` action instead and tell the user.

## 1. Before the first run

Run `doctor`; when it reports `ok`, go to section 2. Without `.tc/engine.yaml`,
run `setup`: it copies the host's own settings (Vivado, queue, memory, OS, the
server it launches through). Tell the user its `summary` and each of its
`notes`, then `doctor` again. An existing config is replaced only when the user
asks (`force`). Without the `vivado_workflow` action, `tc setup --check` names
what is missing and the user's answers go to `tc setup --vivado ... --queue ...
--os-type ...`.

When `doctor` lists `warnings`, tell the user each one before the first
`start`. The usual one: LSF is not reachable from here, so runs would execute
on this machine. If they want the farm, ask for their site's LSF profile and
pass it to `tc setup --env-script <path>`; start only once they have chosen.

**Nothing starts until the setup is complete.** A `start` (or `start_batch`)
that lacks a Vivado, an open queue or an OS type some host has comes back with
`needs: setup` and `fields` (each a `question`, the `problem`, and the queue's
`choices`). Ask the user, pass their answers to `setup`, then start again.
Never pick a queue, an OS type or a Vivado yourself, and never search for one
(`bqueues`, `lshosts`, `find`, `which vivado`): use the one the user named,
else the host's, else `.tc/engine.yaml`'s. Any other `doctor` problem needs the
user, so quote it and stop. Never hand-craft scheduler options.

## 2. Orchestrating a run

### Start

`start` with `dcp` (relative to the workspace or absolute). The result gives the
run's `output_dir` — call it `<dir>` from here on. `already_running: true`
means the run was already going and you are resuming it; that is fine. Starting
again is always safe: it reuses the run, never restarts it.

**Always give the user the run's live page link, right after `start` and before
the loop.** It is the `web_url` of the start reply. If that reply has none, take
it from `status` (`tc snapshot --output-dir <dir>`). If neither has one, say so
and why: the page is off under CI, after `tc setup --web off` or with
`TC_WEB=off`. Write it on its own line, in full, for example
`Live page: <web_url>`; it opens in any browser on the network. The link carries
its access key, so it is for the user and whoever they share it with. Repeat it
whenever you report on the run.

`start` also attaches you to the run (`attached: true`). Where the editor runs
this skill's hooks, a stop guard then holds your turn and a new chat in this
workspace is told to resume the loop; do not count on it — the loop below is
what keeps the run supervised. You release the run with `detach` when you exit.

If it **refuses** (for example "`vivado` is not on PATH"), report the message
and stop — that needs the user; do not try to work around it.

**Versal only.** A checkpoint for any other device is refused before anything is
launched (`tc close` exits 6, "… is not an AMD Versal device"; or, when only the
build probe can tell, the run stops in phase `unsupported_device`). Report it;
there is no override.

**A design stops once a run meets timing.** The engine cancels that design's
other unfinished runs (queued and running; a reference baseline already running
finishes) and starts nothing more: no later stage, Path B, planner round or
retry. `status` shows `closed_by` and the cancelled runs; the final summary lists
them. That is the flow working, not a failure to report.

**Only the winner's checkpoints are kept.** After each stage is collected,
runs that can no longer win lose their `.dcp` files (`output.keep_dcps:
winner`); at the end only the winning run's remain, and the summary says how
much was freed. `tc extend --from` another run still works for flow,
over-constrain and RQS ideas (they re-place from the input checkpoint); for
incremental fixes or a planner round from it, add `--restore` (`restore`),
which re-runs that run first to rebuild its checkpoint -- one more run.

Pass an optional argument only when the user asked for that behaviour:
`design`, `output_dir`, `concurrency`, `no_probe`. These are `tc close` flags
only, also only on request:

| Flag | Use when |
|---|---|
| `--backend lsf` / `--backend local` | force plain `bsub` / this machine |
| `--judge rules` | fully unattended; deterministic defaults decide everything |
| `--post-route-cmd '<tcl>'` | extra Tcl the user asked for; also `--extra-cmd`, `--post-opt-cmd`, `--post-place-cmd`, `--post-popt-cmd`, `--post-route-popt-cmd`; each repeatable, first start only, never edited into a run.tcl. A value may be the path of a `.tcl` file: it is sourced, and a missing file stops the start |

Then wait once with `timeout` 180. If that shows `run_failed` or
`supervisor_stopped`, quote the error from `<dir>/serve.log` and stop.

### The loop

Repeat until one of the exits below: `wait` on `<dir>`. It blocks until
something needs you, or the timeout, and returns one snapshot. A host may cap
the wait at a few minutes; a `timeout` event just means call it again.

**Every wait runs in the foreground, inside your turn.** Never start a wait in
the background and never end your turn while one is outstanding: nothing
brings you back when it returns, and the run goes on unsupervised.

Act on its `event`:

| event | Do this, then wait again |
|---|---|
| `decision` | Answer it (section 4). A `planner` call deserves the most care. |
| `run_failed` | Say which run failed and why; if a `triage` decision is pending, answer it (section 4). The engine decides retries. |
| `phase_change`, `timeout` | One line of progress: phase, runs in flight, best WNS / WHS so far. |
| `supervisor_stopped` | Read `outcome`. `needs_attention`: report its evidence and exit. Anything else (stopped, crashed): `start` again with the same `dcp` — it adopts the jobs already running — and continue. |
| `complete` | Report (section 5), then exit. |

**Exits — the only reasons to end your turn:**
1. the verdict is reported (`complete`);
2. the run needs the user (`needs_attention`, or `start` refused);
3. the user asked you to stop.

On every exit, release the run first: `detach` with `output_dir`, adding
`user_asked: true` only when the user asked you to stop. The run itself keeps
going; only your watch ends. `detach` refuses a run that is still active unless
the user asked — that refusal means go back to the loop.

### Several runs at once

For a list of designs (`designs.cfg`, one `name,dcp` per line), one
`start_batch` with `designs` starts or resumes every run, each under
`runs/<name>`, and answers with how many started, resumed and failed; quote any
that failed. Never `start` them one by one. Give the user the batch's
`web_url` (the page listing every run) before the loop, as for one run, and
each run's own link when you report that run. Then loop on `wait_all`
(`tc wait --all --compact`): its `output_dir` names the run with the event, and
`watching` gives the rest as counts by phase plus the names of runs that have
decisions or whose supervisor stopped. Act on the event for that run. After
reporting a `complete` run, `detach` it. `idle` means no run is active any
more. Keep looping until every run is reported, needs the user, or the user
asks you to stop.

**Large batches (about 20 runs or more): loop on `watch` instead**
(`tc watch --compact`). It reads each run's files rather than starting a
status process per run per poll, ignores phase changes, and returns the same
events; each run that completed or whose supervisor stopped is reported once,
including one that died without an outcome, which `wait_all` does not show. These flags
each do something on their own, so pass one only when the user asked for it,
and tell them what `actions` in each reply says was done:

| Flag (`watch` arg) | Does |
|---|---|
| `--auto-triage` (`auto_triage`) | answers a triage call when the evidence settles it: a known deterministic error, or the first error the design already hit on 2+ other runs (no retry); a first OOM, disk-full, LSF kill or interruption (retry). Recorded as answered by `watch`; anything else still comes to you |
| `--restart-dead` (`restart_dead`) | `tc close` again a run whose supervisor died without an outcome; it adopts the running jobs |
| `--free-space` (`free_space`) | when writes fail, deletes checkpoints superseded in the same run and those of retired `attempt_*` folders, logged to `.tc/watch_deleted_dcps.txt` |
| `--kill-strays` (`kill_strays`) | kills farm jobs still alive in retired `attempt_*` folders |

Event `session_server_down` means the shared MCP server in `.tc/engine.yaml`
is not listening: tell the user; new runs cannot launch until it is back.

### The stop guard and role agents

A stop guard, where installed, may hold your turn while a run you are attached
to is active and tell you what to do next: do it; a new chat in this workspace
is told which runs to resume. Role agents (`tc-launcher`, `tc-monitor`,
`tc-planner`, `tc-judge`, `tc-triage`, `tc-reporter`), where available, keep
long outputs out of your context: hand each the output directory, checkpoint
and decision id every time (they remember nothing), and block on its answer;
every wait still runs in the foreground. Without them, do the steps yourself.

### If your turn ends anyway

A turn limit, a closed window, a lost session: the run continues on its own and
decisions fall back to their rules at their deadlines. Whoever comes back runs
`runs` to find it, `start`s it again (it reuses the run) and resumes the loop.

### Trying more on a run that exists

To try a pblock, a fanout limit, an early-fix switch, another placer or one more
planner round on a checkpoint that already has a run, `extend` that run: never
`start` a new flow for it. Read `references/extend.md` first.

## 3. Looking without orchestrating

`runs` lists every run in the workspace with its phase, best result and pending
decisions; `status` shows one in full; `decisions` lists what is waiting.
`stop` stops a supervisor — runs already submitted keep going, and `start`
adopts them.

## 4. Answering a judgement call

`decision` gives the question, its measured evidence, a deadline and a
deterministic default. Answer with `answer`, passing the answer object as
`answer` (`tc decisions answer ... --answer-json '<json>'`).

Miss the deadline and the default applies and the flow carries on — so a
question you cannot answer well is better left to the rule.

**The answer is all you give: the supervisor applies it.** Never run flow steps
yourself — `planner-start`, `planner-apply`, `stage*-collect` — and never add
`--force`. If an answer is refused, fix what the refusal names or report it and
let the default apply. A forced `planner-start` once moved a flow past an
uncollected Stage 2 and stalled it.

| Point | The question | Answer shape |
|---|---|---|
| `planner` | Why does this design miss timing, and what should we try? | `{"plan_path": "<absolute path to plan.json>"}` |
| `postplace` | Is this placement worth routing? | `{"answer": <bool>, "why": "..."}` |
| `stage2` | Should Stage 1 carry an extra congestion hedge? | `{"answer": <bool>, "why": "..."}` |
| `stage3` | Is the design close enough for the last mile? | `{"answer": <bool>, "why": "..."}` |
| `triage` | Is this failure worth retrying? | `{"answer": <bool>, "why": "..."}` |

`why` is required on every gate. It is recorded with the answer, shown in the
report, and read by the next planner round.

For gates, the defaults already encode published metric gates and what our own
suites measured, so **agree with the default unless the evidence shows
something it cannot see**. An answer that restates the rule adds nothing.

For a `triage` or `planner` call, read `references/decisions.md` first: what
to read in the failed run before answering a triage call, and how to diagnose,
query the design and write a plan for a planner call. Never restart, kill or
edit anything yourself.

## 5. Reporting

Read `status` (or `tc status --output-dir <dir> --json`), `<dir>/final_summary.txt`
and `tc decisions provenance --output-dir <dir>`. Then report, in under 20
lines:

- the run's live page link (`web_url`, also the "Live page" line of
  `final_summary.txt`), or why there is none;
- the verdict line and the verification line, quoted;
- best WNS **and** WHS (and TNS), with the run and stage that produced them — a
  result that misses hold is not usable, however good its setup slack;
- each stage's best;
- which decisions you answered and which the rules answered — two runs of the
  same design can otherwise diverge for reasons that appear nowhere in the
  output;
- anything that failed and was not retried.
