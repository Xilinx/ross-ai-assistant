---
name: esw-runqemu
description: Boot AMD EDF images in QEMU when validating WIC userspace without hardware, pairing board boot artifacts with a common-machine rootfs, or troubleshooting runqemu startup, serial console, and networking failures. Supports Yocto build shells and the EDF SDK shipped with Vitis.
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

Use QEMU to validate firmware handoff, kernel startup, rootfs, networking, and userspace. Do
not claim this validates physical board peripherals, electrical behavior, or on-board boot ROM
handling. Versal SD-boot QEMU loads partition 1's `boot.bin` from the WIC and requires it to
be byte-for-byte equal to the `BOOT-<machine>.bin` selected through `qemuboot.conf`.
For this boot mode, a common-machine WIC is usable only when this condition holds; see the
[SD boot repair procedure](references/troubleshooting.md#sd-bootbin-missing-or-mismatched).

## Instructions

1. Source either the checkout's EDF initializer or the EDF SDK environment shipped with
   Vitis. Confirm `runqemu` is on `PATH`; an SDK shell need not set `BUILDDIR`, and
   `XILINX_VITIS`/`XILINX_VIVADO` alone do not disqualify it. Versal SD boot needs `wic`
   and `mdir` (mtools) to inspect partition 1 and compare `boot.bin` with the kernel file.
   Use SDK-provided tools when available. If native tools need building, switch to a clean
   Yocto build shell, without Vitis/Vivado settings, and obtain approval for that build.
   Before native-tool preparation, query `bitbake-getvar --value TMPDIR` (or the
   release's equivalent) and check `findmnt -T <path>` or its nearest existing parent.
   Do not infer it from the shell's `TMPDIR`; preserve valid local storage and keep
   sanity checks enabled. On query failure or `nfs`/`nfs4`, stop and read
   `${CLAUDE_SKILL_DIR}/references/troubleshooting.md#tmpdir-on-nfs`.
   Existing SDK/QEMU artifacts do not require BitBake, relocation, or rebuilding tools.
2. Obtain both artifacts explicitly:
   - a board-machine `*.qemuboot.conf` beside its `BOOT-<machine>.bin`, QEMU hardware DTBs,
     and QSPI/OSPI image when referenced;
   - an uncompressed common-machine `.wic` rootfs satisfying the SD `boot.bin` requirement
     when using SD boot. Decompress `.wic.xz` to a working copy;
     `runqemu` does not boot the compressed file directly.
3. Read the qemuboot configuration. Record `machine`, `qb_system_name`, `qb_dtb`,
   `qb_default_kernel`, `qb_rootfs_opt`, serial settings, memory, boot mode, and flash-image
   references. Resolve relative paths against the qemuboot file's directory.
4. The wrapper dry run checks paths, transport suffix, and SD size; it does not compare
   the embedded `boot.bin`. Verify that file using the SD boot repair procedure above
   before launching in SD mode. Validate the pair with
   `${CLAUDE_SKILL_DIR}/scripts/run_edf_qemu.sh --dry-run`. A common rootfs machine name
   differing from the board machine is normal in EDF. Require compatible architecture/SoC
   family and release; report differing build IDs or timestamps.
   When `qb_default_fstype` is `wic.qemu-sd`, present an existing raw WIC through a
   `*.wic.qemu-sd` symlink so `runqemu` attaches it as SD rather than inferring generic SCSI
   from `.wic`. Preserve this suffix during path normalization. Xilinx QEMU requires a
   positive power-of-two SD image size up to and including 2 GiB; above 2 GiB, a multiple
   of 512 KiB is required.
   If necessary, preserve the original, make a real copy of the WIC, and extend only that
   copy to the next valid size with `qemu-img resize -f raw`. A symlink is not a copy.
5. Default to safe local execution:
   ```bash
   ${CLAUDE_SKILL_DIR}/scripts/run_edf_qemu.sh \
     --qemuboot <board.qemuboot.conf> \
     --rootfs <common-machine.rootfs.wic.qemu-sd>
   ```
   The wrapper defaults to `nographic slirp snapshot`: terminal console, unprivileged user
   networking, and no persistent writes to the WIC. Pass `--writable` only when the user wants
   guest changes persisted. Pass `--tap` only when tap networking is already configured or the
   user authorizes privileged network setup.
6. **Always launch QEMU inside a detached `tmux` session, and always print the connection
   instructions afterwards.** An agent's own PTY cannot be attached to by the user, so a
   console started there is readable but not typeable — the user cannot log in, and the
   session dies with the caller. `tmux` gives the user a real TTY over their existing SSH
   connection, with no X display, VNC server, or extra port involved. Do not attempt to open
   a terminal window (`xterm`, `gnome-terminal`) instead: an inherited `DISPLAY` often has no
   usable auth cookie, and QEMU here runs `-nographic` with a serial-only console, so an X
   session would host nothing but a terminal emulator.

   The example below uses a Yocto build initializer. For an SDK run, replace its
   `source` command with the EDF SDK environment setup used in step 1; no build
   directory argument is needed by the wrapper.

   ```bash
   tmux new-session -d -s edfqemu -x 200 -y 50 "bash -lc '\
     cd <workdir> && source <setup_yocto_env.sh> <init-build-env> <build-dir> && \
     ${CLAUDE_SKILL_DIR}/scripts/run_edf_qemu.sh --qemuboot <board.qemuboot.conf> --rootfs <common-machine.rootfs.wic.qemu-sd>; \
     exec bash'"
   tmux capture-pane -p -t edfqemu | tail        # poll boot progress without attaching
   tmux capture-pane -p -S -2000 -t edfqemu | grep -i "port forward:"
   ```

   Report to the user, every time an instance is started:
   - Outside tmux: `tmux attach -t <session>` (add `-d` to detach other clients).
     Inside tmux: `tmux switch-client -t <session>`.
     Use `ssh <user>@<host>` first if they are on another machine — the tmux socket under
     `/tmp/tmux-<uid>/` is local to that host and UID.
   - `Ctrl-b d` to detach with QEMU still running, `Ctrl-b [` to scroll the boot log,
     `Ctrl-a x` to quit QEMU. `Ctrl-c` may be delivered to the guest in nographic mode;
     it is not a reliable QEMU shutdown fallback. If QEMU remains, identify and terminate
     the exact host QEMU process, then inspect its helpers.
   - the host-forward ports read from the run's own `runqemu` output, and the login policy
     from step 7.

   Use `tmux list-clients -t <session>` to see whether the user is already attached, and do
   not type into the console while they are driving it.
7. Capture the exact command and console log. A successful validation should reach a login or
   requested service, not merely start the QEMU process. Report boot time, kernel identity,
   mounted root device, and network address when available.
   Some EDF images mark the initial account password expired. Complete the mandatory password
   change on the serial console before testing SSH; SSH authentication may reject the account
   until that first console login succeeds. Never guess credentials. In snapshot mode the
   password change disappears on shutdown, so repeat it after each boot or explicitly use
   writable mode when persistence is required.
   Read the account policy instead of guessing it: `EXTRA_USERS_PARAMS` in the EDF distro conf
   (`meta-amd-edf/.../conf/distro/amd-edf.conf`) declares the user, and a `useradd -p ''` plus
   `passwd-expire` pair means the login takes an empty password and then forces a change
   immediately. Confirm whether `local.conf` enables `empty-root-password`/`allow-root-login`
   before suggesting a root login.
8. On failure, rerun with `--debug`, preserve the console output, and read
   `${CLAUDE_SKILL_DIR}/references/troubleshooting.md`.

## Serial and networking

- `nographic` uses the serial configuration emitted into `qemuboot.conf`; do not add arbitrary
  `-serial` arguments because Xilinx multiarch machines define several serial endpoints.
- Use `qemu-serial-port-tool <qemuboot.conf> --stdout` when the expected console is unclear.
- Prefer `slirp` for non-root testing. It supplies outbound guest networking; `runqemu`
  also creates host forwards to guest SSH (22) and Telnet (23). Other guest services need
  additional forwarding. A forwarded port does not guarantee that its guest service runs.
- Read the actual host-forward ports from `runqemu` output. If its preferred lock files are
  unavailable, it automatically selects different ports; do not assume SSH always uses 2222.
- Tap networking can use pre-created tap devices or have `runqemu` create them through
  its privileged helper. Device creation may require administrator privileges; obtain
  approval before allowing the helper or changing host networking.

## Artifact ownership

Keep the source WIC immutable unless writable mode is explicitly requested. Snapshot mode is
the default. Do not rebuild missing QEMU artifacts implicitly; identify the missing target
(`qemu-helper-native`, `qemu-devicetrees-native`, `qemu-<flash-type>`, or the board boot target)
and obtain approval before starting a build.

For a writable run, prefer a real copy of the image over the deployed artifact. The copy
may live outside `DEPLOY_DIR_IMAGE`: `runqemu` recognizes the basename, not directory
membership. Use a basename containing `-image-` or `.rootfs.` and preserve the transport
suffix, for example
`<image>-<machine>.rootfs.writable.wic.qemu-sd`. An arbitrary renamed file may produce
`Unknown path arg` even when it exists. Keep the board configuration and its companion
artifacts together; do not relocate those just to place a WIC copy elsewhere.

## Handoff

Report the initializer, board machine, qemuboot file, rootfs path, network mode, disk mode,
console command, and outcome. State clearly that QEMU validation does not replace a physical
board boot test.
