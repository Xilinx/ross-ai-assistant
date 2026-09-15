<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-18: Missing input or output delay

## Metadata

- **Check ID**: TIMING-18
- **Severity**: Warning
- **Group**: TIMING
- **Hierarchy**: Timing.Bad Practice
- **Owner**: gservel

## Description

Missing input or output delay constraint on a port relative to clock edges.

## Message

```
An <MESSAGE_STRING> delay is missing on <NETLIST_ELEMENT> relative to the rising and/or falling clock edge(s) of <CLOCK_GROUP>.
```

**Example:**
```
An input delay is missing on data_in[7] relative to the rising and/or falling clock edge(s) of sys_clk.
```

## Explanation

This check identifies top-level I/O ports that lack proper `set_input_delay` or `set_output_delay` constraints. These constraints are essential for:

- Defining the timing relationship between external devices and the FPGA
- Allowing timing analysis of paths that cross the chip boundary
- Ensuring proper setup and hold times are met at I/O interfaces
- Enabling optimization of I/O path timing during implementation

Without these constraints:
- I/O paths will not be properly analyzed or optimized
- Timing violations may go undetected until hardware testing
- The design may fail to meet system-level timing requirements

Common scenarios requiring input/output delays:
- Synchronous interfaces with external devices
- Data buses synchronized to system clocks
- Control signals with defined timing relationships
- Any I/O that interacts with clocked logic

## Generate Data

Collect information about the unconstrained port:
1. Get port details: `report_property [get_ports <NETLIST_ELEMENT>]`
2. Check existing I/O constraints: `report_timing -from [get_ports <NETLIST_ELEMENT>]` or `report_timing -to [get_ports <NETLIST_ELEMENT>]`
3. List all unconstrained ports: `get_ports -filter {DIRECTION == IN && INPUT_DELAY == ""}`
4. List all related clocks: `report_clocks <CLOCK_GROUP>`
5. Check clock interactions: `report_clock_interaction`

## Flow

1. **Gather Data** → Collect port properties and identify related clocks
2. **Analyze** → Determine external device timing requirements
3. **Resolve** → Add appropriate input/output delay constraints or mark as asynchronous
4. **Verify** → Confirm constraints are applied and timing analysis includes I/O paths

## Verification

* [ ] Port has appropriate input_delay or output_delay constraint applied
* [ ] I/O paths appear in timing reports
* [ ] Setup and hold requirements are analyzed
* [ ] No new unresolved methodology warnings are created
* [ ] Asynchronous paths are properly identified with false_path constraints if applicable

## Final Report Guidance

Document the resolution:
- If I/O delays were added: Include the specific XDC constraints with values and justification
- If marked as asynchronous: Document why timing doesn't apply and include false_path constraint
- Reference external device datasheets or interface specifications
- List all affected ports and their constraints
- Note any assumptions made about external timing

## References

- [UG906](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug906-vivado-design-analysis.pdf) - Design Analysis and Closure Techniques
- [UG903](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug903-vivado-using-constraints.pdf) - Using Constraints  
- [UG949](https://www.xilinx.com/content/dam/xilinx/support/documents/sw_manuals/xilinx2024_1/ug949-vivado-design-methodology.pdf) - UltraFast Design Methodology
