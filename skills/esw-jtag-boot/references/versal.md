<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Versal, Versal Net and Versal Gen 2 image loading

## Image and platform checks

Use `versal` for first-generation Versal, `versal-net` for Versal Net, and
`versal-2ve-2vm` for Versal Gen 2 (AI Edge Series Gen 2 and Prime Series Gen 2).
The helper uses device-level programming for all three. Family records the selected workflow; it does not
prove image compatibility. The image owns firmware handoff, so the helper does not reset
an A72/A78 core after programming.

File selection and checks follow `SKILL.md` step 2 and its Versal family section
(complete board-matched image, packaged DTB, R5 partitions).

Machine metadata such as `SDT_URI`, `PDI_PATH` and `DEFAULT_HW_BOOT_MODE`/`HW_BOOT_MODE`
documents build inputs, not the board's current switch setting. A hardware initialization
PDI referenced by `PDI_PATH` may not contain TF-A or U-Boot. Require a complete boot image.
Do not substitute QEMU artifacts or common-machine rootfs images for board boot firmware.

The board must already be configured for JTAG boot. The helper checks mode after target
selection and again after system reset/reselection, before `device program`. For all three
supported families, the installed AMD `xilplmi` register definitions (`versal`,
`versal_net` and `versal_2ve_2vm` `xplmi_hw.h`) give CRP base `0xF1260000`, `BOOT_MODE_USER` at
`0xF1260200` and `BOOT_MODE_POR` at `0xF1260204`. The platform firmware reads the active
boot mode as `BOOT_MODE_USER & 0xF` (`CRP_BOOT_MODE_USER_BOOT_MODE_MASK`), where 0 is JTAG.
The helper requires that active mode to be JTAG and prints POR, the switch setting, for
information. This accepts both the switch-selected JTAG mode and a software override to
JTAG (`0x0100`).

A failed read or unsuitable mode stops the flow and disconnects without programming.
The helper never writes these registers or changes board boot mode.

## Commands

Arguments are positional. Invoke XSDB after artifact validation:

```bash
xsdb <skill-dir>/scripts/versal_jtag_boot.tcl versal /deploy/boot.bin tcp:board-host:3121 'name =~ "Versal*"'
```

For Versal Net, pass `versal-net` as the first argument with its board-matched image
and observed target filter.

The broad filter is suitable only when it matches exactly one intended device. Enumerate
`targets -target-properties` first. Refine the filter with the observed device name and
`jtag_cable_ctx` when needed. `local` uses XSDB's default local connection.

The loading sequence is:

```tcl
connect -url tcp:board-host:3121
targets -set -filter {name == "Versal <device>" && jtag_cable_ctx == "<observed-cable-context>"}
rst -system
after 1000
targets -set -filter {name == "Versal <device>" && jtag_cable_ctx == "<observed-cable-context>"}
device program /deploy/boot.bin
disconnect
```

The helper checks that exactly one Versal device matches before each selection. It prints
the commands with Tcl-safe argument quoting, reports `IMAGE_DOWNLOADED` after successful
programming and disconnects its own session. Keep the XSDB log and display the exact shell
invocation and `device program` command in the response. A programming success does not establish U-Boot readiness.

## Validation status

The reset, reselect and `device program` sequence completed on VEK280
(`Versal xcve2802`) and on VEK385 (`Versal Gen 2 xc2ve3858`) through a remote hw_server,
using XSDB 2026.2 and EDF 2026.2 images. On Versal Gen 2 the CRP boot-mode registers
confirmed JTAG mode. Reset, reselection and `device program` returned `IMAGE_DOWNLOADED`.
Versal Net support uses the same flow and register definitions, confirmed against
Vitis 2026.2 `xilplmi_v2_5/src/versal_net/server/xplmi_hw.h`. Its successful load
sequence and failure handling have been checked with mocked XSDB commands; it has
not been validated on Versal Net hardware.

These runs validate image programming only; U-Boot prompt arrival and Linux boot were
not checked. They predate the current helper revision, which has passed offline mocked
tests only.

## Failure interpretation

- No or multiple matches: resolve cable/device identity before resetting anything.
- Reset or programming failure: preserve XSDB/PLM errors and inspect the image, board,
  silicon revision and boot mode before a further attempt.
- Missing image: route requested builds through `esw-yocto-build`, using the checkout's
  `conf/conf-notes.txt` for the exact machine and supported target.
- Boot mode rejected (active mode nonzero, for example `BOOT_MODE_USER` `0x8108` after a
  software switch to OSPI boot): the board is set to boot from flash. Ask the user to set
  the boot switches to JTAG and power-cycle, or, if they ask, switch it back with the JTAG
  override described in `$esw-program-flash`. Do not write boot-mode registers on your own
  to get past the check.
- Debug access denied: report the restriction; do not attempt to bypass it.
