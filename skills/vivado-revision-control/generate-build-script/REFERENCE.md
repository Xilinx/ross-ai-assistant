<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

---
name: generate-build-script
description: Step 5 — generates automated build.tcl for one-command project recreation.
---

# Generate Build Script (Step 5)

**Source file:** `helper-procedures/helper_scripts.tcl`
**Proc name:** `generate_build_script`

## Procedure

```tcl
generate_build_script "RevisionControl/Scripts/build.tcl" "RevisionControl" $scenario_info
```

**Parameters:**
- `output_file` (string) — path for generated build.tcl
- `output_dir` (string) — base directory containing Sources/ and Scripts/
- `scenario_info` (dict, optional) — output from Step 2 (`analyze_source_locations`). Used to determine `import_files` (local) vs `add_files` (remote) and to emit remote source paths in build.tcl.

**Returns:** the output file path.

## Generated Build Script Structure

The build.tcl follows this 11-step sequence:

1. **Create project** — `create_project` with correct PART and BOARD_PART
2. **Add RTL** — `import_files` for local sources from Sources/RTL/; `add_files` for remote sources (if Mixed scenario)
3. **Add constraints** — add XDC files to constrs_1 fileset
4. **Add simulation** — add files to sim_1 fileset
5. **Add data files** — add .mem, .coe, etc.
6. **Recreate IPs** — source each IP Tcl script from Sources/IP/, then `create_ip_run` for standalone OOC IPs
7. **Recreate BDs** — source each BD Tcl script from Sources/BD/
8. **Create BD wrappers** — `make_wrapper` + `add_files` for BDs that had wrappers in `.gen/`
9. **Generate BD targets + IP runs** — `generate_target all` on each BD, then `create_ip_run` on BD files
10. **Apply settings** — `source project_settings.tcl` (BEFORE finalize so TOP, PR_FLOW etc. are set)
11. **Finalize** — `update_compile_order` for sources and sim filesets

All paths are relative to the script location using `[info script]`.

## Critical: Settings BEFORE Finalize

`project_settings.tcl` is sourced in Step 10, after all sources, IPs, and BDs
are added but BEFORE `update_compile_order`. Properties like TOP, PR_FLOW, and
VERILOG_DEFINE affect how Vivado resolves compile order. IP OOC runs must exist
(created in Steps 6 and 9) before settings for those runs can be applied.

## Critical: IP OOC Runs

For standalone IPs (outside Block Designs), `create_ip_run` is called in Step 6
after sourcing the IP Tcl scripts. For IPs inside Block Designs, Step 9 runs
`generate_target all` on each BD followed by `create_ip_run` using:
```tcl
create_ip_run [get_files -of_objects [get_fileset sources_1] $bd]
```
This ensures OOC synthesis runs exist before project settings are applied.

## DFX: RM Ordering

For DFX projects, RM (Reconfigurable Module) Block Designs must be sourced
BEFORE the static design in Step 7. The static design's Block Design Containers
reference RMs by name — if the RMs don't exist yet, BDC instantiation fails:

```
ERROR: [BD 41-1279] Block Container 'rp1_container' is referencing
an instance 'rp1rm1' that does not exist in the design.
```

The current `generate_build_script` procedure uses a simple glob which may not
guarantee order. For DFX projects, consider manually ordering the BD scripts
in the generated build.tcl (RMs first, then static).

## Testing

Always test the generated build.tcl in a clean directory before committing:
```bash
mkdir /tmp/test_rebuild && cd /tmp/test_rebuild
cp -r /path/to/RevisionControl .
vivado -mode batch -source RevisionControl/Scripts/build.tcl
```

## When to Use vivado_doc_search

- For `create_project` options (e.g., `-in_memory`, `-part` vs `-board_part`)
- For `update_compile_order` behavior
- For `generate_target` usage when recreating IP or BD
