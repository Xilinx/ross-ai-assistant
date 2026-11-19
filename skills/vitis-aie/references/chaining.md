<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Inputs & outputs (artifact contract)

What this skill consumes and produces. It does not orchestrate or name other skills — it
operates on artifacts on disk (or, within an in-console session, on the component by name). The
calling agent decides what to run before or after, from these inputs/outputs.

## This skill's step

| Step | Consumes | Produces |
|------|----------|----------|
| AIE component (create + build) | a device `part` **or** an AIE-capable extensible platform (`.xpfm`) | a built **AIE component** (`libadf.a`), referenced **by name** |

## Create is separate from build

Creates (scaffolds) the AIE component by default; builds (`x86sim` / `hw`) only when the user
asks. The `libadf.a` exists **only after a build** — a created-but-unbuilt component has a
scaffold but no build output.

## Inputs must be built

If a required input (e.g. an AIE-capable platform `.xpfm`) is **missing or unbuilt**, state
what is needed and let the calling agent resolve it — never silently substitute a value, and
never dead-end.

## On-disk / by-name handoff

Report the component **by name** (prefer re-fetching by name so it survives a console restart,
not the live object) and, after a build, its `libadf.a` path — so a later step can consume it.
