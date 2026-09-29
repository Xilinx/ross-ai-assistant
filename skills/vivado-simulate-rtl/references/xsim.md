<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# XSim Backend

Use this reference for AMD Vivado Simulator. XSim is the default backend and
ships with Vivado.

## Contents

- Project-mode flow
- Non-project flow
- Simulation modes
- Batch control and artifacts
- Coverage handoff

## Project-mode flow

Inspect the simulation set before changing it:

```tcl
set simset [get_filesets <simset>]
get_property TOP $simset
get_files -of_objects $simset
update_compile_order -fileset $simset
set_property target_simulator XSim [current_project]
```

Set a bounded runtime on the simulation fileset when the testbench does not
terminate itself:

```tcl
set_property xsim.simulate.runtime 1us $simset
```

Use a duration justified by the clock, reset, protocol latency, and test plan;
do not copy `1us` blindly.

Launch behavioral simulation:

```tcl
catch {close_sim -force}
launch_simulation -simset $simset -mode behavioral
```

`launch_simulation` blocks while XSim launches. Give
`Vivado:vivado_execute` a suitable timeout. If it returns a running handoff,
poll `Vivado:vivado_status` before issuing another Tcl command.

## Simulation modes

Use only a mode supported by the completed design stage:

```tcl
# Behavioral RTL
launch_simulation -simset $simset -mode behavioral

# Post-synthesis functional
launch_simulation -simset $simset -mode post-synthesis -type functional

# Post-implementation functional
launch_simulation -simset $simset -mode post-implementation -type functional

# Post-implementation timing with SDF
launch_simulation -simset $simset -mode post-implementation -type timing
```

For post-synthesis or post-implementation modes, verify the corresponding run
completed successfully. Timing simulation supplements static timing analysis;
it does not replace it.

## Non-project flow

Prefer project mode when an `.xpr` exists because Vivado owns source order, IP
models, libraries, defines, and include paths. For an explicit non-project
flow, use the generated scripts from `export_simulation`, or run the XSim
stages:

1. `xvlog` / `xvhdl` — analyze HDL into libraries
2. `xelab` — elaborate a snapshot
3. `xsim` — run the snapshot

Do not synthesize command lines from memory for IP-heavy or mixed-language
designs. Create a temporary in-memory/project representation and use:

```tcl
export_simulation -simulator xsim -directory <absolute-output-dir>
```

Run the generated compile, elaborate, and simulate scripts outside the
single-threaded Vivado Tcl session. Preserve the generated scripts with the
result.

## Batch control

Self-checking testbenches should:

- print one terminal `TEST_PASS` marker only after all checks pass;
- use `$fatal` or equivalent for failed checks;
- call `$finish`;
- include a watchdog that fails rather than hanging.

When a complete batch waveform is explicitly required and design size permits,
enable project-mode logging before launch:

```tcl
set_property xsim.simulate.log_all_signals true $simset
set_property xsim.simulate.wdb <absolute-path>/<target>.wdb $simset
```

For a focused capture, leave `xsim.simulate.runtime` empty so launch loads the
snapshot without advancing, then log selected objects before running:

```tcl
set_property xsim.simulate.runtime {} $simset
launch_simulation -simset $simset -mode behavioral
log_wave [get_objects <clock-reset-and-interface-signals>]
log_wave [get_objects <expected-observed-and-status-signals>]
run <bounded-duration>
create_wave_config <target>_FAIL
add_wave <focused-signals>
```

Run `log_wave` before advancing simulation; it cannot reconstruct earlier
transitions. `add_wave` controls display, while `log_wave` controls WDB
capture. Do not recursively log every signal by default. Large wave databases
slow simulation and obscure the causal signals.

## Logs and artifacts

The usual behavioral project-mode directory is:

```text
<project>.sim/<simset>/behav/xsim/
```

Common artifacts:

- `xvlog.log` / `xvhdl.log` — analysis
- `xelab.log` — elaboration
- `simulate.log` — runtime transcript
- `<snapshot>.wdb` — waveform database
- generated shell/batch scripts when scripts-only export is used

Paths differ for post-synthesis and post-implementation modes. Discover the
actual directory after launch; do not hard-code `behav/xsim` for every mode.

Read the complete relevant log when deciding PASS/FAIL. A truncated MCP log
tail is navigation evidence, not a verdict. Vivado can return Tcl exit code
zero from `launch_simulation` even when XSim reports a runtime `Fatal`, so the
transcript and explicit terminal marker are mandatory verdict inputs.

## Coverage handoff

For XSim code coverage, use the coverage-closure reference linked directly
from `SKILL.md`. Coverage must be enabled before elaboration, then written and
exported after the run.

## Official sources

- [UG900 Logic Simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation)
- [UG900 batch simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Running-the-Vivado-Simulator-in-Batch-Mode)
- [UG900 post-synthesis and post-implementation simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation/Running-Post-Synthesis-and-Post-Implementation-Simulations)
