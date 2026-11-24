<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# CPM5 PCIe Link Status: Quick Start Prompts

Download the VPK120 PDI and LTX listed in the [prerequisites](README.md#prerequisites).
Replace `<path>` with the absolute path to their directory on the ChipScoPy host and
`<host>` with your board's `hw_server` host.

## Step 1: Program the board and run the CPM5 gate

```
Use /hw-pcie-link-debug. Connect to the hw_server at TCP:<host>:3121, program
<path>/cpm_pcie_pio_wrapper.pdi with <path>/cpm_pcie_pio_wrapper.ltx, then
confirm the device has a live CPM5 PCIe controller.
```

## Step 2: Read the link state

```
Check the PCIe link on each CPM5 controller: link state, PHY ready, DPLL lock
and interrupts. If the design has a PCIe debug core, show the LTSSM state
history too. Report exactly what the tools return.
```

## Step 3: Report

```
Write the usual PCIe link debug report files.
```
