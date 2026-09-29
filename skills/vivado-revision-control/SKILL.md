---
name: vivado-revision-control
description: >
  Comprehensive Vivado project revision control strategies for standard, DFX
  (Partial Reconfiguration), IPI Block Design Container, and Segmented
  Configuration projects. Includes automated tools for project type detection,
  source analysis, settings capture, and build script generation. Use this skill
  whenever the user mentions version control, Git, build scripts, project
  portability, team collaboration, CI/CD for Vivado, exporting sources, preparing
  a project for handoff, or recreating projects — even if they don't explicitly
  say "revision control". Also trigger for "make my project portable",
  "automate project recreation", or "set up Git for my FPGA design".
license: MIT
metadata:
  version: "1.0.0"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Vivado Revision Control Skill

This skill automates revision control setup for Vivado projects through a
5-step pipeline, plus an optional interactive Step 2.5 for preserving
existing generated outputs. It works with Standard RTL-only projects, IPI
Block Designs (including Block Design Containers), DFX (Partial
Reconfiguration), and Versal Segmented Configuration projects.

The pipeline detects project type, analyzes source locations, exports
components, captures settings, and generates a build script — producing a
self-contained directory that can recreate the project from scratch.

## When to use this skill

- Setting up version control for any Vivado project
- Making a project portable for team members or CI/CD
- Exporting and organizing all project sources
- Creating automated build scripts for reproducible builds
- Handling DFX, BDC, or Segmented Config revision control

## When NOT to use this skill

- Project is not yet created (no .xpr file exists)
- Only need to modify an existing build script (edit it directly)
- Need timing analysis or design debugging (use baselining or other skills)

## Prerequisites

Before running, the Vivado project must be open with:
- A valid PART property set
- Source files added to the project
- TOP module configured
- For DFX: PR_FLOW property enabled
- For Segmented Config: SEGMENTED_CONFIGURATION property set (or Versal Gen2 device)

## 5-Step Pipeline (+ optional Step 2.5)

All Tcl procedures live in `helper-procedures/helper_scripts.tcl`. Source this
file first in Vivado, then execute each step sequentially.

```tcl
source <skill-path>/helper-procedures/helper_scripts.tcl
```

### Step 1: Detect Project Flow

Run `detect_project_flow` to identify the project type. This returns a
`flow_info` dictionary needed by Step 3.

```tcl
set flow_info [detect_project_flow]
set flow_type [dict get $flow_info flow_type]
```

Returns one of: "Standard", "DFX (Partial Reconfiguration)", "Segmented
Configuration", "DFX + Segmented Configuration" — with optional "+ IPI BDC"
suffix if Block Design Containers are detected.

For details on detection logic and edge cases, read
[detect-project-flow/REFERENCE.md](detect-project-flow/REFERENCE.md).

### Step 2: Analyze Source Locations

Run `analyze_source_locations` to classify sources as Push Button (all local),
Remote (all external), or Mixed.

```tcl
set scenario_info [analyze_source_locations]
set scenario [dict get $scenario_info scenario]
```

If `scenario` is "Remote", skip Step 3 entirely — sources live in external
repos and should not be duplicated.

For details, read
[analyze-source-locations/REFERENCE.md](analyze-source-locations/REFERENCE.md).

### Step 2.5: Preserve Generated Outputs? (optional, interactive)

Ask whether to keep existing `.gen` outputs for selected IPs/BDs instead of
letting them be fully regenerated from Tcl on rebuild. This step does not
exist in older versions of this skill and is entirely opt-in. How to run it
depends on who's driving the pipeline:

**Human typing directly into a live Vivado session** (GUI Tcl console, or a
manually-driven batch session with a real tty) — as of 2026-09-21, you don't
need to do anything special: if you call Step 3 (`export_all_sources`)
without already having answered this yourself, it detects a real tty on
stdin and, if the project has anything eligible, prompts you automatically
before proceeding. You can still call `prompt_preserve_outputs` explicitly
beforehand if you prefer to control exactly when the prompt happens:

```tcl
set preserve_selection [prompt_preserve_outputs]
```

- Answering "n" (or just pressing enter) returns
  `{enabled 0 ips {} bds {} cache_dir {}}` — Steps 3 and 5 then behave
  exactly as before this feature existed.
- Answering "y" lists every eligible standalone IP and user-created BD (via
  `list_preservable_outputs`) — including XCIs that live outside any Block
  Design — and lets you pick which ones to preserve (`all` or a comma list).
  This selection is **mixed/partial across separate top-level items** (some
  standalone IPs/BDs preserved, others not) — that's the primary supported
  case. It is **not** partial *within* a single BD: a BD is preserved as one
  whole unit, and you cannot cherry-pick which of the IPs customized inside
  it get preserved vs. regenerated (confirmed as an intentional scope cut,
  not a bug — flagged as a possible Q4 enhancement).
- If a selected item has no `.gen` output yet (never generated/synthesized),
  `export_all_sources` warns and exports it normally instead of preserving
  it — nothing to preserve yet is not a hard failure, but it also does not
  silently create an incomplete cache entry.
- Preservation only supports a **remote cache**: a directory *outside* this
  project. After picking IPs/BDs, you're prompted for that cache directory's
  absolute path. An invalid path (relative, empty, or nested inside the
  project) disables preservation for this export.

This proc blocks on `gets stdin`, so it only works with a real tty attached
— which is exactly the condition `export_all_sources` now checks
(`_rc_stdin_is_tty`) before auto-invoking it.

**Automated CI / regression pipelines** (`run_pipeline.tcl`-style harnesses)
— skip this step entirely and pass `{}` (the default) to Steps 3 and 5. That
preserves the old fully-automated 5-step behavior unchanged.

**Agentic (an AI agent driving Vivado via Bash/batch-mode Tcl calls, chatting
with a human)** — do **not** call `prompt_preserve_outputs`: its `gets stdin`
has no real tty to block on and will just read EOF, silently disabling
preservation with no visible prompt at all. Instead:

1. Call `list_preservable_outputs` (pure query, no stdin) to see what's eligible.
2. If it returns any IPs or BDs, ask the human directly in the conversation
   which ones (if any) they want to preserve, AND for an absolute path to a
   cache directory outside the project to store the preserved outputs.
3. Call `build_preserve_selection $chosen_ips $chosen_bds $cache_dir` with
   the human's answers to get the `preserve_selection` dict.
4. If `list_preservable_outputs` returns nothing, skip asking — there's
   nothing to preserve.

```tcl
set items [list_preservable_outputs]
# ... ask the human in conversation, then:
set preserve_selection [build_preserve_selection $chosen_ips $chosen_bds $cache_dir]
```

Pass the returned `preserve_selection` into Step 3 (`export_all_sources`).
For details, read
[helper-procedures/REFERENCE.md](helper-procedures/REFERENCE.md).

### Step 3: Export All Sources (conditional)

Skip this step if Step 2 returned "Remote". Otherwise, export RTL, constraints,
IP (as Tcl via `write_ip_tcl`), Block Designs (as Tcl via `write_bd_tcl`),
simulation files, and data files to an organized directory.

Pass `flow_info` from Step 1 to enable DFX/Segmented Config-specific exports
(pblocks, DCPs, NoC solutions). Pass `preserve_selection` from Step 2.5 (or
omit it) to control per-IP/BD output preservation. If you omit it and stdin
is a real tty, `export_all_sources` prompts for it itself (see Step 2.5
above) rather than silently skipping preservation. For any IP/BD named in
`preserve_selection`, its own `.srcs`/`.gen` subtree (nothing else) is
copied into the external `cache_dir` the selection specifies — never into
`export_dir`/`RevisionControl/` itself.

```tcl
if {$scenario ne "Remote"} {
    export_all_sources "RevisionControl" $flow_info $preserve_selection
}
```

For details on filtering rules, BD-IP handling, and DFX exports, read
[export-all-sources/REFERENCE.md](export-all-sources/REFERENCE.md).

### Step 4: Capture Project Settings

Save all non-default project properties to a Tcl script that can restore them
during rebuild.

```tcl
file mkdir RevisionControl/Scripts
capture_project_settings "RevisionControl/Scripts/project_settings.tcl"
```

Captures: PART, BOARD_PART, TARGET_LANGUAGE, PR_FLOW,
SEGMENTED_CONFIGURATION, TOP, VERILOG_DEFINE, synthesis/implementation
directives, and file-specific properties (VHDL LIBRARY, IS_GLOBAL_INCLUDE).

For details, read
[capture-project-settings/REFERENCE.md](capture-project-settings/REFERENCE.md).

### Step 5: Generate Build Script

Create build.tcl for one-command project recreation.

```tcl
generate_build_script "RevisionControl/Scripts/build.tcl" "RevisionControl" $scenario_info
```

The generated script uses relative paths, imports local sources (`import_files`),
references remote sources in-place (`add_files`), recreates IP and BD from Tcl
scripts, creates OOC IP runs, generates BD targets, sources project_settings.tcl,
and finalizes compile order. For any IP/BD preserved in Step 2.5, it instead
restores that item's `.srcs`/`.gen` from the external cache directory and
`add_files`/`read_ip`/`read_bd`s it directly — no `write_ip_tcl`/`write_bd_tcl`
replay, no resynthesis — while every non-preserved IP/BD still goes through
the standard Tcl-regeneration path. This branching is per-IP/BD, never global
for the whole project. If the cache directory (or a specific preserved
item's data inside it) is missing at rebuild time, the generated script
**hard-errors** rather than silently falling back to regeneration.

For details, DFX RM ordering, and missing-`.gen` handling, read
[generate-build-script/REFERENCE.md](generate-build-script/REFERENCE.md).

## Pipeline Data Flow

```
Step 1: detect_project_flow
  → flow_info dict (passed to Step 3)

Step 2: analyze_source_locations
  → scenario ("Push Button" / "Remote" / "Mixed")
  → If Remote: skip Step 3

Step 2.5: prompt_preserve_outputs (optional, interactive only)
  → preserve_selection dict {enabled, ips, bds, cache_dir} (passed to Step 3)
  → omitted/declined ⇒ {} ⇒ Steps 3 and 5 behave exactly as before this existed
  → if omitted AND stdin is a real tty AND something is eligible, Step 3
    auto-calls this on your behalf (added 2026-09-21) instead of silently
    defaulting to no preservation — no tty (CI/agentic) ⇒ unchanged
  → cache_dir must be an absolute path OUTSIDE the project (remote cache
    only) — an invalid/missing path disables preservation for this export

Step 3: export_all_sources(dir, flow_info, preserve_selection)
  → Sources/ directory with RTL, IP, BD, Constraints (dir itself never
    changes shape based on preserve_selection)
  → (only if preserve_selection selected something) each preserved item's
    OWN .srcs/.gen subtree is copied into cache_dir — never into dir itself

Step 4: capture_project_settings(file)
  → project_settings.tcl

Step 5: generate_build_script(file, dir, scenario_info)
  → build.tcl (automated rebuild; restores any preserved item's .srcs/.gen
    from cache_dir at rebuild time — hard-errors if cache_dir or a
    preserved item's data inside it is missing)
```

## Output Structure

`RevisionControl/` exports *next to* the live Vivado project and its own
shape never changes based on whether Step 2.5 preservation was used —
it never contains `<proj>.gen/`, `<proj>.srcs/`, or `<proj>.xpr` from the
original project (those stay untouched where they are), and XPR is never
revision-controlled in any flow.

```
project_1.gen/            # live original Vivado project — sibling, untouched
project_1.srcs/
project_1.xpr

RevisionControl/
├── Sources/
│   ├── RTL/              # Verilog/SV/VHDL sources
│   ├── Constraints/      # XDC files (+ pblocks.xdc for DFX)
│   ├── IP/               # write_ip_tcl scripts — non-preserved standalone
│   │                     # IPs only (no .xci copied); preserved IPs are
│   │                     # skipped here entirely (see below)
│   ├── BD/               # write_bd_tcl scripts — non-preserved BDs only
│   │                     # (no .bd copied); preserved BDs are skipped here
│   │                     # entirely, with no Tcl fallback (see below)
│   ├── Simulation/       # Testbenches
│   ├── Data/             # .mem, .coe, .mif, .hex, .elf, .csv files
│   ├── Scripts/          # Tcl pre/post hook scripts
│   ├── Checkpoints/      # (DFX only) locked static DCPs
│   └── NoC/              # (Segmented Config only) .ncr files
└── Scripts/
    ├── project_settings.tcl
    └── build.tcl
```

### Preserved outputs (Step 2.5 answered "Yes", ≥1 IP/BD selected)

If Step 2.5 preserved anything, the only artifact is an external **remote
cache directory** — a path outside the project that the user supplied
(`cache_dir` in `preserve_selection`), never copied into `RevisionControl/`
and never git-tracked:

```
<cache_dir>/
└── project_1/
    ├── ip/
    │   └── <ip_name>/
    │       ├── srcs/     # copy of that IP's own .srcs directory (the .xci
    │       │              # and any sibling files it had)
    │       └── gen/      # copy of that IP's own .gen output directory
    └── bd/
        └── <bd_name>/
            ├── srcs/     # copy of that BD's own .srcs directory (the .bd,
            │              # PLUS any nested per-instance ip/<name>/<name>.xci
            │              # files customized only inside that BD)
            └── gen/      # copy of that BD's own .gen output directory
```

Only the *selected* item's own `.srcs`/`.gen` subtree is copied — never the
whole project's generated tree, and never the `.xpr`. Copying the whole
per-BD `.srcs` directory (not just the lone `<bd>.bd` file) also means a
per-instance IP customized only inside a preserved BD is preserved too.

At rebuild time, `generate_build_script`'s generated `build.tcl` reads
directly from this cache directory (an absolute path baked into `build.tcl`
at export time) and restores each preserved item's `.srcs`/`.gen` into the
newly created project's own tree before `add_files`/`read_ip`/`read_bd`ing
it — no `write_ip_tcl`/`write_bd_tcl` replay, no resynthesis. **If the cache
directory, or a specific preserved item's data inside it, is missing at
rebuild time, `build.tcl` hard-errors and stops** rather than silently
falling back to Tcl regeneration — preservation is an explicit opt-in, so a
missing cache is a setup problem to surface, not paper over.

## Special Project Types

For DFX projects, read [dfx-revision-control/REFERENCE.md](dfx-revision-control/REFERENCE.md).
Key requirement: In build.tcl, RM Block Designs must be sourced BEFORE the
static design, or BDC instantiation fails.

For Segmented Config (Versal), read
[segmented-config-revision-control/REFERENCE.md](segmented-config-revision-control/REFERENCE.md).
Key requirement: SEGMENTED_CONFIGURATION property must be captured and restored.
Versal Gen2 devices (xcve2*, xcvp2*, xcvm2*) have this implicitly enabled.

## After Completion

Test the generated build script in a clean directory:
```bash
vivado -mode batch -source RevisionControl/Scripts/build.tcl
```

For DFX projects, configure Git LFS for DCP files:
```bash
git lfs track "*.dcp"
```

### Optional: Verify the Rebuild Matches the Original

Vivado gives no built-in signal that a recreated project actually matches the
original — a silently-skipped BD, a missing wrapper module, or a dropped run
won't error until (or unless) synthesis/implementation is attempted. Two
utility procedures close this gap:

```tcl
# In the ORIGINAL project's session, right after export_all_sources:
capture_verification_manifest "RevisionControl/Scripts/verification_manifest.tcl" "RevisionControl"

# In the RECREATED project's session, after sourcing build.tcl:
verify_rebuild "RevisionControl/Scripts/verification_manifest.tcl"
```

`verify_rebuild` checks that all expected Block Designs, the top module (including
auto-managed wrapper modules with no physical source file), and all expected
runs are present, plus an IP-count sanity check — and prints a PASS/FAIL report.
For details, read
[helper-procedures/REFERENCE.md](helper-procedures/REFERENCE.md).

## Vivado Documentation Lookup

If you need more information about specific Vivado Tcl commands used in this
skill (write_ip_tcl, write_bd_tcl, write_checkpoint, create_project, etc.),
use the `vivado_doc_search` MCP tool to search the Vivado documentation.

Relevant Vivado User Guides:
- **UG892** — Vivado Design Flows Overview
- **UG895** — Using IP in Vivado
- **UG896** — Partial Reconfiguration (DFX)
- **UG994** — Designing IP Subsystems Using IP Integrator
- **UG1281** — Versal System Integration (Segmented Configuration)

## References

- [helper-procedures/REFERENCE.md](helper-procedures/REFERENCE.md) — Full API reference for all Tcl procedures
- [helper-procedures/helper_scripts.tcl](helper-procedures/helper_scripts.tcl) — The Tcl implementation (single source of truth)
