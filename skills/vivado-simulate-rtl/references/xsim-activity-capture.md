<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# XSim Activity Capture

Use this reference for scoped SAIF power activity, general VCD export, and
portable sub-design stimulus capture with `generate_vcd_ports` and
`create_testbench`.

## Contents

- Shared preflight
- SAIF power activity
- General VCD capture
- Portable stimulus capture
- Verification and reporting

## 1. Shared preflight

Record before launch:

```text
Purpose: power activity | interchange VCD | portable sub-design stimulus
Simulation set / top / mode:
Source or DUT hierarchy:
Warm-up interval:
Measurement interval:
Signals or ports:
Expected functional result:
Artifact paths:
```

Require a self-checking test and bounded runtime. Activity artifacts supplement
the transcript; their existence is not functional PASS.

Use `-debug typical` or the installed project property's equivalent because
SAIF and hierarchy access depend on elaboration visibility. Query and preserve
existing properties:

```tcl
set simset [get_filesets <simset>]
list_property $simset
get_property xsim.elaborate.debug_level $simset
get_property xsim.simulate.xsim.more_options $simset
```

Prefer a focused hierarchy and explicit objects. Recursive whole-design
activity capture can produce large files and slow simulation.

## 2. SAIF power activity

Correctness stimulus and representative power stimulus are not automatically
the same. Choose a workload reflecting expected operating modes, duty cycles,
data patterns, idle periods, and clock/reset behavior. Record why it is
representative.

Separate:

1. reset and initialization;
2. warm-up to stable operation;
3. measured activity window;
4. post-window functional checks.

For an exact measurement window, load the snapshot without auto-running,
advance through warm-up, then capture only the intended scope:

```tcl
set_property xsim.simulate.runtime {} $simset
launch_simulation -simset $simset -mode behavioral

run <warm-up-duration>
open_saif <absolute-path>/activity.saif
log_saif [get_objects <dut-scope>/*]
run <measurement-duration>
close_saif

run <bounded-remainder>
```

`open_saif` must precede `log_saif`; `close_saif` must run before closing the
simulation. If the testbench calls `$finish` at the window boundary, preserve
`-onfinish stop` in `xsim.simulate.xsim.more_options` so the Tcl session can
close the SAIF handle and save other in-memory artifacts.

For broad project-property capture, Vivado exposes:

```tcl
set_property xsim.simulate.saif <absolute-path>/activity.saif $simset
set_property xsim.simulate.saif_scope <dut-hierarchy> $simset
```

Use property discovery for release-sensitive capitalization and verify the
readback. Prefer explicit Tcl windowing when warm-up must be excluded.

### SAIF artifact checks

Require:

- file exists and is non-empty;
- header and timescale are present;
- expected DUT hierarchy is present;
- requested ports/signals have activity;
- measurement start/end and workload are recorded;
- test transcript still satisfies the functional contract.

The simulation skill may hand the SAIF to `read_saif`/`report_power`, but must
report hierarchy matching and unmatched nets. Do not claim power signoff:
workload representativeness, netlist correlation, process/voltage/temperature,
clock activity, and the downstream power model remain separate evidence.

Post-implementation timing activity is preferable when realistic glitches are
needed and the design supports that mode. Vivado timing netlists are Verilog;
VHDL-only RTL activity has additional internal-signal limitations.

## 3. General VCD capture

Use VCD for portable signal activity, external analysis, or exchange with a
tool that does not read WDB. WDB remains preferable for native XSim debug.

Example bounded capture:

```tcl
set_property xsim.simulate.runtime {} $simset
launch_simulation -simset $simset -mode behavioral

run <warm-up-duration>
open_vcd <absolute-path>/activity.vcd
log_vcd [get_objects {<clock> <reset> <controls> <data> <outputs>}]
run <measurement-duration>
close_vcd

run <bounded-remainder>
```

Use `start_vcd`, `stop_vcd`, `flush_vcd`, `checkpoint_vcd`, or `limit_vcd`
only when needed for a long capture. Always close the handle. Do not assume
objects in the Wave window are automatically in the VCD; pass them explicitly
to `log_vcd`.

### VCD artifact checks

Parse or inspect:

- `$date`, `$version`, `$timescale`, scopes, and variable declarations;
- exact requested object set;
- first and last timestamps;
- expected transitions and final values;
- absence of unintended recursive hierarchy when focused capture was required.

Verify values against the test contract and transcript. A syntactically valid
VCD can still describe a failing simulation.

## 4. Portable sub-design stimulus capture

Use this only when a working parent simulation can exercise a selected
sub-design and the goal is to reproduce its port activity independently.
It is not a replacement for deriving tests from requirements.

### Capture source behavior

1. Run the original self-checking simulation and prove its verdict.
2. Select the exact hierarchical instance.
3. Start port capture before the behavior of interest.
4. Run a bounded interval.
5. Close the special port VCD handle.

```tcl
launch_simulation -simset <source-simset> -mode behavioral
generate_vcd_ports {/<source-top>/<selected-instance>}
run <capture-duration>
close_vcd -ports
```

Vivado writes a port-activity VCD in the active simulation run location.
Discover the returned/generated path; do not assume a working-directory name.

### Generate an isolated testbench

Create a new simulation set so the source test remains unchanged:

```tcl
create_testbench \
  -name <generated-top> \
  -add_to_simset <new-simset> \
  -set_as_top
update_compile_order -fileset [get_filesets <new-simset>]
```

`create_testbench` generates a simulator-independent SystemVerilog wrapper,
DUT instantiation, declarations, and stimulus include based on the captured
ports. Discover and preserve every generated file.

Before launch, generate or inspect the replay's compile/elaborate command and
compare every injected option with the installed `xelab -help`. In a Vivado
2026.1 smoke test, `create_testbench` emitted the obsolete
`-ignore_localparam_override` option, which that release rejects. If observed:

1. preserve the original generated script and failure log;
2. confirm that installed `xelab -help` exposes
   `-suppress_localparam_override_error`;
3. replace only the obsolete option in the generated replay script;
4. rerun elaboration and simulation;
5. report the workaround as generated-flow remediation, not a DUT change.

Do not blindly apply this substitution to other releases.

### Convert replay into verification

Treat the generated output as replay scaffolding until all are added:

- checks for expected output values/events;
- protocol legality checks where applicable;
- watchdog and bounded completion;
- exactly one terminal PASS marker;
- failure messages with expected/observed values and time;
- documented assumptions about clocks, reset, bidirectional ports, unknowns,
  timing, parameters, and external models.

Do not overwrite the source testbench. Make verification-only edits in the new
simulation set, launch it independently, and compare source versus replay:

```text
Captured interval:
Input sequence:
Expected output sequence/final state:
Source verdict:
Replay verdict:
Differences:
```

Call the artifact portable only after it runs successfully with the intended
simulator flow. Port-level replay does not prove portability across language
semantics, timing models, encrypted IP, or unsupported simulators.

## 5. Result report

```text
Capture purpose:
Backend / mode / top:
Source scope and objects:
Warm-up and measurement interval:
Functional verdict:
SAIF path / size / hierarchy:
VCD path / size / declared objects / time range:
Generated testbench and stimulus files:
Source versus replay result:
Downstream power match (if run):
Limitations / unverified assumptions:
```

## Official sources

- [UG900 SAIF dumping](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Dumping-SAIF-for-Power-Analysis)
- [UG900 VCD dumping](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Dumping-VCD-for-Simulation)
- [UG900 generate_vcd_ports](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/generate_vcd_ports)
- [UG900 create_testbench](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/create_testbench)
- [UG900 power analysis using XSim](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Power-Analysis-Using-Vivado-Simulator)
