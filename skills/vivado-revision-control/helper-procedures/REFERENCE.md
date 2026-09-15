<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

---
name: helper-procedures
description: Tcl procedure library API reference — all five pipeline procedures plus utilities.
---

# Helper Procedures Library

All revision control procedures live in `helper_scripts.tcl`. Source it once
before calling any procedure:

```tcl
source <skill-path>/helper-procedures/helper_scripts.tcl
```

## Procedure Reference

### detect_project_flow

```tcl
set flow_info [detect_project_flow]
```

No parameters. Returns dict with: project_name, device, flow_type, is_dfx,
is_segmented_config, is_versal, is_ipi_bdc, bdc_count, bd_file_count, pblock_count.

### analyze_source_locations

```tcl
set scenario_info [analyze_source_locations]
```

No parameters. Returns dict with: project_dir, scenario, local_count,
remote_count, remote_paths, strategy.

### export_all_sources

```tcl
set export_stats [export_all_sources $export_dir $flow_info]
```

- `export_dir` — target directory (e.g., "RevisionControl")
- `flow_info` — (optional) dict from detect_project_flow

Returns dict of file counts by category.

### capture_project_settings

```tcl
set output_path [capture_project_settings $output_file]
```

- `output_file` — path for the .tcl output

Returns the output file path.

### generate_build_script

```tcl
set output_path [generate_build_script $output_file $output_dir $scenario_info]
```

- `output_file` — path for build.tcl
- `output_dir` — base directory with Sources/ and Scripts/
- `scenario_info` — (optional) dict from `analyze_source_locations`. Controls `import_files` vs `add_files` and emits remote paths.

Returns the output file path.

### pr_verify (utility)

```tcl
pr_verify
```

Validates DFX configuration: checks PR_FLOW, reconfigurable cells, Pblocks.
Returns 1 on success, errors on failure. Only meaningful for DFX projects.

### capture_verification_manifest (optional, utility)

```tcl
capture_verification_manifest $output_file $export_dir
```

- `output_file` — path for the manifest .tcl output (e.g.
  `RevisionControl/Scripts/verification_manifest.tcl`)
- `export_dir` — the export directory used in `export_all_sources` (used to
  derive the expected BD list from the actual exported `Sources/BD/*.tcl` files)

Run in the **original** project's session, after `export_all_sources`.
Snapshots expected BD names, top module (and whether it's an auto-managed
wrapper with no physical file), run names, and IP count, so a later
`verify_rebuild` call can diff the recreated project against it. Returns the
output file path.

### verify_rebuild (optional, utility)

```tcl
verify_rebuild $manifest_file
```

- `manifest_file` — path to the manifest written by `capture_verification_manifest`

Run in the **recreated** project's session, after sourcing `build.tcl`.
Checks (in order): all expected Block Designs present, top module resolves
(explicitly catches the "auto-managed wrapper never generated" bug class —
top references `<bd>_wrapper` but no file exists), all expected runs present,
and IP count is not lower than expected. Prints a PASS/FAIL/WARN report per
category and returns 1 if all checks passed, 0 otherwise.

## Complete Pipeline Example

```tcl
source helper-procedures/helper_scripts.tcl

set flow_info [detect_project_flow]
set scenario_info [analyze_source_locations]
set scenario [dict get $scenario_info scenario]

if {$scenario ne "Remote"} {
    export_all_sources "RevisionControl" $flow_info
}

file mkdir RevisionControl/Scripts
capture_project_settings "RevisionControl/Scripts/project_settings.tcl"
generate_build_script "RevisionControl/Scripts/build.tcl" "RevisionControl" $scenario_info

# Optional: capture a manifest for later rebuild verification
capture_verification_manifest "RevisionControl/Scripts/verification_manifest.tcl" "RevisionControl"
```

Then, in a fresh Vivado session pointed at a clean copy of `RevisionControl/`:

```tcl
source helper-procedures/helper_scripts.tcl
source Scripts/build.tcl
verify_rebuild "Scripts/verification_manifest.tcl"
```

## Extending

Don't modify helper_scripts.tcl directly. Create a separate file and source
both:

```tcl
source helper-procedures/helper_scripts.tcl
source my_custom_helpers.tcl
```
