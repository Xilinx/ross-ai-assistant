<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Simulation Diagnostics

Classify before editing. A simulator failure is not automatically a DUT defect.

## Triage order

1. Find the first causal error in the complete stage log.
2. Identify the stage: analysis, elaboration, runtime, or artifact export.
3. Record simulation time, test/phase, seed, plusargs, mode, and backend.
4. Classify as design, testbench, environment, timeout, or tool defect.
5. Preserve evidence before rerunning.

Later errors often cascade from the first one.

## Failure classes

### Environment

Typical evidence:

- executable or license unavailable;
- compiled simulation library missing, stale, or mapped incorrectly;
- source/include file missing;
- unsupported simulator, host, language, or encrypted IP;
- wrong simulation top or compile order;
- netlist/SDF mismatch;
- generated script cannot find its tools.

Action: report the exact prerequisite or mismatch. Do not edit RTL or silently
switch simulators.

### Testbench

Typical evidence:

- stimulus violates the interface protocol;
- checker/reference model is inconsistent with requirements;
- reset polarity or timing is wrong;
- testbench races the DUT at a clock edge;
- watchdog is too short for legal latency;
- test never observes the response it drove;
- test declares PASS without checking outputs.

Action: tie the defect to the verification contract, make the smallest
verification-only correction when requested, and rerun all affected tests.

### Design

Required evidence:

- legal stimulus reached the DUT;
- the expected result follows from a stated requirement/reference model;
- observed output diverges at a specific event/time;
- checker and environment are credible;
- focused waveforms support the same conclusion.

Action: if the request is diagnosis-only, stop. Otherwise ask before modifying
RTL and apply the smallest requirement-driven fix.

### Timeout

Typical evidence:

- no terminal PASS/FAIL condition;
- simulation remains active beyond the justified bound;
- handshake or state machine makes no progress;
- testbench omitted `$finish` or a watchdog;
- an external simulator waits in GUI/interactive mode.

Action: preserve the transcript and waveform, terminate safely, identify the
last completed phase/event, and classify the likely design/testbench/environment
cause only when evidence supports it.

### Tool defect

Required evidence:

- the input is legal and the environment is correct (supported versions,
  matching libraries, fresh netlist/SDF);
- the tool crashes, rejects, or mis-writes that input reproducibly, for
  example a simulator internal error or a Vivado-generated file the simulator
  cannot parse;
- a rerun of the same step from the same input shows the same result.

Action: report the tool, version, step and reproduction command. Do not edit
RTL or Vivado-generated files to force a PASS; a workaround may be tried only
in a separate scratch directory, labeled as diagnostic, and never produces the
reported verdict.

## Stage-specific checks

### Analysis / compile

Check:

- language and file type assignment;
- source/library compile order;
- include directories and macro definitions;
- VHDL standard and SystemVerilog enablement;
- duplicate modules/packages;
- syntax error's first location.

### Elaboration / optimization

Check:

- top module/entity and architecture;
- missing design unit or library mapping;
- parameter/generic and port mismatch;
- unresolved `glbl` or primitive models;
- encrypted IP support;
- optimized-away debug signals;
- snapshot name and mode.

### Runtime

Check:

- first assertion severity and message;
- expected versus observed values;
- clock/reset and valid/ready qualifiers;
- unknown/high-impedance propagation;
- seed and randomization failure;
- watchdog and terminal marker;
- simulator error/fatal counts.

### Post-implementation timing

Check:

- implementation and generated netlist freshness;
- SDF and netlist originate from the same run;
- correct annotation scope;
- timing checks and pulse rejection messages;
- reset/recovery behavior under delays.

A timing verdict needs the simulator's own annotation-success message: XSim
`[XSIM 43-3452] SDF backannotation was successful`, Questa/ModelSim
`(vsim-3587) SDF Backannotation Successfully Completed`, VCS
`SDF annotation begin` followed by `SDF annotation end` (not
`SDF annotation aborted`), Xcelium `Annotation completed successfully`. Some
simulators give up on an SDF with only warnings and still run the netlist
with zero delay: Xcelium `*W,SDFCNC ... skipping annotation`, and VCS aborts
annotation after repeated `SDFCOM_NNTC` negative setup/hold errors unless
elaborated with `+neg_tchk`. Such a run is not a timing result:
report the annotation failure, never a timing PASS, even when `TEST_PASS`
printed.

An SDF parse error at the end of the file (Questa `syntax error, unexpected
$end`, Xcelium `*E,SDFIRE` on the last line) usually means the SDF Vivado
wrote is structurally incomplete. Compare its opening and closing parenthesis
counts. If they differ, re-run `write_sdf -mode timesim` from the routed
checkpoint to show it is reproducible, and classify it as `tool_defect` in
Vivado's SDF writer, not a design or environment failure. Handle any patched
SDF as described under Tool defect.

Do not treat timing simulation as static timing closure or equivalence proof.

### Coverage export

Check:

- coverage was enabled before elaboration;
- `write_xsim_coverage` ran before closing XSim;
- database and report paths exist;
- expected DUT hierarchy is included;
- raw and exclusion-adjusted reports are not confused;
- merged runs use compatible design/elaboration configurations.

## Focused waveform recipe

Include:

1. clock and reset;
2. transaction qualifiers and control;
3. stimulus data;
4. reference/expected value;
5. DUT result and status;
6. checker state and terminal status;
7. one or two internal signals only if boundary evidence is insufficient.

Avoid recursive capture by default.

## Diagnosis report

```text
Backend / mode:
Stage:
First causal message:
Simulation time / test phase:
Failure class:
Requirement:
Evidence:
Likely root cause:
Recommended next action:
Artifacts:
```

State uncertainty directly. Use `INCONCLUSIVE` when the contract, stimulus,
logs, or observability are insufficient.
