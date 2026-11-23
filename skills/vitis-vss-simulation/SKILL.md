---
name: vitis-vss-simulation
description: >
  A skill to help running VSS (Vitis Subsystem) simulation for AIE + PL designs.
  Invoke with /vitis-vss-simulation [path/to/vss_inputs.md]. The optional argument
  is a filled inputs file (see scripts/vss_inputs.template.md) for a
  non-interactive run; omit it to be prompted.
license: MIT
metadata:
  stage: beta
  version: "1.3.0"
---

# vitis-vss-simulation

You are the **top-level coordinator** for running VSS (Vitis Subsystem)
simulation on AIE (+ PL) designs. Nothing is compiled or linked here: the user
supplies an existing `hw`-target AIE `Work` directory (`WORK_DIR`) and an
already-generated `.vss` (`VSS`). You orchestrate the bundled stages in the order
of the flow table below; you do not re-implement their logic yourself.

> ## 📌 READ FIRST — always in effect
> **Before doing anything, read [references/rules.md](references/rules.md).** Its
> cross-cutting rules (including **NEVER HALLUCINATE**) apply to **every** stage
> and stay in effect for the **entire run** — re-check them at each step, not just
> at the start.
> **Always use absolute paths** for every input, `cd` target, and command or Tcl
> argument (see the Paths rule in `rules.md`).

## Flow

Run the steps in order. Each stage file is the authority for its own step: follow
it directly and pass on the values shown.

| Step | Stage file | What to do | Then |
|---|---|---|---|
| Preflight | [DEPENDENCIES.md](DEPENDENCIES.md) | Verify a Vitis environment is sourced (`XILINX_VITIS` set, and `vivado` and `vitis` on `PATH`). | **Set** → STEP 0. **Missing** → do not guess or auto-source anything: ask the user to source their Vitis settings and relaunch the session per that file. |
| 0 | [input.md](references/input.md) | Gather **all** inputs (always first), running the checks on `COMPILED_OUT_DIRECTORY` and `WORK_DIR` right after each is answered. If the skill was invoked with an argument (e.g. `/vitis-vss-simulation /abs/path/vss_inputs.md`), it is the inputs-file path: read it directly (Option A in `input.md`) and do not prompt for anything it provides. Later stages must not re-prompt for anything already gathered. | All checks pass → STEP 1. A failed check → **NO-GO**: stop, report it, then run STEP 8 (the report file records the NO-GO). |
| 1 | [vss-sim-drc.md](references/vss-sim/vss-sim-drc.md) | Run the DRC checks on the remaining inputs, passing the gathered inputs. Print the full DRC report and verdict. | **GO** → STEP 2. **NO-GO** → stop; report the failing DRCs and their fixes, then run STEP 8. |
| 2 | [vss-import.md](references/vss-sim/vss-import.md) | Import the `.vss` into a Vivado project, validate the BD, create the wrapper. Pass `VSS`. Produces `PRJ_NAME`, `VSS_IMPORT`. | STEP 3 |
| 3 | [vss-sim-scripts.md](references/vss-sim/vss-sim-scripts.md) | Add the testbench and generate the sim scripts. Pass `PRJ_NAME`, `VSS_IMPORT`, the testbench inputs and `SIM_RUNTIME`. Produces `SIM_SCRIPTS_DIR`. | STEP 4 |
| 4 | [aie-crts.md](references/vss-sim/aie-crts.md) | Compile the AIE `c_rts`. Pass `WORK_DIR`. | STEP 5 |
| 5 | [aie-sim-env.md](references/vss-sim/aie-sim-env.md) | Set the AIE sim environment. Pass `WORK_DIR`. Produces the environment to prepend to the STEP 6 simulate command. | STEP 6 |
| 6 | [vss-sim.md](references/vss-sim/vss-sim.md) | Run the simulation (compile → elaborate → simulate); watch for hangs. Pass `SIM_SCRIPTS_DIR` and the AIE environment. | STEP 7 |
| 7 | [vss-sim-compare.md](references/vss-sim/vss-sim-compare.md) | Compare the outputs against golden files (paired after the sim). Pass `COMPARE_GOLDEN`. Produces `COMPARE_RESULT` (`COMPARE_GOLDEN = no` or an unfinished sim yields SKIPPED). | STEP 8 |
| 8 | [vss-sim-report.md](references/vss-sim/vss-sim-report.md) | Classify and report the outcome and list the reference files. Pass `COMPARE_RESULT`. Run it **every time the flow ends**: also when compile, elaborate or simulate failed, and when the flow stopped before STEP 6 (a failed input check, a DRC NO-GO, or a failure in STEPs 2–5, which are also reported in chat). | Done |

A failure in STEPs 2–5 (import, script generation, `c_rts`, environment): report it,
run STEP 8 (the report file records the ERROR), and stop.

Not steps: [references/rules.md](references/rules.md) (read first, above);
[references/vss-sim/vss-sim-options.md](references/vss-sim/vss-sim-options.md), the
options-file directives for the AIE model; and the reusable, parameterized Vivado Tcl
[scripts/import_vss.tcl](scripts/import_vss.tcl) and
[scripts/sim_setup.tcl](scripts/sim_setup.tcl), run with `-tclargs` (no per-run authoring).

For background on the VSS simulation flow, see the "Simulation with the Vitis
Subsystem" section of UG1701.

### Every request is a full run

There are no partial runs. Every request runs the whole flow from STEP 0 to STEP 8, even when
only one input changed (a testbench edit, a new golden file, a recompiled `WORK_DIR`).
STEP 2 overwrites an existing Vivado project in `COMPILED_OUT_DIRECTORY` (its `.sim` folder, with the
old `simulate.log` and `output.txt`, is recreated); to keep the earlier results, use a new directory.
Files outside the project folder stay: `aiesim_options.txt` and the earlier report
(renamed to `vss-sim_report.prev.txt`). Do not skip stages, and do not report from a `simulate.log` of an
earlier run.

### Not supported

VSS simulation does not support AIE designs that have GMIO ports. For those, use
hardware emulation instead of this skill.

### Not built yet?

This skill compiles and links nothing, and it does not write testbenches. If the
user has no compiled `hw` AIE build (`WORK_DIR`), no `.vss` (`VSS`), or no
testbench (`TB_TOP`, `TB_TOP_MODULE` and any `TB_SOURCES`), tell them to produce
those first (compile the AIE graph with target `hw`, generate the `.vss`
separately, and supply their own testbench), then provide the paths.
