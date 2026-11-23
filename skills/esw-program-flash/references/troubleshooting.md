<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Troubleshooting program_flash

Explain each problem to the user in plain words: what happened, the likely cause and the
fix. Diagnose before retrying, and never loop retries.

| What you see | Likely cause | What to do |
|---|---|---|
| Cannot connect to hw_server (connection refused or timeout) | No hw_server is running at that address, or a firewall blocks port 3121 | For a board on this machine, start `<vitis>/bin/hw_server -d`. For a remote server, check host, port and that it is running there. |
| Helper prints no `CABLE` lines | Board is off, USB cable is unplugged or in the wrong port, or the cable driver is missing | Check power and the USB cable to the board's JTAG/UART port. On Linux, the cable driver installer is `<install>/data/xicom/cable_drivers/lin64/install_script/install_drivers/install_drivers` and needs root; ask the user to run it, then replug the cable. |
| Cable listed but no `arm_dap` device | Board not powered, or the board's JTAG source setting points elsewhere | Check power and the board's JTAG source setting in its user guide, then power-cycle. |
| More than one board listed | Several boards share the server | Ask which one, by product and serial. |
| `ERROR: Please specify a valid FSBL file for flash type` | Zynq/ZynqMP run without `-fsbl` | Find the FSBL ELF from the same build (SKILL step 3). |
| `Flash Mismatched` warning | `-flash_type` is a generic type | Harmless; continue. It matters only if a later step fails. |
| Fails during initialization, before `Detected Flash` | Board not in JTAG boot mode, or the setup file (PDI/FSBL) does not match the board | Check boot mode (switches, or the Versal register readback) and that the setup file is from this board's design. |
| Erase, program or verify fails | Wrong flash type or erase sector, unstable cable, or power issue | Check `-flash_type` and `-erase_sector` against the board's flash, try a lower `-frequency`, then rerun once. The board will not boot from flash until a write completes. |
| Log stays empty and the process is gone | The job was started in a shell that exited | Rerun in a session that stays alive (SKILL step 7). |
| `Context does not support memory read` when switching boot mode | The `PMC` target was selected | Select the top-level `Versal <device>` target. |
| `AXI AP transaction error` on the reset write when switching boot mode | The device reset during the write | Expected; reconnect and check the register readback. |
