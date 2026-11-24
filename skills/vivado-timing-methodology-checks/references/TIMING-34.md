<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-34: Bus skew constraint with unrealistic value

## Metadata

- **Check ID**: TIMING-34
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not reported by the Vivado check property (documented in UG906 from 2017.1)

## Description

A `set_bus_skew` constraint has a value that is too aggressive: less than the minimum of half the source and destination clock periods, or less than 1 ns.

### Full Message Description property.

```
Bus Skew constraint with unrealistic value (< min of half source and destination clock period) or < 1 ns requirement (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). Setting a very aggressive value results in increased runtime. First endpoint covered by the constraint: <NETLIST_ELEMENT>
```
**Example:**
```
Bus Skew constraint with unrealistic value (< min of half source and destination clock period) or < 1 ns requirement (see constraint position 14 in the Timing Constraint window in Vivado IDE). Setting a very aggressive value results in increased runtime. First endpoint covered by the constraint: u_cdc/gray_sync_reg[0]/D
```
* `constraint position 14` = the constraint index in the Timing Constraints window. Map it to the XDC file and line using the constraint properties (`FILE_NAME`, `LINE_NUMBER`).
* `First endpoint` = the first destination register D pin covered by the bus skew constraint.

### Explanation
Bus skew is met by adding routing delay (detours) to the faster signals during `route_design`. When the requirement is very small:
* The router needs many detours, which increases `route_design` runtime and routing congestion.
* The router may be unable to meet the target at all.

The `set_bus_skew` value only has to prevent the destination clock domain from capturing the bus across two different destination clock edges. The practical upper bound is about the smallest of the two clock periods; the lower bound for a realistic value is at least half the minimum clock period, and not below 1 ns.

**DO**
✅  Identify the exact `set_bus_skew` constraint using the constraint position, file name, and line number.
✅  Identify the source clock and destination clock of the covered paths and compute their periods.
✅  Compare the value against `max(1.0, min(src_period, dst_period)/2)`.

**DO NOT**
❌ Assume the first `set_bus_skew` in the XDC is the one reported.
❌ Pick a new value without computing it from the actual clock periods.
❌ Change a value that the user set for a documented functional reason without confirming.

## Flow
1. Search the constraints file for the `set_bus_skew` constraint at the reported position. Capture FILE_NAME, LINE_NUMBER, the value, and the `-from` / `-to` objects.
```tcl
report_bus_skew -file ./methodology_results/bus_skew.rpt
set src [get_clocks -of_objects [get_cells <startpoint_cell>]]
set dst [get_clocks -of_objects [get_cells <NETLIST_ELEMENT's cell>]]
puts "src period [get_property PERIOD $src]  dst period [get_property PERIOD $dst]"
```

**DO NOT**
❌ Automate a fix when the FILE_NAME or LINE_NUMBER is null.
❌ Automate a fix when the source or destination clock cannot be determined.
❌ Automate a fix when more than one source or destination clock reaches the paths (see [TIMING-38](./TIMING-38.md)).

2. Compute the minimum realistic value: `min_val = max(1.0, min(src_period, dst_period) / 2)`.

3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The value is below `min_val`, the constraint covers a single source clock and a single destination clock, and the constraint has no comment or attribute that records an explicit user intent for the small value.
* **Assumption**: The user did not intend an extreme skew; the minimum realistic value is a safe replacement because it still prevents multi-edge capture.

1. Modify the value of the `set_bus_skew` constraint to `min_val` (rounded up to 3 decimals).
2. Report the original value, the new value, and the clock periods used for the calculation.
3. State "If a tighter skew is functionally required, review the CDC design (e.g. use a gray-coded pointer or handshake) instead of tightening the constraint."

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: Any other scenario (the value was deliberately small, the clocks cannot be determined, or the constraint covers multiple clocks).
* **Prompt**:
```
The set_bus_skew value <value> ns on <from/to objects> is below the realistic minimum <min_val> ns. Do you want to:
a) Increase the value to <min_val> ns
b) Enter a different value
c) Leave the constraint as is and create a waiver
```
* **Explanation**: Offer an explanation <USER> - <DATE> - <REASON>
* **Examples**:
```
poldark 01/26/26 "Bus skew of 0.4 ns required by the receiving state machine; routing detours accepted"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created
* [ ] `report_bus_skew` shows the constraint with a realistic requirement
* [ ] `route_design` runtime/congestion is not impacted by the bus skew requirement (when re-implemented)

## Final Report
Print a table that shows the difference in the constraint and value:

| Aspect | Constraint | Value | Source Clock Period | Destination Clock Period |
|--------|-----------|-------|---------------------|--------------------------|
| **Before** | `set_bus_skew -from [get_cells u_cdc/gray_reg*] -to [get_cells u_cdc/gray_sync_reg*] 0.400` | 0.400 ns | 5.000 ns | 8.000 ns |
| **After** | `set_bus_skew -from [get_cells u_cdc/gray_reg*] -to [get_cells u_cdc/gray_sync_reg*] 2.500` | 2.500 ns | 5.000 ns | 8.000 ns |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-34
- Vivado Design Suite User Guide: Using Constraints (UG903), `set_bus_skew`
- [TIMING-32](./TIMING-32.md), [TIMING-33](./TIMING-33.md), [TIMING-37](./TIMING-37.md), [TIMING-38](./TIMING-38.md), [TIMING-39](./TIMING-39.md) - related bus skew checks
