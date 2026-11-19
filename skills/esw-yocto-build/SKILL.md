---
name: esw-yocto-build
description: Run AMD EDF (Embedded Development Framework) Yocto/BitBake builds and inspect or modify resulting WIC images, covering explicit target and MACHINE selection, the common-machine rootfs versus board-machine boot-artifact split, multiconfig targets, DISTRO overrides, long-running detached builds, WIC contents, and failure triage. Use when the user asks to build, bitbake, or rebuild an EDF image, packagegroup-boot-artifacts, boot.bin, OSPI/QSPI image, SDK or container target; inspect or modify a .wic/.wic.xz image; check what EDF targets are available; or diagnose a failed EDF build. Requires an already sourced Yocto environment; use $esw-yocto-source-setup when it is not active, and $esw-gen-machine-conf to create a machine that does not exist yet.
metadata:
  version: "1.0.0"
  stage: beta
allowed-tools: Read, Bash, Write
license: MIT
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

Run BitBake against an initialized EDF build directory. Do not fetch sources, source the
environment, or generate machine configuration here.

## Instructions

1. Confirm the shell is initialized and clean:
   ```bash
   ${CLAUDE_SKILL_DIR}/scripts/run_edf_build.sh --env-check
   ```
   This checks `BUILDDIR`, `bitbake` on `PATH`, free disk, that the MACHINE resolves, and that
   `XILINX_VITIS`/`XILINX_VIVADO` are unset. **A Vitis/Vivado environment in the build shell is
   a hard failure, not a warning** — it injects a host `PATH`, `LD_LIBRARY_PATH` and Python that
   corrupt an OE build. Setup and build must happen in the same shell; hand off to
   `$esw-yocto-source-setup` if `BUILDDIR` is unset.

   Before building, query effective `TMPDIR`, `HOSTTOOLS_DIR` and `DEPLOY_DIR_IMAGE`
   with `bitbake-getvar` (or the release's equivalent), for the selected machine and
   enabled multiconfigs. Check `findmnt -T <TMPDIR>` or its nearest existing parent;
   the shell's `TMPDIR` is not proof. Preserve valid local storage and keep sanity
   checks enabled. On query failure or `nfs`/`nfs4`, stop and follow
   `${CLAUDE_SKILL_DIR}/references/troubleshooting.md#tmpdir-on-nfs`.

   On every build, let the wrapper probe the compiler on `PATH` and its existing main or
   multiconfig host-tools trees. It currently scans `$BUILDDIR/tmp*/hosttools`; when the
   evaluated `HOSTTOOLS_DIR` paths are elsewhere, probe those explicitly as described in
   the troubleshooting reference. The probe compiles a `DBL_MAX` expression with `-O2` and
   fails fast on the incompatible-host-compiler signature before BitBake starts. Also inspect
   the links on an unfamiliar host:
   ```bash
   ls -l "<evaluated HOSTTOOLS_DIR>"/{gcc,g++,cpp,gcc-ar}  # repeat per configuration
   command -v gcc                                   # what PATH says — often different
   ```
   OE resolves host tools through separate persistent `tmp*/hosttools` symlink trees, and
   creates entries **only when missing**, so an entry from the first build survives every
   later `PATH` change. A broken host compiler then fails hours later inside a `-native`
   recipe. Check all multiconfig trees, not just the main `tmp/hosttools`. See
   `${CLAUDE_SKILL_DIR}/references/troubleshooting.md#host-toolchain-faults` for the
   probe and the fix.

2. Read the checkout's own target list before proposing anything:
   ```bash
   ${CLAUDE_SKILL_DIR}/scripts/run_edf_build.sh --list-targets     # cats $BUILDDIR/conf/conf-notes.txt
   ```
   `conf-notes.txt` is authoritative per release and lists the valid machines and targets.
   Do not rely on remembered target names. For `packagegroup-boot-artifacts`, inspect the
   active layers' recipe and appends and the evaluated metadata for the selected machine;
   see `${CLAUDE_SKILL_DIR}/references/targets.md#boot-artifact-targets`. The group's
   contents can vary by machine and release, including the active 2026.2 checkout.

3. **Obtain the machine from the user before starting any build.** Do not infer or select a
   machine from the board family, an earlier build, `local.conf`, or the target name. If the
   user did not explicitly name a machine, show the relevant choices from `conf-notes.txt`
   and ask which exact `MACHINE` to use. A recommendation is fine, but do not start until the
   user chooses. If the target/machine combination is unsupported, warn before starting and
   ask for confirmation rather than relying on BitBake's warning during the build.

4. **Establish which half of the build the user wants.** EDF splits a bootable system across
   two builds with two different `MACHINE` values:
   - a **common** machine (`amd-cortexa78-common`, ...) builds the portable rootfs
     (`edf-linux-disk-image`, `edf-platform-disk-image`);
   - a **board SDT** machine (`versal-2ve-2vm-vek385-revb-sdt-seg`, ...) builds boot artifacts
     (`packagegroup-boot-artifacts`, or individual targets such as `xilinx-bootbin`,
     `edf-ospi`, `edf-qspi`, `kria-qspi`, where supported).

   Every common machine is marked "Not bootable on its own" and clears `HW_BOOT_MODE`,
   `SOC_ON_DISK_BOOT_BIN` and `IMAGE_BOOT_FILES`, so it will never emit a `boot.bin`.
   A request like "build an image for VEK385" is therefore ambiguous. State the split, say
   which half you are building and which target/machine pair that implies, and ask before
   assuming the user wants both. Read `${CLAUDE_SKILL_DIR}/references/targets.md` for the
   machine/target matrix and the common-machine-to-SoC-family mapping.

   A request for `packagegroup-boot-artifacts` selects the boot-artifact group. Preserve
   that target rather than substituting a fixed list of recipes or adding a rootfs build.
   Explain its resolved contents and check compatibility with the user's selected machine
   using the wrapper's `--dry-run` before the real build. For individual boot.bin or SPI
   image requests, select the corresponding supported individual recipe.

5. Resolve `MACHINE` explicitly. Pass it in the environment of every BitBake invocation,
   for example `MACHINE=<machine> bitbake <target>`, rather than relying on `local.conf`,
   whose default is often still `qemuarm64`. Never edit `local.conf` to set `MACHINE`.
   The `-m` option of `${CLAUDE_SKILL_DIR}/scripts/run_edf_build.sh` implements this same per-invocation
   environment override and leaves the checkout usable for other machines.
   If the machine does not exist, stop and route to `$esw-gen-machine-conf`; do not substitute a
   similarly named BSP machine.

6. Take `DISTRO` from `conf-notes.txt` when the target needs one (e.g.
   `DISTRO=amd-edf-small` for AMR platforms) and pass it in the BitBake command environment.
   The wrapper's `-D` option implements this environment override. Otherwise leave the
   checkout default alone.

7. Build. **Builds run for hours. Prefer a detached `tmux` session** so they survive the
   caller and the user can attach to the live BitBake UI. The wrapper does this by default
   when `tmux` is installed (`--detach` remains a compatibility alias):
   ```bash
   ${CLAUDE_SKILL_DIR}/scripts/run_edf_build.sh -m <machine> <target>
   tmux attach -t <printed-session>
   tmux capture-pane -p -S -2000 -t <printed-session>
   ```
   Pass `--tmux-session <name>` for a stable custom name. Report `tmux attach -t <session>`
   (or `tmux attach -d -t <session>` when already attached elsewhere), `Ctrl-b d` to detach,
   and the printed capture/log commands. The session remains open after BitBake exits so the
   result can be inspected. Do not start a second BitBake against the same `$BUILDDIR`.

   If `tmux` is unavailable, the wrapper falls back automatically to detached
   `setsid`/`nohup`, prints the PID and log path, and remains usable without installing
   anything. Report that the live attach capability is unavailable in fallback mode.

   Run `--dry-run`, environment checks, and small queries in the foreground.

   For a multiconfig target generated alongside the machine, use BitBake's `mc:` prefix, e.g.
   `mc:<machine>-microblaze-pmc:<target>`; the machine conf's `BBMULTICONFIG` line lists the
   available multiconfigs.

8. On success, report the evaluated `DEPLOY_DIR_IMAGE` and list the artifacts actually
   produced. Check the wrapper's printed deploy path against the evaluated value when
   `TMPDIR` is relocated. When
   both halves were built, report both directories — they differ by machine.
   For `packagegroup-boot-artifacts`, report the resolved group members and their actual
   deploy outputs. Check requested firmware/image files are nonempty and resolve symlinks
   to canonical paths. Identify missing outputs; the packagegroup itself is not a boot
   image, and its success does not prove that every possible boot or SPI format was produced.
   If the user requests hardware loading, hand off the matching board artifacts to
   `$esw-jtag-boot`; for writing QSPI/OSPI flash, use `$esw-program-flash`. Run these
   in a separate Vitis hardware shell. A completed build alone does not authorize
   programming a board or writing flash.

9. On failure, read the `log.do_<task>` file BitBake names, not just the console summary, then
   consult `${CLAUDE_SKILL_DIR}/references/troubleshooting.md`.

## Inspect or modify WIC images

Use Yocto's `wic` utility for partition tables and filesystem contents instead of mounting
the image. In this checkout `scripts/wic` may be absent or the `scripts/esdk-tools/wic`
symlink may be dangling. Use the `wic` installed in the image recipe's native sysroot and
put that sysroot's `usr/bin` first on `PATH` so its `nativepython3` launcher resolves:

```bash
ns="<evaluated RECIPE_SYSROOT_NATIVE for the image recipe>"
wic="$ns/usr/bin/wic"
PATH="$ns/usr/bin:$PATH" "$wic" ls image.wic --native-sysroot "$ns"
PATH="$ns/usr/bin:$PATH" "$wic" ls image.wic:1/ --native-sysroot "$ns"
PATH="$ns/usr/bin:$PATH" "$wic" cp boot.bin image.wic:1/ --native-sysroot "$ns"
```

`wic ls image.wic` needs only partition tools, but FAT directory and copy operations also
need `mdir`/`mcopy` from `mtools-native`. A sysroot may contain `wic` without those programs.
If `wic` reports `Can't find executable 'mdir'`, build the native tool in the clean Yocto
shell and prepend its component directory:

```bash
bitbake mtools-native
mtools="<evaluated COMPONENTS_DIR>/x86_64/mtools-native/usr/bin"
PATH="$mtools:$ns/usr/bin:$PATH" "$wic" ls image.wic:1/ --native-sysroot "$ns"
```

Do not install host mtools when the matching Yocto-native tool is available.

Query `RECIPE_SYSROOT_NATIVE` for the selected image recipe and `COMPONENTS_DIR` for the
native tools in the initialized shell. Verify those directories rather than guessing work
paths or assuming `$BUILDDIR/tmp`. Partition numbers are one-based. Run `wic ls image.wic` first, identify each filesystem
and label, and confirm the requested destination partition when the wording is ambiguous.
For standard EDF disk images, confirm the release's `.wks`: partition 1 (`esp`, `/efi`) holds
`boot.bin`, partition 2 (`storage`) is user storage, and partition 3 (`root`) is the rootfs.
Do not choose the empty storage partition just because it has more space.

Before combining independently produced rootfs and boot artifacts, extract and compare the
WIC's `edf-version.txt` with `bootbin-version.txt` and `boot.bin.manifest.json` from the boot
deploy directory when available. Check the EDF/Xilinx release, target architecture or SoC
family, board machine, hardware boot mode, and build IDs/timestamps. A common-machine rootfs
and matching-family board-machine boot binary are expected to have different machine names;
that alone is not a mismatch. Report differing release builds or timestamps explicitly and do
not claim hardware compatibility unless the combination has actually booted on the board.

After insertion, list the destination, copy the file back out, and verify it byte-for-byte
with `cmp` and `sha256sum`; a successful `wic cp` alone is not sufficient verification.

For `.wic.ufs` images the sector size is 4096 bytes, so every `wic` command must pass
`--sector-size 4096`; without it `wic` assumes 512-byte sectors and misreads the partition
table. Add it to each invocation, for example:

```bash
PATH="$ns/usr/bin:$PATH" "$wic" ls image.wic.ufs --sector-size 4096 --native-sysroot "$ns"
PATH="$ns/usr/bin:$PATH" "$wic" cp boot.bin image.wic.ufs:1/ --sector-size 4096 --native-sysroot "$ns"
```

Treat `.wic.xz` as a compressed derivative: do not attempt to edit it directly. Modify the
uncompressed timestamped `.wic`, then regenerate its matching `.wic.xz` with `xz -T0 -k -f`,
run `xz -t`, and use `xz --robot -l` to verify that its recorded uncompressed size equals the
modified WIC. Be aware that recompressing can change the compressed size while preserving the
same uncompressed image. Read the original uncompressed size with `xz --robot -l` and check
free space before extraction. Conservatively allow about twice that size when preserving the
working WIC while writing a new compressed file; report and retain the uncompressed copy unless
the user asks to remove it.

AMD Account form URLs (`account.amd.com/.../xef.html?filename=...`) may require an authenticated
browser/export-control session and can stall while returning zero bytes to command-line clients.
Use bounded connection and overall timeouts, then validate the payload with `file` and `xz -t`;
never treat HTML, a login response, or an empty partial file as the requested image. If login is
required, ask for a local downloaded path and check only shared artifact locations the user puts
in scope.

## Guardrails

- Never source Vitis/Vivado settings in the build shell; use a separate shell for `sdtgen`.
- Always use the wrapper for real BitBake builds. It prefers `tmux` and safely falls back to
  `setsid`/`nohup` when `tmux` is absent. Do not use plain background jobs or terminal-emulator
  windows. `tmux` improves observability but does not replace the host-compiler checks.
- Never modify `local.conf` to select `MACHINE`, `DISTRO`, parallelism, or other per-build
  settings. Pass those values in the BitBake command environment.
- Never modify files beneath `tmp/work`. They are generated build state, and changes there
  are non-reproducible and may be overwritten. On a recipe or compiler failure, stop and
  report the failing task and log. If a source fix is required, propose a persistent layer
  patch or `.bbappend` and obtain explicit approval before changing it.
- Do not modify `bblayers.conf` or any machine conf to make a build succeed without the
  user's approval. Report the needed change and its effect instead.
- Do not disable `own-mirrors`, `SSTATE_MIRRORS`, or the `BB_DISKMON_DIRS` guards to work
  around a failure; those mask a real environment problem.
- Do not start a build merely to validate configuration — use `--dry-run`.
- When a `-native` recipe fails to compile, check the host compiler before suspecting the
  recipe, and check `tmp/hosttools` before trusting `PATH`.
- Do not run `cleansstate`, `cleanall`, or delete `tmp/` without approval; these discard
  hours of cached work.
- Report the resolved `machine conf` path the wrapper prints. A machine generated into
  `$BUILDDIR/conf/machine` shadows a same-named BSP machine, which is easy to miss.
- Treat shared mirror paths as external state: report when they are missing
  rather than silently building everything from source.

## Handoff

Report the exact `MACHINE`, `DISTRO`, target list, log path, and deploy directory for each
build. For flashing or booting the result, hand off to the relevant `segcfg-*` runtime skill.
