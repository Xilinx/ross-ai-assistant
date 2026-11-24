# Dependencies

Version: 1.0.0

Hardware emulation needs a sourced Vitis environment **plus** four site-specific
artifacts that are never bundled with this skill. This skill does not build
anything, so no compiler or sysroot is required.

## 1. Vitis (required)

Source the Vitis settings so `XILINX_VITIS` is set and the tools the emulation
launch uses (`launch_hw_emu.sh`'s simulator and QEMU) are on `PATH`:

```bash
source <Vitis_install>/settings64.csh   # tcsh/csh
# or
source <Vitis_install>/settings64.sh    # bash/sh
```

> **Pick the script that matches your shell**, not the session's reported shell:
> `.csh` for tcsh/csh, `.sh` for bash/sh/zsh. Source whichever matches the shell
> you launch the session from.

> **Source it, then relaunch the session.** Sourcing `settings64` sets many
> session-wide dependencies (`XILINX_VITIS`, `PATH`, `LD_LIBRARY_PATH`, etc.).
> Source it in your own shell and start the session from that shell so the
> environment carries in.
>
> Do **not** prepend `source ...` to individual commands: each command runs in a
> fresh shell, so it won't persist across the flow.

## 2. Built hw_emu product (required, user-supplied)

The output of a prior build, in `COMPILED_OUT_DIRECTORY`: the packaged hw_emu
directory (`PACKAGE_DIR`: `launch_hw_emu.sh`, `BOOT.bin`, and on VRK160/VEK385 also a
`.pdi` and a `.dtbo`; the file names can be anything), the `.xclbin`, and the
cross-compiled host executable. **Not
bundled and not built by this skill** — built beforehand for the same platform
you emulate on.

## 3. Hardware platform — `.xpfm` (required, user-supplied)

Names the board/platform the product was built for. It selects the boot-mode and
device branches in the staging and launch stages. Provide it via the `PLATFORM`
input. **Not bundled.**

## 4. QEMU prebuilt directory (required, user-supplied)

The embedded run assembles its boot image from a **QEMU prebuilt** directory (the
Yocto `..._qemu_prebuilt` artifacts — `combined.qemuboot.conf`, the rootfs
`.wic`, `qemu-ospi.bin`, board `BOOT-*.bin`). **Not bundled.**

## 5. Yocto SDK env-setup script (required, user-supplied)

The `wic` tool used to stage the rootfs is provided by the Yocto SDK — the run
flow sources `YOCTO_ENV` (the SDK `environment-setup-*` script) before calling
`wic cp`. **Not bundled.**

---

Because of items 2–5, the build and Yocto paths are site-specific and must be
supplied/confirmed by the user. See `references/input.md`.
