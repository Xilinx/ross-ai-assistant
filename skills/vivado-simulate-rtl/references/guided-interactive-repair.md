<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Guided Interactive Repair

Use this optional mode when the user requests waveform-first debugging,
step-by-step repair, approval before RTL changes, or a teaching workflow.
Do not enable it merely because a simulation fails.

## Fast path

This path is XSim only. For Questa or another simulator, keep the steps
below but run `simrtl.py run --backend <sim> --xpr <project.xpr> --simset
<simset> --name <simset>_fail --out outputs/<case>` (it drives
`launch_simulation`, keeps the logs and `<simset>_fail.wlf`), and report
that simulator as `backend`.

After opening and confirming the project in your Vivado MCP session (see
`SKILL.md`, then `close_project`), one command produces all FAIL evidence:

```bash
python3 <skill-dir>/scripts/simrtl.py sim --out outputs/<case> --xpr <project.xpr> \
  --simset <simset> --wcfg <simset>_fail --trace
```

It keeps `<simset>.simulate.log`, a focused `<simset>.wdb`, the
`<simset>_fail.wcfg` wave configuration, and `<simset>.values.md`: the logged
signal values at each clock edge up to the first failure. The printed JSON
gives `pass_count` (tests passed before the failure) and the first failure
verbatim with its time. Diagnose from those; read the DUT only after approval.
After an approved one-line repair, rerun with
`--name <simset>_pass --wcfg <simset>_pass` for comparable PASS evidence. The
script restores every project property it touched and the active simulation
set.

## Applicability

Use guided repair when:

- a runtime failure has a live snapshot or usable waveform;
- the user wants diagnosis followed by a possible DUT repair;
- stopping for evidence review will not disrupt unattended automation.

Do not require this mode for analysis, elaboration, license, or environment
failures that have no runtime waveform. Do not use its pauses for unattended CI,
batch regressions, or a diagnosis-only request.

Workspace-specific restrictions—protected directories, target order, forbidden
source inspection, or display requirements—remain workspace policy. They are
not universal simulation rules.

## Session discipline

Keep one project open and issue one Vivado Tcl command at a time. Before every
relaunch:

```tcl
catch {close_sim -force}
```

If an earlier snapshot remains open, select and close it explicitly:

```tcl
current_sim <simulation-name>
close_sim -force
```

Confirm resulting state instead of assuming a repeated command executed.

## Workflow

### 1. Establish the contract

Record the target, mode, test, expected terminal result, runtime bound, and
signals implied by the requirement. Run the unchanged test and read the
complete transcript.

Classify the first causal failure as design, testbench, environment, or
timeout. Do not expose or modify RTL merely because a simulator reported an
error.

### 2. Build FAIL evidence

For a runtime failure, select:

1. clock and reset;
2. transaction qualifiers;
3. stimulus and reference value;
4. observed DUT result;
5. checker and terminal status;
6. a minimal internal signal only when boundary evidence is insufficient.

Discover actual paths from the live snapshot. Do not use recursive waveform
capture by default. Create a uniquely named FAIL wave configuration when the
backend supports it (XSim `.wcfg`, Questa/ModelSim `.wlf`, and so on), and
preserve a signal manifest even when the run is headless. When the target
simulator is not XSim, follow that backend's reference
([questa.md](questa.md), [modelsim.md](modelsim.md), …) for launch,
transcript location and waveform database; the diagnosis and approval-gate
steps below are the same.

Record:

```text
Tests completed before failure:
First causal message and time:
Failure classification:
Requirement implicated (exactly one: the property the failing check asserts; always reset if no TEST_PASS yet, whatever property the message names; after that, only when the failing message says "model", the test after the last TEST_PASS: N passed tests means requirement N+1 when tests follow the numbered requirements, whatever words the message or the fix uses; otherwise a message naming a property maps to that property's requirement; the `requirement` line from `S sim` applies these rules in this order):
FAIL waveform or database:
Signal manifest:
What the evidence proves:
```

### 3. Pause for the user

Stop before reading or changing DUT RTL. Offer these decisions through the
product's structured question mechanism when available:

- **Repair** — inspect the implicated RTL and apply the smallest justified fix.
- **More evidence** — add only signals or transcript context needed to resolve
  uncertainty.
- **Stop** — preserve evidence and make no source change.

In a non-interactive run, ask in the final answer and still end it with the
requested JSON: the FAIL verdict, `dut_modified: false`, and the three
choices in `notes`.

Do not treat silence or an unrelated reply as repair approval. If the user has
already given the decision for this target, or for every target, act on it
without asking again; still stop at any gate they have not answered.

### 4. Repair only after approval

Inspect only sources needed to test the evidence-backed hypothesis. A
testbench may be inspected when the failure classification or user request
requires it; do not adopt anti-spoiler restrictions unless the workspace
defines them.

Make the smallest requirement-driven change: one changed line that fixes the
wrong expression in place, with no new signals, rewired uses, added comments,
renames or reformatting. If the same term is missing at every use of a
signal, the defect is in the line that defines it (`wire sof = ...;`): edit
that line, not each use. If that edit does not pass,
restore the line before trying another so the final diff stays one line. Do
not refactor, weaken a checker, change backend, lower severity, or alter
expected behavior to obtain PASS.

### 5. Re-run and build PASS evidence

Close the prior simulation and rebuild from changed sources. Run the affected
test, then the applicable regression. Require the normal functional-verdict
gates from `SKILL.md`.

Create PASS evidence using the same boundary signals and ordering as the FAIL
evidence so the repaired event is directly comparable. If exact in-memory
side-by-side views are unavailable, preserve separate FAIL and PASS artifacts.

### 6. Report and stop

Report:

```text
Target and requirement:
First failing check (verbatim from the FAIL log):
Failure classification:
FAIL evidence:
Approved repair:
Files changed:
Affected test result:
Regression result:
PASS evidence:
Artifacts:
Not verified:
```

Ask before moving to another target. Do not turn approval for one repair into
approval for unrelated changes.

Before writing a final report that covers several targets, re-read each saved
FAIL log (or the `first_failure` message `S sim` printed for it) and quote
its first failing check verbatim in that target's own section, with its
count of passed tests before that failure. Do not paraphrase it or
reconstruct it from memory.

The final chat message is the report: a file such as `report.md` may hold a
copy, but the message itself has one `### sim_bugN` (target) section per
target with the line `Requirement N: <title from requirements.md>` and the
quoted first `CHECK_FAIL:` line. A summary table alone is not the report.
`simrtl.py findings --out outputs/<case>` writes those sections from the
saved FAIL logs; paste them and fill in each requirement line.
