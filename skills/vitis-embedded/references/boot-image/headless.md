<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Running `vitis-embedded` headless (no Vitis IDE)

**This skill is already headless.** It is a **shell skill** — it authors a BIF and runs the
`bootgen` binary. It never needs the `runVitisPython` tool, a live `client`, a Vitis workspace,
or a `vitis -s` Python wrapper. It runs in **any terminal** with the Vitis environment on `PATH`
(or `bootgen` invoked by full path). There is no in-console vs headless difference for the recipe
itself — the recipe in `SKILL.md` *is* the headless recipe.

## Invocation

```bash
# bootgen is on PATH after sourcing the Vitis env, or call it by full path:
#   <install>/bin/bootgen ...
bootgen -arch <arch> -image design.bif -o BOOT.BIN -w on
```

- `-arch` is the silicon family: `zynq` | `zynqmp` | `versal` | `versalnet` | `versal_2ve_2vm` | `spartanup`.
- `-w on` overwrites an existing output.
- Output default: **`BOOT.BIN`** for Zynq/ZynqMP; a **`.pdi`** for the Versal family.

## Verify the image structure before delivery

`bootgen` succeeding (rc 0) is necessary but not sufficient — dump the layout to confirm the
partitions are what you intended:

```bash
bootgen -arch <arch> -image design.bif -o /tmp/verify.bin -w on   # rc 0 + expected partition list
```

## Versal naming — a PDI regardless of extension

`bootgen -arch versal` always emits a **PDI**. Name it for the boot medium:
- **SD / eMMC boot → name it `BOOT.BIN`** (the BootROM searches the FAT partition for `boot.bin`).
- **QSPI / OSPI / JTAG → a `.pdi`** is fine — it is passed to `program_flash` / `device_program`
  by path, so the name is arbitrary.

## What this skill consumes / does NOT do

`bootgen` only consumes **on-disk paths** produced by the producer skills (FSBL/PLM, app `.elf`,
bitstream/PDI) — it does not build them. *Segmented Configuration* (`boot.pdi` + `pl.pdi`) and
*DFX partial* PDIs come from the **Vivado / v++ hardware flow**, not bootgen; feed bootgen the PDI
that flow already produced.

For **which** skill does what and how they connect (who produces the ELFs / bitstream / PDI this
skill packages), see [`chaining.md`](../chaining.md). This skill's own `SKILL.md` holds the BIF rules
and the full `bootgen` recipe.
