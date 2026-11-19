---
name: vss-sim-scripts
description: >
  Adds the testbench to the imported Vivado project and generates the XSim
  simulation scripts (compile/elaborate/simulate) with launch_simulation
  -scripts_only. Consumes PRJ_NAME, VSS_IMPORT, TB_TOP, and TB_SOURCES.
  Produces SIM_SCRIPTS_DIR.
metadata:
  version: "1.0.0"
---

# VSS Simulation Scripts

Your job is to add the testbench to the Vivado project and generate the XSim
simulation run scripts without launching the simulator.

## Inputs

- `PRJ_NAME` — the imported Vivado project.
- `VSS_IMPORT` — the basename of the imported `.vss`.
- `TB_TOP` — the top testbench file.
- `TB_TOP_MODULE` — the top testbench module name (gathered up front).
- `TB_SOURCES` — any additional testbench sources (or `none`).
- `SIM_RUNTIME` — `all` or a time such as `10us` (gathered up front).
- `SIMULATOR` — `xsim | questa | vcs | riviera | xcelium`.
- `SIM_LIB_MAP_PATH` / `SIM_GCC_INSTALL_PATH` / `SIM_INSTALL_PATH` — the
  user-supplied simulator paths (non-xsim; `N/A` for xsim).

---

## STEP 1 — Add Testbench & Generate Simulation Scripts

Use the captured testbench (`TB_TOP`, plus `TB_SOURCES` if any) and the
**bundled, reusable** Tcl script — **do not write a new one each run**:

```
<skill-dir>/scripts/sim_setup.tcl
```

It is parameterized via `-tclargs`. Use the gathered `TB_TOP_MODULE` (do not ask
again), and pass, **in this order**:

- `PRJ_NAME` — the imported Vivado project (default `vss_prj`).
- `TB_TOP_MODULE` — the testbench top module name (e.g. `vss_tb`).
- `SIMULATOR` — `xsim | questa | vcs | riviera | xcelium`.
- `SIM_LIB_MAP_PATH` `SIM_GCC_INSTALL_PATH` `SIM_INSTALL_PATH` — the three
  user-supplied simulator paths. For **xsim** pass `N/A N/A N/A` (the script
  ignores them for xsim).
- `SIM_RUNTIME` — `all` or a time such as `10us`.
- `TB_FILE...` — **all** testbench files: `TB_TOP` **plus every `TB_SOURCES`
  dependent file** (the TB may depend on many). Pass them all — the script's file
  list is variadic (`argv 7..end`), and Vivado's `update_compile_order` resolves
  the compile order automatically, so order among the TB files doesn't matter.

Run it in Vivado batch mode **from `COMPILED_OUT_DIRECTORY`** (which contains the project):

```bash
cd <COMPILED_OUT_DIRECTORY>
vivado -mode batch -source <skill-dir>/scripts/sim_setup.tcl \
       -tclargs vss_prj <TB_TOP_MODULE> <SIMULATOR> \
                <SIM_LIB_MAP_PATH> <SIM_GCC_INSTALL_PATH> <SIM_INSTALL_PATH> \
                <SIM_RUNTIME> <TB_TOP> <TB_SOURCES...>
```

The script sets Vivado's `<simulator>.simulate.runtime` property (for example
`xsim.simulate.runtime` or `questa.simulate.runtime`) to `SIM_RUNTIME` before
generating the scripts; without it Vivado stops the run at `1000ns` for every
simulator, which silently cuts off any design that needs longer.

The script opens the project, sets `bd.noc.empty_sim_for_dangling_nsu_nmu` and reads it
back (it must read `1`; if not, the script stops with an error and generates **no**
simulation scripts — report and stop), adds
**all** the TB files to `sim_1`, resolves compile order, selects the
`target_simulator` for `SIMULATOR` (applying the per-simulator switches — e.g.
questa's `-noscelab`), and runs `launch_simulation -scripts_only` — for xsim with
no extra paths, for the third-party simulators with the user-supplied
`-lib_map_path` / `-gcc_install_path` / `-install_path`. This generates the
compile / elaborate / simulate scripts under
`<project>.sim/sim_1/behav/<SIMULATOR>/` without starting the simulator.

Report the path to the generated simulation scripts and whether script
generation completed successfully.

Capture the output to a log in `COMPILED_OUT_DIRECTORY` (for example
`> sim_setup.log 2>&1`). Treat the stage as successful only if Vivado exits with
rc 0 **and** the log contains no `ERROR:` lines. Report every `CRITICAL WARNING`
to the user; only the two listed under "Expected warnings" below are explained as
expected.

### Expected warnings (AIE-only VSS)

`launch_simulation -scripts_only` prints two `CRITICAL WARNING`s that do not stop
script generation:

- **`[Vivado 12-28486]` — `AIE_WORK_DIR` not set.** Vivado says the design contains
  AIE block(s) and that `AIE_WORK_DIR` must be set before running simulation, either
  as an environment variable or with
  `set_property AIE_WORK_DIR <path> [current_project]`. This is expected at this
  stage: this stage only generates scripts, and the variable is set in STEP 5
  ([aie-sim-env.md](aie-sim-env.md)) in the shell that runs `simulate.sh`. Do
  **not** silence it by setting the value to `NONE` — the warning itself says
  that is for designs where you do not want to use the AIE.
- **`[BD 41-2871]` — no valid/complete NoC paths.** Vivado says the logical switch
  network is not generated unless at least one NoC path terminates at an NSU or a
  memory controller. A VSS whose only ports are AXI-stream PLIOs has no such path,
  so the warning is expected. If the design is meant to reach memory over the NoC,
  treat it as a real problem and check the VSS connectivity.

Tell the user these two are expected. Report any **other** `CRITICAL WARNING` or
`ERROR` in the log as a problem.

---

## Output

```
SIM_SCRIPTS_DIR = <COMPILED_OUT_DIRECTORY>/<PRJ_NAME>/<PRJ_NAME>.sim/sim_1/behav/<SIMULATOR>/   (absolute)
```
