<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-35: No common node in paths with the same clock

## Metadata

- **Check ID**: TIMING-35
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not reported by the Vivado check property (documented in UG906 from 2017.1)

## Description

One clock is defined on more than one object (port/pin/net). A path between two registers clocked by that same clock has no common node in the clock tree, so the path is not safely timed.

### Full Message Description property.

```
The clock <CLOCK_GROUP> has paths without a common node. First path found between <NETLIST_ELEMENT> and <NETLIST_ELEMENT>. Please review clock constraints
```
**Example:**
```
The clock sys_clk has paths without a common node. First path found between u_a/data_reg/C and u_b/data_reg/D. Please review clock constraints
```
* The first `NETLIST_ELEMENT` is the startpoint, the second is the endpoint of the first path found.

### Explanation
This normally happens when the same clock name is created on several sources, for example:
```tcl
create_clock -name sys_clk -period 10 [get_ports {clk_p0 clk_p1}]
# or
create_clock -name sys_clk -period 10 [get_ports clk_p0] -add
create_clock -name sys_clk -period 10 [get_ports clk_p1] -add
```
Each source has its own physical origin, so the launch and capture edges do not share a common clock node. Common-path pessimism removal and the clock skew calculation are meaningless, and the timing result is unsafe. The paths between the two sources should be treated as asynchronous.

**DO**
✅  Find every source object that carries the clock named in the message.
✅  Decide whether the clock is intended to be defined on several sources (e.g. multiple identical clock inputs that are muxed) or the extra definition is a mistake.

**DO NOT**
❌ Only look at the first `create_clock` that mentions the clock name.
❌ Add a constraint between the two source objects of a clock before confirming the intent.

## Flow
1. Collect every source of the clock.
```tcl
set clk [get_clocks <CLOCK_GROUP>]
report_property $clk
# Every source object of the clock
get_property SOURCE_PINS $clk
report_clocks -file ./methodology_results/report_clocks.rpt
```
2. Search the constraints file for each `create_clock` / `create_generated_clock` that creates `<CLOCK_GROUP>` and capture FILE_NAME and LINE_NUMBER.
3. For the startpoint and endpoint of the reported path, trace back the clock (see [CLOCK_TRACING.md](./CLOCK_TRACING.md), call `methodology_check::trace_clock_back_to_source`) to find the two sources.
```tcl
report_clock_networks -clocks $clk
report_timing -from <startpoint_cell> -to <endpoint_cell> -delay_type min_max
```

**DO NOT**
❌ Automate a fix when the FILE_NAME or LINE_NUMBER is null.
❌ Automate a fix when the two sources cannot be traced.

4. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: The same clock (same period and waveform) is defined on two sources, only one source reaches register clock pins that also have a path to the other group, and a second source is clearly a duplicate of the first (same net after the input buffer).
* **Assumption**: The second definition is redundant.

1. Remove the extra `create_clock ... -add` / extra port from the clock definition.
2. Report the removed source and the reason.
3. State "If this assumption is incorrect, then rename the clock for the second source and define an asynchronous relationship."

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: Any other scenario.
* **Prompt**:
```
The clock <CLOCK_GROUP> is defined on <source 1> and <source 2>. Paths between registers clocked from different sources are not safely timed. Do you want to:
a) Rename the clock on the second source so each source has a unique clock and add set_clock_groups -asynchronous (or the CDC constraints) between them
b) Keep one definition only and remove the other (they are the same physical clock)
c) Keep the constraint and set false path / max delay datapath only on the paths between the two source groups, with proper CDC circuitry
d) Leave the constraint as is and create a waiver
```
* **Explanation**: Offer an explanation <USER> - <DATE> - <REASON>
* **Examples**:
```
poldark 01/26/26 "Clock mux selects exactly one input at a time; both inputs share the same clock name by design"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created (check TIMING-6/7/8, TIMING-47)
* [ ] `report_clocks` shows one source object for the clock, or distinct clocks with the expected relationship

## Final Report
Print a table that shows the difference in:
* constraint
* clock name
* source objects

| Aspect | Constraint | Clock Name | Source Objects |
|--------|-----------|------------|----------------|
| **Before** | `create_clock -name sys_clk -period 10 [get_ports {clk_p0 clk_p1}]` | sys_clk | clk_p0, clk_p1 |
| **After** | `create_clock -name sys_clk -period 10 [get_ports clk_p0]` | sys_clk | clk_p0 |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-35
- Clock Tracing Methodology (<skill_dir>/references/CLOCK_TRACING.md)
- [TIMING-6](./TIMING-6.md), [TIMING-7](./TIMING-7.md) - related missing common primary clock / node checks
