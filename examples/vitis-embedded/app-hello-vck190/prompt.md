<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Hello-World App on vck190 — Quick Start Prompts

The `vitis-embedded` skill creates by default and builds only when asked. Run both steps in the
same Vitis workspace.

## Step 1 — Create the app (create-only by default)

```
Create a standalone hello_world app named app_vck190_std on the built platform's .xpfm
(<workspace>/gm_vck190_std/export/gm_vck190_std/gm_vck190_std.xpfm).
```

Expected: the app is **created (not built)** — the skill reports the app name,
platform, OS (standalone), domain, and template, and offers to build.

## Step 2 — Build it (explicit follow-up)

```
Now build app_vck190_std.
```

Expected: the build succeeds and an `.elf` is produced; the skill reports the
`.elf` path and offers to package a boot image next.
