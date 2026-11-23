<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-38: Bus skew constraint applied on multiple clocks

## Metadata

- **Check ID**: TIMING-38
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2017.1

## Description

More than one source clock or more than one destination clock reaches the registers covered by a single `set_bus_skew` constraint.

### Full Message Description property.

```
Multiple clocks involved on source or destination of a bus skew constraint (see constraint position <MESSAGE_STRING> in the Timing Constraint Window in Vivado IDE). It is recommended to have only one source clock and one destination clock per bus skew constraint. First endpoint covered by the constraint: <NETLIST_ELEMENT>
```
**Example:**
```
Multiple clocks involved on source or destination of a bus skew constraint (see constraint position 6 in the Timing Constraint Window in Vivado IDE). It is recommended to have only one source clock and one destination clock per bus skew constraint. First endpoint covered by the constraint: u_cdc/wr_gray_sync_reg[0]/D
```

### Explanation
Bus skew is computed between the signals of a bus with respect to one launch clock and one capture clock. If multiple clocks propagate to the source or destination registers (for example a clock mux, or a clock with several generated clocks on the same pin), the analysis is ambiguous and is not representative of hardware.

**DO**
✅  Identify which registers are reached by more than one clock and list all clocks.
✅  Check whether the clocks are mutually exclusive in hardware (clock mux).

**DO NOT**
❌ Delete the bus skew constraint to remove the warning.
❌ Guess exclusivity of clocks. Ask the user.

## Flow
1. Find the `set_bus_skew` constraint (FILE_NAME, LINE_NUMBER, `-from` / `-to`).
2. List the clocks reaching the source and destination registers.
```tcl
set ep [get_pins <NETLIST_ELEMENT>]
puts "dst clocks: [get_clocks -of_objects $ep]"
foreach c [get_cells -of_objects [get_pins -leaf -of_objects [get_nets -of_objects $ep] -filter {DIRECTION==OUT}]] {
  puts "src $c clocks: [get_clocks -of_objects [get_pins -of_objects $c -filter {IS_CLOCK}]]"
}
report_clock_interaction -return_string
report_exceptions -file ./methodology_results/exceptions.rpt
```
3. Check for existing `set_clock_groups -logically_exclusive` / `-physically_exclusive` between the clocks.

**DO NOT**
❌ Automate a fix when the clocks reaching the endpoints cannot be listed.

4. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: Medium
* **Usefulness**: Medium
* **When**: The `set_bus_skew` uses wide wildcards (e.g. `get_cells *`) and the extra clocks reach only part of the covered registers, so it can be split per clock pair using the clock of each register group.
* **Assumption**: The user wants a skew requirement for each clock pair separately.

1. Split the constraint into one `set_bus_skew` per source/destination clock pair, using explicit `-from` / `-to` objects for each group.
2. Report the new constraints and the objects in each.
3. State "If this assumption is incorrect, then review the CDC topology."

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Any other scenario (clocks reach the same registers, e.g. a clock mux).
* **Prompt**:
```
The set_bus_skew at <position> is reached by multiple clocks: <source clocks> -> <destination clocks>. Do you want to:
a) Define the clocks as logically/physically exclusive with set_clock_groups (only if they cannot be active at the same time)
b) Narrow the set_bus_skew -from / -to objects so a single clock pair is covered
c) Leave the constraint as is and create a waiver
```
* **Explanation**: Offer an explanation <USER> - <DATE> - <REASON>
* **Examples**:
```
poldark 01/26/26 "Two clocks come from a mux with static select; exclusive in hardware, groups added in top.xdc"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created
* [ ] Only one source clock and one destination clock reach each `set_bus_skew` constraint

## Final Report
Print a table that shows the difference in:
* constraint
* source clocks
* destination clocks

| Aspect | Constraint | Source Clocks | Destination Clocks |
|--------|-----------|---------------|--------------------|
| **Before** | `set_bus_skew -from [get_cells u_cdc/a_reg*] -to [get_cells u_cdc/b_reg*] 2.000` | clk_a, clk_a_alt | clk_b |
| **After** | `set_clock_groups -logically_exclusive -group [get_clocks clk_a] -group [get_clocks clk_a_alt]` | clk_a, clk_a_alt (exclusive) | clk_b |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-38
- Vivado Design Suite User Guide: Using Constraints (UG903), `set_bus_skew`, `set_clock_groups`
- [TIMING-56](./TIMING-56.md) - multiple clocks generated on the same pin
