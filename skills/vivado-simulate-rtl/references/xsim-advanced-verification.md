<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# XSim Advanced Verification

Use this reference for UVM, constrained-random regressions, SystemVerilog
assertions, and functional coverage. Use
[xsim-coverage-closure.md](xsim-coverage-closure.md) for the shared coverage
database and closure workflow.

## Contents

- Preflight
- UVM
- Seeded random regression
- Assertion-aware verdicts
- Functional coverage
- Run manifests and merging

## 1. Preflight

Record:

```text
Vivado/XSim version:
Simulation set and top:
UVM version and selected test:
Seed list and plusargs:
Assertion constructs and expected failures:
Functional covergroups and closure targets:
Runtime/watchdog:
Artifact root:
```

Query the installed release before adding options:

```tcl
set simset [get_filesets <simset>]
list_property $simset
get_property xsim.compile.xvlog.more_options $simset
get_property xsim.elaborate.xelab.more_options $simset
get_property xsim.simulate.xsim.more_options $simset
```

XSim implements subsets of SystemVerilog assertions, constraints, and
functional coverage. Check the current UG900 **Test Bench Feature** table when
a construct fails analysis or elaboration. Do not rewrite an unsupported
checker into a weaker checker merely to make it compile.

Reject options that hide verification evidence:

- `-ignore_assertions`
- `-ignore_coverage`
- `-ignore_feature assertion`
- `-ignore_feature coverage`
- error/fatal severity downgrade options

If the project already contains any of these, report them and classify the run
as `INCONCLUSIVE` unless the verification plan explicitly requires them.

## 2. UVM

Vivado-integrated XSim provides a precompiled UVM 1.2 library by default. Do
not install or compile a second UVM library for an integrated project run.

For standalone XSim, pass `-L uvm` to both `xvlog` and `xelab`. UVM 1.1 is
available only when explicitly required; pass `-uvm_version 1.1` during
compile and elaboration. Preserve existing options when setting project
properties:

```tcl
set copt [get_property xsim.compile.xvlog.more_options $simset]
set eopt [get_property xsim.elaborate.xelab.more_options $simset]
set_property -name XSIM.COMPILE.XVLOG.MORE_OPTIONS \
  -value [string trim "$copt -uvm_version 1.1"] -objects $simset
set_property -name XSIM.ELABORATE.XELAB.MORE_OPTIONS \
  -value [string trim "$eopt -uvm_version 1.1"] -objects $simset
```

Do not add the 1.1 switches for the normal 1.2 flow.

Select one test explicitly in each run. XSim exposes simulator plusargs through
`-testplusarg`; verify the installed syntax with `xsim -help` and preserve the
existing simulation options. The effective argument must be visible to UVM as
`+UVM_TESTNAME=<test>`.

A UVM PASS requires:

1. the intended test name appears in the transcript;
2. every required phase and scoreboard completes;
3. the explicit terminal PASS contract appears;
4. the UVM report summary has zero `UVM_ERROR` and zero `UVM_FATAL`;
5. no unexpected HDL assertion failure or timeout occurs.

Make the terminal scoreboard message include the expected and observed final
state; a generic "scoreboard passed" string is weaker evidence.

`UVM_INFO` output, dropped objections, or normal simulator exit alone are not
PASS. Preserve the complete transcript because an early UVM error can precede
a clean-looking final message.

## 3. Seeded random regression

Use a declared finite seed list. Deterministic default regressions and wider
random exploration are different jobs; do not silently replace one with the
other.

For each seed:

1. create a unique run directory and manifest;
2. preserve existing XSim options;
3. set exactly one `-sv_seed <integer>`;
4. set the selected test and other plusargs;
5. close the prior XSim process and launch a fresh run with the same elaborated
   design and bounded runtime;
6. classify the transcript and assertion/UVM summary;
7. persist waveform and coverage before closing;
8. never reuse the run directory for a retry.

When regression coverage is requested, enable a uniquely named database for
each passing seed. Record its exact path and byte size. Failed-seed coverage is
diagnostic only and stays out of the passing merge.

Example option construction:

```tcl
set old [get_property xsim.simulate.xsim.more_options $simset]
regsub -all {(^|[[:space:]])-sv_seed[[:space:]]+[^[:space:]]+} \
  $old {} cleaned
set runopts [string trim \
  "$cleaned -sv_seed <seed> -testplusarg UVM_TESTNAME=<test>"]
set_property -name XSIM.SIMULATE.XSIM.MORE_OPTIONS \
  -value $runopts -objects $simset
catch {close_sim -force}
launch_simulation -simset $simset -mode behavioral
```

Restore the original property after the regression. Do not accumulate stale
seeds or test names across runs. Do not rely on `relaunch_sim` alone after
changing `-sv_seed`; a live simulator can retain its prior runtime options.
Confirm the effective seed in the generated XSim command and run manifest.

On failure:

- preserve the failing transcript and focused WDB separately;
- do not merge its coverage into the passing-regression signoff database;
- rerun the exact test, seed, plusargs, sources, parameters, and XSim version;
- require the same random choice and first causal failure;
- call the failure reproducible only after that replay.

## 4. Assertion-aware verification

Inventory immediate assertions, concurrent assertions, and UVM/reporting
checks separately. Record which failures are expected by a negative test.

When concurrent assertion pass accounting is requested, preserve the existing
elaboration options and add:

```tcl
set eopt [get_property xsim.elaborate.xelab.more_options $simset]
set_property -name XSIM.ELABORATE.XELAB.MORE_OPTIONS \
  -value [string trim "$eopt -report_assertion_pass"] -objects $simset
```

Remove or restore the added option after the run. Re-elaborate after changing
elaboration options; a restart of the old snapshot is insufficient.

Read the complete transcript and report:

```text
Assertion label / source:
Kind: immediate | concurrent | UVM/checker
Status: pass | fail | disabled | unsupported | not observed
First failure time:
Expected by test plan: yes | no
Message and sampled context:
```

Persist this classification as an assertion-summary artifact and list the
transcript and focused WDB paths with byte sizes.

An expected negative-test assertion is evidence that the checker fired, but
the simulation's functional verdict is still `EXPECTED_FAIL`, not ordinary
PASS. Unexpected assertion failure is `FAIL`. A compile-time unsupported
construct is an environment/capability result, not an assertion pass.

Never:

- infer all assertions passed from the absence of printed pass messages;
- count a disabled or vacuous property as exercised without explicit evidence;
- downgrade severity or ignore assertions to continue a regression;
- edit the DUT when the user requested diagnosis only.

## 5. Functional coverage

Functional coverage is test-plan coverage, not source-code coverage. Define
covergroups, coverpoints, bins, transitions, and crosses from requirements.
Record the exact target bins before changing stimulus.

XSim creates a coverage database when functional coverage is present. Use
unique `-cov_db_dir` and `-cov_db_name` values, or their installed project
property equivalents, for every run. `write_xsim_coverage` and
`export_xsim_coverage` persist and report both functional and code coverage.

For each uncovered target:

```text
Covergroup / instance:
Coverpoint or cross:
Uncovered bin:
Requirement:
Legal stimulus:
Observable checker:
Reachability:
```

Closure loop:

1. run the unchanged self-checking baseline;
2. save and export text plus HTML coverage;
3. distinguish an uncovered bin from an unsupported construct;
4. add legal focused stimulus and an observable check;
5. run the focused test and full deterministic regression;
6. save each passing run under a unique database;
7. merge compatible passing runs;
8. compare targeted bin counts, not only aggregate percentages.

Do not add meaningless samples, relax bin definitions, remove illegal bins, or
edit DUT behavior solely to inflate functional coverage.

## 6. Manifests and compatible merges

Write one machine-readable or plain-text manifest per run:

```text
run_id:
source_revision_or_hash:
vivado_xsim_version:
simulation_set:
top_and_parameters:
test:
seed:
plusargs:
mode:
runtime_and_watchdog:
expected_verdict:
actual_verdict:
first_failure:
transcript:
waveform:
coverage_database:
```

Add an artifact inventory with exact paths and byte sizes for binary WDB and
coverage files; text-search tools may not enumerate binary formats reliably.

Merge only runs with the same source revision, elaborated top/parameters,
coverage schema, XSim version, and applicable coverage types. Every merged run
must satisfy its expected functional contract. Keep negative tests and failed
runs available for diagnosis but outside the passing-regression database.

The final report must include:

```text
Tests and seeds:
UVM errors / fatals:
Assertions passed / failed / disabled / unsupported:
Functional coverage targets before -> after:
Code coverage before -> after:
Merged run IDs:
Excluded failed or incompatible run IDs and reasons:
Replayed failure result:
Artifacts:
```

## Official sources

- [UG900 UVM support](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Universal-Verification-Methodology-UVM-Support)
- [UG900 SystemVerilog testbench features](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Test-Bench-Feature)
- [UG900 xsim executable options](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/xsim-Executable-Options)
- [UG900 export_xsim_coverage](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/export_xsim_coverage)
- [UG900 write_xsim_coverage](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/write_xsim_coverage)
