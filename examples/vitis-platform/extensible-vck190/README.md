<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Annotate a VCK190 block design as an extensible platform

This example starts from an open, validated Vivado block design. It does not include a block design. The skill sets the platform identity and PFM properties on interfaces that are already in the design. It does not change the design and does not export the XSA.

## Requirements

- An open Vivado project for a VCK190 acceleration platform, with a block design that passes `validate_bd_design`
- A Vivado Tcl connection to that same session

The skill looks for these interfaces in the open block design:

- Mandatory: a NoC or SmartConnect whose slave side traces back to a CIPS AXI master, for kernel control.
- Mandatory: a clock that drives the `slowest_sync_clk` pin of a `proc_sys_reset` block.
- Expected: a NoC with its own DDR memory controller.
- Expected: a free interrupt input. A processor `pl_ps_irq` pin or an interrupt-controller input is valid.
- Optional: a free AXI-Stream interface.

If a mandatory interface is absent, the agent stops, names it and asks how to resolve it. If an expected one is absent, it asks whether to continue without it. It does not add IP or change connections.

## Run the example

1. Open the block design in Vivado.
2. Start the agent and connect it to the same Vivado session.
3. Submit [`prompt.md`](prompt.md).

## Expected result

The agent sets `PFM_NAME` to `amd:vck190:example:1.0`, applies PFM properties to the interfaces listed above, saves the block design, and reports every `PFM.*` property read back from the design.
