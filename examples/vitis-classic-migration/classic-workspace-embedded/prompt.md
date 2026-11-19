<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Classic Workspace Migration — Quick Start Prompts

Use a space-free working directory. Source Vitis 2026.2+ first.

## Step 1 — Generate migration script (planning)

```
I have a Vitis Classic workspace at <absolute-path-to-classic_ws> (contains .metadata).
Migrate it to a new Unified folder <absolute-path-to-unified_out> — empty target, no spaces
in paths. Run classic2unified.py with --classic-embeddedsw <embeddedsw-dir> and show me
migration_notes.md before running migrate.py.
```

Expected: `migrate.py` and `migration_notes.md` appear under `<unified_out>`; Classic source
unchanged. The agent shows the notes and asks whether to run and build, and waits.

## Step 2 — Execute and build the migration (optional follow-up)

```
Run vitis -s migrate.py --build in <unified_out> and report any FAILED steps.
```

(Without `--build`, `migrate.py` only creates the components; `check_launch.py` then fails because
the ELF has not been built yet.)

## Step 3 — Verify (optional, needs embeddedsw + time)

```
Run verify_migration.py and check_launch.py per the skill validation reference, then summarize
verification_report.md.
```
