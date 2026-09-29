<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# IP Configurator -- Usage Guide

## What This Skill Does

Turns a plain-language description of what you want an IP to do into the
`set_property -dict` that actually does it, then proves the IP came out that way.

There is no parameter database behind it. Parameter names come from AMD documentation and
from Vivado's own rejection messages, so the skill works on IP it has never seen and on IP
whose parameters changed between releases.

The reason it verifies rather than just applies: **Vivado accepts writes that do nothing.**

- An unknown `CONFIG.*` key in a block design does not throw. It raises a non-fatal
  `CRITICAL WARNING [BD 41-1276]`, `catch` returns 0, and a script that trusts the exit
  status reports success on a parameter that was ignored.
- A gated attribute reads back cleanly while the feature stays off. Set `RESET_TYPE` without
  `USE_RESET` and the value sticks, the read-back agrees, and the reset pin never appears.

So every run re-reads the cell, checks that each key exists and stuck, checks that enabling
flags are on, and reports coverage per phrase of your prompt.

## Prerequisites

- **Vivado** with a project open and a **block design open** (the skill operates on
  `get_bd_cells`; there is a standalone `create_ip` fallback for parameters that only exist
  outside IPI).
- **Vivado MCP Server** — see [docs/reference/vivado-mcp-tools.md](../../docs/reference/vivado-mcp-tools.md).
  The skill uses exactly three tools: `vivado_doc_search`, `vivado_execute`, and
  `vivado_log_messages`.
- An agent that supports skills (Claude Code, Cursor, or the Copilot/VS Code integration).

Validated on **Vivado 2026.1**. Goldens are version-keyed because the IP catalog changes
between releases.

## Installation

```bash
npx skills add . --skill vivado-ip-configurator
```

## How to Use It

Just describe the IP you want. The skill triggers on ordinary engineering language — you do
not name it, and you do not describe its protocol.

```
Add an AXI GPIO to the block design with a 12-bit output-only channel,
all outputs, plus a second 4-bit input channel with interrupts enabled.
```

```
I need a MIPI CSI-2 RX subsystem over D-PHY for a 4-lane camera: 4 active
lanes, 4 pixels per clock, RAW12, all virtual channels, 4096 line-buffer
depth, active-lanes detection and user-data-type filtering on, ISP bridge
and register interface on, 1500 Mbps line rate.
```

```
Configure the clocking wizard for a 100 MHz input and three outputs at
200, 150 and 74.25 MHz, with an active-low synchronous reset and a
locked output.
```

### Operating modes

Say which one you want at the start of the session; otherwise it assumes `unattended`.

| Mode | Use when | Behaviour |
|---|---|---|
| `unattended` (default) | Scripted, CI, or batch runs | Never asks. Makes the documented best choice and records the assumption in its ledger. Minimizes MCP calls. |
| `interactive` | You are working turn by turn | May ask clarifying questions when doc search is inconclusive, and will use Block Automation and Configurable Example Designs to flesh out the design. |

The default is deliberate: a question nobody answers stalls a batch run until it times out,
whereas a recorded assumption is always recoverable.

### What you get back

Every run reports coverage. When something could not be applied, the skill quotes the exact
phrase from your prompt and names the reason rather than quietly grading itself down:

```
I could not fully parameterize axi_noc2 from the prompt.
Applied: NUM_SI=0, NUM_MI=0, NUM_NSI=2, NUM_CLKS=0.
Could NOT apply: "4 memory controllers (NUM_MC)" because integration-derived.
```

Reasons are drawn from a fixed set: `runtime-register-only`, `integration-derived`,
`gated-no-enabler`, `not-a-config-param`, `value-out-of-range`, `wrong-ip-for-capability`.
The overall result is graded `full`, `full_standalone`, `partial`, `negative`, or
`creation_only`.

### Part swaps are guarded

Many IPs exist only on certain device families. Swapping the part on a design that already
has cells is destructive, so `ipcfg::ensure_part` refuses and returns `WARN:SWAP_BLOCKED`.
In `interactive` mode it asks you first; in `unattended` mode it will only swap on an
otherwise-empty block design, and restores the original part afterwards.

### The helper library

`lib/ipcfg.tcl` holds the repetitive Tcl — availability gating, create, dict-plus-verify,
enabler checks, stub connections, cleanup. Source it once per session, after a block design
is open. The skill does this itself; you only need to know it exists if you are reading the
transcripts.

`cache/learned_params.json` ships empty. It accumulates *where a feature lives*
(`feature → CONFIG.PARAM`) as the skill earns that mapping at runtime, which stops it
re-deriving the same lookup every run. It never stores a parameter *value*, so it cannot
leak an expected answer. Delete it to reset; see [cache/README.md](cache/README.md).

## Limitations

- **Some parameters are unreachable by design.** The Versal NoC's memory-controller identity
  (`NUM_MC`, `NUM_MCP`, the `DDRMC5_CONFIG` sub-dictionary) only becomes valid once the NoC
  is instantiated as an integrated memory controller through device or board automation. On
  a bare PL NoC cell those keys stay gated, and the skill reports them
  `unapplied:integration-derived` instead of pretending otherwise.
- **Widths that are read-only on the boundary port are connection-derived.** The skill can
  drive a stub port so the cell adopts a connected width, but where that fails it grades
  `partial` and records the value under `runtime_params`.
- **Two Tier-2 retries, then it escalates.** It will not grind indefinitely on an error.
- **A block design must be open.** The standalone `create_ip` path exists only as a fallback
  for parameters unavailable in IPI.
