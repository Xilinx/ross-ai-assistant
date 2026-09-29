<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

---
name: capture-project-settings
description: Step 4 — captures all non-default project properties for reproducible builds.
---

# Capture Project Settings (Step 4)

**Source file:** `helper-procedures/helper_scripts.tcl`
**Proc name:** `capture_project_settings`

## Procedure

```tcl
capture_project_settings "RevisionControl/Scripts/project_settings.tcl"
```

**Parameters:**
- `output_file` (string) — path for the generated settings file

**Returns:** the output file path.

## What Gets Captured

| Category | Properties |
|----------|-----------|
| Project | PART, BOARD_PART, TARGET_LANGUAGE, PR_FLOW, SEGMENTED_CONFIGURATION |
| Source fileset | TOP, VERILOG_DEFINE |
| Synthesis run | STEPS.SYNTH_DESIGN.ARGS |
| Implementation run | STEPS.PLACE_DESIGN.ARGS, STEPS.ROUTE_DESIGN.ARGS |
| Hook scripts | STEPS.*.TCL.PRE, STEPS.*.TCL.POST (synth and impl runs) |

The output is a series of `set_property` commands that can be sourced to
restore the project configuration.

## Why This Step Matters

Settings like PR_FLOW and SEGMENTED_CONFIGURATION are invisible in the source
files but critical for correct synthesis and implementation. If they're missing
during rebuild, DFX projects won't enable partial reconfiguration and Versal
projects won't generate dual PDI files.

## Edge Cases

- **Default values are excluded** — only non-default properties are written,
  keeping the file minimal
- **Run after all configuration** — if you capture settings mid-setup, you'll
  miss properties set later
- **Multiple implementation runs** — all impl runs are captured (impl_1, child runs for DFX)
- **IP_REPO_PATHS** — for Remote/Mixed source scenarios, ensure IP_REPO_PATHS
  is set before capture so it gets recorded

## When to Use vivado_doc_search

If a property seems missing from the captured file, use `vivado_doc_search` to
look up the property name format (e.g., `STEPS.SYNTH_DESIGN.ARGS.DIRECTIVE`
vs. `STEPS.SYNTH_DESIGN.ARGS.MORE OPTIONS`).
