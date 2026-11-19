<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-20: Non-clocked latch

## Metadata

- **Check ID**: TIMING-20
- **Severity**: Warning

## Description

A latch cannot be properly analyzed because its control (enable/gate) pin is not reached by a timing clock.

## Message

```
The latch <NETLIST_ELEMENT> cannot be properly analyzed as its control pin <NETLIST_ELEMENT> is not reached by a timing clock
```

**Example:**
```
The latch data_lat_reg cannot be properly analyzed as its control pin data_lat_reg/G is not reached by a timing clock
```

## Explanation

A transparent latch is level-sensitive: its enable/gate (control) pin must be driven by a defined timing clock so the tool can model the transparent window and time borrowing correctly. When the control pin is not reached by any timing clock, the latch cannot be analyzed:

- Paths through the latch are not properly analyzed or optimized
- Time borrowing across the latch cannot be computed
- Timing violations can go undetected until hardware testing
- Implementation may make incorrect placement/routing decisions

This is closely related to [TIMING-17](./TIMING-17.md) (non-clocked sequential cell) but is specific to the **control pin of a latch**.

Common root causes:
- Missing `create_clock` / `create_generated_clock` on the source that drives the latch gate
- Clock gating or combinatorial logic on the control-pin path that blocks clock propagation
- The gate is driven by a signal that was never intended to be a clock (unintended latch inferred in RTL)
- A clock mux / selection logic without proper generated-clock definitions

## Generate Data

Collect information about the non-clocked latch and its control pin:
1. Get latch details: `report_property [get_cells <NETLIST_ELEMENT>]`
2. Identify the control pin and its driver:
```tcl
set g [get_pins <NETLIST_ELEMENT>/G]         ;# latch gate/enable pin (G / GE)
get_nets -of $g
report_timing -to $g
```
3. Trace the control pin back toward clock sources: `all_fanin -flat -to $g`
4. Check existing clock definitions: `report_clocks`
5. Find all non-clocked latches:
```tcl
get_cells -quiet -filter {PRIMITIVE_SUBGROUP == LATCH && CLOCK == ""}
```

## Flow

1. **Gather Data** → Collect latch properties and trace the control-pin path with `all_fanin`.
2. **Analyze** → Determine whether the control pin *should* be clocked (a real latch) or whether the latch was inferred unintentionally in the RTL.

3. **Resolve** → Select the resolution.

**CASE 1: Real latch — define the missing clock on the control path**
* **Type**: User Resolution
* **When**: The latch is intended and its gate is driven by a legitimate clock/enable that lacks a constraint

Trace the control pin back to its clock source and add the appropriate clock constraint so the gate is reached by a timing clock. Propose the constraint to the user for approval:

```tcl
# Example: the gate is driven by a clock net that was never constrained
create_clock -name gate_clk -period <period> [get_ports <clk_source_port>]
# or, if derived from an existing clock through logic:
create_generated_clock -name gate_clk -source [get_pins <src_pin>] \
    -divide_by 1 [get_pins <gate_driver_pin>]
```

**CASE 2: Unintended latch — fix the RTL**
* **Type**: User Resolution
* **When**: The latch was inferred accidentally (e.g. an incomplete `if`/`case`, missing `else`, or a missing default assignment)

Recommend the user fix the RTL so a register (or pure combinational logic) is inferred instead of a transparent latch — complete all branches / add default assignments. A latch that should not exist must not be constrained around; it should be removed at the source.

4. **Verify** → Re-run `report_methodology` and confirm the latch control pin is now reached by a timing clock (or the latch no longer exists).

## Verification

* [ ] The latch control (gate) pin is reached by a properly defined timing clock, **or** the unintended latch has been removed in RTL
* [ ] Paths through the latch appear in timing reports and time borrowing is analyzed
* [ ] No new unresolved methodology warnings are created
* [ ] The clock network driving the latch control is properly defined throughout the design

## Final Report Guidance

Document the resolution:
- Identify the root cause (missing clock constraint vs. unintended inferred latch)
- If constraints were added: Include the specific `create_clock` / `create_generated_clock` commands and the traced clock source
- If the RTL was changed: Document the code fix (completed branches / default assignment) that removed the latch
- List all affected latches and confirm each control pin is now clocked (or removed)

## References

- [UG906](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Message) - Design Analysis and Closure Techniques (TIMING-20; non-clocked latch)
- [UG903](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug903-vivado-using-constraints.pdf) - Using Constraints
- [UG949](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug949-vivado-design-methodology.pdf) - UltraFast Design Methodology
- [TIMING-17](./TIMING-17.md) - Non-clocked sequential cell (related check)
