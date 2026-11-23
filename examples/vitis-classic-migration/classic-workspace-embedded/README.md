<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Classic Workspace → Unified (embedded)

Migrate a **Vitis Classic IDE** embedded workspace (`.metadata/`, `platform.spr`,
`.prj` apps) into a **new, empty** Unified workspace using `classic2unified.py` and
`migrate.py` — without modifying the Classic tree.

## Goal

1. **Generate** `migrate.py` and `migration_notes.md` into a space-free target folder.
2. **Review** `migration_notes.md` with the user, then run `vitis -s migrate.py` (and
   optionally `--build`) plus verification scripts.

## Skill

**vitis-classic-migration** — reads Classic metadata only; writes Unified components via
`vitis -s` and bundled scripts under the skill's `scripts/` directory.

## Prerequisites

- **Vitis Unified 2026.2+** on `PATH` (`source <install>/settings64.sh`).
- **Space-free paths** for Classic source and Unified target.
- **A Classic 2023.2, 2024.1 or 2024.2 embedded workspace.**
- **Classic embeddedsw of the workspace's release** (required when the workspace has BSP sources):
  `<release install>/data/embeddedsw` or a shallow clone of the `xilinx_v<release>` `embeddedsw` tag.

## Fixture

Use your own Classic workspace. The skill's own tests use a trimmed VCK190 standalone hello-world
workspace (a platform, an app and a system project), which is not shipped with the skill.

## How to Run

Follow the prompts in [`prompt.md`](prompt.md).

## Expected Behavior

- Classic workspace is **never modified**; Unified output goes to a new folder.
- Agent runs `vitis -s scripts/classic2unified.py ...` (from the staged skill path), shows
  `migration_notes.md`, and only builds when explicitly asked.
