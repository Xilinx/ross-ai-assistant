<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# NoC Write Timeout Error

A minimal design that reproduces a **single, deterministic AXI write timeout** on
the Versal programmable NoC, then debugs it with the `hw-noc-debug` skill.

The design source in `input/` is complete and self-contained (RTL + Tcl). The
debug step depends on the **`hw-noc-debug` skill**, which is a separate installed
dependency — it lives at [`skills/hw-noc-debug/`](../../../skills/hw-noc-debug/)
in this repo, not inside this example (per repo policy, examples reference
installed skills rather than bundling them).

## Goal

One PL AXI master writes a single beat to a **valid, mapped** slave, but that
slave — a purpose-built "stalling" slave — **never completes the transaction**
(it holds `AWREADY`, `WREADY`, and `BVALID` low forever). The write is accepted
into the NoC and forwarded, but no write response ever returns. Once NoC timeout
detection is enabled, the NoC **Master Unit (NMU)** that issued the request
exceeds its timeout window and latches a write-timeout error
(`REG_ISR.timeout_wr`). The `hw-noc-debug` skill scans the NoC, decodes the
error, and correlates it to the offending master and its outstanding transaction.

This completes the trio of NoC error classes exercised by these examples:
- `write-decode-error` — address **decode** miss at the **NMU** (`addr_map_wr`).
- `axsize-violation` — **protocol** violation at the **NSU** (`xlx_infos_wr`).
- `write-timeout` — **outstanding transaction** timeout at the **NMU**
  (`timeout_wr`).

Unlike the other two, a timeout is only observable **after** NoC timeout
detection is explicitly enabled at debug time (it is disabled by default at
boot). That makes this example a good demonstration of the
`sysdbg_noc_timeout` control flow.

## Skills Used

- **hw-noc-debug** — connects to the board, programs the PDI, enables NoC
  timeouts with `sysdbg_noc_timeout`, scans the NoC with `sysdbg_noc`, decodes
  the latched error, and correlates it to the design.

## Prerequisites

This tutorial is driven by **two MCP servers**:

- **Vivado MCP server** — builds the design from source (sources
  `create_project.tcl`, runs implementation, writes the PDI) and provides design
  correlation for the debug step.
- **ChipScoPy MCP server** — programs the board, enables NoC timeouts
  (`sysdbg_noc_timeout`), and runs the NoC analysis (`sysdbg_noc`). It expects
  **`hw_server` and `cs_server` to be already running on the hardware side** and
  reachable from where the MCP server runs (e.g. `TCP:<host>:3121` for
  `hw_server`, `TCP:<host>:3042` for `cs_server`).

Also required:

- The **`hw-noc-debug` skill** installed (invoked as `/hw-noc-debug`). In this
  repo it lives at [`skills/hw-noc-debug/`](../../../skills/hw-noc-debug/).
- Vivado 2026.1+ installed.
- A Versal board with `hw_server` + `cs_server` running
  (developed on VCK190, `xcvc1902-vsva2197-2MP-e-S`).

## Starting Point

Input files in `input/`:
- `src/axi_timeout_master.v` — a purpose-built minimal AXI4 master. After
  `STARTUP_DELAY` clock cycles it issues exactly one **legal** single-beat write
  (`AWSIZE=2`, 4 bytes) to `TARGET_ADDR`, then waits forever for a write response
  that never arrives. The read channel is tied off.
- `src/stalling_axi_slave.v` — a valid AXI4 slave that is deliberately
  unresponsive: it ties `AWREADY`, `WREADY`, `ARREADY`, `BVALID`, and `RVALID`
  low, so it never accepts or completes any transaction.
- `create_project.tcl` — builds the block design: Versal CIPS + AXI NoC
  (2 SI / 1 MI) + the stalling slave + the timeout master. The stalling slave is
  mapped at `0x201_8000_0000` in the master's address space, so the master's
  `TARGET_ADDR` is **valid** and the write is routed — the failure is that the
  slave never completes it.

Key parameters (set in `create_project.tcl`):

| Parameter       | Value                   | Purpose                                             |
| --------------- | ----------------------- | --------------------------------------------------- |
| `TARGET_ADDR`   | `0x0000_0201_8000_0000` | Mapped stalling-slave base → transaction is routed  |
| `STARTUP_DELAY` | `1000` cycles           | Wait after reset before issuing the write           |

## How to Run

**Follow the prompts in [`prompt.md`](prompt.md) to run this tutorial end to
end.** There are three ordered steps:

1. **Build** — `Step 1` sources `input/create_project.tcl` and implements to
   `write_device_image`, producing the PDI.
2. **Program & baseline scan** — `Step 2` programs the PDI and runs an initial
   NoC scan, which is expected to be clean (timeouts not yet enabled).
3. **Enable timeouts & re-scan** — `Step 3` enables NoC timeouts and re-scans to
   latch and root-cause the `timeout_wr` error.

Beyond the prerequisites above (notably the installed `hw-noc-debug` skill), no
extra setup is needed. The example ships as source only (RTL + Tcl); the Vivado
project, checkpoints, and PDI are build outputs and are git-ignored, so the
design is always rebuilt from source.

## Expected Behavior

The `hw-noc-debug` skill will:
1. Connect to `hw_server` / `cs_server` and program the PDI.
2. Scan the NoC via `sysdbg_noc analyze`. The **initial** scan reports
   **0 findings** — the write is outstanding, but timeout detection is disabled.
3. Enable NoC timeouts on the NMUs via `sysdbg_noc_timeout` (a short timebase
   index). If the control-register writes do not verify, apply the NPI unlock
   workaround (write `0xF9E8D7C6` to `<nmu_base> + 0xC`) and re-issue — see the
   note in [`prompt.md`](prompt.md).
4. Re-scan and report exactly **one** finding: a `timeout_wr` (write timeout)
   error at a **NoC Master Unit (NMU)**.
5. Decode the error-log registers, which capture the offending transaction —
   notably `axid = 0xBAD` and a legal `axsize = 2` (a normal 4-byte write that
   simply never completed) — and correlate them to the `timeout_master_0`
   instance as the root cause.

> The exact NMU index that reports the timeout depends on place-and-route
> placement of the master, so it is not fixed across builds; the skill identifies
> whichever NMU latched the error.
