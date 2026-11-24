---
name: vss-sim-input
description: >
  Gathers the inputs the VSS simulation needs one at a time, from an inputs file or
  interactively, and runs the output-folder and WORK_DIR checks right after each is
  answered. Always launched first.
metadata:
  version: "1.0.0"
---

# vitis-vss-simulation — Input Gathering

**Always launched first.** Collect the values below and hand them off to the
downstream stages.

## Option A — Read inputs from a file (non-interactive)

If the user provides the **absolute path** of an **inputs file** (a `.md`/`.txt`), **read it
instead of prompting**. If they give a relative path or `~`, ask for the absolute path; never
look for an inputs file yourself. Parse `KEY = VALUE` lines matching the captured-values keys below
(ignore blank lines and `#` comments). Then:

- Use every value provided in the file as-is.
- **Only prompt for keys that are missing or empty** in the file.
- Still **check** the values: run the input checks below in the table order, then the
  STEP 1 DRC — a file value that doesn't exist on disk is a FAIL, same as an
  interactive one.

A ready-to-edit template lives at
[../scripts/vss_inputs.template.md](../scripts/vss_inputs.template.md) — copy it,
fill it in, and pass its path.

## Option B — Interactive

> ## 🧭 ASK STEP BY STEP — one input at a time
> Without an inputs file, gather inputs **one at a time**, in the table order below. Ask a
> single question, wait for the answer, resolve/confirm that variable, then move to the next.
> **Never** present the whole list at once or batch questions. If a value can be inferred from
> the compiled design, still confirm it with the user as its own step.

| Variable | How to resolve |
|---|---|
| `COMPILED_OUT_DIRECTORY` | Ask (don't default to the home/skill folder); create if missing. Where all artifacts (`vss/`, the Vivado project, logs) land. Asked first, so a NO-GO report can always be written into it. Then run its **input check** (below). |
| `SIMULATOR` | Ask which simulator to target: **xsim** (default, ships with Vitis) \| **questa** \| **vcs** \| **riviera** \| **xcelium**. Asked before `WORK_DIR`, whose checks use it. |
| `SIM_PATHS` | **Only if `SIMULATOR` != `xsim`:** ask the user for that simulator's paths — the skill **never hardcodes or derives** them. Gather: `SIM_LIB_MAP_PATH` (precompiled Xilinx sim libraries, `-lib_map_path`), `SIM_GCC_INSTALL_PATH` (simulator gcc `bin`, `-gcc_install_path`; also VCS `GCC_PATH`), `SIM_INSTALL_PATH` (simulator tool `bin`, `-install_path`), `SIM_TOOL_HOME` (simulator install home, for the AIE `c_rts` compile). For `xsim`, set all `SIM_*` to `N/A`. |
| `WORK_DIR` | **Ask** for the absolute path of the already-compiled AIE work directory (the folder holding `.target`, e.g. `<component>/build/hw/Work`). Never search/auto-discover it. Must be an `hw` build. It must be the AIE compile the `.vss` was linked from; the skill cannot check this, so as its own step ask the user to confirm it. Then run its **input checks** (below). |
| `VSS` | **Ask** for the absolute path of the already-generated `.vss` file. Never search/auto-discover it. |
| `PART` | Part string the AIE design was compiled for (default `xcvc1902-vsva2197-2MP-e-S`) |
| `TB_TOP` | Ask the user for the absolute path of the top testbench file |
| `TB_TOP_MODULE` | Ask for the name of the top testbench **module** defined in `TB_TOP` (e.g. `vss_tb`). If it is obvious from the file, propose it and confirm it with the user as its own step. |
| `TB_SOURCES` | Ask for the testbench's **dependent files** — every file the TB needs besides the top (other `.v`/`.sv` modules, packages, BFMs, etc.); **there may be many**, list them all. Else `none` |
| `SIM_RUNTIME` | Ask how long the simulation may run: `all` (default — runs until the testbench calls `$finish` or `$stop`, or no events are left; with a free-running clock and no `$finish` the run does not end) or a time such as `10us`. Vivado's default stops the run at `1000ns`, which silently cuts off any design that needs longer. |
| `COMPARE_GOLDEN` | Ask **yes/no**: will the user compare the simulation output against golden reference file(s)? Only the yes/no is captured here — the output-to-golden file pairing is asked later, after the sim, by `vss-sim-compare`, because the output files only exist then. |

**Input checks.** Run the check(s) for an input **right after the user answers it**, before
asking the next question. If a check fails, the flow stops with **NO-GO**: do not re-ask,
report each failure in the NO-GO form of the report stage
([vss-sim/vss-sim-report.md](vss-sim/vss-sim-report.md)), then run that stage (STEP 8 in
`SKILL.md`). If the `COMPILED_OUT_DIRECTORY` check fails there is nowhere to write the report
file: report in chat only.

| Input | Check (command) | PASS | FAIL → fix |
|---|---|---|---|
| `COMPILED_OUT_DIRECTORY` | `test -d "<COMPILED_OUT_DIRECTORY>" && test -w "<COMPILED_OUT_DIRECTORY>"` | Dir exists and is writable | Cannot be created, or not writable → FAIL |
| `WORK_DIR` | `cat "<WORK_DIR>/.target"` | Dir exists, `.target` reads exactly `hw` (not `hw_emu`) | Missing dir/`.target`, or content not `hw` → FAIL (the AIE design must be compiled with target `hw` first; this skill does not compile it) |
| `WORK_DIR` | `ls "<WORK_DIR>/ps/c_rts/systemC/Makefile.<SIMULATOR>"` (skip when `SIMULATOR` is not one of the five names; DRC-05 reports that) | File exists | Missing → FAIL (the c_rts compile stage needs the Makefile the AIE compiler emitted for this simulator) |
| `WORK_DIR` | GMIO command below | Prints `gmio_node_count=0` | Count above `0` → `[FAIL] the AIE design has GMIO ports (gmio_node_count=<N>); VSS simulation does not support them. Use hardware emulation instead.` File missing → `[FAIL] <WORK_DIR>/reports/complexity.csv not found, so GMIO ports can't be ruled out. Recompile the AIE design with target hw and re-run.` No `gmio_node_count` line → `[FAIL] complexity.csv has no gmio_node_count line, so GMIO ports can't be ruled out. Check the file or recompile the AIE design.` |

Run all three `WORK_DIR` checks, then report every failure.

GMIO command (prints the count, or the reason it could not be read):

```bash
f="<WORK_DIR>/reports/complexity.csv"
test -f "$f" || { echo "MISSING: $f"; exit 2; }
awk -F, '$1=="gmio_node_count"{print "gmio_node_count="$2; f=1} END{exit !f}' "$f" || { echo "NO gmio_node_count LINE"; exit 2; }
```

Notes:
- **`COMPILED_OUT_DIRECTORY` is not the simulator's output-PLIO destination.** The
  output-data location stays at its default unless the user asks; handled at
  simulation time.
- **Input/output data is plain text (`.txt`)** written by the testbench.
- **All path inputs must be absolute** (`WORK_DIR`, `COMPILED_OUT_DIRECTORY`, `VSS`, `TB_TOP`,
  every `TB_SOURCES` entry, the `SIM_*` paths). For a relative path or `~`, ask for the
  absolute path; never guess a base directory.
- **The testbench is the user's.** It should instantiate the VSS wrapper `<VSS_IMPORT>_wrapper`
  (`VSS_IMPORT` is the `.vss` basename: `vss_ss.vss` gives `vss_ss_wrapper`), drive every clock
  and reset port of the wrapper, and write its output as `output.txt` with a relative file
  name, so the file lands in the directory `simulate.sh` runs from (`SIM_SCRIPTS_DIR`).
- **The wrapper only exists after STEP 2 (import).** If the user does not know its port names
  yet, offer to stop after STEP 2, so they can read
  `<COMPILED_OUT_DIRECTORY>/vss_prj/vss_prj.srcs/sources_1/imports/hdl/<VSS_IMPORT>_wrapper.v`,
  and then continue with their testbench.

## Captured values (hand off to the rest of the flow)

```
COMPILED_OUT_DIRECTORY = <where artifacts are written / stages are launched>
SIMULATOR              = <xsim | questa | vcs | riviera | xcelium>
SIM_LIB_MAP_PATH       = <non-xsim: precompiled Xilinx sim libraries (-lib_map_path); else N/A>
SIM_GCC_INSTALL_PATH   = <non-xsim: simulator gcc bin (-gcc_install_path; also VCS GCC_PATH); else N/A>
SIM_INSTALL_PATH       = <non-xsim: simulator tool bin (-install_path); else N/A>
SIM_TOOL_HOME          = <non-xsim: simulator install home (AIE c_rts compile); else N/A>
WORK_DIR               = <absolute compiled hw work dir (holds .target = hw)>
VSS                    = <absolute path to the generated .vss>
PART                   = <part string>
TB_TOP                 = <absolute path to top testbench file>
TB_TOP_MODULE          = <name of the top testbench module defined in TB_TOP>
TB_SOURCES             = <testbench dependent files — all files the TB needs besides TB_TOP; space-separated list, or none>
SIM_RUNTIME            = <all | a time such as 10us — default all>
COMPARE_GOLDEN         = <yes | no — no skips the comparison; yes: golden files are asked after the sim>
```

Do not re-prompt for a value already captured here.
