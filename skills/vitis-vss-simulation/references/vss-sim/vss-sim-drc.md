---
name: vss-sim-drc
description: >
  Pre-simulation Design Rule Checks (DRCs) for VSS simulation runs.
  Validates that the .vss and testbench files exist, the part is set, make is
  reachable, the selected simulator's paths are valid (and, for Xcelium,
  its tools are on PATH), and the simulation run time is well formed. The checks
  on the output directory and the compiled AIE work directory run earlier, during
  input gathering ([../input.md](../input.md)).
metadata:
  version: "1.2.0"
---

# VSS Simulation DRC (validation-only)

You are a **pre-simulation gatekeeper** for VSS (Vitis Subsystem) simulation runs.
Run the fixed DRCs below, report each as PASS / FAIL / WARN, then give a final
GO / NO-GO decision.

> **CHECK-ONLY.** All inputs are gathered up front; this pass only **validates**
> them. If a required value is missing/invalid, record a FAIL and report it for
> correction — do **not** search the filesystem or re-prompt for already-gathered
> values.

**Inputs (provided):** `VSS`, `PART`, `SIMULATOR`, `SIM_LIB_MAP_PATH`,
`SIM_GCC_INSTALL_PATH`, `SIM_INSTALL_PATH`, `SIM_TOOL_HOME`, `TB_TOP`,
`TB_TOP_MODULE`, `TB_SOURCES`, `SIM_RUNTIME`.

## DRC Execution Protocol

Run **ALL** the checks (don't stop early); collect every result, print the summary
table, then give the verdict. For each DRC, run only the documented command
against the captured value — no prompting, no filesystem hunting. On a FAIL, do not re-ask:
report the value for correction.

| DRC | Check | Validation command | PASS | FAIL → fix |
|---|---|---|---|---|
| 01 | Generated .vss | `ls "<VSS>"` | File exists | Missing → FAIL (this skill does not generate the `.vss`) |
| 02 | Part | `PART` non-empty (the default `xcvc1902-vsva2197-2MP-e-S` counts) | `PART` is a part string | Empty → FAIL (the import needs a part string) |
| 03 | Build tool reachable | `which make` | Found | Missing → FAIL (`make` runs the AIE `c_rts` compile) |
| 04 | Testbench | `TB_TOP_MODULE` non-empty; `ls "<TB_TOP>"`; `ls` each `TB_SOURCES` entry (skip when `none`) | Module name set and every file exists | Empty module name or a missing file → FAIL (list each missing file) |
| 05 | Simulator selection and paths | `SIMULATOR` is one of `xsim`, `questa`, `vcs`, `riviera`, `xcelium`; for a non-`xsim` simulator `test -d` each of `SIM_LIB_MAP_PATH`, `SIM_GCC_INSTALL_PATH`, `SIM_INSTALL_PATH`, `SIM_TOOL_HOME`, and `test -x` these tools in `SIM_INSTALL_PATH`: `vlog` and `vsim` (questa, riviera), `vcs` and `vlogan` (vcs), `xrun` and `xmvlog` (xcelium) | Valid name; `xsim` with all `SIM_*` = `N/A` → N/A, PASS; others: every path exists and the tools are present | Unknown name, a missing non-`xsim` path, or a missing tool → FAIL (list each; the skill never derives these paths) |
| 06 | Simulation run time | `SIM_RUNTIME` is `all`, or a number followed by `fs`, `ps`, `ns`, `us`, `ms` or `s`, e.g. `10us` | Has one of those forms | Anything else → FAIL |
| 07 | Xcelium environment | `SIMULATOR` is `xcelium`: `which cds_root && which xrun` (N/A, PASS for any other simulator) | Both found, so the Xcelium `bin` directory (`SIM_INSTALL_PATH`) is on `PATH` | Either missing → FAIL; do not change `PATH` yourself: tell the user to put `<SIM_INSTALL_PATH>` on `PATH` in the shell the session is launched from, then relaunch (otherwise the sim scripts from STEP 3 can come out with a wrong path and STEPs 4 and 6 fail) |

## Output Format

After running all checks, print:

```
============================================================
  VSS Simulation DRC Report
  Work dir: <WORK_DIR>
============================================================

  DRC-01  Generated .vss       [PASS|FAIL|WARN]  <detail>
  DRC-02  Part                 [PASS|FAIL|WARN]  <detail>
  DRC-03  Build tool (make)    [PASS|FAIL|WARN]  <detail>
  DRC-04  Testbench            [PASS|FAIL|WARN]  <detail>
  DRC-05  Simulator            [PASS|FAIL|WARN]  <detail>
  DRC-06  Simulation run time  [PASS|FAIL|WARN]  <detail>
  DRC-07  Xcelium environment  [PASS|FAIL|WARN]  <detail>

------------------------------------------------------------
  FAILs: <count>   WARNs: <count>
------------------------------------------------------------
```

Then the verdict.

**0 FAILs:**
```
  VERDICT: GO — all hard checks passed. Safe to proceed.
  <list any WARNs the user should be aware of>
```

**Any FAILs:**
```
  VERDICT: NO-GO — fix the following before running the simulation:

  [FAIL] DRC-XX — <one-sentence fix (usually: supply/correct the value)>
  ...
```

On a NO-GO the flow stops here, and the report stage (STEP 8) writes `vss-sim_report.txt` with status
NO-GO and the failing rows (see [vss-sim-report.md](vss-sim-report.md)).
