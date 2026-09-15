<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# NoC 4KB Boundary Crossing Error

A minimal design that reproduces a **single, deterministic AXI rule violation** on
the Versal programmable NoC — a write burst that crosses a 4 KB address boundary —
then debugs it with the `hw-noc-debug` skill.

The design source in `input/` is complete and self-contained (RTL + Tcl). The
debug step depends on the **`hw-noc-debug` skill**, which is a separate installed
dependency — it lives at [`skills/hw-noc-debug/`](../../../skills/hw-noc-debug/)
in this repo, not inside this example (per repo policy, examples reference
installed skills rather than bundling them).

## Goal

One PL AXI master issues a single **16-beat INCR write burst** (`AWLEN=15`,
`AWSIZE=2` → 4-byte beats, 64 bytes total) to a **valid, mapped** slave (the
BRAM), but starts it at `BRAM_base + 0xFC8`. The burst therefore spans
`0xFC8 … 0x1008` and **crosses the 4 KB boundary at `0x1000`**. The AXI protocol
forbids a burst from crossing a 4 KB boundary, so the NoC **Master Unit (NMU)**
flags an AXI rule violation and latches a protocol error
(`REG_ISR.axi_rules_wr`). The `hw-noc-debug` skill scans the NoC, decodes the
error, matches it to the specific AXI rule, and correlates it to the offending
master and its illegal burst attributes.

Every individual beat is legal (`AWSIZE=2` on a 32-bit port, mapped address) —
the sole fault is the burst geometry crossing the 4 KB boundary. This rounds out
the NoC error-class examples:
- `write-decode-error` — address **decode** miss at the **NMU** (`addr_map_wr`).
- `axsize-violation` — illegal transfer **size** at the **NSU** (`xlx_infos_wr`).
- `burst-4k-crossing` — illegal **burst geometry** at the **NMU** (`axi_rules_wr`).
- `write-timeout` — **outstanding transaction** timeout at the **NMU**
  (`timeout_wr`).

## Skills Used

- **hw-noc-debug** — connects to the board, programs the PDI, scans the NoC with
  `sysdbg_noc`, decodes and rule-matches the latched error, and correlates it to
  the design.

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
- `src/axi_4k_cross_master.v` — a purpose-built minimal AXI4 master. After
  `STARTUP_DELAY` clock cycles it issues exactly one `BURST_LEN+1`-beat INCR write
  burst to `TARGET_ADDR`, then idles. The read channel is tied off.
- `create_project.tcl` — builds the block design: Versal CIPS + AXI NoC
  (2 SI / 1 MI) + BRAM controller + the 4 KB-crossing master. The BRAM is mapped
  at `0x201_0000_0000` (8 KB window) in the master's address space, so the
  burst's start address is **valid** — the error is the boundary crossing, not a
  decode miss.

Key parameters (set in `create_project.tcl`):

| Parameter       | Value                   | Purpose                                                       |
| --------------- | ----------------------- | ------------------------------------------------------------ |
| `TARGET_ADDR`   | `0x0000_0201_0000_0FC8` | Mapped BRAM base + `0xFC8` → burst straddles the 4 KB line    |
| `BURST_LEN`     | `15` (16 beats)         | 16 × 4 B = 64 B → spans `0xFC8..0x1008`, crossing `0x1000`    |
| `STARTUP_DELAY` | `1000` cycles           | Wait after reset before issuing the burst                    |

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
3. Report exactly **one** finding: an `axi_rules_wr` (write AXI-rule) error at a
   **NoC Master Unit (NMU)**, decoded to the rule
   **`axi_rules_burst_cross_4k_boundary`** ("Burst cross 4K boundary").
4. Decode the error-log registers, which capture the offending transaction
   attributes — notably `axlen = 15` (16 beats), `axsize = 2` (4-byte beats),
   `axburst = 1` (INCR), and `axid = 0xBAD` — and correlate them to the
   `cross_master_0` instance as the root cause.

> The exact NMU index that reports the violation depends on place-and-route
> placement of the master, so it is not fixed across builds; the skill identifies
> whichever NMU latched the error.
