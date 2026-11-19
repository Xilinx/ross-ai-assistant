---
name: aiesim-report
description: >
  Classifies and reports the AIE simulation outcome (FINISHED / FAIL / TIMEOUT /
  ERROR / STOPPED BY USER / NO-GO) and lists reference files for inspection. Consumes WORK_DIR,
  the sim console output, and AIESimulator.log (neither of the last two when the
  flow stopped before the launch).
metadata:
  version: "1.3.0"
---

# AIE Simulation Report Agent

You **classify the simulation outcome and print the result summary**. You also run when
the flow stopped before the launch (a failed input check): the status is then **NO-GO**.

## Inputs

```
WORK_DIR               = <compiled hw Work/ dir>
COMPILED_OUT_DIRECTORY = <where artifacts + the report file are written>
Sim output             = simulation console output (Simulation Finished + throughput)
Exit code              = <aiesimulator's exit code, read at launch>
Log                    = <launch-dir>/AIESimulator.log
COMPARE_RESULT         = <per-output results + OVERALL, or SKIPPED, from aiesim-compare>
```

`<launch-dir>` is the directory `aiesimulator` was launched from
(`COMPILED_OUT_DIRECTORY`).

---

## STEP 1 — Determine status

If the flow stopped before the launch (a failed input check in [../input.md](../input.md)),
the status is **NO-GO**. No simulation ran, so do not read a log or an exit code. Skip the
rest of this step.

Check `AIESimulator.log` for the hang markers before deciding — a hung run can
still exit 0 after `Closing Simulator`. Do **not** classify a run as FINISHED on
exit code alone: confirm the completion markers are present *and* the stall
markers are absent.

- **FINISHED**: simulation ran to completion (`core(s) are done executing` and
  `[INFO] : Simulation Finished` both printed) with no stall markers **and no memory-violation lines**
  — i.e. the run *finished successfully*.
- **FAIL**: aiesimulator reported a non-zero `Sim result` or a functional error
  after completing; **or** `core(s) are done executing` was printed but the exit
  code is non-zero (the log then has no `Simulation Finished` line). Quote the exit
  code in `Detail`.
- **TIMEOUT**: either the log shows the hang markers listed under "Detecting a hang" in
  [aiesim.md](aiesim.md) (the run was bounded by `--hang-detect-time` or hit the default
  hang-detect window), or the run was cut off by `--simulation-cycle-timeout`: the log has
  `INFO: AIESimulator app running for <N> cycles` (printed at the start of any run that uses
  the switch), no `core(s) are done executing` text and no memory-violation lines — the
  run still exits 0 and prints `Simulation Finished`. For a hang, fill in the `Stall` line
  from the log. The simulator prints an `--evaluate-fifo-depth` hint after any hang; add
  it to `Detail` only if the graph has a reconvergent path. For a cycle cut-off, say in
  `Detail` that it stopped after <N> cycles, before the cores finished.
- **ERROR**: aiesimulator stopped with an error **before** `core(s) are done
  executing` was printed (quote the first error), or produced no output; **or**
  the log contains memory-violation lines (see below).
- **STOPPED BY USER**: the user stopped the run (Ctrl+C): the exit code is non-zero (for
  example 143), the log has no `Simulation Finished` line, and no stall markers. Quote the
  exit code in `Detail`; the output files written so far are partial.
- **NO-GO**: the flow stopped before the launch (see above). Nothing was simulated.

A log with `Set -1 iterations` (or the bare `run()` line) cannot be FINISHED: the cores never
report done. It is ERROR when memory-violation lines are present (rule below); it is TIMEOUT or
STOPPED BY USER only when the cycle cut-off, the hang markers, or a user stop is what ended it.

> **A memory violation is an ERROR, not a FAIL.** With `--enable-memory-check`,
> violations print as `[ERROR] ... access out of boundary!` and the simulator stops
> there: the text `core(s) are done executing` is missing (the generic
> `Cores are done executing but...` line does not count) and throughput is 0.00. The
> later `Simulation Finished, Sim result: 0` is not a clean finish. Quote the
> violation lines in `Detail`, point to `AIESim_Guidance.json` (the simulator also writes it
> on clean runs with `--enable-memory-check`, so it matters only once a violation is
> reported), and do not guess a cause.

---

## STEP 2 — Print the result summary

Print the summary followed by a short list of reference files the user can open
for further inspection:

```
============================================================
  AIE Simulation Result
  Work dir: <WORK_DIR>
============================================================

  Status   : [FINISHED | FAIL | TIMEOUT | ERROR | STOPPED BY USER]
  Detail   : <one-line summary of outcome>
  Sim time : <total simulation time as printed in the log, e.g. 9409200 ps>
  Wallclock: <wall clock time, e.g. 21.2 s>
  Throughput: <per-PLIO MBps, e.g. din1 IN 1428.57 / dout1 OUT 1538.46>
  Switches : <the switches the user asked for beyond the default --input-dir, e.g.
             "--output-time-stamp=no: output files contain no time values", "--dump-vcd=foo";
             "none" if there were none>
             (if the user asked for both --online and
              --dump-vcd and chose --online, add "--dump-vcd not used, as it cannot be combined
              with --online")
  Compare  : <COMPARE_RESULT as produced by aiesim-compare — one line per output
             file (MATCH / MISMATCH / SKIPPED / NOT VERIFIED) plus OVERALL, or SKIPPED (reason)>
  Stall    : <only on a hang: stalled core + what it waits on + detected-at time,
             e.g. (24,1) Lock stall Lock_East @ T=177600 ps>

  Reference files for inspection:
    - Sim log      : <launch-dir>/AIESimulator.log
    - Sim output   : <output folder>/<output PLIO path> (e.g. data/output.txt or .csv)
    - Reports      : <WORK_DIR>/reports/  (AIEMapper_Report.txt, DMA_report.txt,
                     <graph>_mapping_analysis_report.txt, guidance.html, ...)
    - Summaries    : <WORK_DIR>/<graph>.aiecompile_summary
                     (open with: vitis -a <that file>, as for the run summary)
    - VCD dump     : <launch-dir>/<name>.vcd   (only if --dump-vcd=<name> was used; the simulator appends .vcd)
    - Online trace : <launch-dir>/trdata.aiesim/events.txt and <launch-dir>/vcdanalyze.log
                     (only if --online was used; there is no VCD in that case)
    - Profile data : <output folder>/profile_funct_<col>_<row>.*, profile_instr_<col>_<row>.*
                     (only if --profile was used; e.g. profile_funct_24_0.txt; one set per tile,
                      <row> relative to the AIE array)
    - AIE dbg trace: <launch-dir>/aie_log/   (from ENABLE_AIE_DBG_TRACE)
    - Run summary  : <output folder>/default.aierun_summary
    - This report  : <COMPILED_OUT_DIRECTORY>/aiesim_report.txt

============================================================
```

**When the flow stopped before the launch** (status NO-GO), use this form instead.
There is no log, exit code or timing, so leave those out. After printing this form
and applying the NO-GO-specific guidance immediately following it, proceed directly
to STEP 3. Do not inspect simulation artifacts or give waveform/run-summary
instructions for a run that was never launched.

```
============================================================
  AIE Simulation Result
  Work dir: <WORK_DIR>
============================================================

  Status    : NO-GO
  Stopped at: <the input whose check failed: COMPILED_OUT_DIRECTORY | WORK_DIR | INPUT_DIR>
  Detail    : <one-line summary: nothing was launched>

  Failures:
    - <check name>: <the quoted result> — fix: <the suggestion>
    - ...

  Reference files for inspection:
    - This report : <COMPILED_OUT_DIRECTORY>/aiesim_report.txt

============================================================
```

List each failed input check with its quoted result and fix. For the `.target` check, quote
the target found, or say that `.target` is missing. If `WORK_DIR` has not been asked yet,
write `Work dir: not gathered`. If the report is chat-only (the `COMPILED_OUT_DIRECTORY`
check failed), omit the whole `Reference files for inspection` block.

`<output folder>` is `<launch-dir>/aiesimulator_output`, or the `--output-dir` folder when that
switch was used. `<graph>` is the compiled graph source file's name without the extension, which prefixes
these files (for example `project` for `project.cpp`): list the files that actually exist
in `<WORK_DIR>` (`ls <WORK_DIR>/*.aiecompile_summary`). Pull `Sim time`, `Wallclock`, and `Throughput` from the aiesimulator console
output (the `[INFO] : Simulation Finished ...` line and the PLIO throughput
table). If the `Simulation Finished` line is missing, write "not available" for
`Sim time` and `Wallclock`. Only list reference files that actually exist for this run — omit any
that were not produced.

**VCD check.** Only if `--dump-vcd=<name>` was used, run `ls -l <launch-dir>/<name>.vcd`. If the file is missing, say in `Detail` that the VCD was requested but not
written, and omit the entry.

> **Throughput caveats (UG1076).** Only PLIOs get throughput figures. If the graph
> ran with `graph.run(-1)` and no `--simulation-cycle-timeout`, no throughput is
> reported. A stalled run still prints a table whose figures can look healthy: decide the
> status from the stall markers, never from the throughput. In either case, say so in
> `Throughput` instead of leaving it blank.

> **`FINISHED` is not "correct".** Correctness is shown only by the `Compare` line
> (from `aiesim-compare`), and only against the golden files the user supplied.
> If `COMPARE_RESULT` is SKIPPED (user chose no comparison), or any output is
> SKIPPED or NOT VERIFIED, tell the user those outputs were not verified and that they
> should compare the produced output file(s) against their golden reference.

> **Always tell the user to open the aiesimulator run summary in the Vitis
> Unified IDE Analysis View.** The run summary (`default.aierun_summary`) is written with or
> without a VCD; it records the VCD path when one was dumped. The report tells the user to open it
> with `vitis -a` (for the waveform, only if a VCD was dumped) (plus the Profile tab with `--profile`, and the Memory Violations tab
> with `--enable-memory-check`). With `--online` the summary has no waveform
> entry; say so and point the user to the online trace files listed above:
>
> ```bash
> vitis -a <output folder>/default.aierun_summary
> ```
>
> If the user wants to analyze the raw VCD file on its own, do not assume a
> viewer — **ask whether they have a tool that supports opening VCD files**, and
> use the one they name. **Do not search or probe for viewers** (no `which`, no
> filesystem or install-area lookups); rely only on the user's answer.

---

## STEP 3 — Log the report to a file

If `COMPILED_OUT_DIRECTORY` does not exist or is not writable (its input check failed),
there is nowhere to write the report: report in chat only and say why. Skip the "refer to
the file" message at the end of this step, since there is no file.

**Keep the previous report first.** If `aiesim_report.txt` already exists, rename it
before writing (a rename only — no deletion):

```bash
[ -f <COMPILED_OUT_DIRECTORY>/aiesim_report.txt ] && mv <COMPILED_OUT_DIRECTORY>/aiesim_report.txt <COMPILED_OUT_DIRECTORY>/aiesim_report.prev.txt
```

This keeps only the most recent previous report (an older `aiesim_report.prev.txt` is
replaced). It applies to every status, including NO-GO, so a later failure never erases
the last good report.

**Always write the entire report (the summary block you printed, with its
reference-file list and, when the form has it, the Vitis Unified IDE Analysis View
instruction) to a file**, so the user has a durable copy:

```
<COMPILED_OUT_DIRECTORY>/aiesim_report.txt
```

Write the same content you printed to the console — verbatim. Then **tell the
user to refer to it**, e.g.:

> "Full report written to `<COMPILED_OUT_DIRECTORY>/aiesim_report.txt` — refer to
>  it for the complete result and reference files."
