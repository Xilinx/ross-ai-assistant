<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Troubleshooting JTAG loading

Explain each problem to the user in plain words: what happened, the likely cause and the
fix. Diagnose before retrying, and never loop resets or power cycles.

| What you see | Likely cause | What to do |
|---|---|---|
| Cannot connect to hw_server (connection refused or timeout) | No hw_server is running at that address, or a firewall blocks port 3121 | For a board on this machine, start `<vitis>/bin/hw_server -d`. For a remote server, check host, port and that it is running there. |
| No cable or no device in `targets` | Board is off, USB cable is unplugged or in the wrong port, or the cable driver is missing | Check power and the USB cable to the board's JTAG/UART port. On Linux, the cable driver installer is `<install>/data/xicom/cable_drivers/lin64/install_script/install_drivers/install_drivers` and needs root; ask the user to run it, then replug the cable. |
| `Expected one device, found N` | The filter matches several boards, or none | List boards by product and serial from the cable name, ask which one, and refine the filter. |
| `Board is not in JTAG boot mode` | The board is set to boot from flash or SD | Ask the user to set the boot switches to JTAG and power-cycle, then rerun. |
| `device program` fails (Versal) | Image is not for this board or silicon revision, or is incomplete | Check the image comes from a build for this board, and that it is a complete boot image rather than a hardware-only PDI. |
| FSBL or download step fails (Zynq/ZynqMP) | ELF files from different builds, or not for this board | Use FSBL, U-Boot, PMU firmware and TF-A from the same build for this board. |
| Load succeeds but nothing appears on the console | Wrong serial port or baud rate, or the image does not start on this board | Try the board's other new serial ports (`/dev/ttyUSB*` on Linux, COM ports on Windows) at 115200 baud; then check the image matches the board. |
| Debug access denied | Device security blocks JTAG | Report the restriction; do not try to bypass it. |
