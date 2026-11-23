---
name: report
description: >
  Classifies and reports the VSS simulation outcome (PASS / FAIL / TIMEOUT /
  INCOMPLETE / ERROR / NO-GO) and lists reference files for inspection. Consumes WORK_DIR,
  COMPILED_OUT_DIRECTORY, and the XSim simulate console output.
metadata:
  version: "1.2.0"
---

# VSS Simulation Report

You **classify the simulation outcome and print the result summary**.

**When this stage runs.** **Every time the flow ends**, whatever stopped it: the simulation
finished; compile, elaborate or simulate failed (status FAIL; `Compare` is SKIPPED); or the flow
stopped **before** STEP 6 — a failed input check or a DRC NO-GO (status **NO-GO**), or a failure in
the import, script-generation, c_rts or environment stages (status **ERROR**). A stop before
STEP 6 is also reported in chat per [../rules.md](../rules.md) (issue / reason / suggestions),
and the report file records it too, so that a report left from an earlier run never stays as
the latest result.

If `COMPILED_OUT_DIRECTORY` was never captured, or does not exist, there is nowhere to write the
file: report in chat only and say why.

## Inputs

```
WORK_DIR               = <compiled Work/ dir>
COMPILED_OUT_DIRECTORY = <where artifacts + the report file are written>
Sim output             = XSim simulate console output (completion markers + throughput)
Exit codes             = the EXIT_CODE= lines printed by the compile, elaborate and simulate commands
Log                    = <SIM_SCRIPTS_DIR>/simulate.log
COMPARE_RESULT         = <per-output results + OVERALL, or SKIPPED, from vss-sim-compare>
```

---

## STEP 1 — Determine status

If the flow stopped before STEP 6, the status is **NO-GO** or **ERROR** (below): there is no
simulation output to read. Otherwise check the XSim `simulate` console output for the
completion markers. A run the user stopped (Ctrl+C, then `quit`) exits with code 0 too, so never
classify a run as PASS on exit codes alone.

- **PASS**: the compile, elaborate and simulate exit codes are all 0; the `simulate` output
  has `core(s) are done executing` and `end of simulation invoked!`.
- **FAIL**: one of the compile, elaborate or simulate exit codes is non-zero, or the `simulate`
  output shows an error or fatal message. This includes a simulation that crashed or exited
  without output. Quote the first `ERROR` lines from the
  failing stage in `Detail`. If the simulation did not run (compile or elaborate
  failed), write `not run` for `Sim time`, `Wallclock` and `Throughput`, and omit
  the output and VCD entries from the reference files. Warnings are not failures:
  report their count in `Detail` if it is large. For example, a questa run printed
  about 17,000 `vsim-4025` warnings about the AIE model's SystemC ports and exports,
  with no errors, and was a PASS.
- **TIMEOUT**: the run stalled and the user stopped it. The last progress line in `simulate.log`
  is `Waiting for core(s) of graph <graph> to finish execution ...`, the log stopped growing, and
  `core(s) are done executing` is missing. Hang detection is disabled in this flow (the log
  prints `[INFO]: Disabled Simulator Hang Detect`), so there are no stall markers. Say in
  `Detail` how long the log had been quiet, and that the exit code is 0 after a `quit`.
- **INCOMPLETE**: the compile, elaborate and simulate exit codes are all 0, but
  `core(s) are done executing` is missing and the run ended by itself — for
  example the run stopped at `SIM_RUNTIME`, the testbench ended it early, or the
  simulation ended at time 0 with no testbench activity. Say
  which marker is missing and quote the last simulation message with its time
  (for example the `end of simulation invoked!` line), not the final xsim exit
  line. Do not report PASS.
- **ERROR**: a stage **before** the simulation failed (import, script generation, `c_rts`,
  environment), so the simulation never started. A simulation that ran and failed is **FAIL**.
- **NO-GO**: the flow stopped at a failed input check or a DRC NO-GO verdict. Nothing else ran.

---

## STEP 2 — Print the result summary

Print the summary followed by the list of reference files the user can open:

```
============================================================
  VSS Simulation Result
  Work dir: <WORK_DIR>
============================================================

  Status   : [PASS | FAIL | TIMEOUT | INCOMPLETE | ERROR | NO-GO]
  Detail   : <one-line summary of outcome>
  Sim time : <the time of the testbench's $finish, e.g. 193.6 ns>
  Wallclock: <the elapsed time if the run printed one (only questa: "Elapsed time: ...");
             otherwise "not printed"; for xsim also give the CPU time and session start/exit
             times, for vcs the "CPU Time">
  Throughput: <per-PLIO MBps, e.g. din1 IN 1428.57 / dout1 OUT 1538.46>
  Compare  : <COMPARE_RESULT as produced by vss-sim-compare — one line per output
             file (MATCH / MISMATCH / SKIPPED / NOT VERIFIED) plus OVERALL, or SKIPPED (reason)>

  Reference files for inspection:
    - Sim log      : <SIM_SCRIPTS_DIR>/simulate.log
    - Compile log  : <SIM_SCRIPTS_DIR>/compile.log
    - Elaborate log: <SIM_SCRIPTS_DIR>/elaborate.log   (not produced by riviera)
    - Waveform     : <SIM_SCRIPTS_DIR>/<TB_TOP_MODULE>_behav.wdb   (xsim)
                     <SIM_SCRIPTS_DIR>/vsim.wlf                    (questa)
                     <SIM_SCRIPTS_DIR>/waves.shm                   (xcelium)
                     <SIM_SCRIPTS_DIR>/dataset.asdb                (riviera)
                     (vcs writes none of its own)   — list the one that exists
    - Sim output   : <SIM_SCRIPTS_DIR>/output.txt   (written by the testbench into the
                     directory simulate.sh runs from; or the path given to vss-sim-compare)
    - Reports      : <WORK_DIR>/reports/  (AIEMapper_Report.txt, DMA_report.txt,
                     <graph>_mapping_analysis_report.txt, guidance.html, ...)
    - Summaries    : <WORK_DIR>/<graph>.aiecompile_summary
                     (open with: vitis -a <that file>, as for the run summary)
    - VCD dump     : <SIM_SCRIPTS_DIR>/<name>.vcd   (<name> from the `AIE_DUMP_VCD=` line of
                     aiesim_options.txt; `foo` by default)
    - AIE dbg trace: <SIM_SCRIPTS_DIR>/aie_log/   (AIE debug trace)
    - Run summary  : <SIM_SCRIPTS_DIR>/default.aierun_summary
                     (only if produced by the run)
    - This report  : <COMPILED_OUT_DIRECTORY>/vss-sim_report.txt

============================================================
```

> **If the run produced a run summary (`default.aierun_summary`), tell the user in
> the report to open it with the Vitis Analyzer:**
>
> ```bash
> vitis -a <SIM_SCRIPTS_DIR>/default.aierun_summary
> ```

> **`PASS` is not "correct".** The status means the simulation *ran to completion
> successfully*. Correctness is shown only by the `Compare` line (from
> `vss-sim-compare`), and only against the golden files the user supplied. If
> `COMPARE_RESULT` is SKIPPED, or any output is SKIPPED or NOT VERIFIED, tell the
> user those outputs were not verified and that they should compare the produced
> output file(s) against their golden reference.

`<graph>` is the compiled graph source file's name without the extension, which prefixes these
files (for example `graph` for `graph.cpp`): list the files that actually exist in `<WORK_DIR>`
(`ls <WORK_DIR>/*.aiecompile_summary`).

Pull `Sim time`, `Wallclock`, and `Throughput` from the `simulate` console output.
The time of the testbench's `$finish` is printed differently by each simulator:
xsim `$finish called at time : <N> <unit>` (xsim picks the unit: `ps`, `ns`, `us`, ...); questa `Time: <N> ps` on the line right after
the `$finish` note; xcelium `Simulation complete via $finish(1) at time <N> PS`;
riviera `KERNEL: Time: <N> ps`; vcs `$finish at simulation time <N>` (and `Time: <N> ps`
in the "V C S Simulation Report" block). The PLIO throughput table is printed in the
same output. Only list reference files that actually exist for this run — omit any
that were not produced.

**VCD check** (only when the simulation ran). Read `<name>` from the `AIE_DUMP_VCD=` line of
`<COMPILED_OUT_DIRECTORY>/aiesim_options.txt` and run `ls -l <SIM_SCRIPTS_DIR>/<name>.vcd`. File
found: list it. Line present but file missing: say in `Detail` that the VCD was requested but not
written, and omit the entry. No such line (the user declined the dump): omit the entry.

**When the flow stopped before STEP 6** (status NO-GO or a stage ERROR), use this form instead.
There is no simulation output, so leave out the time, wallclock, throughput and compare
lines, and list every failure:

```
============================================================
  VSS Simulation Result
  Work dir: <WORK_DIR, if it was captured>
============================================================

  Status   : [NO-GO | ERROR]
  Stopped at: [INPUT <COMPILED_OUT_DIRECTORY | WORK_DIR> | DRC | STEP <n> <stage name>]
  Detail   : <one-line summary: the simulation did not run>

  Failures:
    - <input check, DRC-xx or stage>: <the quoted result> — fix: <the suggestion>
    - ...

  Reference files for inspection:
    - This report  : <COMPILED_OUT_DIRECTORY>/vss-sim_report.txt

============================================================
```

For a failed input check, list each failed check with its quoted result and fix; if `WORK_DIR` has
not been asked yet, write `Work dir: not gathered`. For a DRC NO-GO, list each FAIL row and also the
WARN rows. For a failed stage, quote the first error lines from its output. If the report is
chat-only (the `COMPILED_OUT_DIRECTORY` check failed), omit the whole `Reference files for
inspection` block.

---

## STEP 3 — Log the report to a file

**Always write the entire report (the full summary block above, including the
Vitis Analyzer instruction and reference-file list) to a file**, so the user has
a durable copy:

```
<COMPILED_OUT_DIRECTORY>/vss-sim_report.txt
```

**Keep the previous report first.** If a `vss-sim_report.txt` already exists, rename it before
writing (a rename only — no deletion):

```bash
cd <COMPILED_OUT_DIRECTORY>
[ -f vss-sim_report.txt ] && mv vss-sim_report.txt vss-sim_report.prev.txt
```

This keeps only the most recent previous report (an older `vss-sim_report.prev.txt` is replaced).
It applies to every status, including NO-GO, so a later failure never erases the last good report.

Write the same content you printed to the console — verbatim. Then **tell the
user to refer to it**, e.g.:

> "Full report written to `<COMPILED_OUT_DIRECTORY>/vss-sim_report.txt` — refer to
>  it for the complete result and reference files."
