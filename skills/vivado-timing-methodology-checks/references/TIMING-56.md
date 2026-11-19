<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-56: Missing logically or physically excluded clock groups constraint

## Metadata

- **Check ID**: TIMING-56
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2020.2

## Description

Multiple clocks are defined or auto-derived on the same source pin but no `set_clock_groups -logically_exclusive` / `-physically_exclusive` constraint exists between them.

### Full Message Description property.

```
Multiple clocks are user generated or auto-derived on the source pin <NETLIST_ELEMENT> but are not logically or physically exclusive with respect to one another. To have the static timing analysis match the behavior in hardware, there cannot be multiple clocks generated on the same pin and when such situation occurs, the clocks should be defined as physically or logically exclusive. The list of clocks generated on the source pin is: <CLOCK_GROUP>
```
**Example:**
```
Multiple clocks are user generated or auto-derived on the source pin clk_gen/mmcm_i/CLKOUT0 but are not logically or physically exclusive ... The list of clocks generated on the source pin is: clkout0 clkout0_1
```

### Explanation
Only one clock can be on a pin in hardware at a time. When several clocks are generated on the same pin (for example `create_generated_clock -add` for different modes), the timer times the clock pairs that cannot coexist unless they are marked exclusive.

* **Physically exclusive**: the clocks use different physical routes and cannot be on the same net at the same time (for example clocks selected by a clock mux)
* **Logically exclusive**: the clocks share the same network but are never active together (for example mode-selected frequencies)

**DO**
✅ List all clocks on the pin and the constraint that created each (`-add`, `-master_clock`)
✅ Ask the user whether the exclusivity is physical or logical (from the clock tree topology)

**DO NOT**
❌ Pick physical or logical without checking the topology
❌ Use `-asynchronous` as a substitute. Asynchronous groups do not describe the hardware

## Flow
1. List the clocks on the pin.
```tcl
get_clocks -of_objects [get_pins <NETLIST_ELEMENT>]
report_clocks
```
2. Search `constraints.xdc` for `create_generated_clock ... -add` and `create_clock -add` on the same objects.
3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Clocks are mode-selected on the same network
* **Prompt**:
```tcl
set_clock_groups -logically_exclusive -group [get_clocks <clk_a>] -group [get_clocks <clk_b>]
```

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Clocks pass through different routes selected by a mux
* **Prompt**:
```tcl
set_clock_groups -physically_exclusive -group [get_clocks <clk_a>] -group [get_clocks <clk_b>]
```

**CASE 3:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: Duplicate definitions are accidental
* **Prompt**: Ask which definition to keep, remove the extra one. Otherwise waiver `<USER> - <DATE> - <REASON>`.

## Verification
* [ ] Methodology violation is no longer present
* [ ] `report_clock_interaction` shows the pair as exclusive
* [ ] No new unresolved methodology warnings are created

## Final Report
| Aspect | Constraint | Clocks |
|--------|-----------|--------|
| **Before** | none | clkout0, clkout0_1 timed together |
| **After** | `set_clock_groups -logically_exclusive -group [get_clocks clkout0] -group [get_clocks clkout0_1]` | exclusive |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-56
- Vivado Design Suite User Guide: Using Constraints (UG903)
- [TIMING-55](./TIMING-55.md)
