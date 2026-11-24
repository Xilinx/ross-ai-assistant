---
name: hwemu-baremetal-stage
description: >
  Baremetal flow staging — copy the QEMU prebuilt into base_camp_images/ and put the packaged
  boot PDI (the .pdi in PACKAGE_DIR, which already holds the ELF) into the boot image of the
  board: dd into the OSPI image on vek385 / vrk160, or wic cp into the rootfs .wic boot
  partition on vck190 / vek280. Consumes QEMU_PREBUILT, PACKAGE_DIR, PLATFORM, YOCTO_ENV.
metadata:
  version: "1.0.0"
---

# HW Emulation (baremetal flow) — Stage the boot image

Uses: `QEMU_PREBUILT`, `PACKAGE_DIR` (its `.pdi`), `PLATFORM` (for the board), and `YOCTO_ENV`
on the SD boards only. Run from `COMPILED_OUT_DIRECTORY`. There is no rootfs app, no runner and
no login: the ELF is already inside the `.pdi`.

## STEP A — Copy the QEMU prebuilt

**Tell the user first.** This stage replaces `<COMPILED_OUT_DIRECTORY>/base_camp_images/`. If it
already exists, say so before running the command: "`base_camp_images/` already exists in
`<COMPILED_OUT_DIRECTORY>` and will be deleted and replaced; anything staged there earlier is
lost." The prebuilt is large (about 8 GB), so the copy takes about a minute.

```bash
cd <COMPILED_OUT_DIRECTORY>
rm -rf base_camp_images; mkdir -p base_camp_images
cp -prf <QEMU_PREBUILT>/. base_camp_images/.
```

The prebuilt names most images twice: a **dated file** and an **undated soft link** to it. The
copy keeps both (`cp -p` keeps the links).

## STEP B — Put the boot PDI into the board boot image

**Update the dated files, not the links**, so every link that points at them sees the new
content. The `.pdi` in `PACKAGE_DIR` (any file name; exactly one) replaces the contents of the
board boot image:

```bash
cd <COMPILED_OUT_DIRECTORY>
PDI=$(ls <PACKAGE_DIR>/*.pdi)
BOARD_BOOT_BIN=$(readlink -f base_camp_images/BOOT-versal-*-multidomain.bin)
cp "$PDI" "$BOARD_BOOT_BIN"
```

The pattern must match exactly one undated link (`ls base_camp_images/BOOT-versal-*-multidomain.bin`
lists one), and `ls <PACKAGE_DIR>/*.pdi` must list one file; otherwise stop and tell the user.

Then fold it into the medium the board **boots from**. Take the board from the `PLATFORM` file
name:

| Board | Boot mode | Where the boot image goes | Command |
|---|---|---|---|
| **vek385**, **vrk160** | OSPI | the OSPI flash image `qemu-ospi.bin` points at | **A** — `dd` |
| **vck190**, **vek280** | SD | rootfs `.wic` boot partition → `$ROOTFS_WIC:1/boot.bin` | **B** — `wic cp` (needs `YOCTO_ENV`) |

**A — OSPI:**
```bash
OSPI_IMG=$(readlink -f base_camp_images/qemu-ospi.bin)
dd if="$BOARD_BOOT_BIN" of="$OSPI_IMG" conv=notrunc
```

**B — SD:**
```bash
# unset required — YOCTO_ENV aborts if LD_LIBRARY_PATH is set; see rules.md "Toolchain env"
unset LD_LIBRARY_PATH
source <YOCTO_ENV>
ROOTFS_WIC=$(readlink -f base_camp_images/*.rootfs.wic.*)
wic cp "$BOARD_BOOT_BIN" "$ROOTFS_WIC":1/boot.bin
```

`$ROOTFS_WIC` is the dated rootfs `.wic` that the undated `*.rootfs.wic.*` link points to;
partition `:1` is its boot partition.
