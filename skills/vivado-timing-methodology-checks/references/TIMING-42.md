<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-42: Path segmentation detected in the clock tree

## Metadata

- **Check ID**: TIMING-42
- **Severity**: Warning
- **Hierarchy Name**: Timing.Bad Practice
- **First Release**: 2018.1

## Description

A min/max delay constraint (`set_max_delay`, `set_min_delay`, or a `-datapath_only` variant) has a startpoint or endpoint that is not a valid timing startpoint/endpoint. The tool segments the path by disabling the timing arc into that pin, which blocks propagation of a clock through it.

### Full Message Description property.

```
The <MESSAGE_STRING> delay constraint position <MESSAGE_STRING> is blocking the propagation of clock <CLOCK_GROUP> on pin <NETLIST_ELEMENT>
```
**Example:**
```
The max delay constraint position 12 is blocking the propagation of clock clk_sys on pin u_clkgen/bufg_o/O
```
* `max` - which delay constraint type (max or min) causes the segmentation
* `12` - position of the constraint in the Timing Constraint window (NOT a line number)
* `clk_sys` - clock whose propagation is blocked
* `u_clkgen/bufg_o/O` - the clock-tree pin where the arc was disabled

### Explanation
A valid startpoint is a sequential cell clock pin, an input port or a clock source; a valid endpoint is a sequential data pin or an output port. When `set_max_delay -from/-to` names a clock-tree pin (for example a BUFG output, or a combinational cell inside the clock network), the tool must treat that pin as a path boundary. It disables the timing arc through it, so the clock no longer reaches downstream registers. Downstream paths are then untimed or timed against the wrong clock and sign-off is inaccurate. The design can fail in hardware.

The constraint position in the message is the index in the Timing Constraint window. It is not a line number. The offending constraint must be located in `constraints.xdc` by its objects, using the pin in the message.

**DO**
✅  Find the min/max delay constraint whose `-from`, `-to` or `-through` object is the pin (or a net/cell containing the pin) in the message.
✅  Confirm the blocked clock reaches the pin: `get_clocks -of_objects [get_pins <pin>]`.
✅  Keep the original intent of the constraint when moving the startpoint/endpoint.

**DO NOT**
❌ Assume the constraint at "position N" is the Nth line of the XDC.
❌ Delete the constraint without understanding why it was written (it may be the only constraint on a real CDC or I/O path).
❌ Automate a change when the constraint file or line number cannot be determined.

## Generate Data

1. List the delay exceptions and their objects:
```tcl
report_exceptions -ignored
report_exceptions -from [get_pins <NETLIST_ELEMENT>]
report_exceptions -to   [get_pins <NETLIST_ELEMENT>]
```
2. Confirm the pin is in the clock network and which clocks reach it:
```tcl
set p [get_pins <NETLIST_ELEMENT>]
get_clocks -quiet -of_objects $p
get_property REF_NAME [get_cells -of_objects $p]
```
3. Locate the matching constraint in `./methodology_results/constraints.xdc` (original file name and line from the `# Constraints from file :` banners). Record the file, line, command, `-from`/`-to`/`-through` objects.
4. Identify the real startpoints/endpoints the author meant (registers or ports on either side of the segmented pin):
```tcl
all_fanin  -flat -startpoints_only -only_cells [get_pins <NETLIST_ELEMENT>]
all_fanout -flat -endpoints_only   -only_cells [get_pins <NETLIST_ELEMENT>]
```

## Flow

1. Identify the constraint that segments the clock tree (Generate Data steps 1-3).
2. Determine what the constraint was meant to control (steps 4).

**CASE 1:**
* **Type**: Automated Resolution
* **Confidence**: Medium
* **Usefulness**: High
* **When**: The constraint references the clock-tree pin with `-from`/`-to` and exactly one set of valid sequential startpoints/endpoints is unambiguously on the other side of that pin (single driver register or port, single capture register or port).
* **Assumption**: The delay value and `-datapath_only` setting are correct; only the object is wrong.

1. Replace the invalid clock-tree pin in the constraint with the valid registers/ports found in Generate Data step 4.
2. Report the before/after constraint.
3. State "If this assumption is incorrect, then please resolve the issue manually"

**CASE 2:**
* **Type**: User Resolution
* **Confidence**: High
* **Usefulness**: Medium
* **When**: Multiple possible startpoints/endpoints, or the intent of the constraint cannot be determined.
* **Prompt**:
```
The <max|min> delay constraint (<file>:<line>) names the clock-tree pin <NETLIST_ELEMENT>, which blocks propagation of <CLOCK_GROUP>. Do you want to:
a) Re-target the constraint to valid registers/ports (list candidates)
b) Remove the constraint
c) Leave it and create a waiver (justification required)
```
* **Examples**:
```
poldark 01/26/26 "Constraint intentionally breaks the path at a clock-gating cell; clock is re-created downstream with create_generated_clock"
```

## Verification
* [ ] Methodology violation TIMING-42 is no longer present
* [ ] The clock now propagates through the pin (`get_clocks -of_objects [get_pins <pin>]` returns the clock)
* [ ] `report_clock_networks` shows the downstream registers in the clock tree
* [ ] The intended delay requirement is still applied to the real data path

## Final Report

| Aspect | Constraint | Object Types | Clock Reaching Pin |
|--------|------------|--------------|--------------------|
| **Before** | `set_max_delay -datapath_only 5.0 -from [get_pins u_clkgen/bufg_o/O] -to [get_pins u_dst_reg/D]` | clock-tree pin -> data pin | none (blocked) |
| **After**  | `set_max_delay -datapath_only 5.0 -from [get_cells u_src_reg] -to [get_cells u_dst_reg]` | register -> register | clk_sys |

## References

- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906) - TIMING-42
- Vivado Design Suite User Guide: Using Constraints (UG903) - Timing Exceptions, Valid Startpoints and Endpoints
- Clock Tracing Methodology (<skill_dir>/references/CLOCK_TRACING.md)
