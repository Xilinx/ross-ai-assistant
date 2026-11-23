<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-53: No common phase between related clocks from DPLL

## Metadata

- **Check ID**: TIMING-53
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.2

## Description

Versal only. A DPLL master clock and one of its auto-derived clocks are timed together, but the DPLL is not configured for deskew so there is no phase relationship.

### Full Message Description property.

```
The clocks <CLOCK_GROUP> and <CLOCK_GROUP> are timed together but have no phase relationship. The design could fail in hardware. To have a phase relationship the master clock must drive the CLKIN & CLKIN_DESKEW pins and the generated clock must use the phase detector. If those conditions are not met then it is unsafe to time between the master clock of the DPLL and its auto-derived clock (or downstream generated clocks) as the clocks relationship is unknown.
```
**Example:**
```
The clocks clk_in and clkout0_dpll are timed together but have no phase relationship. ...
```

### Explanation
A DPLL output is only phase-aligned to its input when the phase detector is used (`CLKOUTx_PHASE_CTRL = 2'b01`) and the input clock drives both `CLKIN` and `CLKIN_DESKEW`. In all other cases the master-to-derived crossing is asynchronous and the destination domain needs synchronizers.

**DO**
✅ Read `CLKOUTn_PHASE_CTRL` for the output involved and check what drives `CLKIN_DESKEW`
✅ Ask whether the crossing is intended to be synchronous

**DO NOT**
❌ Change DPLL deskew settings without asking. This changes clock latency and may need a fresh clocking-wizard run
❌ Add an asynchronous group without synchronizers in the design

## Flow
1. Inspect the DPLL.
```tcl
set dpll [get_cells <DPLL_cell>]
foreach p {CLKOUT0_PHASE_CTRL CLKOUT1_PHASE_CTRL CLKOUT2_PHASE_CTRL CLKOUT3_PHASE_CTRL} {puts "$p=[get_property $p $dpll]"}
get_nets -of [get_pins $dpll/CLKIN]
get_nets -of [get_pins $dpll/CLKIN_DESKEW]
report_clock_interaction -clocks [get_clocks {<clk_a> <clk_b>}]
```
2. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: User expects the crossing to be synchronous
* **Prompt**: Recommend configuring deskew: connect the same clock to `CLKIN` and `CLKIN_DESKEW` and set `CLKOUTn_PHASE_CTRL` to `2'b01` on the output that is used (RTL or clocking wizard change). Link to [TIMING-55](./TIMING-55.md) and [TIMING-57](./TIMING-57.md) for deskew-related checks.

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Crossing is asynchronous
* **Prompt**: Propose
```tcl
set_clock_groups -asynchronous -group [get_clocks <master_clk>] -group [get_clocks <derived_clk>]
```
and confirm synchronizers exist in the destination domain.

**CASE 3:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: Keep as is
* **Prompt**: Waiver `<USER> - <DATE> - <REASON>`

## Verification
* [ ] Methodology violation is no longer present
* [ ] `report_clock_interaction` shows the intended relationship
* [ ] No new unresolved methodology warnings are created

## Final Report
| Aspect | CLKIN_DESKEW | CLKOUTn_PHASE_CTRL | Crossing |
|--------|--------------|--------------------|----------|
| **Before** | unconnected | 2'b00 | timed, no phase relation |
| **After** | same clock as CLKIN | 2'b01 | timed with deskew (or async group) |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-53
- [TIMING-55](./TIMING-55.md), [TIMING-57](./TIMING-57.md) - deskew related checks
