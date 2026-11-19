<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-37: Bus skew constraint applied on signals with fanout

## Metadata

- **Check ID**: TIMING-37
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2017.1

## Description

A `set_bus_skew` constraint covers clock domain crossing (CDC) signals that fan out to more than one load inside the destination clock domain.

### Full Message Description property.

```
Bus skew constraint is not recommended on signals that have a fanout (see constraint position <MESSAGE_STRING> in the Timing Constraint window in Vivado IDE). First endpoint covered by the constraint: <NETLIST_ELEMENT>
```
**Example:**
```
Bus skew constraint is not recommended on signals that have a fanout (see constraint position 9 in the Timing Constraint window in Vivado IDE). First endpoint covered by the constraint: u_cdc/rd_ptr_gray_reg[2]/D
```

### Explanation
A bus skew constraint is only meaningful when each source register drives exactly one register in the destination clock domain. If a crossing signal fans out inside the asynchronous domain, then the same signal can be sampled differently by different destination registers (logic reconvergence), and hardware can fail even if the skew is met. A signal may fan out only after the synchronization register(s) in the destination domain.

**DO**
✅  Identify the `set_bus_skew` constraint and the first endpoint (D pin) it covers.
✅  Check the fanout of the source register's Q net, and the destination register's Q net.

**DO NOT**
❌ Automate a fix by deleting the bus skew constraint: this hides the CDC risk and does not fix it.
❌ Change the RTL automatically.

## Flow
1. Find the `set_bus_skew` constraint (FILE_NAME, LINE_NUMBER, `-from` / `-to` objects).
2. Check the fanout of the signals crossing the domain.
```tcl
set ep [get_pins <NETLIST_ELEMENT>]
set src_cell [get_cells -of_objects [get_pins -leaf -of_objects [get_nets -of_objects $ep] -filter {DIRECTION==OUT}]]
foreach c $src_cell {
  set q [get_pins -of_objects $c -filter {REF_PIN_NAME==Q}]
  puts "$c fanout = [llength [get_pins -leaf -of_objects [get_nets -of_objects $q] -filter {DIRECTION==IN}]]"
}
report_cdc -details -file ./methodology_results/cdc.rpt
```
3. Confirm whether the fanout is inside the source domain (not a violation of the CDC rule) or on the CDC net itself.

**DO NOT**
❌ Automate a fix when the fanout net cannot be identified.

4. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The CDC net has more than one load in the destination domain (the normal case for this check).
* **Prompt**:
```
Signals covered by set_bus_skew at <position> fan out inside the asynchronous clock domain: <list of nets/fanout>. Do you want to:
a) Modify the RTL so each source register drives a single destination register and the fanout starts after the synchronizer (recommended)
b) Narrow the set_bus_skew -from / -to objects so that it only covers signals with a single load
c) Leave the constraint as is and create a waiver
```
* **Explanation**: Offer an explanation <USER> - <DATE> - <REASON>
* **Examples**:
```
poldark 01/26/26 "Fanout loads are all in the same register bank sampled on the same capture edge; reviewed in CDC sign-off"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created
* [ ] `report_cdc` shows no unsafe fanout on the crossing

## Final Report
Print a table that shows the difference in:
* constraint
* covered signals
* fanout of each signal before/after

| Aspect | Constraint | Covered Signals | Max Fanout in Destination Domain |
|--------|-----------|-----------------|----------------------------------|
| **Before** | `set_bus_skew -from [get_cells u_cdc/gray_reg*] -to [get_cells u_cdc/sync_reg*] 2.500` | 8 | 3 |
| **After** | `set_bus_skew -from [get_cells u_cdc/gray_reg*] -to [get_cells u_cdc/sync_reg*] 2.500` | 8 | 1 |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-37
- Vivado Design Suite User Guide: Using Constraints (UG903), `set_bus_skew`
- [TIMING-39](./TIMING-39.md) - bus skew on paths with logic
