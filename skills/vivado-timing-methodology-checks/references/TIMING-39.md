<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-39: Invalid bus skew constraint on paths that have too many levels of logic

## Metadata

- **Check ID**: TIMING-39
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2017.1

## Description

A `set_bus_skew` constraint covers clock domain crossing paths with more than one logic level between the source and destination registers.

### Full Message Description property.

```
Bus skew constraint has been applied to paths with more than 1 logic level (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). First endpoint covered by the constraint: <NETLIST_ELEMENT>
```
**Example:**
```
Bus skew constraint has been applied to paths with more than 1 logic level (see constraint position 11 in the Timing Constraint window in Vivado IDE). First endpoint covered by the constraint: u_cdc/dst_reg[3]/D
```

### Explanation
Asynchronous CDC buses should have each source register drive exactly one destination register with no combinational logic in between. Logic on a CDC path can reconverge in the destination domain and glitch when inputs arrive at different times, so the bus skew constraint does not guarantee a safe crossing.

**DO**
✅  Identify the `set_bus_skew` and the path starting at its first endpoint.
✅  Report the logic cells (LUT, carry, etc.) between the source and destination registers.

**DO NOT**
❌ Delete the bus skew constraint to hide the logic.
❌ Edit the RTL automatically.

## Flow
1. Find the `set_bus_skew` constraint (FILE_NAME, LINE_NUMBER, `-from` / `-to`).
2. Report the path and its logic levels.
```tcl
set ep [get_pins <NETLIST_ELEMENT>]
report_timing -to $ep -max_paths 1 -path_type full -file ./methodology_results/tm39_path.rpt
set p [get_timing_paths -to $ep -max_paths 1]
puts "logic levels: [get_property LOGIC_LEVELS $p]"
report_cdc -details -file ./methodology_results/cdc.rpt
```

**DO NOT**
❌ Automate a fix when the path cannot be reported.

3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Combinational logic exists on the CDC path (the normal case).
* **Prompt**:
```
Paths covered by set_bus_skew at <position> have logic between source and destination: <cells>. Do you want to:
a) Modify the RTL so the source registers directly drive the destination registers (recommended)
b) Move the logic to the source domain (before the source register) or after the synchronizer in the destination domain
c) Narrow the set_bus_skew -from / -to to only the paths without logic
d) Leave the constraint as is and create a waiver
```
* **Explanation**: Offer an explanation <USER> - <DATE> - <REASON>
* **Examples**:
```
poldark 01/26/26 "Single inverter for polarity only; static in hardware, CDC review approved"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created
* [ ] CDC paths covered by `set_bus_skew` have 0 logic levels between registers

## Final Report
Print a table that shows the difference in:
* constraint
* path
* logic levels

| Aspect | Constraint | Path | Logic Levels |
|--------|-----------|------|--------------|
| **Before** | `set_bus_skew -from [get_cells u_cdc/src_reg*] -to [get_cells u_cdc/dst_reg*] 2.500` | src_reg[3]/C -> LUT2 -> dst_reg[3]/D | 1 |
| **After** | `set_bus_skew -from [get_cells u_cdc/src_reg*] -to [get_cells u_cdc/dst_reg*] 2.500` | src_reg[3]/C -> dst_reg[3]/D | 0 |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-39
- Vivado Design Suite User Guide: Using Constraints (UG903), `set_bus_skew`
- [TIMING-37](./TIMING-37.md) - bus skew on signals with fanout
