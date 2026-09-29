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

The build.tcl follows this sequence:

1. **Create project** — `create_project` with correct PART and BOARD_PART
   (1b: set IP_REPO_PATHS; 1c: restore any preserved outputs from the
   external remote cache — see "Remote Cache Reuse" below — BEFORE anything
   else touches IPs/BDs; hard-errors if the cache is missing/incomplete)
2. **Add RTL** — `import_files` for local sources from Sources/RTL/; `add_files` for remote sources (if Mixed scenario)
3. **Add constraints** — add XDC files to constrs_1 fileset
4. **Add simulation** — add files to sim_1 fileset
5. **Add data files** — add .mem, .coe, .csv, etc.
6. **Recreate IPs** — source each IP Tcl script from Sources/IP/, then `create_ip_run` for standalone OOC IPs. Preserved IPs (see below) are `add_files`'d from the restored `<proj_name>.srcs/<rel_dir>/` instead, per-IP, alongside this step.
7. **Recreate BDs** — source each BD Tcl script from Sources/BD/. Preserved BDs (see below) are `add_files`'d from the restored `<proj_name>.srcs/<rel_dir>/` instead, per-BD, in the same dependency-ordered loop.
8. **Create BD wrappers** — `make_wrapper` + `import_files` for BDs that had wrappers in `.gen/` (preserved BDs are tracked through the same `bd_name_array` mechanism, so an auto-managed-wrapper preserved BD still gets its wrapper created)
9. **Generate BD targets + IP runs** — `generate_target all` on each BD, then `create_ip_run` on BD files
10. **Apply settings** — `source project_settings.tcl` (BEFORE finalize so TOP, PR_FLOW etc. are set)
11. **Finalize** — `update_compile_order` for sources and sim filesets

All paths are relative to the script location using `[info script]`.

## Remote Cache Reuse: Preserved IPs/BDs (optional, per-item)

If Step 2.5 (`prompt_preserve_outputs`/`build_preserve_selection`) selected
one or more IPs/BDs, those specific items skip the standard
Tcl-regeneration path entirely. As of 2026-09-29 (GP methodology feedback),
the source of truth for a preserved item's `.srcs`/`.gen` is an **external
remote cache directory** — a path outside the project, supplied by the user
and baked into `build.tcl` as a literal absolute path (`preserve_cache_dir`).
Unlike the project itself, this cache is never touched by `create_project
-force`, so `generate_build_script` reads from it directly — no intermediate
staging directory is needed:

- **Step 1c** (runs right after `create_project`/IP repo setup):
  1. First checks `[file isdirectory $preserve_cache_dir]` — if the cache
     root itself is missing, it **hard-errors** (`error`) and the whole
     rebuild stops.
  2. For each preserved IP/BD, checks that its own `<cache_dir>/<proj_name>/
     {ip,bd}/<name>/{srcs,gen}/` subfolders both exist — if either is
     missing, it **hard-errors** for that item (no fallback to
     regeneration).
  3. Otherwise, copies that item's cached `srcs/*` into the new project's own
     `<proj_name>.srcs/<rel_dir>/` and cached `gen/*` into
     `<proj_name>.gen/<rel_dir>/`, where `<rel_dir>` is the exact
     fileset-relative path `export_all_sources` recorded for that item (not
     a hardcoded `sources_1` guess — a BD/IP can live in a different
     fileset, e.g. a simulation-only BD under `sim_1/bd/<name>/`, confirmed
     on this skill's own `modnoc-rtl-flat` example design). This plants the
     preserved generated output where Vivado will look for it, before that
     IP/BD is even added to the project.
- **Steps 6/7** then `add_files` the restored `.xci`/`.bd` from
  `<proj_name>.srcs/<rel_dir>/` (instead of `source`-ing a
  `write_ip_tcl`/`write_bd_tcl` script) and call `generate_target`. Because
  the matching `.gen` output was already restored in Step 1c, Vivado's own
  out-of-date check sees the generated products as current and does not
  actually resynthesize/rebuild them — this relies on Vivado's normal
  incremental-generation behavior, not a special flag. Since Step 1c already
  guarantees these sources exist (erroring out the build otherwise), Steps
  6/7 do not re-check for them.
- This is a **per-IP/per-BD** decision, not a project-wide mode switch: any
  IP/BD not named in `preserve_selection` goes through the exact same
  `write_ip_tcl`/`write_bd_tcl` → `source` → `create_ip_run`/`generate_target`
  path as before this feature existed, in the same build.tcl, in the same
  project.

Copying the whole per-BD `.srcs` directory at export time (not just the
lone `<bd>.bd` file) means a per-instance IP customized only inside a
preserved BD is preserved too — this closes a gap present in an earlier
(pre-2026-09-29) version of this mechanism, where only the top-level `.bd`
file was captured and nested `ip/<name>/<name>.xci` files could go missing
on rebuild.

### Missing-cache handling (hard failure, by design)

If the remote cache directory, or a specific preserved item's `srcs`/`gen`
data inside it, is missing at rebuild time (wrong path, cache not
mounted/restored, export never finished for that item), Step 1c raises a
Tcl `error` and the whole `build.tcl` run stops — it does **not** fall back
to Tcl regeneration for that item. This is intentional: the user explicitly
opted into a deterministic, cache-backed rebuild for that IP/BD, so a
missing cache is a setup problem worth surfacing loudly, not a "just
resynthesize instead" situation to paper over silently.

## Backward Compatibility

`preserved_ip_names`/`preserved_bd_names` (and the matching
`preserved_ip_rel_path`/`preserved_bd_rel_path` fileset-relative paths, plus
`preserved_cache_dir`) are read from globals populated by
`export_all_sources`; if that proc was called without a `preserve_selection`
(the default, and the only option before this feature existed), both name
lists are empty and `generate_build_script` emits byte-for-byte the same
Steps 6/7/9 as before — no Step 1c is even emitted. Older exported
`RevisionControl/` trees (from before this feature existed, or from the
2026-08-23–2026-09-29 full-tree-copy design) rebuild exactly as before —
nothing in `generate_build_script` checks for any of these unless it
already has a preserved name to look up.

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

The current `generate_build_script` procedure captures BD dependency ordering
and emits child-before-parent sourcing in the generated build.tcl. This is
intended to preserve correct ordering for multi-BD and BDC designs, including
DFX projects where RM-owned BDs must not be recreated as independent top-level
BDs.

## Known Limitation: BDC Debug Hierarchies with Monitor-Mode Interfaces

Some `make_wrapper`/`validate_bd_design` failures on a BDC (Block Design
Container) child are **not** fixable by reordering the generated `build.tcl`
and are **not** a locked/out-of-date IP problem (see below) — they are a
genuine Vivado limitation in how `write_bd_tcl` exports (and Tcl subsequently
replays) a specific interface pattern.

**Pattern:** a BDC child containing hierarchy-wide debug IP (e.g.
`axi_dbg_hub` + `axis_ila` + `axis_vio`) that exposes a **Monitor-mode**
interface pin (e.g. `SLOT_0_AXI`) wired to the parent design via
`connect_bd_intf_net`. Observed on `ipi_bdc_flat_flow` (Versal `xcve2802`,
BD `design_1` → BDC `debug_hier`):

```
ERROR: [BD 41-258] Could not find the cell: 'debug_hier_inst_0' at the level
of hierarchy: 'design_1', when trying to connect the interface net:
'axi_noc_0_M00_AXI_RID' to interface: '/SLOT_0_AXI_rid'
```

A Monitor interface's AXI ID-width sub-pins (`_rid`, `_bid`, etc.) are
decomposed based on the connected master's ID width, which Vivado resolves by
walking the full design hierarchy — something it does when the design is
built interactively, but which does not happen correctly when Tcl-replaying
an exported design. An ordinary `S_AXI` interface on the same cell
materializes fine; the Monitor interface's sub-pins never appear.

**Confirmed not an ordering bug:** tested 5 independent orderings of
`generate_target`/`validate_bd_design`/the failing `connect_bd_intf_net` call
against real Versal-capable Vivado (2026.2) on the actual repro — moving
`generate_target` earlier/later, validating immediately before the connect,
and moving the connect itself to the end of the BD script. All five produced
the identical error. Direct pin inspection confirmed the sub-pins simply
never materialize via Tcl replay, regardless of sequencing.

Because `validate_bd_design` fails on the BDC child, that BD is left invalid
for the rest of the session — so the parent's `make_wrapper` call then hits
Vivado's generic `ERROR: [Common 17-39] 'make_wrapper' failed due to earlier
errors` guard. This is a cascading symptom of the same root cause, not an
independent bug.

**Do not** reintroduce a per-child `generate_target` call in Step 7, or
otherwise reorder `generate_build_script`'s BD sourcing, to try to fix this —
that path is exhausted. A real fix requires either (a) Xilinx fixing
`write_bd_tcl`'s handling of this pattern, or (b) the skill detecting
Monitor-mode BDC boundary interfaces at export time and special-casing them
(documenting a manual "Set Up Debug" wizard reconnect instead of relying on
Tcl replay).

## Known Limitation: NoC Memory-Controller PHY IP Missing `.elf`/Simulation Outputs

Designs containing a NoC IP with a memory controller (e.g. `axi_noc_0` /
`MC0_ddrc_0`) can fail an OOC synthesis run with:

```
ERROR: [Common 17-275] File does not exist [.../bd_<hash>_MC0_ddrc_0_phy_ddrmc.elf]
```

or fail `generate_target` for the containing top-level BD outright, e.g.:

```
CRITICAL WARNING: [IP_Flow 19-663] Failed to copy file
'<vivado_install>/data/ip/xilinx/noc_mc_ddr5_phy_v1_0/hdl/bfm/PINOUT/map_read_19.sv',
it does not exist.
ERROR: [IP_Flow 19-3505] IP Generation error: Failed to generate IP
'.../bd_<hash>_MC0_ddrc_0_phy'. Failed to generate 'Verilog Simulation' outputs.
ERROR: [BD 41-1030] Generation failed for the IP Integrator block MC0_ddrc
ERROR: generate_target failed for <top>.bd: ERROR: [Common 17-39]
'generate_target' failed due to earlier errors.
```

**Confirmed root cause: a Vivado *installation* gap, not an export/rebuild
bug.** The NoC memory-controller PHY IP (`noc_mc_ddr5_phy_v1_0` in this
install) generates its simulation BFM output by copying numbered
`map_read_<N>.sv` / `pinout_<N>.sv` files from the Vivado install's own
`data/ip/xilinx/noc_mc_ddr5_phy_v1_0/hdl/bfm/PINOUT/` directory. On the
install this was diagnosed against
(`2026.2`, `/proj/xbuilds/CustTA_plus/9999.0_2026_0724_2226plus/...`), several
indices are missing from that directory entirely (confirmed by direct
filesystem inspection: `pinout_14.sv` and `pinout_19.sv`/`map_read_19.sv` do
not exist, while neighboring indices do) — so **any** attempt to generate
this IP's targets on this install fails identically, whether from the
original project, a from-scratch rebuild, or a completely unexported scratch
copy. Confirmed further: none of the 9 `vivado-revision-control` example
designs ship a pre-generated `.gen`/`.elf` for this IP (they're lightweight,
sources-only checkouts), so there is nothing valid to copy through export
either — a "preserve/copy the existing artifact" fix (the pattern already
used for `preserve_selection`) is not viable here; there is no working
artifact anywhere to preserve.

**Not fixable from this skill.** No `export_all_sources`/`generate_build_script`
change can synthesize a Vivado IP-library file that doesn't exist on disk.
Affected projects: `modnoc-rtl-dfx`, `ipi_bdc_dfx` (fails at OOC-synth time),
and `segmented_config` (fails earlier, at `generate_target`, but the
*current* `verify_rebuild`/`run_project.sh` structural checks do not catch
this — see the transcript-ERROR-scan check added to `verify_rebuild` for a
partial mitigation: it turns this into a visible `[FAIL]`/`WARN` instead of a
silent `PASS`). If you hit this on a different Vivado install, check whether
`data/ip/xilinx/noc_mc_ddr5_phy_v1_0/hdl/bfm/PINOUT/` is complete on that
install; if it's not, this is an install/patch issue for whoever owns that
Vivado deployment, not something to re-investigate here.

## Locked/Out-of-Date IPs Can Surface as a make_wrapper Failure

A `make_wrapper` failure during rebuild is frequently a symptom, not the root
cause:

```
ERROR: [Common 17-39] 'make_wrapper' failed due to earlier errors.
```

This generic Vivado message means the session already has a logged error from
an earlier command — usually `validate_bd_design` failing on a BD (often a
Block Design Container child) that contains locked or out-of-date IPs. Once
Vivado logs that error, later unrelated commands like `make_wrapper` can also
fail with the same "failed due to earlier errors" message, which hides the
real cause.

The generated build.tcl wraps `validate_bd_design`, the BDC child's
`generate_target all`, and `make_wrapper` in `catch` blocks (Steps 7 and 8) so
each prints its own diagnostic instead of aborting silently. When you see a
`make_wrapper` failure, scroll back up the rebuild log for a `WARNING:
validate_bd_design reported an error for <bd>` or `WARNING: generate_target
failed for <bd>` — that earlier message names the actual BD at fault.

**Fix:** on the ORIGINAL project (not the recreated one):
```tcl
open_bd_design <bd_name>.bd
report_ip_status
```
then `upgrade_ip` the flagged IPs and re-run the export/build pipeline. The
recreated project rebuilds an exact copy of the original's IP state, so a
locked IP there will always reproduce the same failure downstream.

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
