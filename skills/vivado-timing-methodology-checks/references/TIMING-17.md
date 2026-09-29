<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-17: Non-clocked sequential cell

## Metadata

- **Check ID**: TIMING-17
- **Severity**: Critical Warning
- **Group**: TIMING
- **Hierarchy**: Timing.Bad Practice
- **Owner**: gservel

## Description

Sequential cell clock pin is not reached by a timing clock.

## Message

```
The clock pin <NETLIST_ELEMENT> is not reached by a timing clock
```

**Example:**
```
The clock pin reg1_q_reg[0]/C is not reached by a timing clock
```

## Explanation

This check identifies sequential elements (flip-flops, latches, block RAM, DSP registers, etc.) whose clock pins are not driven by a properly defined timing clock. This is a critical issue because:

- The sequential element will not be included in timing analysis
- Timing paths to/from this element will not be analyzed or optimized
- The implementation tools may make incorrect placement/routing decisions
- The element may not function correctly in hardware

Common root causes include:
- Missing `create_clock` or `create_generated_clock` constraints
- Clock gating logic that blocks clock propagation
- Use of asynchronous resets or enables on clock pins
- Combinatorial logic driving clock pins without proper clock definition
- Clock selection muxes without proper clock definitions

## Generate Data

Collect information about the non-clocked sequential cell:
1. Get cell details: `report_property [get_cells <NETLIST_ELEMENT>]`
2. Trace clock path: `report_timing -to <NETLIST_ELEMENT>/C`
3. Check clock definitions: `report_clocks`
4. Find all non-clocked cells: `get_cells -filter {IS_SEQUENTIAL && CLOCK == ""}`
5. Check driver net: `get_nets -of [get_pins <NETLIST_ELEMENT>/C]`

## Flow

1. **Gather Data** → Collect cell properties and clock path information
2. **Analyze** → Trace clock source and determine why no timing clock exists
3. **Resolve** → Add missing clock constraints or fix clock path issues
4. **Verify** → Confirm cell now has valid timing clock

## Verification

* [ ] Clock pin is reached by a properly defined timing clock
* [ ] Sequential cell appears in timing reports
* [ ] No new unresolved methodology warnings are created
* [ ] Clock network is properly defined throughout the design

## Final Report Guidance

Document the resolution:
- Identify the root cause (missing constraint vs. design issue)
- If constraints were added: Include the specific XDC commands
- If design needed changes: Document the clock network modifications
- List all affected sequential cells
- Confirm all cells are now properly clocked

## References

- [UG906](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug906-vivado-design-analysis.pdf) - Design Analysis and Closure Techniques
- [UG903](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug903-vivado-using-constraints.pdf) - Using Constraints  
- [UG949](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug949-vivado-design-methodology.pdf) - UltraFast Design Methodology
