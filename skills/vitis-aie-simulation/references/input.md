---
name: aiesim-input
description: >
  Gathers the inputs the AIE simulation needs (COMPILED_OUT_DIRECTORY, WORK_DIR,
  INPUT_DIR, COMPARE_GOLDEN) one at a time and runs each input's check right after
  it is answered. Always launched first.
metadata:
  version: "1.0.0"
---

# vitis-aie-simulation — Input Gathering

**Always launched first.** Collect the values below and hand them off to the
downstream stages.

> ## 🧭 ASK STEP BY STEP — one input at a time
> Gather inputs **one at a time**, in the table order below. Ask a single
> question, wait for the answer, resolve/confirm that variable, then move to the
> next. **Never** present the whole list at once or batch multiple questions into
> a single prompt. If a value can be inferred from the compiled design, still confirm it
> with the user as its own step before moving on.

| Variable | How to resolve |
|---|---|
| `COMPILED_OUT_DIRECTORY` | Ask (don't default to the home/skill folder); create if missing. The directory `aiesimulator` is launched from; where its logs and `aiesimulator_output/` land. Asked first, so a NO-GO report can always be written into it. Then run its **input check** (below). |
| `WORK_DIR` | **Ask** for the absolute path of the already-compiled AIE work directory (the folder holding `.target`, e.g. `<component>/build/hw/Work`). Never search/auto-discover it. Must be an `hw` build. Then run its **input checks** (below). |
| `INPUT_DIR` | First read the PLIO file paths from the compiled design (the **PLIO paths** command below), then follow **INPUT_DIR steps** below. Then run its **input check** (below). |
| `COMPARE_GOLDEN` | Ask **yes/no**: will the user compare the sim output against golden reference file(s)? Only the yes/no is captured here — the output-to-golden file pairing is asked later, after the sim, by `aiesim-compare`. |

**Input checks.** Run the check(s) for an input **right after the user answers it**, before
asking the next question. If a check fails, the flow stops with **NO-GO**: do not re-ask,
report each failure in the NO-GO form of the report stage
([aiesim/aiesim-report.md](aiesim/aiesim-report.md)), then run that stage
(STEP 3 in `SKILL.md`). If the `COMPILED_OUT_DIRECTORY` check fails there is nowhere to write
the report file: report in chat only.

| Input | Check (command) | PASS | FAIL → fix |
|---|---|---|---|
| `COMPILED_OUT_DIRECTORY` | `test -d "<COMPILED_OUT_DIRECTORY>" && test -w "<COMPILED_OUT_DIRECTORY>"` | Dir exists and is writable | Cannot be created, or not writable → FAIL |
| `WORK_DIR` | `cat "<WORK_DIR>/.target"` | Dir exists, `.target` reads exactly `hw` (not `hw_emu`) | Missing dir/`.target`, or content not `hw` → FAIL (a `hw_emu` or `x86sim` build cannot be simulated; the design must be compiled with target `hw` first; this skill does not compile) |
| `WORK_DIR` | `ls "<WORK_DIR>/config/scsim_config.json"` | File exists | Missing → FAIL (the simulator reads it at startup and the `INPUT_DIR` step reads the PLIO paths from it; the folder is not complete `hw` compiler output — wrong folder, or an incomplete compile; compile the design with target `hw` and give that `Work` directory; this skill does not compile) |
| `INPUT_DIR` | `INPUT_DIR = N/A` → nothing to check; else `ls "<INPUT_DIR>/<path>"` for **each `INPUT` line** of the PLIO paths command | `INPUT_DIR = N/A` (pure GMIO), **or** every input PLIO file exists under `INPUT_DIR` at the path the graph gives | Any file missing → FAIL; print **each missing full path**, so the user sees which folder `INPUT_DIR` should be. If a listing of the folder the user gave shows where the file actually is, you may name that folder as a hint in the fix; never search elsewhere to find it |

Run both `WORK_DIR` checks, then report every failure.

**PLIO paths.** The compiled design records the file path of every PLIO in
`<WORK_DIR>/config/scsim_config.json` (the file the `WORK_DIR` check confirmed). This command
prints one `INPUT <path>` line per input PLIO and one `OUTPUT <path>` line per output PLIO,
exactly as the graph wrote them (for example `INPUT in/input.txt`, or just `INPUT input.txt`):

```bash
python3 -I - "<WORK_DIR>/config/scsim_config.json" <<'PYEND'
import json, sys
for b in json.load(open(sys.argv[1]))["SimulationConfig"].get("pl_ip_block", []):
    for s in b.get("axi_stream") or []:
        d = s.get("direction")
        if d in ("pl_to_me", "me_to_pl") and b.get("arguments"):
            print("INPUT" if d == "pl_to_me" else "OUTPUT", b["arguments"][0])
PYEND
```

**INPUT_DIR steps**

1. **No `INPUT` line** → the design has no `input_plio`: it is **pure GMIO** (input supplied in code via
   `gm2aie_*`/`aie2gm_*`). Do not ask for a directory. Tell the user it looks like a pure-GMIO design, ask
   them to confirm they want to skip, and on confirm set `INPUT_DIR = N/A`.
2. **≥1 `INPUT` line** → ask for the input data directory: the folder the printed input paths are relative to
   (for `INPUT in/input.txt`, the folder that contains `in/`; for a bare `INPUT input.txt`, the folder that
   holds that file). **Show the printed paths in the question.** Do not assume the folder is called `data`.
   **Never search/auto-discover it.**

Notes:
- **Path inputs must be absolute** (`WORK_DIR`, `COMPILED_OUT_DIRECTORY`, `INPUT_DIR`). If the user gives a
  relative path or `~`, ask for the absolute path; never guess a base directory.
- **`COMPILED_OUT_DIRECTORY` ≠ aiesimulator `--output-dir`.** The latter is the folder
  for the output PLIO files, `aiesim_options.txt` and the run summary
  (`aiesimulator_output` in the launch folder by default); it stays at its default
  unless the user asks, and is handled at simulation time.
- **Input and output file locations come from the graph.** An input file is read from
  `<INPUT_DIR>/<input PLIO path>`; an output file is written to `<output folder>/<output PLIO path>`
  (`aiesimulator_output` unless `--output-dir` is given). A PLIO path can start with `data/`, `in/`,
  `out/`, any other folder, or no folder at all, and the file may be `.txt` or `.csv`.
- **Pure-GMIO handoff:** `INPUT_DIR = N/A` is decided here and passed on; the sim stage then launches
  without `--input-dir` and does not re-derive it.

## Captured values (hand off to the rest of the flow)

```
COMPILED_OUT_DIRECTORY = <where simulator logs and aiesimulator_output/ are written>
WORK_DIR               = <absolute compiled hw work dir (holds .target = hw)>
INPUT_DIR              = <absolute dir such that <INPUT_DIR>/<each input PLIO path> exists ; N/A for pure GMIO>
COMPARE_GOLDEN         = <yes | no — no skips the comparison; yes: golden files are asked after the sim>
```

Do not re-prompt for a value already captured here.
