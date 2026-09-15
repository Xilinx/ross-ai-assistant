<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

---
name: analyze-source-locations
description: Step 2 — classifies source locations as Push Button, Remote, or Mixed to decide whether to export.
---

# Analyze Source Locations (Step 2)

**Source file:** `helper-procedures/helper_scripts.tcl`
**Proc name:** `analyze_source_locations`

## Procedure

```tcl
set scenario_info [analyze_source_locations]
```

Returns a dictionary:

| Key | Type | Description |
|-----|------|-------------|
| `scenario` | string | "Push Button", "Remote", or "Mixed" |
| `local_count` | integer | Files under project directory |
| `remote_count` | integer | Files outside project directory |
| `remote_paths` | list | Unique paths of remote source files |
| `strategy` | string | Human-readable recommendation |

## Decision Logic

- **Push Button** (all local) → proceed to Step 3, export everything
- **Remote** (all external) → skip Step 3 entirely, go to Step 4
- **Mixed** (some local, some external) → export local files in Step 3, document remote paths

The classification compares each file's normalized path against the project
directory (`get_property DIRECTORY [current_project]`). Generated files
(`IS_GENERATED == true`) are excluded from the count.

## Edge Cases

- **IP from shared repositories** — counted as remote; document the IP_REPO_PATHS for build.tcl
- **No source files** — returns scenario "Unknown"; check that sources are added
- **Symlinked sources** — `file normalize` resolves symlinks, so symlinked files may appear as remote

## When to Use vivado_doc_search

If unsure how Vivado resolves source file paths (e.g., IP_REPO_PATHS behavior,
`-no_copy_sources` flag), use `vivado_doc_search` to look up `create_project`
or `set_property IP_REPO_PATHS`.
