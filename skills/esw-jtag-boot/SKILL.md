---
name: esw-jtag-boot
description: Load and run an AMD Embedded Development Framework (EDF) boot image on a Versal, Versal Net (versal-net), Versal Gen 2 (versal-2ve-2vm), Zynq UltraScale+ MPSoC (zynqmp) or Zynq-7000 (zynq) board over JTAG through a local or remote hw_server, without writing flash. On AMD evaluation boards the JTAG connection is the USB cable to the board's JTAG/UART port. Use when the user asks to boot, run, test or load a build on a board over JTAG, load boot.bin or a boot PDI with XSDB, prepare a hardware boot command, or diagnose device programming failures. Use also when reviewing JTAG helper logs or deciding whether IMAGE_DOWNLOADED or IMAGES_LOADED proves that U-Boot or Linux booted. Also discovers companion Zynq/ZynqMP ELF files from a supplied boot.bin path. Reports the exact commands used. To write the image to flash use $esw-program-flash; for missing boot artifacts use $esw-yocto-build; for emulation use $esw-runqemu.
license: MIT
allowed-tools: Read, Bash, Write
metadata:
  version: "1.0.0"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# EDF JTAG image loading

Load a board-matched boot image over JTAG with XSDB and hw_server. Completion means the
family helper reported success (`IMAGE_DOWNLOADED` for Versal, `IMAGES_LOADED` for
Zynq/ZynqMP); it does not prove U-Boot or Linux came up. The board can be attached to a
remote hw_server; it need not be connected to the agent host.

Assume the user may not know these tools. Find the tools, server, board and files yourself,
explain choices in plain words, and ask only for what cannot be determined.

Where this fits: build the image (`$esw-yocto-build`), try it on the board over JTAG (this
skill: fast, and the board's flash is left untouched), then write it to flash with
`$esw-program-flash` when the user wants the board to boot on its own.

For evidence-only requests, review the supplied logs without running the hardware steps
below or connecting to the board. A download or execution-start marker supports only
image loading and execution start. Claim a U-Boot prompt or Linux boot only when captured
console output establishes that milestone; otherwise report it as unverified.

## Instructions

1. **Check the setup.** Use a separate hardware shell: source Vitis/Vivado settings only
   there, never mixed with the EDF setup, gen-machine-conf or BitBake environment. Existing
   images do not require sourcing Yocto or starting a build.
   - **XSDB:** use the install the user names. Otherwise look for `xsdb` on `PATH`, under
     `$XILINX_VITIS/bin`, then in standard install locations such as
     `/tools/Xilinx/<version>/Vitis`. Ask only if none is found. Record its path and release.
   - **hw_server:** use the server the user names and normalize `host:port` to
     `tcp:host:port`. If none is named, the board is on this machine: use `local`; XSDB
     then starts a local hw_server itself when none is running.
   - **Cable:** loading works only over JTAG through hw_server. On AMD evaluation boards this
     is a normal USB cable from the PC to the board's JTAG/UART port; custom boards use a JTAG
     pod such as Platform Cable USB II or SmartLynq. If no cable or device is found, see
     [troubleshooting](references/troubleshooting.md).

2. **Resolve the board and files.** Establish the board, family (`versal`,
   `versal-net`, `versal-2ve-2vm`, `zynqmp` or `zynq`) and silicon revision when relevant. Take the image
   path from the user or the board-machine deploy directory of an EDF build. Find the files
   the family needs (see [Family details](#family-details)) from file names and known
   package conventions. Resolve aliases and moving `latest` paths once and use the canonical
   paths everywhere. Check each file is readable, nonempty and has the expected format and
   architecture; reuse earlier evidence for unchanged files. Do not routinely read BIF files,
   manifests, ESW source or partition tables; consult them only to resolve ambiguous
   candidates or a concrete compatibility problem. Missing metadata alone does not block
   loading.
   - **Unsupported application processors:** classic PL MicroBlaze and MicroBlaze V
     (RISC-V), including SCU200 designs, are not supported by this skill. If the supplied
     hardware or artifacts identify either processor, report that limitation and stop
     before programming, resetting, downloading or starting execution. Read-only target
     discovery is allowed when needed to identify the hardware. A `.pdi` file alone does
     not identify a Versal platform; do not route a MicroBlaze design to the Versal helper.
     This restriction does not apply to the PMU firmware stage of the supported ZynqMP flow.

3. **Choose one target.** Connect to the server and enumerate target properties. Use a
   filter or cable identity that matches exactly one intended device. When several boards
   are present and the request does not identify one, list them to the user by product and
   serial from the cable name and ask. Never select numeric target 1 by default or reset all
   devices.

4. **Load with the family helper.** Run the helper for the family from
   [Family details](#family-details), resolving `<skill-dir>` to this skill's installed
   directory, and capture XSDB output to a run-specific log. The Versal and Zynq helpers check that the
   board's active boot mode is JTAG before their single system reset and again after it,
   and stop on a failed read or another mode. The helpers never change boot-mode registers;
   do not change them yourself to get past the check. The scripts check argument syntax only;
   file checks belong to step 2. Helpers print the Tcl commands as they run them.

5. **Report.** Lead with a plain result, for example "Your image was loaded on the VEK280
   over JTAG; the board is now starting from it." Then tell the user how to see it boot: the
   same USB cable usually provides the serial console as one of its new serial ports
   (`/dev/ttyUSB*` on Linux, COM ports on Windows), at 115200 baud, showing U-Boot and then Linux. Always show the actual shell invocation in a
   copyable code block, with resolved XSDB, script and firmware paths, server and target
   filter or cable identity, correctly quoted, plus the `device program` or `dow`/`con` Tcl
   commands and the log path. Report `IMAGE_DOWNLOADED` or `IMAGES_LOADED` only when the
   helper printed it. Do not claim U-Boot reached its prompt based on a successful download.

6. **Handle failures from the log.** Report the failed command and XSDB/PLM error, then use
   [troubleshooting](references/troubleshooting.md) to explain the likely cause and fix in
   plain words. Diagnose before retrying; do not loop resets or power cycles. Keep the exact
   attempted command and log available.

## Family details

For discovering component files from a build directory, see
[firmware discovery](references/firmware-discovery.md).

### Versal, Versal Net and Versal Gen 2 (`versal`, `versal-net`, `versal-2ve-2vm`)

- **Files:** one complete board-matched PDI/`boot.bin`. Its DTB placement is packaged, so do
  not load a separate DTB. Do not load R5 applications separately; if the PDI contains R5
  partitions, `device program` starts them, so use an APU-only image when R5 firmware must
  be excluded. A hardware-only init PDI is not enough.
- **Command:**
  ```bash
  xsdb <skill-dir>/scripts/versal_jtag_boot.tcl <versal|versal-net|versal-2ve-2vm> <image> <server> '<target-filter>'
  ```
- **What it does:** connects, selects one device, checks boot mode, resets, reselects,
  checks boot mode again, runs `device program`, and disconnects.
- Details: [Versal reference](references/versal.md).

### ZynqMP (`zynqmp`)

- **Files:** PMU firmware, A53 FSBL, U-Boot and TF-A (`bl31.elf`), plus the separate Linux
  DTB when available: `devicetree/cortexa53-linux.dtb` if present, otherwise a unique
  appropriate DTB in the directory. A ZynqMP `boot.bin` is a discovery input for these
  files, not something the helper loads. R5 applications and PL are outside this APU load,
  even in a multidomain package.
- **Command:**
  ```bash
  xsdb <skill-dir>/scripts/zynqmp_jtag_boot.tcl <server> '<cable-context>' <pmufw.elf> <fsbl.elf> <u-boot.elf> <bl31.elf> [--dtb <dtb> [<address>]]
  ```
  The DTB loads at `0x100000` unless the user gives another address.
- **What it does:** polls for target discovery, checks boot mode and enables the PMU debug
  gate before and after its one system reset, starts PMU firmware, runs FSBL for four
  seconds, stops A53 #0, loads the DTB, U-Boot and TF-A, then resumes TF-A. The four-second
  delay assumes a compatible FSBL; do not derive an FSBL checkpoint.
- Details: [ZynqMP reference](references/zynqmp.md).

### Zynq-7000 (`zynq`)

- **Files:** ARM32 FSBL (with its symbol table) and U-Boot, plus an optional separate DTB.
  A Zynq `boot.bin` is a discovery input for these files.
- **Command:**
  ```bash
  xsdb <skill-dir>/scripts/zynq_jtag_boot.tcl <server> '<cable-context>' <fsbl.elf> <u-boot.elf> [<dtb> [<dtb-address>]]
  ```
  The DTB loads at `0x100000` unless the user gives another address. This is a flow
  convention; it does not prove compatibility with every custom memory layout.
- **What it does:** runs FSBL to a hardware breakpoint on its `FsblHandoffJtagExit` symbol
  (60-second timeout), loads the DTB when supplied, then downloads and resumes U-Boot. No PMU
  firmware or TF-A.
- Details: [Zynq-7000 reference](references/zynq.md).

Square brackets in commands mark optional arguments; they are not literal shell syntax.

## Guardrails

- Keep hardware-tool settings separate from the Yocto build environment.
- Do not change build configuration, deploy artifacts or files beneath `tmp/work` to
  make a load succeed. Use persistent inputs through the appropriate EDF workflow.
- Do not program flash, save firmware environment, issue Linux boot commands, change
  boot-mode registers, bypass device security, or kill shared debug servers as part of
  loading the image.

## Handoff

Report the board/family, machine and build identity when known, canonical image path,
server/device selection, exact executed commands, programming result and XSDB log.
Mark missing metadata as unknown.

| Need | EDF handoff |
|---|---|
| Write the image to flash so the board boots on its own | `$esw-program-flash` |
| Generate SDT from an XSA | `$esw-xsa-to-sdt`, in its own tool shell |
| Prepare a Yocto checkout/build environment | `$esw-yocto-source-setup` |
| Generate or change board-machine configuration | `$esw-gen-machine-conf`, in the Yocto shell |
| Produce missing or corrected boot artifacts | `$esw-yocto-build`, with the exact board machine and release target list |
| Boot in emulation | `$esw-runqemu` |

Perform additional stages only when covered by the user's request. Do not start a build
merely to validate a hardware loading command.
