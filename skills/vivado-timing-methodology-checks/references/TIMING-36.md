<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-36: Invalid Generated Clock due to missing edge propagation

## Metadata

- **Check ID**: TIMING-36
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2017.1

## Description

No rising or falling edge of the master clock can propagate to the source pin of the generated clock, so there is no known clock relationship.

### Full Message Description property.

```
There is no <MESSAGE_STRING> edge propagation between master clock <CLOCK_GROUP> to generated clock <CLOCK_GROUP>
```
**Example:**
```
There is no rising edge propagation between master clock clk_in to generated clock gen_div2
```
* `<MESSAGE_STRING>` is `rising` or `falling` (the edge type that cannot propagate).

### Explanation
A `create_generated_clock -source <pin>` assumes that the master clock reaches `<pin>` through enabled timing arcs. This breaks when:
* The `-source` pin is not in the fanout of the master clock.
* A timing arc along the path is disabled (`set_disable_timing`, or a case analysis / `set_logic_zero` constant that stops the clock).
* A min/max delay constraint with an invalid startpoint/endpoint segments the clock path (see [TIMING-42](./TIMING-42.md)).
* The edge is blocked by logic such as a gate whose enable is constant.

Without propagation, no relationship exists between master and generated clock. Sign-off timing between the two is not accurate and the design can fail in hardware.

**DO**
✅  Identify the `create_generated_clock` that creates the generated clock and its `-source` pin.
✅  Identify the master clock and verify physically that the master clock reaches the `-source` pin.

**DO NOT**
❌ Assume the master clock named in the generated clock is the clock that physically drives the source pin.
❌ Remove disabled arcs without understanding why they were disabled.

## Flow
1. Search the constraints file for the `create_generated_clock` that creates `<CLOCK_GROUP>` (the generated clock) and capture FILE_NAME and LINE_NUMBER.
```tcl
set gclk [get_clocks <generated clock>]
get_property MASTER_CLOCK $gclk
get_property SOURCE_PINS $gclk
report_property $gclk
```
2. Check whether the master clock reaches the source pin and whether arcs are disabled.
```tcl
report_disable_timing -file ./methodology_results/disable_timing.rpt
report_case_analysis -file ./methodology_results/case_analysis.rpt
report_clock_networks -clocks $gclk
```
3. Trace the clock with [CLOCK_TRACING.md](./CLOCK_TRACING.md) (call `methodology_check::trace_clock_back_to_source`) from the generated clock's source pin back to the master clock source.

**DO NOT**
❌ Automate a fix when the FILE_NAME or LINE_NUMBER is null.
❌ Automate a fix when the physical path to the master clock cannot be determined.

4. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Tracing shows a different clock (or a different pin of the same clock) that does reach the `-source` pin, and that clock has the same period as the one required by the divide/multiply ratio of the generated clock.
* **Assumption**: The master clock name in the constraint was wrong.

1. Modify `-source` (and master clock if specified with `-master_clock`) of the `create_generated_clock` to the pin/clock that physically drives it.
2. Report the original and new master clock.
3. State "If this assumption is incorrect, then review the clock topology."

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: The path is blocked by a disabled arc, a constant, or a segmenting delay constraint, or any other scenario.
* **Prompt**:
```
There is no <rising|falling> edge propagation from master clock <master> to generated clock <generated>. Do you want to:
a) Change the create_generated_clock -source pin / master clock to the pin that the master clock physically reaches
b) Remove the disable_timing / constant / delay constraint that blocks the clock edge (only if it is not needed)
c) Remove the generated clock (it is not needed in hardware)
d) Leave the constraint as is and create a waiver
```
* **Explanation**: Offer an explanation <USER> - <DATE> - <REASON>
* **Examples**:
```
poldark 01/26/26 "Generated clock is used only when test mode is active; test mode logic keeps the clock gated in normal mode"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created
* [ ] `report_clock_interaction` shows a timed relationship between master and generated clock
* [ ] `report_clocks` shows the generated clock with the expected master clock and source pin

## Final Report
Print a table that shows the difference in:
* constraint
* master clock
* source pin

| Aspect | Constraint | Master Clock | Source Pin |
|--------|-----------|--------------|------------|
| **Before** | `create_generated_clock -name gen_div2 -source [get_ports clk_in] -divide_by 2 [get_pins u_div/q_reg/Q]` | clk_in | clk_in |
| **After** | `create_generated_clock -name gen_div2 -source [get_pins u_div/q_reg/C] -divide_by 2 [get_pins u_div/q_reg/Q]` | clk_in | u_div/q_reg/C |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-36
- Vivado Design Suite User Guide: Using Constraints (UG903), `create_generated_clock`
- Clock Tracing Methodology (<skill_dir>/references/CLOCK_TRACING.md)
- [TIMING-42](./TIMING-42.md) - path segmentation in the clock tree
