---
name: aiesim-compare
description: >
  Compares the aiesimulator output files against user-supplied golden reference
  files, pairing each output with its golden file after the simulation, and
  produces COMPARE_RESULT (one MATCH / MISMATCH / SKIPPED result per pair).
  Consumes COMPARE_GOLDEN and the output PLIO files. Optional —
  skipped when COMPARE_GOLDEN is no.
metadata:
  version: "1.2.0"
---

# AIE Simulation Compare Agent

You **compare the simulation outputs against the golden references** the user
supplies, and hand the result to the report stage. Report only what the commands
actually output. If a listing or comparison command errors out (e.g. a file is
unreadable), report it per [../rules.md](../rules.md), then stop.

## Inputs

```
COMPARE_GOLDEN = <yes | no>
Output files   = <launch-dir>/aiesimulator_output/<output PLIO path>, one per `OUTPUT` line of the
                 "PLIO paths" command in ../input.md (or under <output-dir>/ if --output-dir was set)
Log            = <launch-dir>/AIESimulator.log
Time stamps    = <on by default | off if the user asked for --output-time-stamp=no>
```

---

## STEP 1 — Decide whether to compare

- `COMPARE_GOLDEN = no` → `COMPARE_RESULT = SKIPPED (user chose no comparison)`.
  Done — ask nothing, list nothing.
- The sim did **not** finish — `Simulation Finished` or `core(s) are done executing`
  missing from the log, memory-violation lines present, or the actual stall/closing
  markers from "Detecting a hang" in [aiesim.md](aiesim.md) present →
  `COMPARE_RESULT = SKIPPED (sim did not finish)`. Done; do not prompt for pairs.
- None of the output files exists (e.g. a pure-GMIO design, whose results are not
  written as PLIO output files) →
  `COMPARE_RESULT = SKIPPED (no PLIO output files)`. Done; do not prompt for
  pairs and do not look elsewhere.
- Otherwise continue to STEP 2.

## STEP 2 — List the output files

List the output files the simulator actually produced, so the user can see them —
once per `OUTPUT` path:

```bash
ls -l <output file>
```

Show the list to the user. Only look at these paths — do not search elsewhere.

## STEP 3 — Pair each output with its golden file

> **Ask one output at a time.** For each listed output file, ask the user for the
> path to its golden file (or `skip` to leave it unverified). Never
> search/auto-discover golden files, and never guess a pairing.

Outputs the user skips are reported as `SKIPPED (user chose skip)`. Any other
output left without a golden file is reported as `NOT VERIFIED`. If every output
is skipped, the overall result is `SKIPPED`.

## STEP 4 — Compare each pair

The default launch writes time values into the output files. A `.txt` output interleaves `T <time>`
lines and `TLAST` lines (one per frame) with the data values, so strip both. aiesimulator also writes a
trailing space after each value, so diff ignoring trailing whitespace on both sides (`-Z`, a GNU
`diff` option). Run once per pair:

```bash
grep -v -e '^T ' -e '^TLAST' <output file> | diff -Z - <golden file>
```

If the sim was run with `--output-time-stamp=no` (the user asked for it), the `.txt` output holds data
values only, so diff it directly:

```bash
diff -Z <output file> <golden file>
```

Use the form that matches how the sim was run; if you can't tell from the launch
command, look at the first lines of the output file rather than guessing.

- **MATCH** — `diff` prints nothing.
- **MISMATCH** — `diff` prints differences; quote the first few lines of the
  actual diff in the result. Never paraphrase or guess at the cause.
- A `.csv` output (from `.csv` PLIO files) is not a values-only file, whatever the flags. It has a
  header line `CMD, D, …, D, TLAST, TKEEP` with **one `D` column per 32 bits of PLIO width**
  (32-bit PLIO: `CMD, D, TLAST, TKEEP`; 64-bit: `CMD, D, D, TLAST, TKEEP`), and one row per beat
  with `, ` separators: `DATA:1, <the D values>, <TLAST>, <TKEEP>` (for example `DATA:1, 1, 0, -1`
  at 32 bits, `DATA:1, 0, 2, 0, -1` at 64 bits). `TLAST` is 0 except on a beat where the graph's
  kernel asserts it (the input's TLAST is not passed through). The default launch adds a last column
  `TIME_NS` (`CMD, D, TLAST, TKEEP, TIME_NS`); `--output-time-stamp=no` leaves it out. A `.csv` output
  never matches a values-only golden file. A golden `.csv` must have the same header, columns and
  separators, whatever the width. When the output has `TIME_NS` and the golden has no such column,
  drop that last column from the output before the diff:
  `sed 's/, [^,]*$//' <output file> | diff -Z - <golden file>`. If the golden has the column too, or
  the output has none, use `diff -Z <output file> <golden file>`.
  A `.csv` of any width belongs here, not in the "any other layout" case below.
- For any other layout, do **not** assume a format — show a few lines of each file and ask
  the user how to compare that pair.

This compares text only and modifies neither file; it asserts nothing beyond
matching the supplied golden files.

---

## Output

One line per output file:

```
COMPARE_RESULT =
  <output file> vs <golden file> : <MATCH | MISMATCH (first diff lines)>
  <output file>                  : SKIPPED (user chose skip)
  <output file>                  : NOT VERIFIED (no golden file given)
  ...
  OVERALL: <MATCH if every pair matched and none SKIPPED / NOT VERIFIED
            | MISMATCH | PARTIAL | SKIPPED if every output was skipped>
```

or, when nothing was compared:

```
COMPARE_RESULT = SKIPPED (<reason>)
```
