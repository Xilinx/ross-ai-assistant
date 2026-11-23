---
name: hwemu-edf-copy-prebuilt
description: >
  EDF flow step 1 — stage the QEMU prebuilt into base_camp_images/.
  Consumes QEMU_PREBUILT.
metadata:
  version: "1.0.0"
---

# HW Emulation (EDF flow) — Copy the QEMU prebuilt files

Uses: `QEMU_PREBUILT`.

Stage the QEMU prebuilt into `base_camp_images/`. Run from `COMPILED_OUT_DIRECTORY`.

**Tell the user first.** This stage replaces `<COMPILED_OUT_DIRECTORY>/base_camp_images/`. If it
already exists, say so before running the command: "`base_camp_images/` already exists in
`<COMPILED_OUT_DIRECTORY>` and will be deleted and replaced; anything staged there earlier,
including a boot image or rootfs changed by a previous run, is lost." The prebuilt is large
(about 8 GB), so the copy takes about a minute. Then continue:

```bash
cd <COMPILED_OUT_DIRECTORY>
rm -rf base_camp_images; mkdir -p base_camp_images
cp -prf <QEMU_PREBUILT>/. base_camp_images/.
```

This stages the board boot image, the rootfs `.wic`, `combined.qemuboot.conf`,
`qemu-ospi.bin`, etc. into `base_camp_images/`. The prebuilt names most images twice: a
**dated file** (for example `BOOT-versal-<board>-multidomain-<timestamp>.bin`) and an
**undated soft link** to it. The copy keeps both (`cp -p` keeps the links).
