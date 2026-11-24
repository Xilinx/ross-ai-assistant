<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-46: Multicycle path with tied CE pins

## Metadata

- **Check ID**: TIMING-46
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2019.1

## Description

A multicycle path is defined between two registers that are directly connected, and the CE pins of the source/destination registers are tied to VCC. Data changes every clock, so hardware captures it with a single-cycle requirement and the multicycle exception does not reflect real behavior.

### Full Message Description property.

```
One or more multicycle paths are defined between registers <NETLIST_ELEMENT> and <NETLIST_ELEMENT> with a direct connection and the CE pins connected to VCC (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE).  This may result in an inaccurate path requirement.
```
**Example:**
```
One or more multicycle paths are defined between registers data0_reg and data1_reg with a direct connection and the CE pins connected to VCC (see constraint position 4 in the Timing Constraint window in Vivado IDE).  This may result in an inaccurate path requirement.
```

### Explanation
A multicycle path (`set_multicycle_path N -setup`) tells the tool the data is captured N cycles after launch. That is only true if the source holds its value (or the destination only samples) on a periodic enable. When both CE pins are tied high, a new value is launched and captured every cycle, so the real requirement is one cycle. Timing is then optimistic (the path appears to have extra time it does not have) and the design can fail in hardware.

**DO**
✅  Identify the multicycle constraint from the register names and the position.
✅  Inspect the CE connectivity of both registers.
✅  Ask whether the intent is a real multi-cycle path (enable logic is missing) or a tied-enable single-cycle path (exception is wrong).

**DO NOT**
❌ Add enable logic or modify RTL automatically; this skill only edits constraints.
❌ Guess the intended cycle count.

## Generate Data

1. Locate the multicycle constraint(s):
```sh
grep -n "set_multicycle_path" ./methodology_results/constraints.xdc
```
Record file, line, `-setup`/`-hold`, `-start`/`-end`, multiplier, from/to objects.
2. Check CE connectivity of the named registers:
```tcl
foreach r {<reg1> <reg2>} {
  set ce [get_pins -quiet $r/CE]
  puts "$r CE net: [get_nets -quiet -of_objects $ce] driver: [get_pins -leaf -quiet -filter {DIRECTION==OUT} -of_objects [get_nets -quiet -of_objects $ce]]"
}
```
A driver of GND/VCC (or no CE pin with the net tied to VCC) confirms the tied CE.
3. Check the path: `report_timing -from [get_cells <reg1>] -to [get_cells <reg2>]`.
4. Report any other exceptions: `report_exceptions -from [get_cells <reg1>] -to [get_cells <reg2>]`.

## Flow

1. Gather the data above and determine whether other signals gated by the same multicycle constraint have a dynamic CE.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The CE pins are tied and the design intent is single-cycle capture.
* **Prompt**:
```
Multicycle path <file>:<line> (<N> cycles) covers <reg1> -> <reg2>, but both CE pins are tied to VCC. Do you want to:
a) Remove the multicycle path (real requirement is one cycle)
b) Keep it and add a dynamic CE enable in RTL that toggles per the multicycle (you must edit the RTL)
c) Leave it and create a waiver (justification required)
```

**CASE 2:**
* **Type**: Automated Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: User chose (a) in a previous answer in this session for the same constraint, or the multicycle constraint covers only the flagged registers.
* **Assumption**: The tied CE is the actual hardware behavior.

1. Remove the multicycle path (or the offending register pair from its objects) in `constraints_after_fix.xdc`.
2. Report the removed constraint and state that no RTL was changed.

## Verification
* [ ] Methodology violation TIMING-46 is no longer present
* [ ] `report_timing` for the path now shows a one-cycle requirement (or the CE is dynamic and matches the multicycle)
* [ ] No new setup/hold violations were introduced that were hidden by the exception (review them)

## Final Report

| Aspect | Constraint | Multiplier | From | To |
|--------|-----------|------------|------|----|
| **Before** | `set_multicycle_path 2 -setup -from [get_cells data0_reg] -to [get_cells data1_reg]` | 2 | data0_reg | data1_reg |
| **After**  | removed | 1 (default) | data0_reg | data1_reg |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - TIMING-46
- Vivado Design Suite User Guide: Using Constraints (UG903) - Multicycle Paths
- [TIMING-29](./TIMING-29.md) - inconsistent setup/hold multicycle pair
