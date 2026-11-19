<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# NoC Illegal AxSIZE Protocol Error

A minimal design that reproduces a **single, deterministic AXI write protocol
error** on the Versal programmable NoC, then debugs it with the `hw-noc-debug`
skill.

The design source in `input/` is complete and self-contained (RTL + Tcl). The
debug step depends on the **`hw-noc-debug` skill**, which is a separate installed
dependency — it lives at [`skills/hw-noc-debug/`](../../../skills/hw-noc-debug/)
in this repo, not inside this example (per repo policy, examples reference
installed skills rather than bundling them).

## Goal

One PL AXI master writes a single beat to a **valid, mapped** slave address (the
BRAM) but drives an **illegal `AWSIZE`** — 16 bytes (`AWSIZE=4`) on a 32-bit
(4-byte) data port. A burst size larger than the interface data width is an
AXI/Xilinx rule violation, so the NoC **Slave Unit (NSU)** latches a protocol /
information error (`REG_ISR.xlx_infos_wr`). The `hw-noc-debug` skill scans the
NoC, decodes the error, and correlates it to the offending master and the illegal
transaction attributes.

This is a useful companion to the `write-decode-error` example: it exercises a
different error class (protocol violation at the **NSU**) versus an address
decode miss at the **NMU**. Because the address is valid, the transaction is
actually routed to a slave — the failure is purely the illegal size.

## Skills Used

- **hw-noc-debug** — connects to the board, programs the PDI, scans the NoC with
  `sysdbg_noc`, decodes the latched error, and correlates it to the design.

## Prerequisites

This tutorial is driven by **two MCP servers**:

- **Vivado MCP server** — builds the design from source (sources
  `create_project.tcl`, runs implementation, writes the PDI) and provides design
  correlation for the debug step.
- **ChipScoPy MCP server** — programs the board and runs the NoC analysis
  (`sysdbg_noc`). It expects **`hw_server` and `cs_server` to be already running
  on the hardware side** and reachable from where the MCP server runs
  (e.g. `TCP:<host>:3121` for `hw_server`, `TCP:<host>:3042` for `cs_server`).

Also required:

- The **`hw-noc-debug` skill** installed (invoked as `/hw-noc-debug`). In this
  repo it lives at [`skills/hw-noc-debug/`](../../../skills/hw-noc-debug/).
- Vivado 2026.1+ installed.
- A Versal board with `hw_server` + `cs_server` running
  (developed on VCK190, `xcvc1902-vsva2197-2MP-e-S`).

## Starting Point

Input files in `input/`:
- `src/axi_axsize_error_master.v` — a purpose-built minimal AXI4 master. After
  `STARTUP_DELAY` clock cycles it issues exactly one single-beat write to
  `TARGET_ADDR` with `AWSIZE = ILLEGAL_AWSIZE`, then idles. The read channel is
  tied off.
- `create_project.tcl` — builds the block design: Versal CIPS + AXI NoC
  (2 SI / 1 MI) + BRAM controller + the AxSIZE-error master. The BRAM is mapped
  at `0x201_0000_0000` in the master's address space, so the master's
  `TARGET_ADDR` is **valid** — the error is the illegal burst size, not a decode
  miss.

Key parameters (set in `create_project.tcl`):

| Parameter        | Value                  | Purpose                                        |
| ---------------- | ---------------------- | ---------------------------------------------- |
| `TARGET_ADDR`    | `0x0000_0201_0000_0000`| Mapped BRAM base → transaction is routed       |
| `ILLEGAL_AWSIZE` | `4` (16 bytes)         | Exceeds the 32-bit port width → protocol error |
| `STARTUP_DELAY`  | `1000` cycles          | Wait after reset before issuing the write      |

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md) to run this tutorial end to
end.** There are two ordered steps:

1. **Build** — `Step 1` sources `input/create_project.tcl` and implements to
   `write_device_image`, producing the PDI.
2. **Debug** — `Step 2` uses the `/hw-noc-debug` skill to program that PDI and
   root-cause the NoC error.

Beyond the prerequisites above (notably the installed `hw-noc-debug` skill), no
extra setup is needed. The example ships as source only (RTL + Tcl); the Vivado
project, checkpoints, and PDI are build outputs and are git-ignored, so the
design is always rebuilt from source.

## Expected Behavior

The `hw-noc-debug` skill will:
1. Connect to `hw_server` / `cs_server` and program the PDI.
2. Scan the NoC via `sysdbg_noc analyze`.
3. Report exactly **one** finding: an `xlx_infos_wr` (write protocol/information)
   error at a **NoC Slave Unit (NSU)**.
4. Decode the error-log registers, which capture the offending transaction
   attributes — notably `axsize = 4` (the illegal 16-byte size), `axid = 0xBAD`,
   and the upper target address `0x201` — and correlate them to the
   `axsize_master_0` instance as the root cause.
