<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# XSCT → Unified Python — Quick Start Prompts

Place your XSCT script in a space-free directory (or use the repo test fixture path).

## Step 1 — Translate only

```
Convert my XSCT script at ./xsct_platform.tcl into a Unified Python script
./xsct_platform_unified.py following the xsct-to-python reference. Explain each XSCT command
you mapped. Do not run a build yet.
```

Expected: a reviewable Python file with `vitis -s` entry point and workspace setup.

## Step 2 — Execute (optional)

```
Run vitis -s ./xsct_platform_unified.py from this directory and report platform/app creation
results from disk (.xpfm / project dirs), not from return codes alone.
```

## Step 3 — Debug handoff (optional)

```
Extract any XSCT debug commands (connect, targets, dow, con) into a separate xsdb.tcl script
for bin/xsdb as the skill describes.
```
