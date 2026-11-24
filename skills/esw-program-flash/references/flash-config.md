<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Flash configuration and boot mode

## Known board settings

These settings have programmed and verified flash on hardware with Vitis 2026.2. Use them
when the user names one of these boards; otherwise ask for the board's flash layout.

| Board | Family | Flash | `-flash_type` | Extra options | Detected part | Setup file |
|---|---|---|---|---|---|---|
| ZCU104 | ZynqMP | QSPI 64 MB | `qspi-x4-single` | — | `mt25qu512a` | FSBL ELF from the same build |
| VEK280 | Versal | OSPI 256 MB | `ospi-x8-single` | `-erase_sector 131072` | `mt35xu02g` | Board init PDI from the same design |

Use these values in [the command in step 7](../SKILL.md#instructions).

## Flash types

`-flash_type` selects one of the flashing U-Boot images in the Vitis install,
`data/xicom/cfgmem/uboot/<family>_<flash>_<config>[_<density>].bin`. Families present in
2026.2: `zynq`, `zynqmp`, `versal`, `versal_net` and `versal2` (the Vitis file prefix for
Versal Gen 2, `versal-2ve-2vm`). Values listed by
`program_flash -help` for SoC devices:

- QSPI: `qspi-x1-single`, `qspi-x2-single`, `qspi-x4-single`, `qspi-x8-dual_parallel`,
  `qspi-x1-dual_stacked`, `qspi-x2-dual_stacked`, `qspi-x4-dual_stacked`
- OSPI (listed as Versal only): `ospi-x8-single`, `ospi-x8-dual_stacked`
- NAND: `nand-x8` / `nand-x16` (Zynq), `nand-x8-single` / `nand-x8-dual` (ZynqMP)
- NOR (Zynq), eMMC (ZynqMP, Versal)

## Switching boot mode on Versal without the switches (only when the user asks)

For a remote board, or when the user asks you to do it, a software boot-mode override plus a
system reset boots the board from flash without changing its switches. At a desk, setting the
board's boot-mode switches and power-cycling is simpler.

Register addresses and the boot-mode mask come from the Versal `xilplmi` sources
(`xplmi_hw.h`): the platform firmware reads the active boot mode as
`BOOT_MODE_USER & 0xF` (`CRP_BOOT_MODE_USER_BOOT_MODE_MASK`).

| Register | Address | Value |
|---|---|---|
| PMC_GLOBAL `PMC_MULTI_BOOT` | `0xF1110004` | `0x0` to boot the image at flash offset 0 |
| CRP `BOOT_MODE_USER` | `0xF1260200` | write `0x8100` OSPI, `0x1100` QSPI24, `0x2100` QSPI32, `0x0100` JTAG |
| CRP `RST_PS` | `0xF126031C` | `0x8` triggers the system reset |

```tcl
connect -url tcp:<host>:<port>
targets -set -nocase -filter {name =~ "Versal*" && jtag_cable_name =~ "*<cable serial>*"}
mwr 0xF1110004 0x0
mwr 0xF1260200 0x8100
catch {mwr 0xF126031C 0x8}   ;# resets the device, so the write itself reports an error
```

- Select the top-level `Versal <device>` target. The `PMC` target can fail with
  `Context does not support memory read`.
- The reset write is expected to return `AXI AP transaction error`.
- Reconnect and read `BOOT_MODE_USER`. Its low four bits are the active mode: `0x8` OSPI,
  `0x1` QSPI24, `0x2` QSPI32, `0x0` JTAG (observed `0x8108` after switching to OSPI).
  Running APU/RPU cores alone do not prove the flash image booted; check the serial console.
- To return the board to JTAG boot, run `mwr 0xF1260200 0x0100` and then
  `catch {mwr 0xF126031C 0x8}` on the same target; afterward the readback has low bits
  `0x0`. A power cycle also clears the override and returns the board to its switch setting.
