<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-33: Invalid bus skew constraint on safely timed paths

## Metadata

- **Check ID**: TIMING-33
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not recorded in the check metadata (documented in UG906 2022.1 and later)

## Description

`set_bus_skew` covers paths that Vivado times normally (synchronous clock-domain crossing or intra-clock paths). Bus skew is intended for asynchronous crossings, usually alongside `set_max_delay -datapath_only`; it can also be used on a synchronous crossing only when a `set_false_path` exception covers the path.

### Full Message Description property.

```
Bus Skew constraint set on paths that are safely timed (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). The command set_bus_skew is recommended on asynchronous paths or on paths between synchronous clock domains but with a set_false_path exception. First endpoint covered by the constraint: <NETLIST_ELEMENT>
```
**Example:**
```
Bus Skew constraint set on paths that are safely timed (see constraint position 9 in the Timing Constraint window in Vivado IDE). The command set_bus_skew is recommended on asynchronous paths or on paths between synchronous clock domains but with a set_false_path exception. First endpoint covered by the constraint: u_cdc/gray_sync_reg[0]
```

### Explanation
* The paths are either intra-clock or between synchronous clocks, so they are already timed and need no skew constraint.
* Or the crossing is really asynchronous but the clock relationship/exception is missing, so Vivado times it as synchronous. Then the **missing** constraint is the defect, not the `set_bus_skew`.

**DO**
✅  Determine the clock relationship of the covered paths (`report_clock_interaction`)
✅  Decide whether the crossing is genuinely asynchronous before choosing a fix
✅  Check TIMING-6/7/8/47 for the same clock pair

**DO NOT**
❌ Add `set_clock_groups -asynchronous` on clocks that are truly synchronous (see TIMING-47)
❌ Remove a `set_bus_skew` on a real asynchronous crossing just to clear the warning

## Generate Data

1. Locate the `set_bus_skew` constraint (position in message) and capture value, objects, clocks.
2. Get the source/destination clocks of the first endpoint path:
```tcl
report_timing -to [get_pins <NETLIST_ELEMENT>/D] -format full_clock_expanded
report_clock_interaction -return_string
```
3. Check the exceptions on the crossing: `report_exceptions` and grep `constraints.xdc` for the two clocks.
4. Confirm the crossing structure with `report_cdc`.

## Flow

1. Find the `set_bus_skew` and the covered clocks.
2. Classify: (a) intra-clock path, (b) synchronous clocks, (c) asynchronous in hardware but constrained as synchronous.
3. Select a case.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: Medium
* **Usefulness**: Medium
* **When**: All covered paths are intra-clock (source and destination clock are the same) and no exception covers them
* **Assumption**: A bus skew constraint on a single-clock path is a mistake; the path is already timed

1. Remove the `set_bus_skew` constraint.
2. Report the assumption. State "If this path is a real asynchronous crossing, the clock definitions are wrong; re-run after reviewing the clocks".

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The paths are between different clocks and the user states the crossing is asynchronous but Vivado times it as synchronous
* **Prompt**:
```
Do you want to:
a) Add the missing asynchronous constraint for the clock pair (set_clock_groups -asynchronous, or set_max_delay -datapath_only <value> on the crossing) and keep set_bus_skew
b) Add a set_false_path on the crossing and keep set_bus_skew
c) Remove the set_bus_skew (the crossing is synchronous and safely timed)
d) Leave as is and create a waiver
```
Never choose for the user: asynchronous grouping on truly synchronous clocks triggers TIMING-47.

## Verification
* [ ] Methodology violation is no longer present
* [ ] `report_clock_interaction` shows the intended relationship for the clock pair
* [ ] No new TIMING-6/7/8/47 warnings are created

## Final Report

| Aspect | Constraint |
|--------|-----------|
| **Before** | `set_bus_skew -from [get_cells u_cdc/gray_reg[*]] -to [get_cells u_cdc/gray_sync_reg[*]] 2.000` *(only)* |
| **After** | `set_max_delay -datapath_only -from [get_cells u_cdc/gray_reg[*]] -to [get_cells u_cdc/gray_sync_reg[*]] 4.000` + original `set_bus_skew` |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - Appendix A, TIMING-33
- Vivado Design Suite User Guide: Using Constraints (UG903) - Bus Skew
- [TIMING-6](./TIMING-6.md), [TIMING-7](./TIMING-7.md), [TIMING-8](./TIMING-8.md) - Missing clock relationship checks
- [TIMING-32](./TIMING-32.md), [TIMING-34](./TIMING-34.md) - Other bus skew checks
