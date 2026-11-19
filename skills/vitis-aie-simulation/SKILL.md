---
name: vitis-aie-simulation
description: >
  Runs aiesimulator on an already-compiled AIE (AI Engine) hw build (Work
  directory): gathers and checks inputs, launches aiesimulator, compares
  against golden files, and reports the outcome.
  Simulation only; it does not compile the graph.
  Use when the user wants to simulate a compiled AIE graph in a Vitis/Versal flow.
license: MIT
metadata:
  stage: beta
  version: "1.2.0"
---

# vitis-aie-simulation

You are the **top-level coordinator** for running aiesimulation on an
**already-compiled** AIE design (an existing `hw`-target `Work` directory). You
orchestrate the bundled stages under `references/` in the order of the flow table
below; you do not re-implement the input-check or launch logic yourself.

> ## 📌 READ FIRST — always in effect
> **Before doing anything, read [references/rules.md](references/rules.md).** Its
> cross-cutting rules (including **NEVER HALLUCINATE**) apply to **every** stage
> and stay in effect for the **entire run** — re-check them at each step, not just
> at the start.
> **Always use absolute paths** for every input, `cd` target, and command argument
> (see the Paths rule in `rules.md`).

## Flow

Run the steps in order. Each stage file is the authority for its own step: follow
it directly and pass on the values shown.

| Step | Stage file | What to do | Then |
|---|---|---|---|
| Preflight | [DEPENDENCIES.md](DEPENDENCIES.md) | Verify a Vitis environment is sourced (`XILINX_VITIS` set and `aiesimulator` on `PATH`). | **Set** → STEP 0. **Missing** → do not guess or auto-source anything: ask the user to source their Vitis settings and relaunch the session per that file. |
| 0 | [input.md](references/input.md) | Gather **all** inputs the flow needs (always first), running each input's check right after it is answered. Do not proceed until they are captured; later stages must not re-prompt for anything already gathered. | All checks pass → STEP 1. A failed check → **NO-GO**: stop, report it, then run STEP 3 (the report file records the NO-GO). |
| 1 | [aiesim.md](references/aiesim/aiesim.md) | Launch the simulation against `WORK_DIR`; watch for hangs. Pass `WORK_DIR` and the gathered inputs. | STEP 2 |
| 2 | [aiesim-compare.md](references/aiesim/aiesim-compare.md) | Compare the sim outputs against golden files (paired after the sim). Pass `COMPARE_GOLDEN`; the outputs are the output PLIO files (`aiesimulator_output/` unless `--output-dir` was set). Produces `COMPARE_RESULT`; `COMPARE_GOLDEN = no` or an unfinished sim yields SKIPPED. | STEP 3 |
| 3 | [aiesim-report.md](references/aiesim/aiesim-report.md) | Classify and report the simulation outcome and list the reference files. Pass `COMPARE_RESULT`. | Done |

Two more files are not steps: [references/rules.md](references/rules.md) (read first,
above) and [references/aiesim/aiesim-options.md](references/aiesim/aiesim-options.md),
the table of `aiesimulator` options (from UG1076 2026.2, cross-checked with
`aiesimulator --help-list`), which the simulate stage consults before it proposes
any switch.

### Full flow only

This skill always runs the full flow, from the simulation to the report. If the user
asks for only the compare or only the report on an earlier simulation, say that this
skill does not run a subset of the stages and offer the full flow.

### Not compiled yet?

If the user has no compiled `hw` build, this skill does not compile it. Tell
them to build the AIE graph first with target `hw`, then provide the resulting
work directory.
