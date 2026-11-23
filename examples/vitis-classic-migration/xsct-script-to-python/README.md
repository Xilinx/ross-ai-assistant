<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# XSCT Tcl → Unified Python

Convert an **XSCT** project script (`setws`, `platform create`, `app create`, `bsp config`)
into a **`vitis -s`** Python script for the Unified IDE, keeping debug steps as XSDB Tcl
where appropriate.

## Goal

1. **Translate** the Tcl script using the mapping rules in the skill reference
   `references/xsct-to-python.md`.
2. **Save** `<script>_unified.py` next to the original (or in a new space-free workspace).
3. **Run** `vitis -s <script>_unified.py` only when the user asks to build.

## Skill

**vitis-classic-migration** — Step 2B in `SKILL.md`; no Classic IDE or XSCT install required
for the translation itself.

## Sample Tcl

A minimal platform and app script (the one the skill's smoke tests use):

```tcl
setws unified_ws
platform create -name zcu102_platform -hw ./zcu102/design_1_wrapper.xsa
domain create -name a53_domain -os standalone -proc psu_cortexa53_0
domain active a53_domain
bsp config -append extra_compiler_flags "-pg"
platform write
platform generate
app create -name hello_a53 -platform zcu102_platform -domain a53_domain -template "Hello World"
```

## How to Run

Follow [`prompt.md`](prompt.md).

## Expected Behavior

- Python script uses the mandatory workspace guard (non-empty folder, no blind `rmtree`).
- BSP parameter names are resolved with Unified API patterns, not hard-coded guesses.
- Agent does not claim a successful platform build without running `vitis -s`.
