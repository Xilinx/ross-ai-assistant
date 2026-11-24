<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-41: Invalid forwarded clock defined on an internal pin

## Metadata

- **Check ID**: TIMING-41
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2018.1

## Description

A forwarded clock (a clock sent to an output port via an ODDR/OSERDES) is defined on an internal leaf pin instead of on the output port.

### Full Message Description property.

```
The forwarded clock <CLOCK_GROUP> is defined on the pin <NETLIST_ELEMENT> instead of the port <NETLIST_ELEMENT>
```
**Example:**
```
The forwarded clock fwd_clk is defined on the pin oddr_clk/Q instead of the port clk_out
```
* The first `NETLIST_ELEMENT` is the internal pin where the generated clock is defined, the second is the output port it drives.

### Explanation
For correct I/O timing (set_output_delay relative to a forwarded clock, source-synchronous interface), the forwarded clock must be defined on the output port. When it is defined on the leaf pin that drives the port, the delay from that pin to the port (output buffer and routing) is not included in the clock path, and output delay analysis is inaccurate.

**DO**
✅  Identify the `create_generated_clock` that creates `<CLOCK_GROUP>` and check which constraints refer to this clock.
✅  Keep the clock name unchanged so existing `set_output_delay` constraints continue to work.

**DO NOT**
❌ Change the `-source`, `-divide_by`, `-multiply_by`, `-invert` or other options of the generated clock.
❌ Define the clock on the output buffer pin or on the port net.

## Flow
1. Search the constraints file for the `create_generated_clock` that creates the forwarded clock and capture FILE_NAME and LINE_NUMBER.
```tcl
set c [get_clocks <CLOCK_GROUP>]
report_property $c
get_property SOURCE_PINS $c
get_ports -of_objects [get_nets -of_objects [get_pins <internal pin>]]
```
2. Confirm the internal pin drives exactly one output port through an output buffer.
```tcl
set pin [get_pins <internal pin>]
set port [get_ports -quiet -of_objects [get_nets -of_objects [get_pins -leaf -filter {DIRECTION==OUT} -of_objects [get_nets -of_objects $pin]]]]
```

**DO NOT**
❌ Automate a fix when the FILE_NAME or LINE_NUMBER is null.
❌ Automate a fix when the pin does not map to exactly one output port.

3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: The forwarded clock is defined by a `create_generated_clock` on an internal pin and that pin drives exactly one output port.
* **Assumption**: The forwarded clock is intended to be seen at the output port.

1. Modify the `create_generated_clock` so that the target object is the output port, keeping `-name`, `-source` and all other options.
2. Report the original and new target.
3. State "If this assumption is incorrect, then review the interface constraints."

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Low
* **When**: The pin drives multiple ports or no port, or any other scenario.
* **Prompt**:
```
The forwarded clock <CLOCK_GROUP> is defined on <pin>. Do you want to:
a) Move the definition to output port <port>
b) Leave the definition on the internal pin (the clock is not forwarded off-chip) and create a waiver
```
* **Explanation**: Offer an explanation <USER> - <DATE> - <REASON>
* **Examples**:
```
poldark 01/26/26 "Clock is used internally; output port only carries a buffered copy that is not timed"
```

## Verification
* [ ] Methodology violation is no longer present
* [ ] No new unresolved methodology warnings are created
* [ ] `report_clocks` shows the clock with the output port as its target
* [ ] Existing `set_output_delay -clock <CLOCK_GROUP>` constraints still resolve

## Final Report
Print a table that shows the difference in:
* constraint
* clock name
* definition object

| Aspect | Constraint | Clock Name | Definition Object |
|--------|-----------|------------|-------------------|
| **Before** | `create_generated_clock -name fwd_clk -source [get_pins oddr_clk/C] -divide_by 1 [get_pins oddr_clk/Q]` | fwd_clk | oddr_clk/Q |
| **After** | `create_generated_clock -name fwd_clk -source [get_pins oddr_clk/C] -divide_by 1 [get_ports clk_out]` | fwd_clk | clk_out |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906), Appendix A, TIMING-41
- Vivado Design Suite User Guide: Using Constraints (UG903), forwarded clocks and `create_generated_clock`
