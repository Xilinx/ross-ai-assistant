<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-23: Combinational loop found

## Metadata

- **Check ID**: TIMING-23
- **Severity**: Warning

## Description

A combinational timing loop was detected. Vivado disabled a timing arc to break the loop, which means part of the path is no longer timed. The loop must be removed in RTL.

## Message

```
A timing loop has been detected on a combinational path. A timing arc has been disabled between <NETLIST_ELEMENT> and <NETLIST_ELEMENT> to disable the timing loop
```

## Flow

Trace the loop and fix the source. This cannot be safely automated.

* **Type**: Manual Resolution

1. Identify the loop: `report_timing -from <NETLIST_ELEMENT> -to <NETLIST_ELEMENT>` and inspect the disabled arc.
2. Fix the RTL so the combinational feedback is broken (e.g. register the path, or use a proper primitive such as a `LUT`-based latch only where intended).
3. Re-run `report_methodology` to confirm the loop is gone.

## References

- [UG906](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Message) - Design Analysis and Closure Techniques (TIMING-23)
- [UG949](https://docs.amd.com/r/en-US/ug949-vivado-design-methodology) - UltraFast Design Methodology
