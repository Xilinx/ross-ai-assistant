---
name: vitis-embedded
description: Build the Vitis embedded software lifecycle end to end — create an embedded hardware platform, create a standalone/FreeRTOS/Linux application on that platform, and package a bootable image (BOOT.BIN / PDI via Bootgen). Works INSIDE the Vitis Unified IDE's AI agent via the `runVitisPython` tool against the live `client`, or HEADLESS on any host via `vitis -s` (the boot image is a Bootgen shell step). Use whenever the user asks to create an embedded platform, create an embedded app on a built platform, or create/generate a boot image / BOOT.BIN / PDI; a missing `runVitisPython` tool is never a reason to decline — detect the mode and continue headless. Each step is create-only by default; build/package runs only when the user asks. Load the reference for the step in play — `references/platform.md`, `references/app.md`, or `references/boot-image.md`.
license: MIT
compatibility: Requires a Vitis 2024.2+ install (Bootgen for boot images); in-IDE mode needs the `runVitisPython` tool and a connected `client`, headless mode needs `vitis -s`.
allowed-tools: runVitisPython shellExecute create_file read_file vivado_doc_search
metadata:
  stage: beta
  version: "1.0.2"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vitis embedded

One skill for the embedded software lifecycle the user actually runs. The three
steps chain — platform feeds app, app (+ platform artifacts) feed the boot image —
but each has its own trigger, so route to the step the user asks for and load only
that reference.

| Step | Trigger | Produces | Reference |
| --- | --- | --- | --- |
| **Platform** | "create an embedded platform", from an XSA (+ domains) | `.xpfm` | [`references/platform.md`](references/platform.md) |
| **Application** | "create a standalone/FreeRTOS/Linux app" on a built platform | `.elf` | [`references/app.md`](references/app.md) |
| **Boot image** | "create a boot image / BOOT.BIN / PDI" — author a BIF (run Bootgen only when asked to build) | `BOOT.BIN` / `.pdi` | [`references/boot-image.md`](references/boot-image.md) |

> **"Produces" is the *built* artifact.** Each step is **create-only by default** — create emits
> the component/BIF; the `.xpfm` / `.elf` / `BOOT.BIN` appears only after an explicit build (a
> created app has no `.elf` yet, a created platform only a stub `.xpfm`).

## Shared conventions (apply to every step)

- **Two execution modes, identical API calls.**
  - *In-IDE:* call the Vitis Python API through the `runVitisPython` tool against the
    pre-connected `client` — no script file, no port, no `create_client`/`set_workspace`/`dispose`.
  - *Headless:* run the same calls through `vitis -s` on any host with a Vitis install.
    There is no 1-hour ceiling headless (unlike in-console). See each step's flow doc and
    its `headless.md` for the exact recipe.
  - **One of these is required.** If neither `runVitisPython` nor a Vitis install for `vitis -s`
    is available, **stop and report that Vitis is required** — never fall back to hand-written
    Makefiles/CMake or other scripts as a substitute (the real build shells out to Vitis's own
    tooling; a hand-rolled build is not valid).
- **Headless `vitis -s` startup check — verify the artifact, not the exit code.** On some
  releases (e.g. 2026.1), if `$HOME` lacks ~100 MB for Vitis's startup check, `vitis -s` prints a
  disk-space message and can **exit 0 without running your script**. Redirect the check with
  `XILINX_VITIS_DATA_DIR=<writable dir>`, and after each headless step confirm the expected
  artifact exists (`.xpfm` / `.elf` / image) before continuing — don't trust the exit code alone.
- **Create-only by default.** Emit the component/BIF; run the build/package
  (`platform.build()`, `app.build()`, Bootgen) only when the user explicitly asks.
- **Resolve platforms by anchored exact name.** When a step consumes a platform, match
  the full `.xpfm` name (e.g. `/<plat>/<plat>.xpfm`), never a substring — a bare name like
  `vck190_base` can silently resolve to the wrong flow (EDF vs PetaLinux). Echo the resolved
  platform + flow before continuing, and keep `packaging_mode` matched to that flow.
- **Report only real artifacts.** Read back the produced `.xpfm` / `.elf` / `BOOT.BIN` from
  disk; never invent paths or values. **`create` runs an automatic quick build that writes a
  stub `.xpfm`, so its presence alone does not mean the platform is built** — confirm a real
  build via the export build-status marker (see `references/platform.md`), not file existence.
- **Never generate security material.** Do not create, derive, or substitute signing/encryption
  keys, `.nky`, or certificates on your own. If the user asks for an authenticated/encrypted
  image but supplies no keys, leave the key paths as stand-ins and stop at the BIF; generate
  keys only when the user explicitly asks for *development* keys. (The documented ZynqMP
  simple-mode `.nky` auto-generation shortcut is the one exception.)

## Chaining

The full deployment path is platform → app → boot image. Programming the board with
JTAG, `program_flash`, or an SD card, and any on-target bring-up/debug, is optional
and outside this skill.
