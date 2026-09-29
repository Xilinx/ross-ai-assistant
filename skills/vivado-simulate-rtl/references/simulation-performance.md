<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Simulation Performance

Answer "where does the time go" with measurements, then reduce the dominant
stage. Do not speculate about simulator speed.

## Measure first

Record wall time separately for each stage:

- analysis: `xvlog` and `xvhdl`;
- elaboration: `xelab`;
- runtime: `xsim` up to the terminal marker.

Report the dominant stage and its share. An elaboration problem and a runtime
problem have no remedies in common, and most "simulation is slow" reports are
one or the other, not both.

Also record simulated time reached, wall time per simulated microsecond, peak
memory, host CPU count, and whether the filesystem is local or networked.
A network-mounted working directory can dominate everything else.

## Common causes by stage

Analysis: recompiling unchanged sources every run, very large generated files,
or IP being regenerated.

Elaboration: heavy debug level, deep parameterized hierarchy, large
concatenations and aggregates, and full-design optimization being redone each
time. Incremental elaboration helps only when the snapshot inputs are stable.

Runtime: logging every signal into the waveform database, coverage enabled on
the whole design, an unnecessarily long runtime bound, zero-delay or
delta-cycle churn, verbose transcript output, and DPI or SystemC boundary
crossings in the inner loop.

## Levers, with their cost

- Reduce debug visibility. Faster, but less observable; do not do this on a
  run whose purpose is diagnosis.
- Log a focused signal set instead of the whole design. Faster and usually
  better evidence.
- Disable coverage for pure performance runs, then re-enable for the coverage
  run. Never report coverage from a run that had it disabled.
- Use a runtime bound justified by the test, not a round number.
- Exploit multi-threading where the release supports it.

Every lever changes what the run proves. State which ones were active when
reporting a functional verdict.

## Comparing simulators

A cross-simulator timing comparison is only meaningful with identical debug
level, logging scope, coverage settings, optimization, source set, and host.
Otherwise it measures configuration, not the tool. Run on the same machine,
repeat at least twice, and report the spread.

If XSim is genuinely slower for a specific construct, reduce to a small case
and research it with
[known-issue-research.md](known-issue-research.md) before reporting it as a
product gap.

## Prohibited

- Claiming a speedup from a single unrepeated run.
- Comparing a debug-enabled run against a debug-disabled run.
- Presenting a faster configuration as equivalent when it captures less.

## Report

```text
Host / CPUs / filesystem:
Analysis / elaborate / runtime wall time:
Dominant stage and share:
Simulated time reached, wall time per simulated unit:
Peak memory:
Settings in effect (debug, logging, coverage, threads, runtime bound):
Change applied and measured effect (repeat count and spread):
What the faster configuration no longer proves:
```
