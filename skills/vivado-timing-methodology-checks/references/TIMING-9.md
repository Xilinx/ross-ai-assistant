<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-9: Unknown CDC Logic

## Metadata

- **Check ID**: TIMING-9
- **Severity**: Warning
- **Hierarchy Name**: Timing.CDC
- **First Release**: 2014.1

## Description

Detects clock domain crossings (CDC) that are constrained with `set_false_path`, `set_clock_groups`, or `set_max_delay -datapath_only` but have no recognized CDC synchronizer structure (e.g., double-register flip-flop chain, XPM_CDC primitive) on the capture clock side.

## Message

```
One or more asynchronous Clock Domain Crossing has been detected between 2 clock domains through
a set_false_path or a set_clock_groups or set_max_delay -datapath_only constraint but no
double-registers logic synchronizer has been found on the side of the capture clock. It is
recommended to run report_cdc for a complete and detailed CDC coverage. Please consider using
XPM_CDC to avoid Critical severities.
```

## Explanation

This violation occurs when:
1. A CDC exception constraint (`set_false_path`, `set_clock_groups -asynchronous`, or `set_max_delay -datapath_only`) exists between two clock domains
2. Vivado cannot detect a recognized synchronizer circuit on the receiving (capture) clock domain
3. The crossing is therefore labeled "unknown" — it may or may not be properly synchronized

Without a recognized CDC structure:
- ❌ Metastability risk is unchecked — the design may fail in hardware
- ❌ Vivado cannot calculate MTBF (Mean Time Between Failures)
- ❌ Signal integrity of the crossing is unverified
- ❌ Timing analysis cannot validate the capture path

### Common Root Causes

1. **Custom synchronizer circuits**: Non-standard 2-FF chains that Vivado doesn't recognize
2. **Missing synchronizers**: CDC paths without any synchronization structure
3. **Single flip-flop synchronizers**: Only one FF on the capture side (not sufficient)
4. **Gray-coded busses without XPM_CDC**: Multi-bit crossings without proper handshake
5. **Clock domain crossings in IP**: Third-party IP with non-standard CDC patterns

## Resolution:

> ⚠️ **This check is NOT resolved within the `vivado-timing-methodology-checks` skill.**
>
> TIMING-9 requires detailed CDC analysis that goes beyond constraint checking. User should run `report_cdc` and conduct analysis

## Generate Data

No data collection is required. The `report-cdc` skill generates all necessary CDC data independently.

## Flow

1. **Document** → Record that TIMING-9 is present and note the affected clock domains from the violation message
2. **Do NOT attempt to fix** within this skill
3. **Update final report** → Mark TIMING-10 as "User to fix by hand"
4. **Notify user** → Instruct the user to fix by hand



## Verification

None

## References

- [UG906](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug906-vivado-design-analysis.pdf) — Design Analysis and Closure Techniques (CDC Analysis chapter)
- [UG903](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug903-vivado-using-constraints.pdf) — Using Constraints
- [PG768](https://www.xilinx.com/content/dam/xilinx/support/documents/ip_documentation/xpm_cdc/v1_0/pg768-xpm-cdc-constraints.pdf) — XPM_CDC Constraints Guide
- UG949 — UltraFast Design Methodology (CDC Best Practices)
