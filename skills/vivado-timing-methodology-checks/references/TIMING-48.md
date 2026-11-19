<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-48: Max delay datapath only constraint on latch input

## Metadata

- **Check ID**: TIMING-48
- **Severity**: Advisory
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.2

## Description

A `set_max_delay -datapath_only` constraint ends on (or passes into) the input of a latch. The constraint changes the path requirement and can create unrealistic latch time borrowing that does not match hardware and hurts the QoR of downstream paths.

### Full Message Description property.

```
A Max Delay Datapath Only constraint has been detected on the input of latch <NETLIST_ELEMENT> (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). This constraint is typically used on asynchronous clock domain crossings and can trigger unrealistic latch time borrowing which will impact the QoR of the downstream timing paths.
```
**Example:**
```
A Max Delay Datapath Only constraint has been detected on the input of latch lat_reg/D (see constraint position 3 in the Timing Constraint window in Vivado IDE). This constraint is typically used on asynchronous clock domain crossings and can trigger unrealistic latch time borrowing which will impact the QoR of the downstream timing paths.
```

### Explanation
A latch is transparent for part of the clock period and borrows time from the following stage. `set_max_delay -datapath_only` removes clock skew/phase from the requirement, so the tool computes a requirement unrelated to the latch's transparency window. The tool may then borrow an unrealistic amount of time and push the problem to downstream paths. This check is an **Advisory**: it does not claim a failure, only that the constraint must be verified against hardware.

**DO**
✅  Confirm that the latch input really is an asynchronous crossing needing `-datapath_only`.
✅  Check the downstream paths from the latch for large borrowing/negative slack.

**DO NOT**
❌ Remove the constraint automatically; it may be the only constraint on a real CDC.
❌ Treat the Advisory as a hardware failure without verifying it.

## Generate Data

1. Locate the constraint: `grep -n "set_max_delay" ./methodology_results/constraints.xdc` and record file, line, value, objects.
2. Latch details: `report_property [get_cells -of_objects [get_pins <NETLIST_ELEMENT>]]`; control pin `G`/`GE` clock: `get_clocks -of_objects [get_pins <latch>/G]` (see also [TIMING-20](./TIMING-20.md)).
3. Show time borrowing on downstream paths: `report_timing -through [get_pins <NETLIST_ELEMENT>] -max_paths 5`.
4. Identify the source clock and whether the crossing is asynchronous (`report_clock_interaction`).

## Flow

1. Identify the constraint and latch; determine whether the crossing is intentionally asynchronous.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Medium
* **When**: Always (hardware behavior must be verified by the user).
* **Prompt**:
```
A max delay datapath-only constraint (<file>:<line>) ends on latch <NETLIST_ELEMENT>. Does it match hardware behavior? Do you want to:
a) Remove the constraint (latch time borrowing analysis then follows the clock)
b) Keep the constraint, but move it to the register feeding the latch / end of the synchronizer
c) Leave it and create a waiver (justification required)
```
* **Examples**:
```
poldark 01/26/26 "Latch is the capture element of a documented async handshake; datapath-only requirement is intended"
```

## Verification
* [ ] Methodology violation TIMING-48 is no longer present (or waived)
* [ ] Latch time borrowing on downstream paths is realistic
* [ ] No new unresolved methodology warnings are created

## Final Report

| Aspect | Constraint | Value | Target Latch |
|--------|-----------|-------|--------------|
| **Before** | `set_max_delay -datapath_only 5.000 -from [get_clocks clk_a] -to [get_pins lat_reg/D]` | 5.000 ns | lat_reg |
| **After**  | removed (or waived) | - | lat_reg |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - TIMING-48
- Vivado Design Suite User Guide: Using Constraints (UG903) - set_max_delay -datapath_only
- [TIMING-20](./TIMING-20.md) - non-clocked latch, [TIMING-50](./TIMING-50.md) - same-level latches
