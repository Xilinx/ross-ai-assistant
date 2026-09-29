<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# XSim Coverage Closure

Use this workflow to improve code and functional coverage without weakening
verification. XSim supports statement, branch, condition, and toggle code
coverage plus SystemVerilog covergroups, coverpoints, bins, transitions, and
crosses. Read [xsim-advanced-verification.md](xsim-advanced-verification.md)
for UVM, seeded regressions, and assertion-aware verdicts.

## Contents

- Define the goal
- Capture a baseline
- Read and classify gaps
- Write focused tests
- Iterate and merge
- Close functional coverage
- Stop and report
- Integrity rules

## 1. Define the goal

Record before running:

- DUT hierarchy and source scope
- required code-coverage types
- functional covergroups, points, crosses, and target bins
- target metric or closure criterion for each coverage model
- applicable tests and required PASS markers
- permitted testbench files
- exclusions already approved by the verification plan

Coverage percentage is not the contract. Functional coverage must trace to the
test plan; code coverage traces to exercised implementation structure. Tie
every new test to reachable behavior and an observable expected result.

## 2. Capture a baseline

Preserve the existing regression and baseline testbench files unchanged.
Assign unique names so each run is auditable:

```tcl
set simset [get_filesets <simset>]
set_property target_simulator XSim [current_project]
set_property xsim.elaborate.coverage.name baseline $simset
set_property xsim.elaborate.coverage.dir <absolute-artifact-dir>/baseline $simset
set_property xsim.elaborate.coverage.type sbct $simset
set xopts [get_property xsim.simulate.xsim.more_options $simset]
regsub -all {(^|[[:space:]])-onfinish[[:space:]]+(quit|stop)} $xopts {} xopts
set new_xopts [string trim "$xopts -onfinish stop"]
set_property -name XSIM.SIMULATE.XSIM.MORE_OPTIONS -value $new_xopts -objects $simset
catch {close_sim -force}
launch_simulation -simset $simset -mode behavioral
```

Before closing the live simulation, persist the in-memory coverage:

```tcl
file mkdir <absolute-artifact-dir>/baseline-report
write_xsim_coverage \
  -cov_db_name baseline \
  -cov_db_dir <absolute-artifact-dir>/baseline

export_xsim_coverage \
  -cov_db_name baseline \
  -cov_db_dir <absolute-artifact-dir>/baseline \
  -output_dir <absolute-artifact-dir>/baseline-report \
  -report_format all \
  -open_html false
```

Use `sbct` for statement, branch, condition, and toggle coverage. If the
project accepts descriptive values, `all` is equivalent. Query the installed
release before setting it:

```tcl
list_property_value XSIM.ELABORATE.COVERAGE.TYPE $simset
```

Some releases return an empty list for this string property. In that case use
the documented `sbct`, set it, and verify the readback with
`get_property XSIM.ELABORATE.COVERAGE.TYPE $simset`.

`write_xsim_coverage` must run while the simulation session is active. Closing
first discards unsaved in-memory data. In Vivado's integrated XSim flow, a
testbench `$finish` ends runtime but leaves the simulation snapshot loaded, so
write coverage immediately after `launch_simulation` returns and before
`close_sim`. XSim documents `-onfinish stop` as the explicit mechanism; preserve
any existing `xsim.simulate.xsim.more_options` when adding it rather than
blindly replacing project options.

Create every coverage database and report parent directory before invoking
XSim/export. `export_xsim_coverage` can print final scores even after failing to
create a missing output directory, so require the report files to exist.

Require the baseline regression to pass. Do not optimize coverage from a
failing baseline until the failure is classified and resolved.

## 3. Read and classify gaps

Read `<output-dir>/codeCoverageReport/dashboard.txt` for aggregate code
metrics and the functional-coverage text/HTML sections for covergroup,
coverpoint, cross, and bin counts. Use per-file reports for source-level code
inspection. The code aggregate includes testbench and support modules unless
scope is constrained; report DUT-scoped metrics rather than improving a score
with testbench-only activity. For each material uncovered item, record:

```text
Source / hierarchy:
Coverage type:
Uncovered line, branch, condition outcome, or toggle:
Reachability: reachable | unreachable | elaborated-out | unknown
Requirement:
Stimulus needed:
Observable check:
```

For functional coverage, also record:

```text
Covergroup instance:
Coverpoint or cross:
Target bin:
Current hit count:
Test-plan requirement:
```

Prioritize:

1. reachable safety/error handling and protocol corner cases;
2. reachable branches and condition outcomes tied to requirements;
3. reset, enable, backpressure, overflow/underflow, saturation, and recovery;
4. meaningful control/status toggles;
5. incidental datapath toggles.

Do not infer reachability solely from a zero count. Check parameters,
generates, constants, protocol legality, and design requirements.

## 4. Write focused self-checking tests

Reuse the existing verification infrastructure, but do not overwrite the
baseline testbench. Add a new test case, sequence, or testbench file to the
simulation set so baseline behavior remains reproducible.

When independent testbench tops are required, give each one a simulation set:

```tcl
create_fileset -simset sim_cov_01
add_files -fileset sim_cov_01 <absolute-new-testbench-path>
set_property top <new-testbench-top> [get_filesets sim_cov_01]
update_compile_order -fileset sim_cov_01
```

Run each applicable simulation set separately, persist its uniquely named
coverage database, require functional PASS, then merge the compatible
databases for the regression report.

Every added scenario must:

- drive legal stimulus to the uncovered behavior;
- assert the expected externally observable result;
- preserve existing assertions and scoreboards;
- include a deterministic terminal result and watchdog;
- document random seed and plusargs if randomized;
- avoid hierarchy force/deposit unless explicitly required by the test plan.

Do not edit synthesizable RTL solely for coverage. If a coverage gap exposes a
design defect or genuinely dead code, present the evidence and ask before an
RTL change.

## 5. Iterate

For iteration `N`:

1. create a unique database and report directory such as `iter-01`;
2. run the focused test;
3. run every applicable existing regression test;
4. require all tests to pass;
5. save coverage before `close_sim`;
6. export text and HTML;
7. compare each metric and the specific targeted item to the previous run.

Example persistence:

```tcl
write_xsim_coverage \
  -cov_db_name iter-01 \
  -cov_db_dir <absolute-artifact-dir>/iter-01

export_xsim_coverage \
  -cov_db_name iter-01 \
  -cov_db_dir <absolute-artifact-dir>/iter-01 \
  -output_dir <absolute-artifact-dir>/iter-01-report \
  -report_format all \
  -open_html false
```

For a regression with separate coverage databases, merge during export:

```tcl
export_xsim_coverage \
  -cov_db_name test-reset -cov_db_dir <artifact-dir>/run-reset \
  -cov_db_name test-saturate -cov_db_dir <artifact-dir>/run-saturate \
  -merge_dir <artifact-dir>/merged \
  -merge_db_name regression-01 \
  -output_dir <artifact-dir>/regression-01-report \
  -report_format all \
  -open_html false
```

Use exact paths and names returned by the run. Verify that database and report
files were created; a Tcl command returning without an exception is not enough.
The exported report can contain both code and functional coverage from the
same XSim database.

## 6. Functional coverage closure

Close named requirements, not only an aggregate score:

1. capture the baseline hit count for each target bin or cross;
2. confirm that the construct is supported by the installed XSim release;
3. derive legal stimulus from the requirement;
4. add an assertion, scoreboard check, or reference-model observation;
5. rerun the focused test and the full applicable regression;
6. require PASS before admitting the database to the merge;
7. verify that the specific target count increased;
8. preserve bins that remain unreachable or blocked with evidence.

Do not:

- sample a covergroup artificially outside the behavior being measured;
- broaden a bin merely to count an easier event;
- delete or ignore an uncovered legal bin;
- merge failed negative tests into passing-regression coverage;
- merge databases with different source revisions, elaborated parameters,
  covergroup schemas, XSim versions, or coverage settings.

For randomized closure, record each test and `-sv_seed`. Merge only compatible
passing seeds and replay every failure with the exact manifest before
classifying it as reproducible.

## 7. Plateau and stop conditions

Continue only when the preceding iteration:

- retains full regression correctness;
- closes its targeted item or produces a useful diagnosis;
- makes measurable progress toward the agreed goal.

Stop when:

- the requested target is met;
- remaining items are proven unreachable or elaborated out;
- the same metric and uncovered set do not improve for two focused iterations;
- requirements, stimulus models, licenses, resources, or observability block
  further progress;
- the next step would require RTL changes or exclusions without approval.

## 8. Exclusions

Do not create a coverage-exclusion file by default. An exclusion requires:

1. evidence that the item is unreachable, generated out, third-party code, or
   outside the verification scope;
2. a written rationale tied to configuration or requirements;
3. explicit user approval;
4. separate reporting of raw and exclusion-adjusted metrics.

Apply an approved file only during report generation:

```tcl
export_xsim_coverage \
  -cov_db_name <name> \
  -cov_db_dir <dir> \
  -ccExclusionFile <approved-file> \
  -output_dir <report-dir> \
  -report_format all \
  -open_html false
```

Never exclude reachable code merely to meet a target.

## Final report

```text
Coverage scope:
Regression result:
Metric       Baseline   Final   Delta
Statement:
Branch:
Condition:
Toggle:
Functional covergroup:
Target coverpoints / crosses / bins:
New tests and requirements exercised:
Remaining reachable gaps:
Unreachable/elaborated-out items:
Approved exclusions (raw and adjusted metrics):
Coverage databases and reports:
```

## Official sources

- [UG900 code coverage](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Code-Coverage-Support)
- [UG900 XSim executable options](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/xsim-Executable-Options)
- [UG900 write_xsim_coverage](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/write_xsim_coverage)
- [UG900 export_xsim_coverage](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/export_xsim_coverage)
