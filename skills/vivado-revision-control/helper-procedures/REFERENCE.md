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

### list_preservable_outputs (utility)

```tcl
set items [list_preservable_outputs]
```

No parameters. Returns dict with `ips` and `bds`, each a list of `{name type
has_gen gen_dir}` dicts — standalone IPs (XCIs outside any Block Design, via
`IS_BD_CONTEXT`) and user-created top-level BDs, mirroring the exact
filtering `export_all_sources` itself uses so enumeration and export never
disagree on what's eligible. `has_gen` reports whether a `.gen` output
already exists for that item.

### prompt_preserve_outputs (Step 2.5, optional/interactive)

```tcl
set preserve_selection [prompt_preserve_outputs]
```

No parameters. Calls `list_preservable_outputs`, asks (via `gets stdin`)
whether to preserve generated outputs, and if yes, which named IPs/BDs
(`all` or a comma list) and, per 2026-09-29 methodology feedback, an
absolute path to a **remote cache directory** (outside the project) to
store the preserved `.srcs`/`.gen`. Returns `{enabled 0|1, ips {name ...},
bds {name ...}, cache_dir <absolute path>}`. An invalid/empty cache path
degrades the whole selection back to `enabled 0`, same as an empty/no-match
IP/BD selection. **Blocks on stdin — only call from an interactive
session**, never from an automated/batch pipeline. Automated callers should
pass `{}` (or omit the argument) to `export_all_sources`/`generate_build_script`
instead, which is fully backward compatible with the pre-existing 5-step
pipeline. An AI agent driving Vivado non-interactively should not call this
proc either (its `gets stdin` can't reach the human) — use
`build_preserve_selection` below instead.

### build_preserve_selection (Step 2.5, agentic/non-interactive)

```tcl
set preserve_selection [build_preserve_selection $sel_ips $sel_bds $cache_dir]
```

- `sel_ips` — list of IP names to preserve (already chosen, e.g. by an AI
  agent that asked the human directly in conversation)
- `sel_bds` — list of BD names to preserve
- `cache_dir` — absolute path to a remote cache directory OUTSIDE the
  project, to store the preserved `.srcs`/`.gen` (per 2026-09-29 methodology
  feedback, only a remote/external cache is supported)

Stdin-free equivalent of `prompt_preserve_outputs`, for callers that cannot
block on a real tty. Validates the given names against
`list_preservable_outputs` the same way the interactive path does, and
validates `cache_dir` the same way `prompt_preserve_outputs` does (absolute,
non-empty, not nested under the project); an empty/fully-invalid IP/BD
selection OR an invalid `cache_dir` returns `{enabled 0 ips {} bds {}
cache_dir {}}`. The intended flow: call `list_preservable_outputs` yourself,
ask the human which (if any) to preserve AND for the cache directory path,
then call this proc with their answers. Returns `{enabled 0|1, ips {name
...}, bds {name ...}, cache_dir <absolute path>}`, same shape as
`prompt_preserve_outputs`.

### export_all_sources

```tcl
set export_stats [export_all_sources $export_dir $flow_info $preserve_selection]
```

- `export_dir` — target directory (e.g., "RevisionControl")
- `flow_info` — (optional) dict from detect_project_flow
- `preserve_selection` — (optional) dict from `prompt_preserve_outputs`/
  `build_preserve_selection`. When omitted/`{}` or `enabled 0`, behavior is
  unchanged from before this existed. If left at its raw default AND stdin
  is a real interactive tty (`_rc_stdin_is_tty`) AND something is eligible,
  `export_all_sources` calls `prompt_preserve_outputs` itself instead of
  silently skipping preservation. When it selects IPs/BDs, those are
  excluded from the standard `write_ip_tcl`/`write_bd_tcl` export; instead,
  each selected item's own `.srcs` directory and matching `.gen` output
  directory are copied directly into `preserve_selection`'s `cache_dir`
  (external, outside the project, never git-tracked) at
  `$cache_dir/<proj_name>/{ip,bd}/<name>/{srcs,gen}/`. `export_all_sources`
  also records each preserved item's fileset-relative `.xci`/`.bd` path
  (`preserved_ip_rel_path`/`preserved_bd_rel_path`) and the resolved cache
  directory (`preserved_cache_dir`) for `generate_build_script` to restore
  from at rebuild time. Preservation granularity is whole-item only: with
  multiple standalone IPs/BDs eligible, any subset may be selected (mixed
  preservation across items is the primary use case) — but a single BD is
  always preserved or not preserved as one unit; you cannot preserve some of
  the IPs customized inside one BD while regenerating others. If a selected
  item has no `.gen` output yet, `export_all_sources` warns and exports it
  through the standard path instead of preserving it (never writes an
  incomplete cache entry) — see
  [export-all-sources/REFERENCE.md](../export-all-sources/REFERENCE.md).

Returns dict of file counts by category, including `preserved_ip_outputs`
and `preserved_bd_outputs` when preservation was used.

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

# Step 2.5 is optional. Use {} (the default) in automated/batch pipelines.
# A human at a real tty can call prompt_preserve_outputs (interactive).
# An AI agent should call list_preservable_outputs, ask the human directly,
# then call build_preserve_selection -- see SKILL.md Step 2.5 for the flow.
set preserve_selection [prompt_preserve_outputs]

if {$scenario ne "Remote"} {
    export_all_sources "RevisionControl" $flow_info $preserve_selection
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
