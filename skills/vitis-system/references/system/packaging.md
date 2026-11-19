<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Packaging Reference — deployable images, `hw_emu` vs `hw`

The system project's **package** step turns the built hardware + host app into a
deployable image. This is the Requirement-6 deliverable.

## `hw_emu` vs `hw` (the core split)

Building and *running* are separate concerns — and building the `hw` image does
**not** require a board (Vivado implementation + `v++ --package` run headlessly).

| | `hw_emu` (emulation) | `hw` (hardware) |
|--|----------------------|-----------------|
| `proj.build(target=…, build_comps=True)` | emulation model | full Vivado synth/place-route + package |
| Output | `launch_hw_emu.sh`, emulation `.xclbin`, emu boot dir | `sd_card.img` / `BOOT.BIN` + `Image` + rootfs + `.xclbin` |
| Build time | minutes | tens of minutes → hours |
| Board needed to BUILD | no | **no** (headless impl + package) |
| Run / verify | on this host under QEMU | on the physical board only |
| success criterion | package built | deployable image exists (non-trivial) |
| Deploy | n/a (runs here) | write to SD / boot on board → the on-board debug/flash flow |

**So:** `hw_emu` = build-and-run here; `hw` = build-image here, deploy-on-board via
the on-board debug/flash flow. The skill never claims a `hw` design "ran" on this host (no board) — it reports
the produced image + how to deploy it.

> **Build gotcha (`hw` and `hw_emu`).** Don't pass `build_package=True`: it builds only the
> package step, and in an EDF project the package target does not depend on the link, so `v++ -p`
> fails on the missing `build/<target>/hw_link/<container>.xsa`. Use
> `proj.build(target=..., build_comps=True, build_package=False)`; for `hw_emu` the emulation
> package (`launch_hw_emu.sh` + emu `.xclbin`) is produced by that build.

## Packaging modes

- **`EDF`** (default) — the standard Vitis embedded packaging (assembles the boot
  image + SD layout from the platform's packaging metadata).
- **`PetaLinux`** — packaging driven by a PetaLinux project/output.

Set with `create_sys_project(..., packaging_mode='EDF'|'PetaLinux')`. It **must match the
platform's flow** — an EDF/Yocto platform (e.g. `vck190_base`) needs `'EDF'`; a PetaLinux platform
(e.g. `xilinx_vck190_base_202620_1`) needs `'PetaLinux'`. A mismatch fails packaging.

### How the user selects EDF vs PetaLinux

| User says | Set |
|---|---|
| EDF / Yocto / embedded-accel SD flow | `packaging_mode='EDF'` |
| PetaLinux flow / "package with PetaLinux" | `packaging_mode='PetaLinux'` |
| (nothing) + a shipped platform in the `system.md` table | the table row for that exact name |
| (nothing) + a custom `.xpfm` (e.g. an `*_e2e` export from the embedded platform flow) | **Ask** — do NOT assume `'EDF'` just because it's the API default |

**Verify after compose:** `"packagingMode"` in `<project>/vitis-sys.json` must match intent
(`PACKAGING_MODE_EDF` / `PACKAGING_MODE_PETALINUX`). A mismatch packages the wrong image layout —
fix `packaging_mode` at create time (recreate/clone), don't silently repackage.

### EDF vs PetaLinux — `v++` package and `--package.generate_sdcard`

Rigel generates the package command from **`packaging_mode` at project create**. Do **not** add
`generate_sdcard` to `package.cfg` for a normal `.xpfm`-platform `proj.build()` flow. **Exception:**
a fixed-`.xsa` platform — see "Fixed-XSA platform" below.

| `packagingMode` in `vitis-sys.json` | Typical generated command | `--package.generate_sdcard` |
|-------------------------------------|---------------------------|-----------------------------|
| `PACKAGING_MODE_PETALINUX` | `v++ --platform … --package …` | **Present** in `<project>/package/CMakeLists.txt` |
| `PACKAGING_MODE_EDF` | `v++ -p -s -f <linked.xsa> …` | **Absent** (intentional) |

The `v++ --platform … --package` / `generate_sdcard` row applies when the project's platform is an
**`.xpfm`**. If `"platform"` in `vitis-sys.json` is a **fixed `.xsa`** (secondary system projects
from a Mode A template, or a system created from an integration output), Rigel generates
`v++ -p -s -f <fixed>.xsa …` **without** `--package.generate_sdcard` even for
`PACKAGING_MODE_PETALINUX` — see "Fixed-XSA platform" below.

The Python API defaults `packaging_mode='EDF'`. User wording such as *PetaLinux flow* does **not**
change that — you must pass `packaging_mode='PetaLinux'` on
`create_sys_project` / `create_system_and_integration_project`.

**When the user asks for PetaLinux:**

1. Pass `packaging_mode='PetaLinux'` at create.
2. Confirm `vitis-sys.json` shows `PACKAGING_MODE_PETALINUX`.
3. If the project's platform is an `.xpfm`, confirm `<project>/package/CMakeLists.txt` contains
   `--package.generate_sdcard` (fixed-`.xsa` platform: Rigel does not add it — use the "Fixed-XSA
   platform" section instead). If (2) or (3) fails, **stop** before `proj.build()` and recreate
   with the correct mode — for an `.xpfm` platform, do not patch `package.cfg` to add
   `generate_sdcard`.

This skill pins **no** PetaLinux release; it needs a Vitis 2024.2+ install and the user-supplied
sysroot/rootfs/kernel for a Linux `hw` image. Document what the user provided, not a guessed version.

## Fixed-XSA platform: PetaLinux boot files (`hw` SD image)

When `vitis-sys.json` `"platform"` is a **fixed `.xsa`** (`…/hw_link/binary_container_1.xsa`) there is
no platform `sw/boot` for v++ to fall back on. In the PetaLinux flow v++ accepts either an `.xpfm` or a
fixed `.xsa` after `-f`; with a fixed `.xsa` the boot files below are needed (EDF needs none of them).
Verified on 2026.2 (vck190, `--target hw` and `--target hw_emu`, both with `generate_sdcard`): without `bl31_elf` / `uboot` / `dtb` the `BOOT.BIN` is PDI-only (~0.45 MB, no
U-Boot/bl31) and `sd_card/` has no `system.dtb`; with them `BOOT.BIN` is ~2 MB (bl31 + U-Boot) and
`system.dtb` is included.

For a PetaLinux SD image (**`hw` or `hw_emu`**) from a fixed-XSA project, add under `[package]` in
`package/package.cfg` (same mechanism as `bma` below), taking the files from the extensible
platform's boot dir:

```ini
[package]
bl31_elf=<platform>/sw/boot/bl31.elf
uboot=<platform>/sw/boot/u-boot.elf
dtb=<platform>/sw/boot/system.dtb
generate_sdcard=true
```

- **Not needed** when the platform is an `.xpfm` (it ships these; they are then optional overrides), and
  not needed for the EDF flow. **Needed for both `hw` and `hw_emu`** with a fixed `.xsa`.
- v++ prints `82-12058` (options deprecated in 2026.2); they still work — not an error.
- Rigel does not add `--package.generate_sdcard` for a fixed-XSA project. Use
  `generate_sdcard=true` in `package.cfg` as above — the `USER_PACKAGE_OPTIONS` CMake variable is
  **not** used in the `-f <fixed>.xsa` command, so don't rely on it.
- Verify: `sd_card/` contains `system.dtb`, `BOOT.BIN` is the larger (~MB) image, and
  `sd_card.img` exists. Tested through Rigel `proj.build(target='hw')` and by hand with `v++ --target hw_emu`;
  on-board / QEMU boot not validated.
- Host: `generate_sdcard` needs `parted` on `PATH` (`/usr/sbin` on RHEL) — a missing `parted` is a
  host-env failure, not a design error.

## Fixed-XSA platform from an AIE design: supply `libadf.a`

A fixed `.xsa` linked from a design with an AIE graph carries an `AIE_METADATA` section, and
`v++ -p` needs the AIE archive again at package time. A system project whose platform is that
fixed `.xsa` and which holds only the host does **not** pass it (the generated `_AIE_ARCHIVES`
is empty), so packaging fails:

```
ERROR: [v++ 82-3880] Input file '.../link.xclbin' contains AIE_METADATA section, which requires either an AIE Compiler archive file directly, or a VSS file containing an AIE Compiler archive.
```

Fix: add the archive as a **top-level** `input_files=` line (above `[package]`; it is a v++
common option, not a `[package]` key) in `<proj>/package/package.cfg`, then build as usual:

```ini
temp_dir=package.build
save-temps=1
input_files=<workspace>/<aie_component>/build/hw/libadf.a

[package]
out_dir=./package
defer_aie_run=true      # optional; the template-generated projects pass --package.defer_aie_run
```

- Verified for `hw_emu` on 2026.2 (rc 0; `package/launch_hw_emu.sh`, `container.xclbin`,
  `container.pdi`); the `hw` and `.vss` variants were not run.
- Don't `proj.add_component(<aie_component>)` on the fixed-XSA project: it adds an empty
  `hw_link`, the build prints "Nothing to be done" and returns rc 0 with **no** package outputs.
  `add_container` is rejected (no `hw_link`). `USER_PACKAGE_OPTIONS` is not used by this command.
- The secondary system projects a Mode A template creates already pass `libadf.a`; this applies
  to a system project you create yourself on a fixed `.xsa`.

## BMA metadata (`--package.bma`)

`v++ --package` accepts **`--package.bma <path>`** (2026.2). There is **no** `embedded_component_paths`
key for BMA — only `sysroot`, `kernel_image`, `root_fs`, `sysroot_toolchain`, `use_sysroot_toolchain`.

**When:** some EDF / user-exported platforms ship a `metadata.bma` next to the extensible hardware
(e.g. under the platform export tree). If packaging needs it (or the user asks to pass BMA metadata),
add it to the system project's package config:

1. Ensure `package/package.cfg` exists (from the template or the project dir).
2. Under a `[package]` section, add the path (absolute, or relative to the cfg file):

   ```ini
   [package]
   bma=/path/to/metadata.bma
   ```

3. No registration needed: `package/package.cfg` is already listed in `vitis-sys.json`
   (`configFiles: ["package/package.cfg"]`). Don't call `proj.add_cfg_files(name='PACKAGE', ...)`;
   it is rejected with `NOT_FOUND`.
4. Package / `proj.build(...)` so v++ is invoked with `--config …/package/package.cfg`.

**Confirm it was used:** check the **v++ package command line** in the build log for `--config`
pointing at your `package.cfg`. v++ **may not log `bma` by name** even when the cfg is honored — the
practical check is the cfg on the command line + a successful package; on-board boot is the ultimate validation.

## Linux deployable image inputs (required for an SD image)

A bootable **Linux** SD image needs, via `embedded_component_paths` (driver flags
`--sysroot` / `--rootfs` / `--kernel-image`). **These are user inputs — ask the user for the
three paths; the skill ships no default location and must not hardcode, guess a device-family
directory, or scan the filesystem to find them:**

- **`sysroot`** — PetaLinux/Yocto sysroot (must have a top-level `/lib`, like the app
  skill's Linux sysroot). Validate top-level `/lib` on whatever the user gives you.
- **`root_fs`** — the root filesystem image.
- **`kernel_image`** — the kernel `Image`.

The shipped `edge_use_case_1.py` reads these from `IDE_VERSAL_SYSROOT`,
`IDE_VERSAL_ROOT_FS`, `IDE_VERSAL_KERNEL_IMAGE` (env-var **names**, not paths to assume). Baremetal/standalone accelerated
apps and `hw_emu` may not need all three; if they are missing for a `hw` Linux SD
image, build with `--no-package` (link/build only) or `hw_emu`, and document the SD
path rather than producing it.

## Boundary with standalone bootgen

Don't double-package. This skill's package step is the acceleration/system path — it
*internally* runs bootgen against the platform's packaging metadata + sysroot/rootfs/
kernel to build the SD image. Standalone bootgen is the IDE-agnostic
tool for explicit BIF/component boot images (embedded, non-acceleration). Use
the system-project package for a deployable image; use standalone bootgen when you have the components and want
a boot image directly.

## Deploying a `hw` image (handoff to the on-board debug/flash flow)

- `sd_card.img`: `sudo dd if=sd_card.img of=/dev/sdX bs=1M status=progress` → insert + power on.
- `BOOT.BIN` + `Image` + rootfs: copy to the SD boot/root partitions per the package layout.
- Or hand the image to the on-board debug/flash flow for scripted on-board flash/boot.
