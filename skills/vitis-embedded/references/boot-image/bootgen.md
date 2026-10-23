<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Bootgen Reference (boot-image creation)

Ground truth for running Bootgen to produce a boot image. BIF *content* lives in
[platform-rules.md](platform-rules.md) (valid values per platform) and
[canonical-bifs.md](canonical-bifs.md) (28 examples); this file covers the Bootgen
CLI and image validation.

## Bootgen CLI (per arch)

```bash
bootgen -arch <arch> -image <design.bif> -o <output> -w on -log error
```

| Arch | Output | Example |
|------|--------|---------|
| `zynqmp` | BOOT.BIN | `bootgen -arch zynqmp -image boot.bif -o BOOT.BIN -w on` |
| `zynq` | BOOT.BIN | `bootgen -arch zynq -image boot.bif -o BOOT.BIN -w on` |
| `versal` | .pdi | `bootgen -arch versal -image design.bif -o design.pdi -w on` |
| `versalnet` | .pdi | `bootgen -arch versalnet -image design.bif -o design.pdi -w on` (alias `psx`) |
| `versal_2ve_2vm` | .pdi | `bootgen -arch versal_2ve_2vm -image design.bif -o design.pdi -w on` (alias `telluride`) |
| `spartanup` | .pdi | `bootgen -arch spartanup -image design.bif -o design.pdi -w on` |

- `-w on` overwrites an existing output.
- `-log error` keeps output quiet (use `-log info`/`-log trace` to debug).
- **BIF comments must be `//` or `/* */`.** A `#` anywhere makes bootgen fail with
  `Invalid character (0x23)`. The `# src:.. | ug:..` citations in canonical-bifs.md
  are documentation only — strip them from the BIF before running (the driver does
  this automatically into a sanitized copy).
- **`syntax error` on a `type =`/attribute → version drift, do NOT brute-force.**
  If bootgen rejects a token with `syntax error` (e.g. `type = imagestore` is invalid
  in 2026.2 — `imagestore` is the attribute `imagestore = <id>`), the reference may
  predate this Bootgen. **Run `bootgen -bif_help <attribute>` to get the correct
  syntax for the installed version, fix the BIF, and report the drift.** Do not try
  alternative spellings repeatedly or grep the binary — check `-bif_help` once.
- Inspect a built image: `bootgen -arch <arch> -read <image>` (boot header /
  partition table) or `-dump`/`-dump_dir` to extract partitions.

## BIF formats (summary)

Full rules in [platform-rules.md](platform-rules.md), examples in [canonical-bifs.md](canonical-bifs.md). Quick reference:

### ZynqMP / Zynq — bracket format (`the_ROM_image:`)

```
the_ROM_image:
{
  [bootloader, destination_cpu=a53-0] fsbl.elf
  [destination_cpu=pmu] pmufw.elf          // ZynqMP only
  [destination_device=pl] design.bit       // optional bitstream
  [destination_cpu=a53-0] application.elf
}
```
Zynq: no `destination_cpu` (e.g. `[bootloader] fsbl.elf`, then `system.bit`, then `app.elf`).

### Versal-family — `new_bif:` format

```
new_bif:
{
  id_code = 0x14CA8093        // versal; versalnet 0x14D80093; 2ve_2vm 0x04DB0093
  extended_id_code = 0x01
  id = 0x2
  image { name = pmc_subsys, id = 0x1c000001
          { type = bootloader, file = plm.elf }
          { type = pmcdata, file = pmc_data.cdo } }
  ... lpd_subsys / fpd_subsys / apu_subsys ...
}
```
Versal PDIs usually need PMC data + subsystem CDOs — author per
[canonical-bifs.md](canonical-bifs.md) (Ex.1/3/5) rather than the driver's simple mode.

## BIF source per case

| Case | BIF source | This skill |
|------|-----------|------------|
| Non-secure ZynqMP/Zynq BOOT.BIN | Auto shortcut: driver simple-mode (`--bootloader/--pmufw/--app/--bitstream/--bl31/--u-boot`) | assemble BIF + run Bootgen + validate |
| Secure (RSA/ECDSA/AES/LMS/HSS), HSM | Authored: canonical-bifs.md Ex.2/10/13/15/24 -> `--bif <file>` | run Bootgen + validate |
| Versal/VersalNet/2VE-2VM/SpartanUP PDI | Authored: canonical-bifs.md Ex.1/3/5/7 -> `--bif <file>` | run Bootgen + validate |
| SSIT / imagestore / register-init | Authored: canonical-bifs.md Ex.14/17/18 -> `--bif <file>` | run Bootgen + validate |

[platform-rules.md](platform-rules.md) + [canonical-bifs.md](canonical-bifs.md)
encode the auth/key/core/subsystem rules (cross-checked against Bootgen source +
UG1283); the driver never invents BIF security content — it only runs Bootgen on
the BIF and validates the image.

## Where boot components come from

- **FSBL / PLM, PMUFW** — built by `vitis-embedded` (boot domain), or
  templates `zynqmp_fsbl`, `zynqmp_pmufw`, `versal_plm` via `vitis-embedded`.
- **Application ELFs** — built by `vitis-embedded`.
- **Bitstream / design PDI** — from Vivado / the platform XSA.
- **Linux boot artifacts** (`u-boot.elf`, `bl31.elf`, `Image`, `boot.scr`,
  `system.dtb`) — from a PetaLinux build or the AMD Common Image.

## Validation of the produced image

- Bootgen returns 0 and a non-trivial output file is written.
- Optional deeper check: `bootgen -arch <arch> -read <image>` lists the boot
  header / partitions; confirm the expected partitions are present.
