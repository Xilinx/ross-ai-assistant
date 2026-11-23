<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-27: Invalid primary clock on hierarchical pin

## Metadata

- **Check ID**: TIMING-27
- **Severity**: Critical Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: Not recorded in the check metadata (documented in UG906 2022.1 and later)

## Description

A primary clock is created on an internal (hierarchical) pin whose driver also drives other clock pins and is already traversed by a clock. Cells downstream of the hierarchical pin are timed against a different clock than the cells on the rest of the driver's fanout.

### Full Message Description property.

```
A primary clock <CLOCK_GROUP> is created on an inappropriate internal pin <NETLIST_ELEMENT>. It is not recommended to create a primary clock on a hierarchical pin when its driver pin has a fanout connected to multiple clock pins
```
**Example:**
```
A primary clock clk_core is created on an inappropriate internal pin u_core/clk_in. It is not recommended to create a primary clock on a hierarchical pin when its driver pin has a fanout connected to multiple clock pins
```

### Explanation
* The driver of the hierarchical pin is already carrying a clock (for example `clk_top` from the top-level port).
* The new `create_clock` on `u_core/clk_in` makes the `u_core` registers use `clk_core` while sibling registers on the same driver use `clk_top`.
* If any synchronous path exists between the two, **skew is computed incorrectly and sign-off is invalid**. This can cause hardware failures.

**DO**
✅  Identify the clock that already reaches the driver of the hierarchical pin
✅  Compare period and waveform of the clock on the hierarchical pin with the driver clock
✅  Check which exceptions reference the clock name before removing it

**DO NOT**
❌ Assume the clock is redundant before comparing period/waveform
❌ Remove the definition when exceptions reference its name without preserving the name (CASE 1)

## Generate Data

1. Find the constraint creating <CLOCK_GROUP> (file, line) using `get_property FILE_NAME` / `LINE_NUMBER` on `get_clocks <CLOCK_GROUP>` and match to the XDC.
2. Get the driver pin and the clocks propagating to it:
```tcl
set hpin [get_pins <NETLIST_ELEMENT>]
set drv  [get_pins -leaf -filter {DIRECTION==OUT} -of_objects [get_nets -of_objects $hpin]]
get_clocks -of_objects $drv
```
3. Capture the period and waveform of both clocks: `get_property PERIOD`, `get_property WAVEFORM`.
4. Find exceptions referencing <CLOCK_GROUP> (grep `constraints.xdc`, or `report_exceptions`).
5. Count clock pins in the fanout of the driver: `llength [get_pins -of_objects [get_nets -of_objects $drv] -filter {IS_CLOCK}]`.

## Flow

1. Search `constraints.xdc` for the `create_clock` that creates <CLOCK_GROUP> and confirm FILE_NAME / LINE_NUMBER match.
2. Trace the driver clock using `CLOCK_TRACING.md`.
3. Compare the period/waveform of <CLOCK_GROUP> with the driver clock and select a case.

**DO NOT**
❌ Automate fixes when the FILE_NAME or LINE_NUMBER is null or does not match the constraint found
❌ Automate fixes when the driver has no clock (the check would not have fired; re-verify the data)

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Period and waveform are identical to the driver clock **and** exceptions reference <CLOCK_GROUP>
* **Assumption**: The hierarchical clock is a redundant redefinition of the driver clock

1. Replace the `create_clock` with a generated clock that keeps the name and uses the driver clock as master:
```tcl
create_generated_clock -name <CLOCK_GROUP> -source [get_pins <driver_pin>] -divide_by 1 [get_pins <NETLIST_ELEMENT>]
```
2. Report that existing exceptions keep working because the name is preserved.

**CASE 2:**
* **Type**: Automated Resolution
* **Confidence**: High
* **Usefulness**: High
* **When**: Period and waveform are identical to the driver clock and **no** exception references <CLOCK_GROUP>
1. Remove the `create_clock` on <NETLIST_ELEMENT>.
2. Report that no exceptions referenced the clock name and state that no assumptions were made.

**CASE 3:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Medium
* **When**: Period or waveform differ from the driver clock (the downstream clock is genuinely a different frequency/phase)
* **Prompt**:
```
Do you want to:
a) Remove the constraint that creates <CLOCK_GROUP> (the driver clock then applies)
b) Replace it with a create_generated_clock using the driver clock as master (-divide_by/-multiply_by/-edges as required by the logic between the driver and the pin)
c) Leave as is and create a waiver
```
Never guess the divide/multiply factors; ask the user or derive them from the clock-modifying logic.

## Verification
* [ ] Methodology violation is no longer present
* [ ] Cells under the hierarchical pin are reached by the master clock (`get_clocks -of_objects <pin>`)
* [ ] No new TIMING-2/3/4 warnings are created
* [ ] Exceptions that referenced the old clock still resolve (no "No valid object" warnings)

## Final Report

| Aspect | Constraint | Period | Waveform | Clock Definition Object |
|--------|-----------|--------|----------|------------------------|
| **Before** | `create_clock -name clk_core -period 10.000 [get_pins u_core/clk_in]` | 10.000 ns | {0 5.000} | clk_core |
| **After** | `create_generated_clock -name clk_core -source [get_pins clk_buf/O] -divide_by 1 [get_pins u_core/clk_in]` | 10.000 ns | {0 5.000} | clk_core |

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - Appendix A, TIMING-27
- Vivado Design Suite User Guide: Using Constraints (UG903) - Generated Clocks
- [TIMING-4](./TIMING-4.md) - Redefinition of an existing clock downstream (related check)
- Clock Tracing Methodology (<skill_dir>/references/CLOCK_TRACING.md)
