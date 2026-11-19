---
name: hwemu-edf-update-bootbin
description: >
  EDF flow step 2 — place the packaged BOOT.bin onto the platform's boot medium
  (selected from PLATFORM) so QEMU boots the emulated design. Consumes PACKAGE_DIR
  and YOCTO_ENV.
metadata:
  version: "1.0.0"
---

# HW Emulation (EDF flow) — Update BOOT.bin

Uses: `<PACKAGE_DIR>/BOOT.bin`.

**Update the dated files, not the links.** The prebuilt stages each image as a dated file plus
an undated soft link to it. Write to the **dated** (real) file, so every link that points at
it sees the new content. Resolve it from the undated link with `readlink -f`:

```bash
cd <COMPILED_OUT_DIRECTORY>
BOARD_BOOT_BIN=$(readlink -f base_camp_images/BOOT-versal-*-multidomain.bin)
```

The pattern must match exactly one undated link (`ls base_camp_images/BOOT-versal-*-multidomain.bin`
lists one); if it lists none or several, stop and tell the user.

**The fold command varies by the platform's boot mode.** First overwrite the board boot
image with the packaged `<PACKAGE_DIR>/BOOT.bin`:

```bash
cp <PACKAGE_DIR>/BOOT.bin "$BOARD_BOOT_BIN"
```

Then fold it into the medium the platform **boots from**. **Select the branch from
`PLATFORM`** per the table — **do not assume OSPI**:

| `PLATFORM` | Boot mode | BOOT.bin fold target | Command |
|---|---|---|---|
| **VRK160**, **VEK385** | OSPI | the OSPI flash image `qemu-ospi.bin` points at | **A** — `dd` |
| **vck190**, **vek280** | SD | rootfs `.wic` boot partition → `$ROOTFS_WIC:1/boot.bin` | **B** — `wic cp` |

**A — OSPI** — write the boot image into the OSPI flash image QEMU boots from:
```bash
OSPI_IMG=$(readlink -f base_camp_images/qemu-ospi.bin)
dd if="$BOARD_BOOT_BIN" of="$OSPI_IMG" conv=notrunc
```

**B — SD** — write the boot image into the rootfs `.wic` **boot partition (`:1`)**
as `boot.bin` via `wic cp` (needs the SDK env for the `wic` tool):
```bash
# unset required — YOCTO_ENV aborts if LD_LIBRARY_PATH is set; see rules.md "Toolchain env"
unset LD_LIBRARY_PATH
source <YOCTO_ENV>
ROOTFS_WIC=$(readlink -f base_camp_images/*.rootfs.wic.*)
wic cp "$BOARD_BOOT_BIN" "$ROOTFS_WIC":1/boot.bin
```

`$ROOTFS_WIC` is the dated rootfs `.wic` that the undated link (`*.rootfs.wic.*`, e.g.
`*.rootfs.wic.qemu-sd`) points to; partition `:1` is its boot partition, `:2` its rootfs
(where `copy-wic` stages the run artifacts).
