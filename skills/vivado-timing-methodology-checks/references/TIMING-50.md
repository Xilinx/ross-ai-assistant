<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-50: Unrealistic path requirement between same-level latches

## Metadata

- **Check ID**: TIMING-50
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.2

## Description

Two cascaded latches with the same sensitivity level (both transparent-high or both transparent-low) have a conservative 0 ns path requirement between them.

### Full Message Description property.

```
There is a timing paths between the source pin <NETLIST_ELEMENT> and the destination pin <NETLIST_ELEMENT>. The two latches are <MESSAGE_STRING> level-sensitive with a 0ns path requirement. The 0ns path requirement is coming from the conservative latch analysis and could severely impact the runtime due to time borrowing calculation. It is not recommended to have such topology unless there is a multicycle path constraint to adjust the path requirement to a realistic value (at least half clock period).
```
**Example:**
```
There is a timing paths between the source pin lat_a_reg/Q and the destination pin lat_b_reg/D. The two latches are positive level-sensitive with a 0ns path requirement. ...
```
* `<MESSAGE_STRING>` is `positive` or `negative`
* First `<NETLIST_ELEMENT>` = source latch output pin, second = destination latch data pin

### Explanation
The timing engine gives the path between two same-level latches a conservative requirement of 0 ns. All of the budget has to come from time borrowing on the second latch, which can severely increase timing analysis and implementation runtime.

Cascaded latches normally use **opposite** levels so data cannot ripple through both latches in the same clock phase. Same-level cascades are uncommon and are often unintended (for example two inferred latches from incomplete RTL).

**DO**
✅ Confirm first whether both latches are really intended (see [TIMING-20](./TIMING-20.md) for unintended latches)
✅ Confirm the level sensitivity of both latches from `IS_*`/primitive type (LDCE/LDPE = transparent-high, `IS_G_INVERTED`/`IS_GE_INVERTED` flips it)
✅ Confirm the data really must pass through both latches within one phase before offering a multicycle path

**DO NOT**
❌ Guess a multicycle multiplier. The value comes from hardware behaviour, so only the user can confirm it
❌ Apply `set_false_path`/`set_max_delay` to hide the path

## Flow
1. Get the source and destination latch cells from <NETLIST_ELEMENT> and read their properties.
```tcl
set src [get_cells -of [get_pins <source_pin>]]
set dst [get_cells -of [get_pins <dest_pin>]]
report_property $src
report_property $dst
get_clocks -of_objects [get_pins -filter {REF_PIN_NAME==G||REF_PIN_NAME==GE} -of $src]
get_clocks -of_objects [get_pins -filter {REF_PIN_NAME==G||REF_PIN_NAME==GE} -of $dst]
```
2. Search `constraints.xdc` for existing `set_multicycle_path` between the two clocks or cells.
3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The same-level cascade is unintended (RTL inferred it, or one latch should be the opposite level)
* **Prompt**: Recommend an RTL change so one of the latches is opposite level (or replace the latch pair with a register). This skill does not edit RTL.

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: The same-level cascade is intended
* **Assumption**: Hardware behaviour has been confirmed by the user. The multiplier is chosen so the requirement is at least half a clock period
* **Prompt**: Ask the user for the intended requirement, then propose:
```tcl
set_multicycle_path <N> -setup -from [get_pins <source_pin>] -to [get_pins <dest_pin>]
set_multicycle_path <N-1> -hold  -from [get_pins <source_pin>] -to [get_pins <dest_pin>]
```
Show the command and ask "Execute this command? [Y/n]" per SKILL.md.

**CASE 3:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: User knows the topology is correct and wants to keep it as is
* **Prompt**: Offer a waiver `<USER> - <DATE> - <REASON>`

## Verification
* [ ] Methodology violation is no longer present (or is waived with justification)
* [ ] The path between the latches shows a realistic requirement in `report_timing`
* [ ] No new unresolved methodology warnings are created

## Final Report
| Aspect | Constraint / Design | Path Requirement | Latch Levels |
|--------|--------------------|------------------|--------------|
| **Before** | none | 0 ns | positive / positive |
| **After** | `set_multicycle_path 2 -setup -from [get_pins lat_a_reg/Q] -to [get_pins lat_b_reg/D]` | >= half period | positive / positive |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-50
- Vivado Design Suite User Guide: Using Constraints (UG903)
- [TIMING-20](./TIMING-20.md) - Non-clocked latch (related)
