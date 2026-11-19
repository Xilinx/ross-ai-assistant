---
name: aiesim
description: >
  Launches aiesimulator against a compiled Work/ directory, streaming output and
  watching for hangs. Consumes WORK_DIR and INPUT_DIR. Produces the sim console
  output and AIESimulator.log.
metadata:
  version: "1.3.0"
---

# AIE Simulate Agent

You **launch aiesimulator** against the compiled `Work/` directory and monitor
the run for completion or hang.

## Inputs

```
WORK_DIR  = <compiled Work/ dir>
INPUT_DIR = <absolute dir such that <INPUT_DIR>/<each input PLIO path> exists, or N/A>
```

---

## STEP 1 — Launch aiesimulator

> For the full list of `aiesimulator` switches (from UG1076), see
> [aiesim-options.md](aiesim-options.md).

**Always set the AIE debug-trace environment variable by default — do not ask
the user.** It **must be set in the SAME shell process that runs aiesimulator**
(env vars do not carry across separate command invocations).

Use the syntax of the shell that actually executes the launch command (below): `export` in
bash / sh / zsh (what automated/tool execution uses), `setenv` in tcsh / csh.

> **Pitfall:** `setenv` is a tcsh builtin — it **silently fails in bash/sh**
> (leaves the variable unset), so the trace is not enabled and no `aie_log/`
> appears. When running the launch programmatically (bash), use `export`.
> To verify, run `export ENABLE_AIE_DBG_TRACE=true; echo $ENABLE_AIE_DBG_TRACE` (should
> print `true`) in the same command as aiesimulator.

This is a default, not an option — no prompting. With it set, the run creates an
**`aie_log/`** folder at the launch path (the directory aiesimulator is launched from).

### The default command

Run the `cd`, the trace variable and the launch as **one command** — each command starts
a fresh shell, so none of them carries over to the next:

- **bash / sh / zsh:**
  ```bash
  cd <COMPILED_OUT_DIRECTORY> && export ENABLE_AIE_DBG_TRACE=true && aiesimulator --pkg-dir=<WORK_DIR> --input-dir=<INPUT_DIR>; echo "EXIT_CODE=$?"
  ```
- **tcsh / csh:**
  ```tcsh
  cd <COMPILED_OUT_DIRECTORY> && setenv ENABLE_AIE_DBG_TRACE true && aiesimulator --pkg-dir=<WORK_DIR> --input-dir=<INPUT_DIR>; echo "EXIT_CODE=$status"
  ```

The trailing `echo` prints the simulator's **exit code** in the same command (see "While it
runs"). The outputs (`AIESimulator.log`, `aiesimulator_output/`, `aie_log/`, and a VCD only if the user asked for one) land in
`COMPILED_OUT_DIRECTORY`, the `<launch-dir>` the later stages read from.

`--input-dir` is the **always-on default** — do not ask about it, and do not
run a plain command first:

| Flag | Rule |
|---|---|
| `--input-dir=<INPUT_DIR>` | Always included for designs with ≥1 `input_plio`; it resolves the PLIO **input** files (default `.`). `INPUT_DIR` is already gathered and validated. **If `INPUT_DIR = N/A`** (pure GMIO), **omit it** — the launch is `aiesimulator --pkg-dir=<WORK_DIR>`. |

**Tell the user before launching** which switches the command has, and name any switch you added at
the user's request.

### Every other switch — consult, then ask

For **every other switch** — `--dump-vcd`, `--output-dir`, `--options-file` and all diagnostic /
bounding switches in the hang section below — **first CONSULT
[aiesim-options.md](aiesim-options.md)** (confirm the flag name, syntax, default
and semantics), **then ASK before adding it.** Do the lookup *before* proposing
the switch — never propose a flag from memory and check the table afterward.

- Never add one on your own initiative: propose the switch, say why, and wait for confirmation.
- Do not assume the run will hang and add `--hang-detect-time` (or any other diagnostic flag)
  up front — run the default command first, and propose diagnostic switches only after the
  results justify them. Add diagnostic switches **one at a time**, re-running between each.
- Switches the user asked for in the request are not diagnostics: after the consult-then-ask
  step, put them on the initial launch, together when they belong together (for example
  `--options-file` selects the modules of a VCD, so it goes with `--dump-vcd`).
- **`--sim-mode`** — never passed on the command line, and never asked about.
- **`--output-dir`** — not added by default; add `--output-dir=<DIR>` only if the
  user wants the output folder (the output PLIO files, `aiesim_options.txt` and the run
  summary) somewhere other than `aiesimulator_output/` (see Output below).

### `--online`

`--online` cannot be combined with `--dump-vcd` (see the `--online` row in
[aiesim-options.md](aiesim-options.md)). No VCD is written by default, so `--online` alone needs no
extra step. If the user asks for **both**, tell them: "`--online` and `--dump-vcd` cannot be
combined." Then **ask them which one to use** before launching. If they pick `--online`, launch with
`--online` and without `--dump-vcd`; if they pick `--dump-vcd=<name>`, launch with that and without
`--online`. An `--online` run produces no VCD file and its run summary has no waveform entry; the
report stage lists the online trace files instead.

### While it runs

Print the full simulation output to the user as it runs.

Take the **exit code** from the `EXIT_CODE=` line the launch command prints, and pass it on
with the console output and `AIESimulator.log`. The simulator does not print it, the tool
result shows a code only when it is non-zero, and it is gone after the next command.

> **On any error at launch or during the run, report it and ask before exiting the
> simulator.** If the launch fails or aiesimulator emits an error (e.g. `[AIESIMULATOR ERROR]`,
> `[CRITICAL WARNING]`, `[ERROR]`, an exception/backtrace, or a non-zero result),
> do **not** silently kill or abandon the run. Follow the error-reporting
> convention in [../rules.md](../rules.md), then **ask the user whether you may
> exit (stop/kill) the simulator** — only stop it if the user says yes. Never
> terminate the simulator process on your own initiative.

> **Note — runtime and VCD size scale with design size.** A small graph finishes in
> seconds, but a large design can take **hours**, and a VCD (when the user asked for `--dump-vcd`)
> covers the whole run and can be very large (watch free space in `<launch-dir>`). A
> long-running sim is not necessarily hung: confirm the stall markers below are in
> `AIESimulator.log` first; a healthy big run keeps advancing sim time without them. Set
> expectations accordingly and do not kill a run just for being slow.

> **Note — a run that cannot finish by itself.** The log shows `Set -1 iterations for the
> core(s)…`, or, for a bare `graph.run()`, a `Calling adf::graph::run() without specifying
> number of iterations` line. The cores never report done, so such a run ends only when the
> input runs out and the cores stall (hang detection then ends it), when
> `--simulation-cycle-timeout` cuts it off, or when the user stops it. Propose
> `--simulation-cycle-timeout` (consult, then ask). Do not stop the process yourself.

> **Note — a stalled run can look idle for a long time.** When the input is finite and the
> kernels need more data than it holds, the cores stall once the input runs out. The simulator
> ends the run only after the full hang-detect window, which by default is long. The log
> prints that window (`hang detection time of <N>ns`) only once the hang is detected. The
> default launch prints no progress, so a quiet `AIESimulator.log` after `Waiting for core(s)…
> to finish execution` is only a **possible** stall, not a confirmed hang: tell the user and
> propose `--display-run-interval` (consult, then ask) to see whether simulation time
> advances. Diagnose a hang only from the stall markers below; to get them sooner, propose a
> re-run with a small `--hang-detect-time` (see below). A hang-detected run ends with the
> stall markers, a throughput table and exit code 0. In a terminal, one Ctrl+C stops a run
> (exit code 143, no `Simulation Finished`); the output files written so far remain.

> **Do not pipe the launch command through `tail`/`head`.** Those buffer and emit
> nothing until the process exits, so you cannot see progress or detect a hang
> live. aiesimulator already streams a live copy to **`AIESimulator.log`** (in
> the launch directory) via `tee` — monitor that file for progress and for the
> hang markers below, rather than relying on captured stdout.

### Detecting a hang

Watch `AIESimulator.log` (not buffered stdout). Treat the run as **hung** if,
after `Waiting for core(s) ... to finish execution`, it stops progressing and the
log shows any of these markers:

```bash
grep -E "stalled state|stall ->|Closing Simulator" \
  <launch-dir>/AIESimulator.log
```

- `All the cores are in stalled state ... for a period of <N>ns` — hang detected
- `(<col>,<row>) -> <kind> stall -> <resource> detected at T=...` (for example
  `Stream stall -> SS0`, `Lock stall -> Lock_East`) — the stalled core and what it waits on
- `[CRITICAL WARNING]: Closing Simulator` — the simulator gave up and exited

The simulator keeps running for the whole hang-detect window after the stall begins, then
closes. The default window is long, so a hung run looks merely "slow"; the log prints the
window (`hang detection time of <N>ns`) only after the hang is detected. The stall `detected at T=...` timestamp in the log
shows when it *actually* stalled.

### If the simulation hangs

A hang is a stream/DMA deadlock or a core waiting on data that never arrives
(e.g. a kernel reading more samples than the input stream provides).
**Do not just kill it silently** — capture the stall markers above first.

Guide the user to add switches under the "consult, then ask" rule above, one at a
time. Start with what bounds the run, then add diagnostics only if you still need
more detail:

1. **Bound it first** so it stops instead of hanging forever — pick one:
   - `--hang-detect-time=<ns>` — lower it to fail fast (see
     [aiesim-options.md](aiesim-options.md)).
   - `--simulation-cycle-timeout=<cycles>` — hard-stop after N cycles.
2. **See where it stalls** (only if step 1 doesn't make it obvious):
   `--display-run-interval=<ns>` to check whether sim time is still advancing.
3. **Find the stuck core / deadlock** (only if still needed):
   `--profile`, or `--dump-vcd=<name>` to inspect the stall in a waveform viewer.

For example, a first bounded re-run is the default command plus `--hang-detect-time=10000`.

The simulator prints an `--evaluate-fifo-depth` hint after any hang, whatever the cause.
Pass it on to the user only if the graph has a reconvergent path.

If the sim is stopped this way, the **report** stage classifies it as a hang and
points the user at the relevant output (profiling / VCD).

---

## Output

The sim console output (`Simulation Finished` line, PLIO throughput table), `AIESimulator.log`,
and the files the report stage lists under "Reference files for inspection"
([aiesim-report.md](aiesim-report.md)); `<output folder>` is defined there too.
