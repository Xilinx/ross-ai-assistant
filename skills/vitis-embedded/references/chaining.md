<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Inputs & outputs (artifact contract)

What this skill consumes and produces across its three steps. It does not orchestrate or name
other skills — it operates on artifacts on disk. The calling agent decides what to run before
or after, from these inputs/outputs.

## This skill's steps

| Step | Consumes | Produces |
|------|----------|----------|
| platform | board shortcut / `.xsa` | a built **`.xpfm`** |
| app | a built **`.xpfm`** + domain | an **`.elf`** (+ `build/.buildstatus`) |
| boot-image | FSBL/PLM + app **`.elf`** + PDI/CDO/bitstream | **`BOOT.BIN` / `.pdi`** |

## Create is separate from build

Each step **creates/authors by default and builds (or runs Bootgen) only when the user asks**.
The `.xpfm` / `.elf` / image exist **only after** the build/Bootgen step — a created-but-unbuilt
component has a scaffold but no build output.

## Inputs must be built

When a step needs a **built** input (e.g. the app needs a built `.xpfm`), check its **build
state, not just that it exists**. If the input is this skill's own earlier step and is unbuilt,
**offer to build it (ask + approve)**; if it is missing entirely, state what is needed and let
the calling agent resolve it — never auto-build silently, and never dead-end.

## On-disk handoff

End each step by emitting its concrete **artifact path** (verified) and begin by consuming a
path. On-target download / bring-up / debug is a separate runtime and is out of scope here.
