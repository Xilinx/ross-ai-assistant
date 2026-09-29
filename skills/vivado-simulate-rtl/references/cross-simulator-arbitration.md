<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Cross-Simulator Arbitration

Decide whether a failure belongs to the design or to the simulator by running
the same test on more than one backend. This is an additional experiment, not
a backend substitution.

## When to arbitrate

- XSim crashes, hangs, or reports a fatal on source that looks legal.
- A result disagrees with synthesis, hardware, or the user's expectation.
- A construct is rejected as unsupported.
- An IP or example design fails to simulate.

Do not arbitrate merely because a self-checking test failed honestly. A
reproducible functional mismatch with a credible checker is a design
investigation first.

## Preconditions

Arbitration needs a second simulator that the site actually provides:

- user-supplied installation path and license configuration;
- version-matched libraries from `compile_simlib` for that simulator;
- identical sources, include paths, defines, generics, top, timescale, and
  seed;
- the same bounded runtime and the same terminal marker.

Read the backend reference before running anything. Any backend that is
unavailable, unlicensed, or unsupported on this host is an `UNVERIFIED` cell,
never a pass and never a silent omission.

## Procedure

1. Export scripts per simulator with the canonical identifier:

```tcl
export_simulation -simulator <xsim|questa|modelsim|vcs|xcelium|riviera|activehdl> \
  -directory <absolute-output-dir> \
  -lib_map_path <absolute-compiled-library-dir>
```

2. Run each generated compile, elaborate, and simulate flow unchanged.
3. Record, per backend: version, verdict, first causal message and stage, and
   the transcript path.
4. Build the matrix before interpreting any single cell.

## Interpretation

| Pattern | Reading |
|---|---|
| Every backend fails the same way | Design or testbench defect. Diagnose normally. |
| Only XSim fails | Likely simulator defect. Reduce, then research. |
| Only one third-party backend fails | That backend's setup or defect; check libraries and version support first. |
| Verdicts agree, values differ | Suspect timescale/resolution, X-propagation, optimization, uninitialized state, or a race. |

A value difference caused by a race or by reliance on unspecified scheduling
is a testbench defect, even though the simulators disagree. Fix the test, do
not file a tool bug.

Majority vote is evidence, not proof of LRM correctness. When you claim one
simulator is wrong, cite the LRM, a user guide, or an Answer Record found via
[known-issue-research.md](known-issue-research.md).

## Prohibited

- Switching the user's requested backend because another one passes.
- Reporting a backend as passing when its license or host blocked the run.
- Changing sources, defines, or runtime between backends and still calling the
  comparison a controlled experiment.
- Concealing that a "workaround" backend produces different coverage,
  waveform, or performance characteristics.

## Report

```text
Design / simset / top / mode:
Contract and terminal marker:
Runtime bound / seed / defines:

| Simulator | Version | Verdict | First causal message | Log |
|---|---|---|---|---|

Reading:
Cited source for the correctness claim:
Recommended next step:
Unverified cells and why:
```

## Official sources

- [UG900 supported simulators](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Supported-Simulators)
- [UG900 export_simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/export_simulation)
- [UG973 compatible third-party tools](https://docs.amd.com/r/en-US/ug973-vivado-release-notes-install-license/Compatible-Third-Party-Tools)
