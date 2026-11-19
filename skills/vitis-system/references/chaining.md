<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Inputs & outputs (artifact contract)

What this skill consumes and produces. It does not orchestrate or name other skills — it
operates on artifacts on disk (or, within an in-console session, on components by name). The
calling agent decides what to run before or after, from these inputs/outputs.

## This skill's steps

| Step | Consumes | Produces |
|------|----------|----------|
| Integration (link) | built AIE component(s) + HLS kernel(s)/`.xo` + an extensible platform (`.xpfm`) | a fixed **`.xsa`** (or **`.vma`** / **`.vss`**) + kernel **`.xo`** |
| System (package) | the linked containers / `.xo` + an extensible platform; for a fixed `.xsa` linked from an AIE design, also the AIE archive (`libadf.a`) — see [`system/packaging.md`](system/packaging.md) | a deployable image — **SD image / `BOOT.BIN` / `.xclbin`** (`hw`), or an emulation package (`launch_hw_emu.sh` + emu `.xclbin`) (`hw_emu`) |

## Create is separate from build

Composes (scaffolds) by default; builds/packages only when the user asks. The output artifacts
above exist **only after the build step runs** — a composed-but-unbuilt project has a scaffold
but no build output.

## Inputs must be built

When a step needs a **built** input (e.g. a built platform `.xpfm` or a built AIE component),
check its **build state, not just that it exists**. If a required input is **missing or
unbuilt, state exactly what is needed** (the artifact, and that it must be built) and let the
calling agent resolve it — never silently substitute a value, and never dead-end.

## Packaging mode is part of the platform input

The extensible platform the system (package) step consumes has a packaging **flow** — EDF/Yocto
vs PetaLinux. For a custom/user-built `.xpfm` (e.g. an `*_e2e` export from the embedded platform
flow) this flow is not in any built-in table, so the calling agent must state it: pass the matching
`packaging_mode` (ask if unknown), because the API default `'EDF'` silently mispackages a PetaLinux
platform. See [`system/packaging.md`](system/packaging.md).

## On-disk handoff

End each step by emitting its concrete **artifact path** (verified), and begin by consuming a
path (or, within an in-console hop, a component **by name** — prefer re-fetching by name so it
survives a console restart). The on-disk artifact is the hand-off, not a live REPL object.

## In-console builds vs `timeoutSeconds`

Builds run in the live IDE session. `hw_emu` builds run in-console; a full **`hw`**
implementation build may run longer than the `timeoutSeconds` you pass, so it runs headless
(`vitis -s`) outside the console.
