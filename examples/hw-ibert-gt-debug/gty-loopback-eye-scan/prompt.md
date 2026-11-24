<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# IBERT GTY Loopback and Eye Scan: Quick Start Prompts

Download the VCK190 PDI and LTX listed in the [prerequisites](README.md#prerequisites).
Replace `<path>` with the absolute path to their directory on the Vivado host and
`<host>` with your board's `hw_server` host.

## Step 1: Program the board and discover the IBERT cores

```
Use /hw-ibert-gt-debug. Connect Hardware Manager to the hw_server at
TCP:<host>:3121, program <path>/chipscopy_wrapper.pdi with
<path>/chipscopy_wrapper.ltx, and show me the IBERT status: the IBERT cores,
their transceiver type, and the GT quads in each.
```

## Step 2: Make a loopback link and check it

```
Run an IBERT loopback test on Quad_205 channel 0: create a link, set PRBS 31
on TX and RX and Near-End PMA loopback, then check the link status and BER.
```

## Step 3: Run an eye scan

```
Run a 2D eye scan on that link and report the eye opening.
```

## Step 4: Report and clean up

```
Write the usual IBERT report files, then remove the link.
```
