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
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Vivado Revision Control Skill

This skill automates revision control setup for Vivado projects through a
5-step pipeline. It works with Standard RTL-only projects, IPI Block Designs
(including Block Design Containers), DFX (Partial Reconfiguration), and
Versal Segmented Configuration projects.

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

## 5-Step Pipeline

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

### Step 3: Export All Sources (conditional)

Skip this step if Step 2 returned "Remote". Otherwise, export RTL, constraints,
IP (as Tcl via `write_ip_tcl`), Block Designs (as Tcl via `write_bd_tcl`),
simulation files, and data files to an organized directory.

Pass `flow_info` from Step 1 to enable DFX/Segmented Config-specific exports
(pblocks, DCPs, NoC solutions).

```tcl
if {$scenario ne "Remote"} {
    export_all_sources "RevisionControl" $flow_info
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
and finalizes compile order.

For details and DFX RM ordering, read
[generate-build-script/REFERENCE.md](generate-build-script/REFERENCE.md).

## Pipeline Data Flow

```
Step 1: detect_project_flow
  → flow_info dict (passed to Step 3)

Step 2: analyze_source_locations
  → scenario ("Push Button" / "Remote" / "Mixed")
  → If Remote: skip Step 3

Step 3: export_all_sources(dir, flow_info)
  → Sources/ directory with RTL, IP, BD, Constraints

Step 4: capture_project_settings(file)
  → project_settings.tcl

Step 5: generate_build_script(file, dir, scenario_info)
  → build.tcl (automated rebuild)
```

## Output Structure

```
RevisionControl/
├── Sources/
│   ├── RTL/              # Verilog/SV/VHDL sources
│   ├── Constraints/      # XDC files (+ pblocks.xdc for DFX)
│   ├── IP/               # write_ip_tcl scripts (standalone IPs only)
│   ├── BD/               # write_bd_tcl scripts (user-created BDs only)
│   ├── Simulation/       # Testbenches
│   ├── Data/             # .mem, .coe, .mif, .hex, .elf files
│   └── Scripts/          # Tcl pre/post hook scripts
├── Scripts/
│   ├── project_settings.tcl
│   └── build.tcl
├── Checkpoints/          # (DFX only) locked static DCPs
└── NoC/                  # (Segmented Config only) .ncr files
```

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
