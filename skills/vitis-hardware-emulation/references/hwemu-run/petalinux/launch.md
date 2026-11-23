---
name: hwemu-petalinux-launch
description: >
  PetaLinux flow launch (verified on one design) — boot QEMU from the package's sd_card.img and
  run the app in-guest; capture embedded_run.log. Consumes PACKAGE_DIR and RUN_SCRIPT; HOST_EXE and XCLBIN are asked and checked.
  The package's launch_hw_emu.sh has no -enable-edf.
metadata:
  version: "1.1.0"
---

# HW Emulation (PetaLinux flow) — Launch the emulation

> **Status: verified on one design** (a makefile-built gmio-to-DDR example on vck190, status PASS,
> VCD captured). The markers below were confirmed in that run.

The PetaLinux package is self-contained: `PACKAGE_DIR` holds `launch_hw_emu.sh` and
`sd_card.img`, and `launch_hw_emu.sh` has **no** `-enable-edf` (DRC-14 checks this).
There is no QEMU prebuilt, no Yocto SDK and no staging, so STEPs 2–4 do not run. The host
executable, the `.xclbin` and the runner script are already inside `sd_card.img`; `HOST_EXE`
and `XCLBIN` are still asked and checked to exist on the host. The user supplies the runner
script as `RUN_SCRIPT`, and the launch runs it.

The AIE debug and AIE enable critical warnings and questions, keeping the previous log, monitoring the run, the leftover-process check and the optional
AIE VCD dump are shared by every flow: see [../common/monitor.md](../common/monitor.md).
Run the launch as a background job, as described there. Run from `COMPILED_OUT_DIRECTORY`.

## Pre-launch checks (run these before the launch command)

The DRC already checks that `sd_card.img` and `BOOT.BIN` are in `PACKAGE_DIR` (DRC-04). Before
launching, run two more checks on the package.

**1. The SD image contents.** The package's `sd_card/` folder — the files that went into the
image — must hold `BOOT.BIN` (any letter case), `Image`, and files with the same **file names**
as `XCLBIN`, `HOST_EXE` and `RUN_SCRIPT`:

```bash
S="<PACKAGE_DIR>/sd_card"
[ -d "$S" ] || { echo "NO sd_card FOLDER: $S"; exit 1; }
m=""
ls "$S" | grep -qix 'boot.bin' || m+="BOOT.BIN "
[ -e "$S/Image" ] || m+="Image "
for f in "<XCLBIN>" "<HOST_EXE>" "<RUN_SCRIPT>"; do [ -e "$S/$(basename "$f")" ] || m+="$(basename "$f") "; done
[ -z "$m" ] || { echo "NOT IN $S: $m"; exit 1; }
echo "OK: BOOT.BIN, Image, $(basename "<XCLBIN>"), $(basename "<HOST_EXE>"), $(basename "<RUN_SCRIPT>") are in the SD image"
```

**2. The boot image description.** The boot image `.bif` in `PACKAGE_DIR` (normally
`boot_image.bif`) must name the processor-side boot files: `bl31.elf`, a `.dtb` and `u-boot.elf`.
Without them `BOOT.BIN` holds only the platform-management and AIE images, and the guest never
boots Linux:

```bash
B=$(ls "<PACKAGE_DIR>/boot_image.bif" "<PACKAGE_DIR>"/*.bif 2>/dev/null | head -1)
[ -n "$B" ] || { echo "NO .bif IN <PACKAGE_DIR>"; exit 1; }
m=""
grep -qi 'bl31.*\.elf' "$B" || m+="bl31.elf "
grep -qi '\.dtb' "$B" || m+="dtb "
grep -qi 'u-boot.*\.elf' "$B" || m+="u-boot.elf "
[ -z "$m" ] || { echo "NOT IN $B: $m"; exit 1; }
echo "OK: $B names bl31.elf, a dtb and u-boot.elf"
```

If either fails, **do not launch**. Report in chat, in the issue / reason / suggestions format of
[../../rules.md](../../rules.md): the issue is that the package is incomplete, the reason is the
printed list of names that are missing (from `<PACKAGE_DIR>/sd_card/` or from the `.bif`), and the
suggestion is to repackage with them (the processor-side files `u-boot`, `bl31` and the device
tree for the boot image; the app files for the SD image), or to give the file names that are in
the image. The launch has not started; the report stage then writes `hw-emu_report.txt` with status NO-GO
and the printed list.

## Launch command

There is **no `-qemu-config`** (the package supplies its own boot configuration). The
login and password are both `petalinux`:

```bash
cd <COMPILED_OUT_DIRECTORY>
set -o pipefail
export ENABLE_AIE_DBG_TRACE=true && \
<PACKAGE_DIR>/launch_hw_emu.sh \
  -login "petalinux" -password "petalinux" \
  -run-app "<RUN_SCRIPT file name>" \
  | tee embedded_run.log; exit $?
```

`launch_hw_emu.sh` takes care of the login (the first login forces a password change, which it
handles), the mount (`/dev/mmcblk0p1` on `/mnt`, done as root with `sudo su`) and the `cd /mnt`.
It then runs the `-run-app` value as `./<value>` — **it adds the `./` itself** — so pass only
the script's file name, with no `mount`, no `cd` and no `./`. Without `-run-app` the launcher
ends the emulation right after the `cd /mnt`.

**The launch always turns the AIE debug trace on** — no question is asked. The command sets
`ENABLE_AIE_DBG_TRACE=true`, which writes `<SIM_DIR>/aie_log/` (it can be large for big or long
designs; tell the user it is on). It, and any other host-side AIE simulation variable, must be set
in the **same shell** as `launch_hw_emu.sh`: prepend them to the command.

## The runner script (`RUN_SCRIPT`)

The user supplies the runner script: the absolute path of the script on the host, whose
file name is the name it has in the image. This skill does not generate, edit or place it;
the package is assumed to provide it on the partition `launch_hw_emu.sh` mounts on `/mnt`.
The launch runs it by its file name (the launcher turns it into `./<RUN_SCRIPT file name>`).
A reference runner is written for the user when `RUN_SCRIPT` is asked: see
[runner-reference.md](runner-reference.md).

The pass or fail comes from the host's own `TEST PASSED` / `TEST FAILED` line. The launcher itself
prints `INFO: Embedded host run completed.` when the run-app command ends, so the script does not
have to; the launch then exits by itself.

## Markers (PetaLinux flow)

Strip ANSI codes first.

| Class | Markers |
|---|---|
| **PASS** | `TEST PASSED`, or `INFO: Embedded host run completed.` with no `ERROR: Embedded host run failed` before it. |
| **App failure** | `ERROR: Embedded host run failed, RC=<rc>`, `terminate called`, `Aborted`, `core dumped`, `No such device`, or `TEST FAILED`. |
| **Boot trouble** | `emergency mode`, `Timed out waiting for device`, `Dependency failed`. |
| **Quiet log** | no new output in `embedded_run.log` for 10 minutes. |

What to do for each class is in [../common/monitor.md](../common/monitor.md).

## Good to know

- **The run changes `sd_card.img`.** The guest writes to the image; for example the forced
  password change at the first login happens on the **first** run only, and a rerun reuses the
  changed image. To start from a clean image, repackage, or keep a copy of `sd_card.img`
  before the first run.
- **Relaunch is just launching again.** There is no staging, so a relaunch needs only
  `COMPILED_OUT_DIRECTORY`, `PACKAGE_DIR` and the `RUN_SCRIPT` file name (and the same
  `PLATFORM` for the board check). Keep the previous log as in
  [../common/monitor.md](../common/monitor.md).
- **Optional launcher flags.** `-verbose` and `-no-reboot` appear in some makefile flows; the
  run passes without them.

## With the AIE VCD dump

Add `-aie-sim-options ${PWD}/aiesim_options.txt` to the launch command (before the
`| tee`); creating the file is in [../common/monitor.md](../common/monitor.md).
