---
name: hwemu-rules
description: >
  Cross-cutting rules for every hardware-emulation stage: orchestration, scope, error reporting, paths and toolchain environment.
metadata:
  version: "1.0.0"
---

# vitis-hardware-emulation — Rules (apply throughout the flow)

Cross-cutting rules for every stage (input → hw-emu-drc → hwemu-run → report).
Consult this first.

| Area | Rule |
|---|---|
| Orchestration | The bundled `references/` copies are the authoritative, self-contained stages — follow them directly. This skill does not depend on any external stage skill. |
| Orchestration | Run each stage's command **directly** (the script / `wic` / `launch_hw_emu.sh` invocations documented in the stage files). Site-specific values are supplied by the user (or an inputs file). Run commands from `COMPILED_OUT_DIRECTORY` (paths resolve relative to `${PWD}`). |
| Orchestration | `input.md` runs **first** and gathers every input (from the user or an inputs file, echoing the site-specific values back for confirmation). Downstream stages **consume** these — never re-prompt for a captured value. |
| Scope | **This skill runs hw_emu on an already-built design; it does emulation only.** It never compiles the AIE graph or PL kernels, never runs `v++` link or package, and never cross-compiles the host. If the user lacks a built product, tell them to build it first — never build or write it. See [input.md](input.md) "Scope check". |
| Per-platform builds | The built product (the packaged hw_emu directory, `.xclbin`, host executable) is platform-specific — it must have been built for the same `PLATFORM` you emulate on; never reuse a product across platforms. A mismatch (e.g. built for vck190, run as VRK160) is invalid; tell the user to rebuild for the right platform. |
| Error reporting | On any error (DRC, staging, run), report: **(1)** hit an issue — problem + which step; **(2)** failure reason — quote the actual error, explain plainly; **(3)** suggestions — concrete next steps, recommended one marked. Then write the report file (STEP 6; see "Error reporting" below) and stop. In interactive runs, wait for the user's decision; in non-interactive runs, do not block for approval (let the harness clean up). |
| Error reporting | **Application run failures (the run/launch stage, hwemu-run):** if the embedded host run fails inside QEMU (non-zero RC, aborted, or runtime exception), report the failure and the quoted error output, then **stop and wait for the user's explicit confirmation before doing any debugging or making any changes**. Do not modify scripts, files, or the WIC on your own initiative — other than the report file `hw-emu_report.txt` and its `hw-emu_report.prev.txt` copy, which the report stage always writes. |
| Error reporting | **Report every failure and hang — never leave one unreported.** Applies to every stage: a non-zero exit, an `ERROR`/`CRITICAL` line, a missing expected artifact (e.g. `<PACKAGE_DIR>/BOOT.bin` absent), or a run that stalls (e.g. guest in emergency mode, `Timed out waiting for device`, `Dependency failed`, `No such device`, no log progress). Do not rely on the exit code alone — a hang or a skipped output can still exit 0. Plain warnings are not reported on their own. State the failure/hang to the user in chat, using the (1) issue (2) reason (3) suggestions format above, as soon as it is seen — do not wait for the run to end. Then run the report stage (STEP 6) whenever the launch (STEP 5) started, even if the run failed or hung: it classifies the outcome (ERROR / TIMEOUT / FAIL) and writes `hw-emu_report.txt`. If the flow stopped before the launch (a DRC NO-GO, a failed pre-launch check, or a failure in STEPs 2–4), report the problem in chat and also run the report stage: it writes `hw-emu_report.txt` with status NO-GO or ERROR and the list of failures (the exception is a missing or non-existent `COMPILED_OUT_DIRECTORY`, where there is nowhere to write it). Never kill a hung run on your own initiative in interactive runs — report it and wait for the user. **Exception:** on an app failure (a failure marker such as `ERROR: Embedded host run failed, RC=<rc>`), the run exits by itself through the runner's markers, so the launch stage reports, waits for the exit, and kills only as a fallback if it does not exit or leaves processes behind (see [hwemu-run/common/monitor.md](hwemu-run/common/monitor.md)); a boot stuck in `emergency mode` for more than 10 minutes is also killed after notifying the user; other boot trouble and quiet-log notices still wait for the user. |
| Error reporting | Never hallucinate and never search the tool install area. Report only what the actual command/error shows — don't invent names/options/causes/output, don't hunt Vitis/aietools for alternates. If unknown, say so. |
| Error reporting | Search only with the user's permission, and only within `COMPILED_OUT_DIRECTORY` — never elsewhere. Ask first. |
| Inputs & paths | Don't assume the home/skill folder for outputs. Ask for `COMPILED_OUT_DIRECTORY` (the built product's directory; where staging runs and `embedded_run.log` lands). **Always use absolute paths.** |
| Inputs & paths | The site values — `PLATFORM`, `YOCTO_ENV`, `QEMU_PREBUILT` — are machine-specific. **Echo them back and let the user correct** before proceeding. |
| Inputs & paths | Input/output data files may be `.txt` **or** `.csv` — match either extension. |
| Toolchain env | **Sourcing `YOCTO_ENV`:** the SDK `environment-setup` script **aborts if `LD_LIBRARY_PATH` is set** (it is, from the sourced Vitis env), so every stage that sources `YOCTO_ENV` (update-bootbin / copy-wic `wic cp`) must `unset LD_LIBRARY_PATH` **first**, then `source <YOCTO_ENV>`. Stage files keep those two commands inline for self-containment — this rule owns the *why*. |
