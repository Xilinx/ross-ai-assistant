<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-10: Missing property on synchronizer

## Metadata

- **Check ID**: TIMING-10
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2013.3

## Description

Detects a recognized logic synchronizer (double-register flip-flop chain) between two clock domains where one or both of the synchronizer registers are missing the `ASYNC_REG` property. Without `ASYNC_REG`, the tool does not protect the synchronizer registers from optimization and does not place them for best metastability resolution.

## Message

```
One or more logic synchronizer has been detected between 2 clock domains but the synchronizer
does not have the property ASYNC_REG defined on one or both registers. It is recommended to run
report_cdc for a complete and detailed CDC coverage
```

## Explanation

This violation occurs when:
1. A 2-FF (or deeper) synchronizer chain is detected on a clock domain crossing
2. One or both of the synchronizer registers do not have `ASYNC_REG` set to `TRUE`
3. The synchronizer is therefore not fully protected for metastability hardening

Without `ASYNC_REG` on the synchronizer registers:
- ❌ The registers may be optimized, replicated, or moved, breaking the synchronizer
- ❌ Placement does not prioritize keeping the FFs close for best MTBF
- ❌ Metastability resolution time is reduced
- ❌ CDC coverage is incomplete

### Common Root Causes

1. **Hand-coded synchronizers**: 2-FF chains written in RTL without the `ASYNC_REG` attribute
2. **Missing attribute propagation**: `ASYNC_REG` set on only one of the two registers
3. **Third-party IP**: Synchronizers inside IP without the property applied
4. **Non-XPM synchronizers**: Custom CDC structures instead of `XPM_CDC` primitives

## Resolution: 

> ⚠️ **This check is NOT resolved within the `vivado-timing-methodology-checks` skill.**
>
> TIMING-10 requires detailed CDC analysis that goes beyond constraint checking. User should run `report_cdc` and conduct analysis

## Generate Data

No data collection is required. The `report-cdc` skill generates all necessary CDC data independently.

## Flow

1. **Document** → Record that TIMING-10 is present and note the affected clock domains from the violation message
2. **Do NOT attempt to fix** within this skill
3. **Update final report** → Mark TIMING-10 as "User to fix by hand"
4. **Notify user** → Instruct the user to fix by hand

## Verification

None

## References

- [UG906](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug906-vivado-design-analysis.pdf) — Design Analysis and Closure Techniques (CDC Analysis chapter)
- [UG903](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug903-vivado-using-constraints.pdf) — Using Constraints (`ASYNC_REG` property)
- [PG768](https://www.xilinx.com/content/dam/xilinx/support/documents/ip_documentation/xpm_cdc/v1_0/pg768-xpm-cdc-constraints.pdf) — XPM_CDC Constraints Guide
- UG949 — UltraFast Design Methodology (CDC Best Practices)
