---
name: vitis-hardware-emulation
description: >
  Runs hardware emulation (hw_emu) on an already-built Versal AIE + PL design:
  gathers inputs, runs pre-emulation DRC checks, stages the QEMU boot image and
  the run artifacts, launches the QEMU-based emulation, and reports the outcome.
  Emulation only; it does not compile, link, package, or cross-compile anything.
  Invoke with /vitis-hardware-emulation [path/to/hwemu_inputs.md]. The optional
  argument is a filled inputs file (see scripts/hwemu_inputs.template.md) for a
  non-interactive run; omit it to be prompted.
license: MIT
metadata:
  stage: beta
  version: "3.0.0"
---

# vitis-hardware-emulation

You are the **top-level coordinator** for running hardware emulation (`hw_emu`)
on an **already-built** Versal AIE + PL design. This skill does **emulation
only** — it never compiles the AIE graph or PL kernels, never links or packages
the `hw_emu` system, and never cross-compiles the host. The user supplies the
build products (the packaged hw_emu directory, the `.xclbin`, the host executable). You do
not re-implement the DRC, staging, or launch logic yourself — you orchestrate the
bundled stages under `references/`, in order:

0. `input.md` — gather all inputs (always first)
1. `hw-emu-drc` — pre-emulation Design Rule Checks (gatekeeper, validation-only)
2. `copy-prebuilt` — copy the QEMU prebuilt into `base_camp_images/`
3. `update-bootbin` — fold the packaged `BOOT.bin` into the board boot image + the platform's boot medium (selected from `PLATFORM`)
4. `copy-wic` — `wic cp` the runner script and the run files into the rootfs `.wic`
5. `launch` — boot QEMU + run the app in-guest → `embedded_run.log`
6. `report` — classify and report the outcome

Stages 2–5 are the **EDF flow**. Each flow has its own launch file under
`references/hwemu-run/<flow>/`, and the launch handling every flow shares is in
`references/hwemu-run/common/monitor.md`. The `edf`, `petalinux` and `baremetal` flows are written; `edf` and `petalinux` are tested on real designs.

Each stage **runs its command directly** (the script / `wic` / `launch_hw_emu.sh`
invocations documented in the stage files). Site-specific values are supplied by
the user (interactively or via an inputs file).

## Prerequisite

A sourced Vitis environment (`XILINX_VITIS` set) plus three user-supplied
artifacts: a **built hw_emu product** (the packaged hw_emu directory, `.xclbin`, host
executable), a **QEMU prebuilt** directory, and the Yocto **SDK env-setup**
script (for `wic`). These are site-specific and **not bundled**. See
[DEPENDENCIES.md](DEPENDENCIES.md).

## References

| Reference | Stage | Description |
|---|---|---|
| [references/rules.md](references/rules.md) | — | **Cross-cutting rules for every stage; consult first.** |
| [references/input.md](references/input.md) | STEP 0 | The input-gathering front door; **always launched first**, collects every input the flow needs. |
| [references/hw-emu-drc/hw-emu-drc.md](references/hw-emu-drc/hw-emu-drc.md) | STEP 1 | The DRC gatekeeper: validation-only checks over the input.md values, report format, and GO/NO-GO verdict. |
| [references/hwemu-run/edf/copy-prebuilt.md](references/hwemu-run/edf/copy-prebuilt.md) | STEP 2 | Copy the QEMU prebuilt into `base_camp_images/`. |
| [references/hwemu-run/edf/update-bootbin.md](references/hwemu-run/edf/update-bootbin.md) | STEP 3 | Fold the packaged `BOOT.bin` into the board boot image + the platform's boot medium (selected from `PLATFORM`). |
| [references/hwemu-run/edf/copy-wic.md](references/hwemu-run/edf/copy-wic.md) | STEP 4 | `wic cp` the user's runner script and the run artifacts into the rootfs `.wic`. |
| [references/hwemu-run/edf/runner-reference.md](references/hwemu-run/edf/runner-reference.md) | STEP 0 | EDF flow: the reference runner written for the user when `RUN_SCRIPT` is asked (never staged or run). |
| [references/hwemu-run/edf/launch.md](references/hwemu-run/edf/launch.md) | STEP 5 | EDF flow: boot QEMU + run the app in-guest → `embedded_run.log`; its markers. |
| [references/hwemu-run/petalinux/runner-reference.md](references/hwemu-run/petalinux/runner-reference.md) | STEP 0 | PetaLinux flow: the reference runner written for the user when `RUN_SCRIPT` is asked (never staged or run). |
| [references/hwemu-run/petalinux/launch.md](references/hwemu-run/petalinux/launch.md) | STEP 5 | PetaLinux flow (**verified on one design**): launch from `sd_card.img` with login `petalinux`, no `-qemu-config`; its markers. |
| [references/hwemu-run/baremetal/stage.md](references/hwemu-run/baremetal/stage.md) | STEPs 2–3 | Baremetal flow: copy the QEMU prebuilt and put the boot PDI into the board boot image (`dd` on OSPI boards, `wic cp` on SD boards). |
| [references/hwemu-run/baremetal/launch.md](references/hwemu-run/baremetal/launch.md) | STEP 5 | Baremetal flow: pre-launch checks, launch with `-qemu-config` (no login, no `-run-app`), markers. |
| [references/hwemu-run/common/monitor.md](references/hwemu-run/common/monitor.md) | STEP 5 | Shared by every flow: keep the previous log, monitor the run, leftover-process check, optional AIE VCD dump. |
| [references/report/report.md](references/report/report.md) | STEP 6 | Classify the outcome (PASS/FAIL/TIMEOUT/ERROR/NO-GO) and list reference files. |

## Flow

> **Before starting, read [references/rules.md](references/rules.md)** — the
> cross-cutting rules that apply to every stage below.

### Preflight — Verify Vitis environment

Verify a Vitis environment is sourced (`XILINX_VITIS` set), per
[DEPENDENCIES.md](DEPENDENCIES.md).

- If it is **set** — proceed to STEP 0.
- If it is **missing** — do not guess or auto-source anything. Ask the user to
  source their Vitis settings and relaunch the session per
  [DEPENDENCIES.md](DEPENDENCIES.md).

### STEP 0 — Gather inputs (always first)

**If the skill was invoked with an argument** (e.g.
`/vitis-hardware-emulation /path/to/hwemu_inputs.md`), treat it as the
**inputs-file path** and read it directly (Option A in `input.md`) — do not prompt
for anything the file provides.

Otherwise, launch [references/input.md](references/input.md) and follow it to
collect **all** inputs the flow needs:

```
FLOW, COMPILED_OUT_DIRECTORY, PLATFORM, PACKAGE_DIR, XCLBIN, HOST_EXE, RUN_SCRIPT,
YOCTO_ENV, QEMU_PREBUILT, SIMULATOR, CAPTURE_VCD
```

`FLOW` (`edf` | `petalinux` | `baremetal`) is asked **first**, with no default, and decides
which of the others are asked — the order and the per-flow list are in
[references/input.md](references/input.md). `XCLBIN` is asked for `edf` and `petalinux`; `RUN_SCRIPT` for
`edf` and `petalinux`; `YOCTO_ENV` for `edf` and for `baremetal` on vck190 / vek280; `QEMU_PREBUILT` for `edf`
and `baremetal`.

If `CAPTURE_VCD` is `yes`, also ask for the optional `AIE_WORK_DIR` (the AIE compile Work
directory; blank is allowed).

`SIMULATOR` is asked with a default of `xsim` (`xsim | questa | vcs | riviera |
xcelium`).

Echo the site-specific ones back to the user for confirmation. Do not proceed
until these are captured; downstream stages **consume** these values and must not
re-prompt for anything already gathered.

### STEP 1 — Run DRC checks

Follow [references/hw-emu-drc/hw-emu-drc.md](references/hw-emu-drc/hw-emu-drc.md)
directly as the gatekeeper, passing the gathered inputs. It **only validates**
the values from STEP 0. Print the full DRC report and verdict.

- **NO-GO** — stop; report the failing DRCs and their fixes, then run STEP 6 (the report file records the NO-GO).
- **GO** with `FLOW` = `edf` — proceed to STEP 2.
- **GO** with `FLOW` = `petalinux` — go to STEP 5 with
  [references/hwemu-run/petalinux/launch.md](references/hwemu-run/petalinux/launch.md)
  (no staging: skip STEPs 2–4), then STEP 6.
- **GO** with `FLOW` = `baremetal` — STEP 2 and STEP 3 use
  [references/hwemu-run/baremetal/stage.md](references/hwemu-run/baremetal/stage.md) (STEP 4 is
  skipped), then STEP 5 with
  [references/hwemu-run/baremetal/launch.md](references/hwemu-run/baremetal/launch.md), then STEP 6.

### STEP 2 — Copy the QEMU prebuilt files (copy-prebuilt) — EDF flow

For `baremetal`, STEPs 2 and 3 are one stage, [references/hwemu-run/baremetal/stage.md](references/hwemu-run/baremetal/stage.md); follow it and go to STEP 5.

Follow [references/hwemu-run/edf/copy-prebuilt.md](references/hwemu-run/edf/copy-prebuilt.md)
directly, using `QEMU_PREBUILT`. It stages the prebuilt into `base_camp_images/`.

- If it fails — report it, run STEP 6 (the report file records the ERROR), and stop.
- Otherwise proceed to STEP 3.

### STEP 3 — Update BOOT.bin (update-bootbin) — EDF flow

Follow [references/hwemu-run/edf/update-bootbin.md](references/hwemu-run/edf/update-bootbin.md)
directly, using `PACKAGE_DIR` — and `YOCTO_ENV` where the fold uses `wic cp`. It
folds the packaged `BOOT.bin` into the board boot image, then into the platform's
boot medium (selected from `PLATFORM` — see the stage's table).

- If it fails — report it, run STEP 6 (the report file records the ERROR), and stop.
- Otherwise proceed to STEP 4.

### STEP 4 — Copy files into the rootfs .wic (copy-wic) — EDF flow

Follow [references/hwemu-run/edf/copy-wic.md](references/hwemu-run/edf/copy-wic.md)
directly, using `YOCTO_ENV`, `RUN_SCRIPT`, `HOST_EXE`, and `XCLBIN`/`PDI`/`DTBO`. It `wic cp`s
the runner script and the other artifacts into the rootfs `.wic`.

- If it fails — report it, run STEP 6 (the report file records the ERROR), and stop.
- Otherwise proceed to STEP 5.

### Relaunch emulation (on user request)

If the user asks to **relaunch the emulation** on an already-staged product, go
straight to STEP 5 and then STEP 6 — skip STEPs 0–4. See the relaunch section in the launch file of
the flow: [edf](references/hwemu-run/edf/launch.md) or
[baremetal](references/hwemu-run/baremetal/launch.md) (`petalinux` has no staging; see its
"Good to know").

### STEP 5 — Launch the emulation (launch)

Follow the launch file of the flow — [edf](references/hwemu-run/edf/launch.md),
[petalinux](references/hwemu-run/petalinux/launch.md) or
[baremetal](references/hwemu-run/baremetal/launch.md) — directly, using `PACKAGE_DIR`. It boots
QEMU via `launch_hw_emu.sh` and captures `embedded_run.log`. For `edf` and `petalinux`, the
user's `RUN_SCRIPT` runs in the guest.

- On any error during the run, report it per [references/rules.md](references/rules.md)
  and never kill the run on your own initiative in interactive runs. Then proceed
  to STEP 6.

### STEP 6 — Report (report)

Follow [references/report/report.md](references/report/report.md) directly to
classify the outcome (Status: PASS / FAIL / TIMEOUT / ERROR / NO-GO) and list the
reference files for inspection. Run it every time the flow ends — also when the run failed or
hung, and when the flow stopped before STEP 5 (a DRC NO-GO, a failed pre-launch check or a
staging failure, which are also reported in chat); see `report.md`.

### Not built yet?

If the user has no built hw_emu product (the packaged hw_emu directory, `.xclbin`, host
executable), this skill does not build it. Tell them to build the AIE graph, PL
kernels, link, package, and cross-compile the host first, then provide the
resulting build directory.
