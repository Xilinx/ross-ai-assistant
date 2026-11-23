<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Full simulation workflow

The complete method behind [SKILL.md](../SKILL.md). Read it when the routing
table there does not cover the request, or for the classification, approval
and report rules in detail. Paths below are relative to the skill directory.

Verify behavior against an explicit contract. A clean compile or simulator exit
is not functional proof.

## Fast path

Most requests are covered by one command of the bundled
`scripts/simrtl.py` (path relative to this skill's directory). It runs a whole
flow in one batch Vivado process: project open or generator Tcl, launch,
focused waveform, coverage via `xcrg`, SAIF/VCD windows, seeds with manifests
and replay, `create_testbench` replay with an output oracle. It copies every
run's logs and databases to `--out` under run-specific names and prints a
compact JSON verdict. Use it first; read the printed JSON instead of raw logs.
Then go to the MCP and the references below only for what it does not cover.
Each extra MCP round trip re-reads the whole conversation, so batch work into
one command where you can.

Required first step for every simulation request (the tool policy below puts
project work in the Vivado MCP): open or build the project once in your Vivado
MCP session and confirm it, in one `Vivado:vivado_execute` (after
`Vivado:vivado_start`), ending with `close_project` so the script can reopen it:

```tcl
# generator Tcl (repeat the block per fixture/build)
set ::EVAL_FIXTURE <f>; set ::EVAL_OUTPUT_DIR <abs-ws>/outputs/<case>/<f>; source -notrace <abs-ws>/<gen>.tcl; puts "top=[get_property TOP [get_filesets sim_1]] sim=[get_property target_simulator [current_project]]"; close_project
# existing project
open_project <abs>.xpr; puts "simsets=[get_filesets -filter {FILESET_TYPE==SimulationSrcs}] top=[get_property TOP [get_filesets <simset>]]"; close_project
```

Then run one command (`--xpr` takes the `.xpr` or the directory it was
created in; a comma list runs one build each):

```text
R = python3 scripts/simrtl.py sim --out outputs/<case> --xpr <project> --simset <simset>
run / prove PASS ............ R
diagnose, waveform evidence . R --wcfg <simset>_fail --trace      (<run>.values.md: values at clock edges)
assertions .................. R --assert-pass
UVM ......................... R --uvm-test <test>
seeds + replay .............. R --seeds <seed>,<seed> --replay-failures
coverage / closure .......... R --cov [--tb tb_<dut>_closure.sv]  (merged report of passing runs)
parameter builds ............ R with --xpr outputs/<case>/a,outputs/<case>/b --cov (build-compare.md lists elaborated-out scopes)
SAIF / VCD window ........... R --saif /tb/dut --window 100ns:240ns | --vcd "clk ..." --window A:B
port capture + replay ....... R --ports /tb/dut --expect <port>=<v> --replay-marker <NAME>
third-party prerequisites ... python3 scripts/simrtl.py backends --check questa[,vcs,...] --out outputs/<case> [--matrix]
recorded logs (triage) ...... python3 scripts/simrtl.py logs runs --out outputs/<case>
methodology report .......... python3 scripts/simrtl.py advise <bisect|reproducer|performance|headless|known-issue|new-backend|arbitration> --out outputs/<case>/report.md
```

`backends` never launches anything or changes `target_simulator`; report the
backend by the `name` it prints. For platform or release-support questions
also call `Vivado:vivado_doc_search` (UG973 compatible third-party tools) and
cite what it returns. The rest of
this file still governs classification, the approval gate, research and the
report. In the final `notes`, quote the first causal message verbatim with its
simulation time (or name the terminal marker), name the transcript and
waveform paths the summary lists under `files`, and state what the evidence
does not prove.

For recorded-log triage the JSON `notes` itself (not only `report.md`) must:
(a) quote the first causal message verbatim with its stage and log path;
(b) for every control, give its simulate-log path, its marker, and the values
its testbench checked (the `after ...` guard in `log-matrix.md`);
(c) name the trigger the variants isolate and the source facts behind it
(e.g. port dimensions, index form);
(d) say which reported results were not re-run here (other simulators, other
OS, customer claims);
(e) state the classification and that no workaround is verified without a
functional run.

## Tool policy

Use the Vivado MCP for interactive project inspection and Vivado Tcl that the
fast path does not cover:

- `Vivado:vivado_start` — start one Tcl session.
- `Vivado:vivado_execute` — open/configure the project and launch simulation.
- `Vivado:vivado_status` — poll a command that is still running.
- `Vivado:vivado_log_messages` — inspect Vivado errors and warnings.
- `Vivado:vivado_doc_search` — verify unfamiliar or release-sensitive commands,
  and find Answer Records, known issues, and documented workarounds for an
  observed failure signature.

Documentation research is part of diagnosis, not an optional extra. A tool
crash, unsupported construct, or library failure is not final until it has
been researched; see
[known-issue-research.md](known-issue-research.md).

Use the exact configured server prefix if it differs from `Vivado`. Never issue
two `Vivado:vivado_execute` calls concurrently on one session. Keep one project open
while iterating.

Find simulators, licenses and compiled libraries through `PATH`, the
simulator's environment variables (for example `VCS_HOME` or
`MGLS_LICENSE_FILE`), the project's compiled-library settings, and paths the
user supplied. Do not scan the file system (`find /`, home directories,
network mounts) for them; if they are not found that way, report the
prerequisite as missing.

## Read the relevant reference

`references/` and `scripts/` paths are relative to this skill's directory, not
the workspace. Do not search the file system for them.

- XSim execution and artifacts: [xsim.md](xsim.md)
- Iterative XSim coverage closure:
  [xsim-coverage-closure.md](xsim-coverage-closure.md)
- XSim UVM, seeded regression, assertions, and functional coverage:
  [xsim-advanced-verification.md](xsim-advanced-verification.md)
- XSim SAIF, VCD, and portable sub-design stimulus:
  [xsim-activity-capture.md](xsim-activity-capture.md)
- Questa setup and execution: [questa.md](questa.md)
- ModelSim: [modelsim.md](modelsim.md)
- VCS: [vcs.md](vcs.md)
- Xcelium: [xcelium.md](xcelium.md)
- Riviera-PRO: [riviera.md](riviera.md)
- Active-HDL: [activehdl.md](activehdl.md)
- Release snapshot and shared third-party rules:
  [other-vivado-simulators.md](other-vivado-simulators.md)
- Failure diagnosis: [diagnostics.md](diagnostics.md)
- XSim error signatures and what they mean:
  [xsim-error-signatures.md](xsim-error-signatures.md)
- Answer Records and documented workarounds:
  [known-issue-research.md](known-issue-research.md)
- Deciding design defect versus simulator defect:
  [cross-simulator-arbitration.md](cross-simulator-arbitration.md)
- Reducing a failure to a reportable case:
  [minimal-reproducer.md](minimal-reproducer.md)
- Host, library, and license preflight:
  [environment-preflight.md](environment-preflight.md)
- Finding the release that introduced a regression:
  [version-bisect.md](version-bisect.md)
- Compile, elaborate, and runtime performance:
  [simulation-performance.md](simulation-performance.md)
- Waveform evidence without a GUI:
  [headless-waveform.md](headless-waveform.md)
- Approval-gated waveform-first repair:
  [guided-interactive-repair.md](guided-interactive-repair.md)
- Adding a backend: [adding-a-simulator.md](adding-a-simulator.md)

Read only the references needed for this request. When the fast path covers
the request, run it instead of following a reference's manual Tcl procedure.

## Required inputs

Discover from the workspace and project before asking the user:

1. `.xpr` project, or explicit non-project source list
2. simulation set and top module
3. backend: default `xsim`; never silently substitute another backend
4. mode: behavioral, post-synthesis functional, post-implementation
   functional, or post-implementation timing
5. test contract and terminal PASS condition
6. bounded runtime or testbench-controlled termination
7. selected UVM test, seed list, plusargs, expected assertions, and coverage
   targets when advanced verification is requested
8. warm-up interval, measurement interval, hierarchy, and object set for SAIF,
   VCD, or portable-stimulus capture
9. requested artifacts: transcript, waveform, coverage, activity, generated
   testbench, and scripts

Ask only for inputs that cannot be discovered. For third-party simulators,
require the user-provided installation, license, and compiled-library paths.
Never guess site-specific paths.

## Preflight

1. Start or reconnect to a Vivado MCP session.
2. Open the `.xpr` project once, or load the non-project source list.
3. Confirm part, project mode, simulator, simulation sets,
   top modules, source files, compile order, include directories, defines, and
   language mix.
4. Confirm that the selected mode is available. Post-synthesis and
   post-implementation modes require the corresponding completed design stage.
5. Confirm a finite runtime. Do not use an unbounded `run all` unless the
   self-checking testbench has a proven watchdog and terminal `$finish`.
6. For a third-party backend, complete its reference preflight before changing
   `target_simulator`.
7. When a previous run failed to launch, linked against the wrong libraries, or
   the host is unfamiliar, run the environment doctor first. It is bundled at
   `scripts/sim_env_doctor.py` in this skill's directory; see
   [environment-preflight.md](environment-preflight.md).

Do not modify design RTL during preflight. Adding or editing verification-only
files is allowed when the user requested a testbench or coverage improvement.

## Common simulation workflow

Track this sequence for each target:

```text
- [ ] Discover project, simulation set, top, backend, mode, and contract
- [ ] Complete backend preflight
- [ ] Run a bounded simulation
- [ ] Read the complete transcript and identify the first failure
- [ ] Classify the result
- [ ] Research a documented cause and workaround for a tool or host failure
- [ ] Preserve transcript, waveform, scripts, and coverage artifacts
- [ ] Modify verification code only when requested
- [ ] Re-run the full applicable regression
- [ ] Report evidence, exclusions, and remaining risk
```

### 1. Discover

Use Vivado Tcl to query, not assume:

```tcl
get_filesets -filter {FILESET_TYPE == SimulationSrcs}
get_property TOP [get_filesets <simset>]
get_files -of_objects [get_filesets <simset>]
get_property target_simulator [current_project]
```

Update compile order before launch. Do not add duplicate sources already owned
by the project.

### 2. Run

Use the backend reference. Prefer a self-checking testbench and batch execution.
Close a prior active simulation before relaunching. A long-running
`launch_simulation` is blocking; allow an appropriate tool timeout and poll
status if the MCP reports it as still running.

### 3. Determine the verdict

Read the transcript and require all applicable gates:

- compile and elaboration succeeded;
- simulator returned normally;
- explicit terminal PASS marker or equivalent test-framework result exists;
- every required test completed;
- no unexpected error, fatal assertion, or timeout occurred.
- UVM summary has zero unexpected errors/fatals and the selected test completed;
- assertion results match the test plan without ignore or severity downgrade;
- randomized failures replay with the exact seed before being called
  reproducible;
- in timing mode, the simulator reported that SDF annotation succeeded. A
  skipped or ignored SDF means a zero-delay run, not a timing PASS.

Classify non-PASS as `design`, `testbench`, `environment`, `timeout`, or
`tool_defect` (reproducible tool failure on legal input) using
[diagnostics.md](diagnostics.md). Quote the first causal
error and its simulation time. Later cascaded errors are secondary.

### 4. Diagnose with focused evidence

Select clock/reset, transaction qualifiers, inputs, expected values, observed
outputs, and test status. Prefer interface and testbench signals; add internal
DUT signals only when necessary and available in the elaborated snapshot.

Do not edit RTL merely because a simulation failed. If the user asked only to
diagnose, stop after the evidence-backed diagnosis. If a DUT change is needed,
obtain approval and apply the smallest change that satisfies the contract.

#### Guided interactive repair (optional)

When the user requests step-by-step repair, waveform-first debugging, or an
approval pause before RTL changes, read
[guided-interactive-repair.md](guided-interactive-repair.md).
Preserve focused FAIL evidence, stop for an explicit repair/more-evidence/stop
decision, then—only after approval—make the smallest justified change, rerun
the affected test and applicable regression, and capture comparable PASS
evidence.

Do not enable this mode by default. Its pauses are inappropriate for unattended
CI, batch regressions, diagnosis-only requests, and pre-runtime failures with no
waveform.

### 5. Resolve environment and tool failures

An `environment` classification is a starting point, not an answer. When the
simulator crashed, rejected legal source, failed to link, or disagreed with
another tool, continue until the user has either a verified workaround or a
reportable defect:

1. Identify the signature and stage using
   [xsim-error-signatures.md](xsim-error-signatures.md).
2. Decide design versus simulator with
   [cross-simulator-arbitration.md](cross-simulator-arbitration.md)
   when another licensed backend is available.
3. Search for a documented cause and workaround with
   [known-issue-research.md](known-issue-research.md),
   then verify the workaround by rerunning the same bounded test.
4. If the failure is new since an upgrade, locate the boundary with
   [version-bisect.md](version-bisect.md).
5. If no workaround exists, reduce the case with
   [minimal-reproducer.md](minimal-reproducer.md) so the
   defect is reportable.

Report the Answer Record consulted, whether it applied, and what any
workaround changes. Never apply a vendor patch, alter the user's project, or
switch the requested backend without approval. Never claim a workaround works
without a rerun.

Slow but correct simulation is a performance question, not a failure; use
[simulation-performance.md](simulation-performance.md).
A broken viewer or missing display never blocks a verdict; use
[headless-waveform.md](headless-waveform.md).

### 6. Re-run

After any testbench or approved DUT change:

1. close the prior simulation;
2. rebuild from changed sources;
3. run the affected test;
4. run the full applicable regression;
5. compare the same evidence and acceptance gates.

Never claim success from a stale snapshot.

## Creating self-checking testbenches

When the user requests a testbench or coverage improvement:

- derive legal stimulus and expected behavior from requirements, interfaces,
  existing tests, and protocol documentation;
- reuse existing clocks, resets, agents, scoreboards, and project conventions;
- include deterministic checks with useful expected/actual messages;
- emit one unambiguous terminal marker such as `TEST_PASS`;
- include a watchdog that fails with the current phase and simulation time;
- avoid force/deposit/backdoor access unless the verification plan requires it;
- keep tests deterministic by default; record seed and plusargs when randomized;
- never weaken an assertion, checker, or acceptance gate to obtain PASS.

Generated testbenches are verification artifacts, not proof until they have
been run successfully.

## XSim coverage closure

When coverage is requested, read
[xsim-coverage-closure.md](xsim-coverage-closure.md) and
iterate:

1. run the unchanged regression and export baseline statement, branch,
   condition, and toggle coverage;
2. identify a reachable uncovered behavior and tie it to a requirement;
3. add focused stimulus plus a self-checking observation;
4. rerun the targeted test and then the full regression;
5. export a new uniquely named coverage database/report and compare deltas;
6. repeat while coverage improves and regression correctness is retained.

Do not game coverage. Never exclude reachable logic, remove checks, add DUT
backdoors, or change RTL solely to increase a metric. Separate untested,
unreachable, and elaborated-out items. Ask before adding exclusions or changing
RTL. Stop at the requested target, a justified plateau, or a blocking
requirement/environment gap.

## XSim advanced verification

For UVM, constrained-random, assertion-aware, or functional-coverage requests,
read [xsim-advanced-verification.md](xsim-advanced-verification.md).

- create one manifest per test and seed;
- select the UVM test and record every plusarg;
- preserve exact failing seeds and replay before diagnosis;
- report immediate, concurrent, UVM, and checker results separately;
- treat functional bins and crosses as requirement-linked targets;
- merge only compatible passing coverage databases;
- never ignore assertions/coverage or downgrade error/fatal severity to pass.

## XSim activity and portable stimulus

For SAIF, VCD, or sub-design replay, read
[xsim-activity-capture.md](xsim-activity-capture.md).

- separate reset/warm-up from the bounded measurement interval;
- log only the intended hierarchy or object set;
- close and verify every activity file;
- require the simulation's independent functional PASS;
- treat `create_testbench` output as scaffolding until it has an oracle,
  watchdog, terminal marker, and successful replay;
- do not claim power signoff or cross-simulator portability from artifact
  generation alone.

## Result report

Report:

```text
Backend and version:
Project / simulation set / top:
Mode and runtime:
Functional verdict: PASS | FAIL | INCONCLUSIVE
Failure class and first causal message:
Cross-simulator matrix (when arbitrated):
Known issue / Answer Record and whether it applied:
Workaround, verification result, and what it changes:
Tests run / passed / failed:
Coverage before -> after (if requested):
UVM test / seeds / replay:
Assertions passed / failed / disabled / unsupported:
Activity window and hierarchy:
Artifacts: transcript, waveform, coverage, SAIF, VCD, generated testbench/scripts
Changes made:
Not verified / exclusions / blockers:
```

Do not claim timing closure, CDC correctness, formal proof, equivalence,
security, safety compliance, or hardware validation from simulation alone.
