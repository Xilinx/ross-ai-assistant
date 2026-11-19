---
name: vss-import
description: >
  Imports the generated .vss (Vitis Subsystem) into a Vivado project via a Tcl
  script in batch mode, validates the block design, and creates the wrapper.
  Consumes PART and VSS. Produces PRJ_NAME and VSS_IMPORT.
metadata:
  version: "1.1.0"
---

# VSS Import

Your job is to import the generated `.vss` into a Vivado project, validate the
block design, and create the wrapper.

## Inputs

- `PART` — the target part.
- `VSS` — absolute path of the generated `.vss` (user-supplied).

---

## STEP 1 — Import the .vss into Vivado

Import the generated `.vss` into a Vivado project using the **bundled, reusable**
Tcl script — **do not write a new one each run**:

```
<skill-dir>/scripts/import_vss.tcl
```

It is parameterized via `-tclargs`, so pass the run's values instead of editing it:

- `PRJ_NAME` — Vivado project name (default: `vss_prj`).
- `PART` — the target part.
- `VSS_IMPORT` — the basename of the `.vss`, derived from `VSS` (e.g. for
  `/path/vss/vss_ss.vss`, `VSS_IMPORT=vss_ss`).
- `VSS_DIR` — the absolute directory holding the `.vss`, derived from `VSS`
  (e.g. `/path/vss`).

Run it in Vivado batch mode **from `COMPILED_OUT_DIRECTORY`**:

```bash
cd <COMPILED_OUT_DIRECTORY>
vivado -mode batch -source <skill-dir>/scripts/import_vss.tcl \
       -tclargs vss_prj <PART> <VSS_IMPORT> <VSS_DIR>
```

The script creates the project, runs `vitis::import_vss`, validates the BD, and
creates the wrapper. Report whether the import, `validate_bd_design`, and wrapper
creation completed successfully.

The `.vss` must come from the same Vitis version as the Vivado that runs the import. A `.vss`
linked with an older build fails `validate_bd_design` with `[BD 5-336]` and a locked IP
(for example `vss_ss_ai_engine_ctrl_0`): report it and ask for a `.vss` linked with this version.

Capture the output to a log in `COMPILED_OUT_DIRECTORY` (for example
`> import_vss.log 2>&1`). Treat the stage as successful only if Vivado exits with
rc 0 **and** the log contains no `ERROR:` lines. Report every `CRITICAL WARNING`
to the user; only the one listed under "Expected warning" below is explained as
expected.

### Expected warning

`CRITICAL WARNING: [filemgmt 20-730] Could not find a top module in the fileset
sources_1.` is printed right after `vitis::import_vss` and the first
`update_compile_order`, before `validate_bd_design` and `make_wrapper` run. At
that point the project has no top module yet; the wrapper step creates one. It is
expected here and did not stop the import. If it appears **after** the wrapper
step, or together with an `ERROR` from `validate_bd_design` or `make_wrapper`,
treat it as a real problem.

---

## Output

```
PRJ_NAME   = <Vivado project name (default vss_prj)>
VSS_IMPORT = <basename of the imported .vss>
```
