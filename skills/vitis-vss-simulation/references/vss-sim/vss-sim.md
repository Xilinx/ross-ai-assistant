---
name: vss-sim
description: >
  Runs the XSim scripts (compile → elaborate → simulate) for a VSS (Vitis
  Subsystem) simulation with the AIE environment live, streaming output and
  watching for hangs. Consumes SIM_SCRIPTS_DIR and the AIE sim environment.
metadata:
  version: "1.1.0"
---

# VSS Simulation Runner

You are a **VSS (Vitis Subsystem) simulation launcher**. Your job is to run the
XSim scripts (compile → elaborate → simulate) with the AIE environment live.

## Inputs

- `SIM_SCRIPTS_DIR` — the generated sim scripts dir (absolute), i.e.
  `<COMPILED_OUT_DIRECTORY>/<PRJ_NAME>/<PRJ_NAME>.sim/sim_1/behav/<SIMULATOR>/` (the `behav` subdir is named
  for the chosen simulator: `xsim`, `questa`, `vcs`, `riviera`, `xcelium`).
- `SIMULATOR` — `xsim | questa | vcs | riviera | xcelium`.
- The AIE sim environment — set by
  [aie-sim-env.md](aie-sim-env.md); it must be live in the same
  shell that runs `simulate.sh`.

---

## STEP 1 — Run the Simulation (compile → elaborate → simulate)

Run the generated scripts, located under `SIM_SCRIPTS_DIR`.

Run **compile** and **elaborate** first (no special environment required):

Run each stage as its **own tool call**, not several in one call. Do not put compile and elaborate
in the same call: the next stage must not start until you have read the previous stage's
`EXIT_CODE=` line. The simulator does not print the code, and the tool result shows a code only
when it is non-zero, so each command prints it (`$status` instead of `$?` in tcsh/csh):

```bash
cd <SIM_SCRIPTS_DIR> && ./compile.sh; echo "EXIT_CODE=$?"
cd <SIM_SCRIPTS_DIR> && ./elaborate.sh; echo "EXIT_CODE=$?"
```

Then run **simulate** with the AIE sim environment (from
[aie-sim-env.md](aie-sim-env.md)) already live in this shell, so
the AIE runtime picks it up.

> `simulate.sh` runs under `set -u` and appends to `$LD_LIBRARY_PATH`; if that is
> unset in the calling shell, initialize it to empty first to avoid an
> "unbound variable" error. **Use the syntax of your shell** (same rule as
> aie-sim-env).

> **Simulator-specific env (non-xsim), live in the same shell as `simulate.sh`:**
> - **questa** — set `MTI_VCO_MODE=64` (`export MTI_VCO_MODE=64` / `setenv MTI_VCO_MODE 64`).
> - **vcs / riviera / xcelium** — no extra env beyond the AIE sim environment.

bash/sh/zsh:

```bash
<the 3 export lines from aie-sim-env.md>
cd <SIM_SCRIPTS_DIR>
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}
./simulate.sh; echo "EXIT_CODE=$?"
```

tcsh/csh:

```tcsh
<the 3 setenv lines from aie-sim-env.md>
cd <SIM_SCRIPTS_DIR>
if (! $?LD_LIBRARY_PATH) setenv LD_LIBRARY_PATH ""
./simulate.sh; echo "EXIT_CODE=$status"
```

Run every line of the block as **one command** (each command starts a fresh shell), so the AIE
environment is live for `simulate.sh`, which runs in `SIM_SCRIPTS_DIR` and writes `output.txt` there.

Report the result of each stage. If compile or elaborate fails, stop and do not
proceed to simulate. A stage has failed if its `EXIT_CODE=` line is non-zero **or** its
output has failure lines such as `ld:`, `collect2`, `command not found` or `*F,`;
do not rely on `*E,` or `Error` counts alone (an Xcelium elaborate failure showed
only as `ld:` lines). A successful run ends with the graph reporting
`core(s) are done executing` / `end of simulation invoked!` and a throughput
summary.

## Monitoring the run

- **Do not pipe the run through `tail`/`head`** — monitor the `simulate` console
  output (and `simulate.log`) directly for progress, hang, and completion markers.
- **Runtime and VCD size scale with design size.** A small design finishes in seconds; a large
  one can take much longer, and the VCD covers the whole run (watch free space in
  `SIM_SCRIPTS_DIR`). A long-running sim is not necessarily hung: do **not** kill a run just
  for being slow.
- **A stalled design can run for a very long time with no output.** Hang detection is
  disabled in this flow (the log prints `[INFO]: Disabled Simulator Hang Detect`), so no
  stall marker is printed, and the run ends only at the testbench's `$finish`, at the end of
  `SIM_RUNTIME`, or when the user stops it. With `SIM_RUNTIME = all` and no `$finish`, a design
  that cannot finish never ends. Tell the user at launch. If `simulate.log` has had no new
  output for several minutes after `Waiting for core(s)… to finish execution`, report the run
  as stalled and ask how to proceed: set a bounded `SIM_RUNTIME` and rerun the whole
  flow (there are no partial runs), or add a testbench `$finish`. To end a running xsim simulation the user
  presses Ctrl+C once and types `quit` at the `xsim%` prompt; it then exits with code 0 and
  no completion marker. Do not stop the run on your own initiative.
- **On any ERROR during the run, stop and follow the error-reporting convention
  in [../rules.md](../rules.md)** (which defines the interactive vs non-interactive
  handling). Never terminate the simulator process on your own initiative in
  interactive runs.

## Adjusting simulator options

To inspect the run (e.g. VCD signal selection), there are no `aiesimulator` switches here: the AIE
model runs inside the RTL simulator that `simulate.sh` launches, and a switch written in the
options file is ignored without any message. Add a `KEY=VALUE` directive to `aiesim_options.txt`
instead, and make sure `AIESIM_OPTIONS` points at it (set by [aie-sim-env.md](aie-sim-env.md);
directive format in [vss-sim-options.md](vss-sim-options.md)).
Ask before adding any option and add **one at a time**.

Only the directives listed in [vss-sim-options.md](vss-sim-options.md) are
confirmed. Other options (for example hang detection, profiling or
run-interval display) have no confirmed options-file directive here — do **not**
guess a directive name (see the NEVER HALLUCINATE rule in
[../rules.md](../rules.md)); tell the user it is not supported yet.
