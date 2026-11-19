<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# EDF RunQEMU Troubleshooting

## Artifact and configuration failures

- **No qemuboot configuration:** build the board boot target that emits it. Do not use only a
  common-machine rootfs; it lacks the board QEMU DTBs, firmware, and boot configuration.
- **Missing `qb_default_kernel`, `qb_dtb`, or flash file:** keep the qemuboot file with its
  original deploy directory. Relative paths resolve from that directory. Rebuild the named
  board target only with user approval.
- **Compressed rootfs supplied:** use `xz -d -k` on a working copy, verify with `xz -t` first,
  and pass the uncompressed `.wic`.
- **Different machine names:** expected when pairing an EDF common-machine rootfs with a board
  SDT machine. Check architecture/SoC family and release rather than requiring identical names.
- **`SD1 boot mode selected but no SD device found`:** `runqemu` inferred plain `wic` and
  attached the image through generic SCSI even though the qemuboot file declares
  `qb_default_fstype = wic.qemu-sd`. Create a `*.wic.qemu-sd` symlink to the raw WIC and pass
  that name without resolving the symlink; the suffix selects the SD transport.
- **`Error listing disk image contents` / `Can't find executable 'mdir'`:** Versal SD boot
  makes `runqemu` inspect partition 1 for `boot.bin`. Add
  SDK mtools to `PATH`, or use `<evaluated COMPONENTS_DIR>/*/mtools-native/usr/bin`
  in a build environment; query `COMPONENTS_DIR`
  in the initialized Yocto shell rather than assuming `$BUILDDIR/tmp`. If it does not exist,
  build `mtools-native` in the clean Yocto environment after obtaining approval.
- **`Invalid SD card size`:** use a positive power of two up to and including 2 GiB, or a
  multiple of 512 KiB above 2 GiB. Round up to the next valid size (for example, 1536 MiB
  becomes 2 GiB). Preserve the deliverable WIC, make a real QEMU-only copy, and extend it
  with `qemu-img resize -f raw <copy>.wic.qemu-sd <bytes>`. Padding preserves existing
  partitions; the backup GPT may no longer be at the physical end of the image.
- **Missing `qemu-qspi.bin` or another `@DEPLOY_DIR_IMAGE@` file:** parse every deploy-relative
  reference in `qb_opt_append` during preflight. For `QEMU_FLASH_TYPE = "qspi"`, build
  `qemu-qspi` for the same board machine, then retry with the qemuboot file and all companion
  artifacts kept in one deploy directory.

## SD boot.bin missing or mismatched

Versal SD boot (`-boot mode=5`) loads the WIC's partition 1 `boot.bin`. The launcher
requires byte-for-byte equality with the `BOOT-<machine>.bin` passed as `-kernel`,
including when the WIC comes from a common-machine build.

`Error: boot.bin not found in <img>:1` means partition 1 lacks that file.
`Error: boot.bin does not match -kernel file` means the two files differ. A launcher
failure may still return status 0; inspect the log and require actual boot progress.

Preserve the source and repair a real copy, not a symlink to the source:

```bash
cp --reflink=auto -- <source>.wic <image>-<machine>.rootfs.qemu.wic.qemu-sd
wic cp <BOOT-<machine>.bin> <image>-<machine>.rootfs.qemu.wic.qemu-sd:1/boot.bin
# Use a new, empty verification directory.
mkdir <verify-dir>
wic cp <image>-<machine>.rootfs.qemu.wic.qemu-sd:1/boot.bin <verify-dir>/
cmp -- <BOOT-<machine>.bin> <verify-dir>/boot.bin
```

Use the board configuration's actual kernel file. Check `wic cp` succeeds and `cmp`
returns 0 before launching the copy; also check its SD size. This SD-boot requirement
does not establish the behavior of OSPI/QSPI boot.

- **`Unknown path arg <path>` for a writable copy:** preserve the original image/machine
  basename pattern containing `-image-` or `.rootfs.` and the `.wic.qemu-sd` suffix. The WIC need not be inside
  `DEPLOY_DIR_IMAGE`; moving it there alone does not fix an unrecognized basename.

## Startup and console failures

- **`runqemu` or QEMU binary missing:** source the EDF initializer or the EDF SDK environment
  shipped with Vitis in the same shell. SDK runs need neither `BUILDDIR` nor BitBake;
  Vitis/Vivado environment variables alone are not an error. If native
  QEMU artifacts were never built, identify the exact missing recipe before proposing a build.
- **No console after QEMU starts:** use `qemu-serial-port-tool <qemuboot.conf> --stdout` and
  inspect `serial_consoles` plus `qb_opt_append`. Xilinx multiarch QEMU commonly routes the
  usable APU console through one of several serial devices.
- **QEMU exits immediately:** rerun the wrapper with `--debug` and inspect the expanded command
  for unresolved `@DEPLOY_DIR_IMAGE@`, missing hardware DTBs, flash images, or an unsupported
  `qb_system_name`.
- **Guest cannot mount root:** confirm `qb_rootfs_opt`, partition table, and the WIC's root label.
  Ensure the image is an uncompressed raw WIC rather than `.wic.xz`.
- **Boot stalls before Linux:** distinguish PMC/PLM output from APU output. Verify the board
  `BOOT.bin`, QEMU hardware DTBs, flash image, and boot mode all came from the same board deploy
  set.
- **SSH reaches the server but returns `Permission denied (publickey,password)`:** verify the
  username and credentials supplied by the user. Some EDF images require an immediate password
  change at the first serial-console login; complete that exchange before retrying SSH. Do not
  guess passwords or interpret a successful SSH handshake as successful authentication.
- **The first-login password change vanishes after reboot:** expected in snapshot mode. Repeat
  the console initialization each boot, or restart with explicitly authorized `--writable` mode
  to persist account changes.

## Networking and shutdown

- **Tap setup fails or requests root:** tap devices may be pre-created or created by
  runqemu's privileged helper. Use slirp when its forwarding meets the connectivity need.
  Do not alter host networking without approval.
- **No inbound access with slirp:** `runqemu` forwards guest ports 22 (SSH) and 23 (Telnet)
  by default. Read its `Port forward:` lines and verify the guest service is listening.
  Other services require an additional forwarding rule with chosen host and guest ports.
- **SSH works on a different port than expected:** read the `Port forward:` line. When a
  preferred `/tmp/qemu-port-locks/<port>.lock` cannot be acquired, `runqemu` selects the next
  available host port automatically.
- **WIC changed unexpectedly:** use snapshot mode. Writable mode intentionally persists guest
  writes and must be explicit.
- **Exit sequence:** use `Ctrl-a x` in a foreground nographic session. `Ctrl-c` can reach
  the guest and is not a reliable host shutdown fallback. If a process remains, identify
  the exact QEMU PID before terminating it and inspect remaining helpers; never use broad
  `pkill qemu` commands.
- **`sessions should be nested with care` from tmux attach:** inside tmux, use
  `tmux switch-client -t <session>`. Outside tmux use `tmux attach -t <session>`; `-d`
  detaches other clients and does not solve nested attachment.

## TMPDIR on NFS

OE's sanity checker rejects NFS-backed `TMPDIR` when preparing missing QEMU or
mtools native tools. Existing QEMU artifacts alone do not require a work-tree
relocation or a rebuild. Native-tool builds still require user approval.

1. Query `bitbake-getvar --value TMPDIR` (or the release's equivalent) in the
   initialized Yocto shell. Use `findmnt -T <path>` on that path or its nearest
   existing parent. Do not infer it from `$BUILDDIR/tmp` or the shell's `TMPDIR`.
2. Preserve valid local storage. If the query fails, inspect the active configuration
   before retrying. For `nfs`/`nfs4`, stop preparation; keep the sanity checker enabled.
   Sources and build configuration may remain on NFS.
3. Agree on writable local storage unique to this user/build, with enough free space.
   Prefer persistent scratch; use `/tmp` only after checking its cleanup policy,
   filesystem and capacity.
4. With existing or new authorization, set `TMPDIR = "<local-path>"` in
   `$BUILDDIR/conf/local.conf`, preserving unrelated settings and resolving existing
   assignments. For a session-only setting, use the release's supported passthrough
   mechanism; `export TMPDIR` alone may be overridden and must be reapplied later.
5. Preserve the old work tree; do not delete or copy generated state. Relocation may
   rebuild native tools. Re-query `TMPDIR`, verify its filesystem and free space,
   and report the path and configuration change before retrying authorized work.

After relocation, re-query `COMPONENTS_DIR`, the needed recipe's
`RECIPE_SYSROOT_NATIVE`, and each image machine's `DEPLOY_DIR_IMAGE` before selecting
tools. Writable WIC copies may live outside the deploy directory. Report the effective
paths, filesystem and configuration change. Retry only the approved tool preparation or QEMU run;
relocation does not authorize an image build.
