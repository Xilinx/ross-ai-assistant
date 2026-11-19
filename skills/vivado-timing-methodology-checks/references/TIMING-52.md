<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-52: No common phase between related clocks from Spread Spectrum MMCM

## Metadata

- **Check ID**: TIMING-52
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.2

## Description

Spread spectrum is enabled on an MMCM and two different outputs of that MMCM are timed together.

### Full Message Description property.

```
Spread Spectrum is enabled for MMCM <NETLIST_ELEMENT>. In this mode, it is unsafe to time between <CLOCK_GROUP> and <CLOCK_GROUP> due to the variations of the VCO frequency.
```
**Example:**
```
Spread Spectrum is enabled for MMCM clk_gen/mmcm_i. In this mode, it is unsafe to time between clkout0_ss and clkout1_ss due to the variations of the VCO frequency.
```

### Explanation
With spread spectrum the VCO frequency is modulated over time, so two outputs of the same MMCM do not keep a fixed phase relationship. The timer still treats the outputs as synchronous, so crossing paths are not safe.

**DO**
✅ Read `SS_EN` on the MMCM cell named in the message and confirm it is enabled (also `SS_MODE`, `SS_MOD_PERIOD`)
✅ List every path between the two reported clocks
✅ Ask whether spread spectrum is really required (EMI reduction)

**DO NOT**
❌ Disable spread spectrum without asking. It is a functional or EMI requirement
❌ Add a clock group without confirming synchronizers exist

## Flow
1. Identify the MMCM and the clocks.
```tcl
set mmcm [get_cells <NETLIST_ELEMENT>]
get_property SS_EN $mmcm
get_property SS_MODE $mmcm
get_property SS_MOD_PERIOD $mmcm
report_clock_interaction -clocks [get_clocks {<clk_a> <clk_b>}]
```
2. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Spread spectrum is not required
* **Prompt**: Recommend `set_property SS_EN FALSE [get_cells <NETLIST_ELEMENT>]` (or turn it off in the clocking wizard)

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: Spread spectrum is required
* **Prompt**: Offer, in this order:
a) Use a single MMCM output and divide outside the MMCM (BUFGCE_DIV) so only one MMCM clock exists in the crossing (RTL / clocking change)
b) Treat the crossing as asynchronous and make sure CDC circuitry exists:
```tcl
set_clock_groups -asynchronous -group [get_clocks <clk_a>] -group [get_clocks <clk_b>]
```
c) Waiver `<USER> - <DATE> - <REASON>`

## Verification
* [ ] Methodology violation is no longer present
* [ ] Only one MMCM clock is timed into the crossing, or the crossing is async with synchronizers
* [ ] No new unresolved methodology warnings are created

## Final Report
| Aspect | MMCM | SS_EN | Crossing |
|--------|------|-------|----------|
| **Before** | clk_gen/mmcm_i | TRUE | clkout0_ss -> clkout1_ss timed |
| **After** | clk_gen/mmcm_i | TRUE | async group + synchronizer |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-52
- Vivado Design Suite User Guide: Using Constraints (UG903)
