---
name: report
description: >
  Classifies and reports the hardware-emulation outcome (PASS / FAIL / TIMEOUT /
  ERROR / NO-GO) and lists reference files for inspection. Consumes COMPILED_OUT_DIRECTORY,
  PACKAGE_DIR, and the run console output.
metadata:
  version: "1.2.0"
---

# HW Emulation Report

You **classify the emulation outcome and print the result summary**.

**When this stage runs.** Run it **every time the flow ends**, whatever stopped it: the launch
finished (PASS or FAIL), the app failed (ERROR), the run hung (TIMEOUT), or the flow stopped
**before** the launch — a DRC NO-GO (status **NO-GO**), a failed pre-launch check (**NO-GO**), or
a failure in the prebuilt copy, boot image or rootfs staging (**ERROR**). A stop before the launch
is also reported in chat per [../rules.md](../rules.md) (issue / reason / suggestions), and the
report file records it too, so that a report left from an earlier run never stays as the latest result.

The only case without a file is when `COMPILED_OUT_DIRECTORY` was never captured, or does not
exist (DRC-02 fails): there is nowhere to write it, so report in chat only and say why.

## Inputs

```
FLOW                   = <edf | petalinux | baremetal>
COMPILED_OUT_DIRECTORY = <where artifacts + the report file are written>
PACKAGE_DIR            = <packaged hw_emu directory>
SIMULATOR              = <xsim | questa | vcs | riviera | xcelium>
Run output             = embedded_run.log (boot markers, host output, pass/fail markers)
```

---

## STEP 1 — Determine status

Check the run console output for markers before deciding — **a hang can still
exit 0**:

- **PASS**: the host reported a passing result (`TEST PASSED`, or
  `INFO: Embedded host run completed.` with no `ERROR: Embedded host run failed`
  before it) and the emulator shut down cleanly. The runner prints the `INFO:` line
  even after a failure, so it counts as a pass **only** when no `ERROR:` line
  precedes it.
- **FAIL**: the host ran to completion but reported a functional mismatch /
  `TEST FAILED` / non-zero result.
- **TIMEOUT**: the run hung — QEMU never reached the host, or the host stalled
  and the emulator had to be closed.
- **ERROR**: the run errored out (missing xclbin, XRT device not found, boot
  failure) or produced no host output; also a failure in a staging step (STEPs 2–4) before the launch.
- **NO-GO**: the flow stopped before the launch because the DRC returned NO-GO or a pre-launch
  check failed. Nothing was launched, so there is no run output to classify.

Markers to look for in `embedded_run.log` (strip ANSI codes first; the same lists the launch stage
monitors — see "Markers" in the launch file of the flow:
[edf](../hwemu-run/edf/launch.md), [petalinux](../hwemu-run/petalinux/launch.md) or
[baremetal](../hwemu-run/baremetal/launch.md)). The lists below are the `edf` and `petalinux` ones:

- **PASS:** `TEST PASSED`, or `INFO: Embedded host run completed.` with no
  `ERROR: Embedded host run failed` before it.
- **FAIL / ERROR:** `ERROR: Embedded host run failed, RC=<rc>`, `terminate called`,
  `Aborted`, `core dumped`, `No such device`, `TEST FAILED`, or boot trouble (`emergency mode`,
  `Timed out waiting for device`, `Dependency failed`).
- **TIMEOUT:** only when the user decides the run is hung and stops it, or the run
  ends with no host output — never from a quiet log alone.

**Baremetal flow.** Use the "Markers (baremetal flow)" table in
[../hwemu-run/baremetal/launch.md](../hwemu-run/baremetal/launch.md) instead of the lists above:
its **PASS** is PASS here, **FAIL** is FAIL, and **INCOMPLETE** is reported as **ERROR** (say in
`Detail` that the simulation ended before the ELF finished, and quote the last lines of the log).
A hang is a TIMEOUT as in the other flows.

> **Important:** do **not** classify a run as PASS on exit code alone — confirm
> the host's pass marker is present *and* no hang/error markers appear. Never
> treat `INFO: Embedded host run completed.` alone as a pass — check that no
> `ERROR: Embedded host run failed` line comes before it.

---

## STEP 2 — Print the result summary

```
============================================================
  HW Emulation Result
  Build dir: <COMPILED_OUT_DIRECTORY>
  Package : <PACKAGE_DIR>
  Flow     : <FLOW>
  Simulator: <SIMULATOR>
============================================================

  Status   : [PASS | FAIL | TIMEOUT | ERROR]
  Detail   : <one-line summary of outcome>
  AIE enable: <only when the AIE enable critical warning was raised: "cores enabled at PDI load
              (aie.cdo.enable.bin in the boot image) AND started by the host; the user chose to proceed".
              Leave this line out otherwise>
  AIE debug: <only when the AIE debug critical warning was raised: "ON — package built with
              --package.enable_aie_debug (AI Engine cores halt at the PDI load); the user chose to proceed".
              Leave this line out when the option is off>
  Wallclock: <wall clock time, if available>

  Reference files for inspection:
    - Run log       : <COMPILED_OUT_DIRECTORY>/embedded_run.log
    - Run summary   : <SIM_DIR>/default.aierun_summary (only if produced by the run)
    - Emulation log : <PACKAGE_DIR>/ (launch_hw_emu.sh output, QEMU logs)
    - VCD dump      : <SIM_DIR>/foo.vcd (only if run with the `-aie-sim-options` VCD variant)
    - AIE dbg trace : <SIM_DIR>/aie_log/ (the launch always sets ENABLE_AIE_DBG_TRACE=true; list it only if it exists)
    - This report   : <COMPILED_OUT_DIRECTORY>/hw-emu_report.txt

============================================================
```

`<SIM_DIR>` is the directory after `-pl-sim-dir` in `<PACKAGE_DIR>/launch_hw_emu.sh`
(for example `<PACKAGE_DIR>/sim/behav_waveform/xsim`; the last component is the simulator the
product was built for). Read it from the script instead of building it from the `SIMULATOR`
input.

Only list reference files that actually exist for this run — omit any that were
not produced.

**When the flow stopped before the launch** (status NO-GO or a staging ERROR), use this form
instead. There is no run log, run summary or wallclock, so leave those out, and list every failure:

```
============================================================
  HW Emulation Result
  Build dir: <COMPILED_OUT_DIRECTORY>
  Package : <PACKAGE_DIR, if it was captured>
  Flow     : <FLOW, if it was captured>
  Simulator: <SIMULATOR>
============================================================

  Status   : [NO-GO | ERROR]
  Stopped at: [DRC | pre-launch checks | STEP <n> <stage name>]
  Detail   : <one-line summary: nothing was launched>

  Failures:
    - <DRC-xx or check name>: <the quoted result> — fix: <the suggestion>
    - ...

  Reference files for inspection:
    - This report   : <COMPILED_OUT_DIRECTORY>/hw-emu_report.txt

============================================================
```

For a DRC NO-GO, list each FAIL row and also the WARN rows. For a failed pre-launch check, quote the
printed list of what is missing.

If the AIE enable critical warning was raised for this package (see the same file), put the `AIE enable:` line in the
summary block when the user chose to proceed, and, when the status is TIMEOUT or the host stopped without a result, say in `Detail`
that a host stalling in `ghdl.end()` is the known symptom of this combination. If the user answered **no**, the flow ended before the launch: status NO-GO,
`Stopped at: AIE enable check`, and `Detail` says the user chose to repackage with `--package.defer_aie_run`.

If the AIE debug critical warning was raised for this package (see
[../hwemu-run/common/monitor.md](../hwemu-run/common/monitor.md)), put the `AIE debug:` line in the
summary block (and so in `hw-emu_report.txt`) whenever the user chose to **proceed** with the option on —
whatever the status. When the status is TIMEOUT or the host stopped without a result, also say in
`Detail` that the AI Engine cores were halted at the PDI load by that option. If
the user answered **no**, the flow ended before the launch: status NO-GO, `Stopped at: AIE debug
check`, and `Detail` says the user chose to repackage without the option.

If the VCD was captured with `AIE_WORK_DIR` blank, say in `Detail` that the run summary has
no AIE compiler summary and no GMIO throughput.

---

## STEP 3 — Log the report to a file

**Always write the entire report** (the full summary block above) to a durable
file, on every run regardless of outcome:

```
<COMPILED_OUT_DIRECTORY>/hw-emu_report.txt
```

**Keep the previous report first.** If a `hw-emu_report.txt` already exists, rename it before
writing (a rename only — no deletion), the same way the launch keeps the previous log:

```bash
cd <COMPILED_OUT_DIRECTORY>
[ -f hw-emu_report.txt ] && mv hw-emu_report.txt hw-emu_report.prev.txt
```

This keeps only the most recent previous report (an older `hw-emu_report.prev.txt` is replaced).
It applies to every status, including NO-GO, so a later failure never erases the last good report.

Write the same content you printed to the console — verbatim. Then **tell the
user to refer to it**:

> "Full report written to `<COMPILED_OUT_DIRECTORY>/hw-emu_report.txt` — refer to
>  it for the complete result and reference files."

Never skip writing this file.
