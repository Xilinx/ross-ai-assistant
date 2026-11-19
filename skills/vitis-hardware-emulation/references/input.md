---
name: hwemu-input
description: >
  Input gathering for hardware emulation: the inputs the flow needs, which flows ask which, and how they are captured (interactively or from an inputs file).
metadata:
  version: "1.0.0"
---

# vitis-hardware-emulation — Input Gathering

**Always launched first.** Collect the values below and hand them off to the
downstream stages (hw-emu-drc through report). Do not re-prompt downstream for
anything captured here.

The skill runs the stage commands directly (see [rules.md](rules.md)).

## Scope check — what this skill can and cannot do

This skill RUNS hardware emulation on a design that has **already been built**.
It does NOT compile, link, package, or cross-compile anything, and it does not
write design code.

If the user asks to build any piece (AIE graph, PL kernels, link, package, host),
say plainly, before gathering inputs:

> "This skill only runs the emulation. I need the finished build products from you:
>    1. the packaged hw_emu directory (launch_hw_emu.sh, BOOT.bin, and on
>       VRK160/VEK385 also a .pdi and a .dtbo)
>    2. the packaged .xclbin
>    3. the cross-compiled embedded host executable
>  Please build these first (absolute paths), then I'll run the emulation."

Do not build or modify any of them on the user's behalf, and do not proceed to
STEP 1 until they are supplied.

## Option A — Read inputs from a file (non-interactive)

**Only** when the user explicitly provides an **inputs file** path (a
`.md`/`.txt`) — **read it instead of prompting**. Do **not** search for, glob, or
auto-discover inputs files (e.g. a `hwemu_inputs.md` in the CWD); if the user did
not name a file, go interactive (Option B). Parse `KEY = VALUE` lines matching the
captured-values keys below (ignore blank lines and `#` comments). Then:

- Use every value provided in the file as-is.
- **Only prompt for keys still missing or empty** in the file.
- Still **validate** the values downstream (STEP 1 DRC) — a value that doesn't
  exist on disk is a FAIL, same as an interactive one.

A ready-to-edit template lives at
[../scripts/hwemu_inputs.template.md](../scripts/hwemu_inputs.template.md) — copy
it, fill it in, and pass its path.

## Option B — Interactive

> ## 🧭 ASK STEP BY STEP — one input at a time
> Gather inputs **one at a time**, in the table order below. Ask a single
> question, wait for the answer, resolve/confirm that variable, then move to the
> next. **Never** present the whole list at once or batch multiple questions into
> a single prompt. If a value can be inferred (e.g. the defaults below), still
> confirm it with the user as its own step before moving on.

Ask for the **flow** first, then the build directory, then each variable below in table
order. The site-specific values (platform, Yocto artifacts) are machine-specific —
**echo them back to the user for confirmation** before proceeding.

**`FLOW` question** (always asked first, no default — offer the three options):

> Which flow was your hw_emu product built for?
> 1. **EDF**
> 2. **PetaLinux**
> 3. **Baremetal**

`FLOW` decides which of the later inputs are asked: skip any input whose "Asked for"
column does not list the chosen flow.

| Variable | Asked for | Source / meaning |
|---|---|---|
| `FLOW` | all | The software flow of the built product: `edf` \| `petalinux` \| `baremetal`. Always ask, no default |
| `COMPILED_OUT_DIRECTORY` | all | The build directory holding the built product — where the emulation runs and `base_camp_images/`, `embedded_run.log` land (`${PWD}`) |
| `PLATFORM` | all | Hardware `.xpfm` the product was built for; the board (`vrk160`, `vek385`, `vck190`, `vek280`) is read from its file name. `edf` and `baremetal`: for a base platform of a Vitis install (under `…/Vitis/base_platforms/`) a plain `vck190_base…`, `vek280_base…`, `vrk160_base…` or `vek385_base…` (no `xilinx_` prefix, not `_dfx`); for `edf` it also selects boot mode / device branches. `petalinux`: only the plain `xilinx_vck190_base…` / `xilinx_vek280_base…` base platforms of a Vitis install (not the `_dfx` ones) |
| `PACKAGE_DIR` | all | The packaged hw_emu directory that holds `launch_hw_emu.sh`. Its name and location depend on how the product was packaged — ask for the absolute path, no default |
| `XCLBIN` | `edf`, `petalinux` | Packaged `.xclbin` (any file name) — absolute path, no default |
| `HOST_EXE` | all | `edf`, `petalinux`: the cross-compiled embedded host executable; `baremetal`: the ELF (any file name) — absolute path, no default |
| `RUN_SCRIPT` | `edf`, `petalinux` | The runner script the launch runs in the guest (absolute path on the host; its file name is the name it has in the image). `edf`: the skill copies it into the rootfs and it must be executable. No default |
| `YOCTO_ENV` | `edf`; `baremetal` on the SD boards | SDK env-setup script, sourced before `wic cp`. For `baremetal` ask it only when the board (from the `PLATFORM` file name) is `vck190` or `vek280`; the OSPI boards (`vek385`, `vrk160`) copy the boot image with `dd` and do not need it |
| `QEMU_PREBUILT` | `edf`, `baremetal` | QEMU prebuilt dir, copied into `base_camp_images/` |
| `SIMULATOR` | all | Simulator the product was built for: **xsim** (default, built-in) \| **questa** \| **vcs** \| **riviera** \| **xcelium** |
| `CAPTURE_VCD` | all | Capture the AIE waveform (VCD)? **yes** \| **no** — ask, no default |
| `AIE_WORK_DIR` | all, only when `CAPTURE_VCD` is `yes` | Absolute path of the AIE compile Work directory (the folder holding `.target`). Its name and location vary, so there is no default and nothing is derived from `COMPILED_OUT_DIRECTORY`. **Optional**: blank is allowed |

That is 12 inputs; a user is asked 8 to 12 of them, depending on the flow and on `CAPTURE_VCD`.

**`edf`, `petalinux` and `baremetal` are written** (`edf` and `petalinux` are tested on real designs). Gather the inputs above and run the DRC for any flow; what happens after a GO
is in `SKILL.md` (STEP 1).

`PDI` and `DTBO` are not asked — on **VRK160** and **VEK385** they are the `.pdi` and
`.dtbo` files found in `PACKAGE_DIR` (any file names; only the extensions are fixed).
On **vck190** and **vek280** they are `N/A`: the PDI is already in `BOOT.bin`. (`edf` only.)

Notes:
- **Simulator selection (asked; default `xsim`).** It is recorded for the
  report only — `launch_hw_emu.sh` is simulator-independent, so no stage passes it
  to the launch. Accept the default unless the product was built for a
  third-party simulator. The skill never hardcodes
  simulator tool paths.
- **`PLATFORM`, `YOCTO_ENV`, and `QEMU_PREBUILT`** are site-specific — the values
  may not match this machine, so **echo them and let the user correct** before
  proceeding.
- The built product must have been built for the **same `PLATFORM`** — the
  `libadf.a` / `.xo` / package are platform-specific (see [rules.md](rules.md)).
- Run the stage commands from `COMPILED_OUT_DIRECTORY`.
- For `petalinux`, `RUN_SCRIPT`, `HOST_EXE` and `XCLBIN` must have the same file names as the
  files in the package's SD image (the package's `sd_card/` folder); the launch runs
  `RUN_SCRIPT` by its file name. **When asking for `RUN_SCRIPT` for `petalinux`, first write the
  reference runner for the user** ([hwemu-run/petalinux/runner-reference.md](hwemu-run/petalinux/runner-reference.md))
  and tell them where it is, so they can copy and edit it.
- For `edf`, the skill copies `RUN_SCRIPT`, `HOST_EXE` and `XCLBIN` into the rootfs under their own
  file names, and the launch runs `RUN_SCRIPT` by its file name; the script must call the host
  and the `.xclbin` by those names. **When asking for `RUN_SCRIPT` for `edf`, first write the
  reference runner for the user** ([hwemu-run/edf/runner-reference.md](hwemu-run/edf/runner-reference.md))
  and tell them where it is, so they can copy and edit it.
- **VCD without `AIE_WORK_DIR`.** The VCD is still written, but the run summary has no AIE
  compiler summary and no GMIO throughput. Say so when the user leaves it blank.

## Captured values (hand off to the rest of the flow)

```
FLOW                   = <edf | petalinux | baremetal>
COMPILED_OUT_DIRECTORY = <build dir holding the built product; where commands run>
PLATFORM               = <path to the .xpfm>
PACKAGE_DIR            = <packaged hw_emu directory>
XCLBIN                 = <packaged .xclbin>                  (edf, petalinux)
HOST_EXE               = <host executable, or the ELF for baremetal>
RUN_SCRIPT             = <runner script path>                (edf, petalinux)
YOCTO_ENV              = <SDK environment-setup script>      (edf; baremetal on vck190/vek280)
QEMU_PREBUILT          = <..._qemu_prebuilt directory>       (edf, baremetal)
SIMULATOR              = <xsim (default) | questa | vcs | riviera | xcelium>
CAPTURE_VCD            = <yes | no>
AIE_WORK_DIR           = <AIE compile Work directory, or blank; only when CAPTURE_VCD = yes>
PDI                    = <the .pdi in PACKAGE_DIR>   (derived; edf on VRK160/VEK385 only, else N/A)
DTBO                   = <the .dtbo in PACKAGE_DIR>  (derived; edf on VRK160/VEK385 only, else N/A)
```

Do not re-prompt for a value already captured here.
