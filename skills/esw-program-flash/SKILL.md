---
name: esw-program-flash
description: Write (flash) a boot image to the QSPI or OSPI flash on an AMD Zynq-7000, ZynqMP, Versal or Versal Gen 2 board over its JTAG connection, using the Vitis program_flash tool through a local or remote hw_server. On AMD evaluation boards the JTAG connection is the USB cable to the board's JTAG/UART port. Use when the user asks to flash, program, write or copy an image (.bin) to the board's flash, or to make the board boot from its own flash. Use also when reviewing program_flash logs or deciding whether Flash Operation Successful and verification prove that Linux booted from flash. To run an image once over JTAG without writing flash, use $esw-jtag-boot.
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

# EDF flash programming with program_flash

Write a flash image to the board's QSPI or OSPI flash with Vitis `program_flash`. The tool
initializes the board over JTAG, loads a small flashing U-Boot shipped with Vitis, then
erases, writes and optionally verifies the flash. Completion means `program_flash` reported
`Flash Operation Successful`; it does not mean the board has booted from flash.

Assume the user may not know these tools. Find the tools, server, board and files yourself,
explain choices in plain words, and ask only for what cannot be determined.

Where this fits: build the image (`$esw-yocto-build`), optionally try it once over JTAG
(`$esw-jtag-boot`, fast and leaves flash untouched), write it to flash (this skill), then
boot from flash and check the serial console (step 9).

For evidence-only requests, review the supplied logs without running the hardware steps
below or connecting to the board. Programming success and verification establish the
flash operation, not a subsequent boot. Claim Linux booted from flash only when captured
console output and boot-source evidence establish it; otherwise report it as unverified.

## Instructions

1. **Check the setup.**
   - **Vitis:** use the install the user names. Otherwise look for `program_flash` and `xsdb`
     on `PATH`, under `$XILINX_VITIS/bin`, then in standard install locations such as
     `/tools/Xilinx/<version>/Vitis`. Ask only if none is found. Resolve moving paths such as
     `..._latest/...` once with `readlink -e` and use the canonical path everywhere.
   - **hw_server:** use the server the user names and normalize `host:port` to
     `tcp:host:port`. If none is named, the board is on this machine: use `tcp:localhost:3121`
     and, if nothing is listening on port 3121, start `<vitis>/bin/hw_server -d`.
   - **Cable:** `program_flash` only works over JTAG through hw_server. On AMD evaluation boards
     this is a normal USB cable from the PC to the board's JTAG/UART port; custom boards use a
     JTAG pod such as Platform Cable USB II or SmartLynq. If no cable is found later, see
     [troubleshooting](references/troubleshooting.md).

2. **Resolve the image.** Use the file the user gives. If they give a build deploy directory,
   pick the flash image: EDF typically names it `edf-<qspi|ospi>-<family>-<board>-...bin`,
   which also tells you the flash kind, family and board. If the name does not make this
   clear, or several files match, ask. Check the file is
   readable and nonempty; record its size.

3. **Find the board setup file.** `program_flash` must initialize the board before it can
   reach the flash. This file is design specific: never substitute one from another board or
   build.
   - **Zynq-7000 / ZynqMP:** the FSBL ELF (`-fsbl`). Take the unique `fsbl*.elf` in the same
     directory as the image, from the same build. Check with `file -L`: ARM 32-bit for Zynq-7000,
     AArch64 for ZynqMP. It is required even though `program_flash -help` lists it as optional;
     without it the tool stops with `ERROR: Please specify a valid FSBL file for flash type: ...`.
     The flash image itself holds an FSBL, but `program_flash` needs the ELF file.
   - **Versal / Versal Gen 2:** an initialization PDI for the board design (`-pdi`). Use the one
     the user supplies or one from the matching build, such as a board init PDI next to the
     image. If none is identified, ask for it and explain it is the board setup file from the
     same hardware design.

4. **Find the board.** Run the read-only helper against the server:
   ```bash
   xsdb <skill-dir>/scripts/flash_targets.tcl tcp:<host>:<port>
   ```
   Use the `target_name` of the `arm_dap` device on the intended board's cable for
   `-target_name`. The helper also prints each cable's supported JTAG rates. Check the board
   matches the image (for example a VEK280 cable for a `...-vek280-...` image). If the server
   has more than one board and the request does not identify one, ask, listing boards by
   product and serial from the cable name (for example `<board>, serial <serial>`);
   never rely on the tool's default of the first device on the first cable.

5. **Choose the flash type.** Use the user's value, or the known board settings in
   [flash configuration](references/flash-config.md). Otherwise ask for the board's flash
   layout (QSPI/OSPI, single/dual, bus width), pointing the user to the board's user guide.
   `program_flash` detects the actual flash part, but it needs `-flash_type` to choose its
   flashing U-Boot. Add `-flash_density` for dual stacked/parallel layouts and
   `-erase_sector` for flashes whose erase sector is not 64 KB.

6. **Confirm in plain words before starting.** Say which board, which file and size, that the
   board's current flash contents will be erased, and that large images take tens of minutes to
   over an hour. Example: "This will erase the flash on the VEK280 (serial ...) and write
   `edf-ospi-...bin` (256 MB), then verify it. It can take about an hour. Continue?" Skip the
   question only when the user already asked for exactly this write.

7. **Run `program_flash`.** Use the Vitis install's `bin/program_flash`:
   ```bash
   program_flash -f <image> -offset 0 \
     -fsbl <fsbl.elf> | -pdi <init.pdi> \
     -flash_type <type> [-flash_density <MB>] [-erase_sector <bytes>] \
     -frequency <max supported hz> -verify \
     -target_name '<arm_dap target_name>' -url tcp:<host>:<port> \
     2>&1 | tee /tmp/<board>-program-flash-<date>.log
   ```
   (`-fsbl` or `-pdi` by family.) Omit `-blank_check`: the erase already leaves the flash
   blank, and the check adds about as much time as the write. Keep `-verify` unless the user
   asks to skip it. Use `-offset 0` for a complete flash image; use another offset only when
   the user specifies one. Set `-frequency` to the highest supported rate from step 4.
   Judge the result from the log: with `| tee` the shell exit status is that of `tee`, so
   success means the log ends with `Flash Operation Successful`.

   Run it in a session that stays alive for the whole job, such as a persistent terminal
   session that you poll. Do not start it with `nohup ... &` from a tool shell that exits
   right away: the process can be killed when the shell returns, leaving an empty log.
   Within the first minute, confirm the log shows `Connected to hw_server` and the process is
   still running. Poll the log, give progress updates from its percentage output, and do not
   interrupt or restart a running write.

8. **Report.** Lead with a plain result ("The flash on your VEK280 now holds `<image>` and was
   verified."), then the exact command, log path, the `Detected Flash` line, each step's
   `Elapsed time` and the final result. A `Flash Mismatched` warning is expected when the
   selected type is generic; say it is harmless unless the operation failed. Say that booting
   from flash was not tested unless it was.

9. **Explain how to boot from flash.** Tell the user the next step:
   - **Board at the user's desk:** power off, set the board's boot-mode switches to the flash
     that was written (QSPI or OSPI; positions are in the board's user guide), power on, and
     watch the serial console. The same USB cable usually provides the console as one of its
     new serial ports (`/dev/ttyUSB*` on Linux, COM ports on Windows), at 115200 baud. A good boot shows the boot loader messages,
     U-Boot, then a Linux login prompt.
   - **Remote board, or the user asks you to switch it:** for Versal, follow the boot-mode
     section in [flash configuration](references/flash-config.md), confirm the register
     readback, and tell the user how to return the board to JTAG mode.

10. **On failure,** report the tool's error line and the last completed step, then use
    [troubleshooting](references/troubleshooting.md) to explain the likely cause and fix in
    plain words. Diagnose before retrying. Do not loop retries. If a write stopped part way,
    tell the user the board will not boot from flash until a write completes.

## Guardrails

- The operation erases and rewrites flash. Program only the board the user named, and only
  after the target is uniquely identified.
- The board must be in JTAG boot mode for programming. Do not change boot-mode registers or
  switch the board to flash boot unless the user asks.
- Use only the image, setup file and settings resolved above. Do not modify build artifacts.
- Do not kill or restart shared hw_server instances. Starting a local one when none is
  running is fine.

## Scope

The Vitis 2026.2 install ships flashing U-Boot images for QSPI on Zynq-7000, ZynqMP, Versal,
Versal Net and Versal Gen 2, and for OSPI on the Versal families. NAND, NOR (Zynq-7000) and
eMMC are also listed by `program_flash -help`. UFS is not supported. Writing flash without
JTAG (USB boot/DFU modes, or from a running U-Boot or Linux) is outside this skill.
Hardware-tested so far: ZynqMP QSPI and Versal OSPI over the onboard USB-JTAG, including
Versal boot-mode switching to OSPI; see [flash configuration](references/flash-config.md).
