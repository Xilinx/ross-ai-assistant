<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

---
name: export-all-sources
description: Step 3 — exports all project components (RTL, constraints, IP, BD, simulation, data) to an organized directory.
---

# Export All Sources (Step 3)

**Source file:** `helper-procedures/helper_scripts.tcl`
**Proc name:** `export_all_sources`

## Procedure

```tcl
export_all_sources "RevisionControl" $flow_info $preserve_selection
```

**Parameters:**
- `export_dir` (string) — output directory, e.g. "RevisionControl"
- `flow_info` (dict, optional) — output from Step 1. Enables DFX and Segmented Config-specific exports. If omitted, assumes Standard project.
- `preserve_selection` (dict, optional) — output from Step 2.5's `prompt_preserve_outputs`/`build_preserve_selection`: `{enabled, ips, bds, cache_dir}`. If omitted/`{}`, behaves exactly as before this feature existed (100% backward compatible with older/existing projects and callers). If it selects one or more IPs/BDs, those are routed to the preserve-outputs branch (see below) instead of the standard `write_ip_tcl`/`write_bd_tcl` export. `cache_dir` must be an absolute path outside the project (remote-cache-only, per 2026-09-29 methodology feedback) — an invalid/missing `cache_dir` while IPs/BDs are named disables preservation for the whole export.

**Returns:** dictionary of export counts (rtl_files, constraint_files, ip_files, bd_files, sim_files, data_files, hook_scripts, dfx_dcp_files, pblock_files, noc_files, preserved_ip_outputs, preserved_bd_outputs).

## Preserve-Outputs Branch (per-IP/BD, optional)

When `preserve_selection` names a given standalone IP or user-created BD,
that item is **excluded** from the standard `write_ip_tcl`/`write_bd_tcl`
export path. As of 2026-09-29 (GP methodology feedback), the item's own
`.srcs` directory (the `.xci`/`.bd` plus any sibling files — for a BD, this
also includes any nested per-instance `ip/<name>/<name>.xci` customized only
inside that BD) and its matching `.gen` output directory are copied directly
into an external **remote cache directory** (`preserve_selection`'s
`cache_dir`, outside the project, never git-tracked) at:
`$cache_dir/<proj_name>/{ip,bd}/<name>/{srcs,gen}/`. Only that item's own
subtree is copied — never the whole project's `.gen`/`.srcs`, and never the
`.xpr`. `export_all_sources` also records, per selected item, the
fileset-relative path of its `.xci`/`.bd` (e.g. `sources_1/ip/<name>/<name>.xci`
— do not assume `sources_1`, a BD/IP can live in a different fileset) so
`generate_build_script` knows exactly where inside the recreated project to
restore it, rather than guessing.

**Preservation is whole-item only, not partial within a BD:** a BD is either
preserved as a single unit (its `.srcs` directory and everything customized
inside it) or not preserved at all — there is no way to preserve only some
of the IPs customized inside a given BD while regenerating others. Partial
preservation ACROSS separate top-level items (some standalone IPs/BDs
preserved, others left to the standard export path) is fully supported and
is the primary intended use case; partial preservation WITHIN a single BD
is an intentional scope cut (possible future enhancement), not a bug.

**If a selected item has no `.gen` output yet** (never generated/synthesized
— `list_preservable_outputs`'s `has_gen` reports this in advance, but is
only surfaced as a warning in the interactive prompt, not enforced there),
`export_all_sources` itself checks for this and, if the `.gen` directory
doesn't exist, prints a `WARNING` and exports that one item through the
standard `write_ip_tcl`/`write_bd_tcl` path instead of adding it to the
cache — it does not silently write a cache entry with an empty/missing
`gen/` subfolder (which would otherwise only surface as a confusing hard
error much later, at rebuild time). This check runs regardless of caller
(interactive or agentic), so an agentic caller that skips the interactive
`has_gen` warning still gets this enforcement. Items skipped this way are
listed in the final export summary under "Preservation Requested But
Skipped" — generate/synthesize them and re-export to actually preserve them.

If a selected item's `.xci`/`.bd` is located OUTSIDE `$proj_dir` (Remote/Mixed
scenario), it falls back to the standard export path instead — cache
preservation only ever captures what's inside `$proj_dir`, so there's
nothing local to copy for an external source. There is no Tcl fallback
written for a preserved item that succeeds: rebuild hard-errors instead if
the cache is ever missing/incomplete (see
[generate-build-script/REFERENCE.md](../generate-build-script/REFERENCE.md)).

Every other, non-selected IP/BD in the same project is completely
unaffected and continues through the standard export path below — this
branching is per-item, not a project-wide switch.

## What Gets Exported

| Category | Source | Destination | Method |
|----------|--------|-------------|--------|
| RTL | Verilog/SV/VHDL/VHDL2019/VH/SVH in .srcs/ | Sources/RTL/ | file copy |
| Constraints | XDC in .srcs/ (not _ooc.xdc) | Sources/Constraints/ | file copy |
| Standalone IP | IPs NOT inside Block Designs | Sources/IP/ | `write_ip_tcl` |
| Block Designs | User-created BDs in .srcs/ | Sources/BD/ | `write_bd_tcl` |
| Simulation | Files from sim filesets | Sources/Simulation/ | file copy |
| Data | .mem, .mif, .coe, .hex, .elf, .csv in project dir | Sources/Data/ | file copy |
| Hook Scripts | Tcl pre/post hooks on synth/impl runs | Sources/Scripts/ | file copy |
| Pblocks (DFX) | XDC containing pblock defs | Sources/Constraints/ | file copy |
| DCPs (DFX) | Locked static checkpoints | Sources/Checkpoints/ | file copy |
| NoC (Seg.Config) | .ncr solution files | Sources/NoC/ | file copy |

## Critical Filtering Rules

**Skip generated files:** Any file with `IS_GENERATED == true` or in a `.gen/` directory is excluded. These are auto-produced by Vivado and should not be version-controlled.

**Skip BD-embedded IPs:** IPs that are part of a Block Design (`IS_BD_CONTEXT == true`) are NOT exported via `write_ip_tcl` — they're already captured by `write_bd_tcl`. Attempting to export them separately produces warnings and redundant scripts.

**Skip auto-generated BDs:** BDs in `.gen/` or outside `.srcs/` are excluded. Only user-created BDs from the project's `.srcs/` directory are exported.

**Local vs. remote constraint files:** each constraint fileset's file-tail list (used later by `generate_build_script` to rebuild `import_files`/`add_files` calls) only includes files that `_rc_is_exportable_local` confirms were actually copied into `Sources/Constraints`. Files outside the project directory (Mixed/Remote scenario) are never copied — they're referenced in-place via the "Remote constraints" `add_files` block instead — so listing them here would target a path `build.tcl` never creates, aborting the rebuild. This also governs DFX pblock XDCs: a *local* pblock XDC is copied into `Sources/Constraints` and must stay in the list, or `build.tcl` silently drops the pblock constraint; a *remote* one must not appear here, or it gets double-imported.

**generate wrapper for BD:** If BD wrapper is created in `.gen/`. Check for .v or .vhd with name `bd_name_wrapper`. If `bd_name_wrapper` is added to the top hierarchy. If above both are true, Then generate flag bd_name_wrapper_flag = 1. repeat the same for all the User-created BDs in .srcs
## Edge Cases

- **Encrypted IP** — `write_ip_tcl` may fail; use Remote strategy instead
- **Flat RTL hierarchy** — all files land in Sources/RTL/ regardless of original subfolder structure
- **Multiple constraint filesets** — all XDC files from all constraint filesets are exported
- **DFX without checkpoints** — if no locked DCPs exist yet, a note is printed; user must export after implementation

## When to Use vivado_doc_search

- If `write_ip_tcl` or `write_bd_tcl` produces unexpected output, search for their documentation
- If unsure about IP file types or BD properties, search for `get_property IS_BD_CONTEXT` or `get_ips`
