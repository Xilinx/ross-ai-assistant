<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# IBERT GTY Loopback and Eye Scan

Check the GTY transceivers of a VCK190 with the `hw-ibert-gt-debug` skill:
find the IBERT core, make a loopback link, read its status and BER, and run a
2D eye scan.

There is nothing to build. The example uses the prebuilt ChipScoPy example
design for the VCK190. On Versal the IBERT function is part of the transceivers, so any
design with GTs can be checked this way. This one has a GTY IBERT core on four
quads. The skill lives at
[`skills/hw-ibert-gt-debug/`](../../../skills/hw-ibert-gt-debug/).

## Goal

1. **Discover**: list the IBERT cores, their transceiver type, and the GT
   quads in each.
2. **Link**: on one quad, create a TX to RX link on channel 0, set PRBS 31 on
   both sides and Near-End PMA loopback.
3. **Status**: read the line rate, PRBS lock and BER.
4. **Eye scan**: run a 2D eye scan on the link and report the eye opening.
5. **Report**: write the skill's report files and remove the link.

## Skills Used

- **hw-ibert-gt-debug**: discovers IBERT cores and GT quads
  (`get_hw_sio_iberts`, `get_hw_sio_gtgroups`), creates and configures links
  (`create_hw_sio_link`, `commit_hw_sio`), reads status (`refresh_hw_sio`),
  and runs eye scans (`create_hw_sio_scan`, `run_hw_sio_scan`). All through
  Vivado Hardware Manager Tcl via `vivado_execute`.

## Prerequisites

- **Vivado MCP server** with Vivado 2026.1 or later.
- **A VCK190** with its `hw_server` running at the same Vivado release (for
  example `TCP:<host>:3121`).
- Download `chipscopy_wrapper.pdi` and `chipscopy_wrapper.ltx` from the
  [public ChipScoPy VCK190 design](https://github.com/Xilinx/chipscopy/tree/923b8cc15cfced3f3f59c6cb62834590b7c5367c/chipscopy/examples/designs/vck190/production/chipscopy_ced).
  Save both files in one directory accessible to Vivado.
- The **`hw-ibert-gt-debug` skill** installed.

## How to Run

Follow [`prompt.md`](prompt.md). It has four steps: program and discover, make
a link and check it, run an eye scan, then report and clean up.

## Expected Behavior

The skill will:

1. Connect Hardware Manager to the `hw_server` and program the PDI with its LTX.
2. Find one IBERT core, `IBERT_0` ("IBERT Versal GTY"), with quads `Quad_105`,
   `Quad_201`, `Quad_204` and `Quad_205`.
3. Create a link on `Quad_205` CH0, set PRBS 31 and Near-End PMA, commit it,
   refresh, and report the line rate, PRBS lock and BER the hardware shows.
4. Run a 2D eye scan and report its status and open, horizontal and vertical
   percentages.
5. Write `REPORT.md` and `report_data.json` under
   `vivado_agentic_ai_reports/hw-ibert-gt-debug/`, then remove the link.

Line rate, lock, BER and eye opening depend on the board. Near-End PMA loopback
stays inside the transceiver, so it checks the GT itself, not the board or a
cable. To check a real channel, connect a partner and use loopback `None`
(`User Design`).
