---
name: vitis-system
description: Compose a Vitis system — V++ link AIE + HLS components into a fixed XSA/VMA/VSS (the integration step), then package the binary containers into a deployable image such as an SD-card image, BOOT.BIN, or XCLBIN (the system step). Works INSIDE the Vitis Unified IDE's AI agent via the `runVitisPython` tool against the live `client`, or HEADLESS on any host via `vitis -s`. Use whenever the user asks to create an integration or system project, V++ link kernels to the AI Engine, wire AIE + HLS components together, or produce/package a deployable image. Compose-only by default; the V++ link and the build+package run only when the user asks. Load the reference for the step in play — `references/integration.md` (link) or `references/system.md` (package).
license: MIT
compatibility: Requires a Vitis 2024.2+ install; in-IDE mode needs the `runVitisPython` tool and a connected `client`, headless mode needs `vitis -s` (full hw / Vivado-impl builds run headless).
allowed-tools: runVitisPython shellExecute read_file vivado_doc_search
metadata:
  stage: beta
  version: "1.0.12"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vitis system

One skill for composing a system from already-built components. The two steps are the
same lifecycle — **link, then package** — so route to the step the user asks for and load
only that reference.

| Step | Trigger | Produces | Reference |
| --- | --- | --- | --- |
| **Integration (link)** | "create an integration project", "V++ link AIE + HLS", "wire kernels to the AI Engine" | fixed `XSA` / `VMA` / `VSS` | [`references/integration.md`](references/integration.md) |
| **System (package)** | "create a system project", "package the binary containers", "produce an SD-card image / BOOT.BIN / XCLBIN" | deployable image | [`references/system.md`](references/system.md) |

## Shared conventions (apply to both steps)

- **Two execution modes, identical API calls.**
  - *In-IDE:* call the Vitis Python API through the `runVitisPython` tool against the
    pre-connected `client` — no script file, no port, no `create_client`/`set_workspace`/`dispose`.
  - *Headless:* run the same calls through `vitis -s` on any host with a Vitis install; a
    full hw (Vivado impl) build runs headless with no tool timeout. See each step's flow doc
    and its `headless.md`.
  - **One of these is required.** If neither `runVitisPython` nor a Vitis install for `vitis -s`
    is available, **stop and report that Vitis is required** — never fall back to hand-written
    Makefiles/CMake or other scripts as a substitute.
- **Headless `vitis -s` startup check — verify the artifact, not the exit code.** On some
  releases (e.g. 2026.1), if `$HOME` lacks ~100 MB for Vitis's startup check, `vitis -s` prints a
  disk-space message and can **exit 0 without running your script**. Redirect the check with
  `XILINX_VITIS_DATA_DIR=<writable dir>`, and after each step confirm the expected artifact
  (XSA/VSS/image) exists before continuing — don't trust the exit code alone.
- **Compose-only by default.** Assemble the project; run the V++ link (`proj.build()`) or the
  build+package only when the user explicitly asks.
- **Resolve platforms/targets by anchored exact name.** Match the full `.xpfm` / target name,
  never a substring — a bare name can silently resolve to the wrong flow. Keep `packaging_mode`
  matched to the platform's flow (EDF vs PetaLinux). Echo the resolved target before continuing.
- **Report only real artifacts.** Read back the produced XSA/VSS/image from disk; never invent
  paths or values.

## Chaining

Consumes built AIE + HLS components (by name) and an extensible platform; produces a fixed
`.xsa` / `.vma` / `.vss` and a packaged image. See
[`references/chaining.md`](references/chaining.md) for the artifact contract (inputs/outputs).
Boot-image authoring via Bootgen (outside a system package) is out of scope for this skill.
