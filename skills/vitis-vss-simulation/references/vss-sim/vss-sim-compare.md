---
name: vss-sim-compare
description: >
  Compares the simulation output files against user-supplied golden reference
  files, pairing each output with its golden file after the simulation, and
  produces COMPARE_RESULT (one MATCH / MISMATCH / SKIPPED result per pair).
  Consumes COMPARE_GOLDEN. Optional — skipped when COMPARE_GOLDEN is no.
metadata:
  version: "1.1.0"
---

# VSS Simulation Compare

You **compare the simulation outputs against the golden references** the user
supplies, and hand the result to the report stage. You report only what the
commands actually output (see [../rules.md](../rules.md)).

The output and golden files are **plain text (`.txt`)** — the testbench creates
them. They hold data values only; this flow produces no `T <time>` timestamp lines.

If a listing or comparison command errors out (e.g. a file is unreadable), report it per the
error-reporting convention in [../rules.md](../rules.md), then stop.

## Inputs

```
COMPARE_GOLDEN = <yes | no>
Log            = <SIM_SCRIPTS_DIR>/simulate.log
```

---

## STEP 1 — Decide whether to compare

- `COMPARE_GOLDEN = no` → `COMPARE_RESULT = SKIPPED (user chose no comparison)`.
  Done — ask nothing.
- The sim did **not** finish — the completion markers are missing (see
  [vss-sim-report.md](vss-sim-report.md)) →
  `COMPARE_RESULT = SKIPPED (sim did not finish)`. Done; do not prompt for pairs.
- Otherwise continue to STEP 2.

## STEP 2 — Pair each output with its golden file

> **Ask one output at a time.** The output files only exist after the run. The
> testbench's output is expected at `<SIM_SCRIPTS_DIR>/output.txt`: propose it, and
> **ask the user** to confirm it and to give the absolute path of its golden file
> (or `skip` to leave it unverified). If the testbench wrote the output somewhere
> else, ask for that path. Never search for or guess output or golden files.

Outputs the user skips are reported as `SKIPPED (user chose skip)`. Any other
output left without a golden file is reported as `NOT VERIFIED`. If every output
is skipped, the overall result is `SKIPPED`. If a path the user gives does not
exist, report that and ask again; do not look elsewhere.

## STEP 3 — Compare each pair

Diff the output against the golden file, ignoring trailing whitespace on both
sides (`-Z`). Run once per pair:

```bash
diff -Z <output file> <golden file>
```

If an output or golden file is not plain data values (a different extension, or
a layout with extra columns or header lines), do **not** assume a format:
show the first few lines of each file and ask the user how to compare that pair.

This only reads the files — neither the output nor the golden file is modified.

- **MATCH** — `diff` prints nothing.
- **MISMATCH** — `diff` prints differences; quote the first few lines of the
  actual diff in the result. Never paraphrase or guess at the cause.

This compares text only; it does not assert the design is functionally correct
beyond matching the supplied golden files.

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
