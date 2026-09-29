<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# TIMING-2: Invalid primary clock source pin

## Metadata

- **Check ID**: TIMING-2
- **Severity**: Critcal warning
- **Group**: 1
- **Priority**: 1
- **First Release**: 2025.1

## Description

Invalid primary clock source pin

## Message

```
A primary clock <CLOCK_GROUP> is created on an inappropriate pin <NETLIST_ELEMENT>. It is recommended to create a primary clock only on a proper clock source (input port or primitive output pin with no timing arc)
```

```real example
A primary clock new_clk is created on an inappropriate pin reg1_q_reg[0]/Q. It is recommended to create a primary clock only on a proper clock source (input port or primitive output pin with no timing arc)
```

### Explanation

Incorrect object specfied for `create_clock` definition. Clocks should be defined either on a port or an output pin with no timing arcs

### Flow
1. Using CLOCK_TRACING, trace from the <NETLIST ELEMENT>> to find the most appropriate object to set the `create_clock` constraint on.
2. Gather any information on CMBs that change the clock period or 
3. Write out the full constraints file
3. Determine the best solution to offer the user.

**CASE 1:**
* **Type**: User Resolution
* **Condfidence**: High
* **Usefulness**: Low
* **When**: If there are no returned objects from CLOCK_TRACING, 
* **Action**: Recommend the user either removes the constraint or waives the violation

**CASE 2:**
* **Type**: Auto Resolution
* **Condfidence**: High
* **Usefulness**: Med
* **When**: 
* [ ] CLOCK_TRACING returns a ground of power type driver
* **Action**: Remove the constraint

**CASE 3:**
* **Type**: Auto Resolution
* **Condfidence**: High
* **Usefulness**: High
* **When**: 
* [ ] There are returned objects from CLOCK_TRACING
* [ ] No constraint is defined on the CLOCK_TRACING objects
* **Action**: 
* [ ] Propose a create_clock constraint on the CLOCK_TRACING object based off the period values included in current constraint accounting for any CMBs in the path
* [ ] Remove the existing constraint

**CASE 4:**
* **Type**: Auto Resolution
* **Condfidence**: High
* **Usefulness**: High
* **When**: 
* [ ] There are returned objects from CLOCK_TRACING
* [ ] A constraint is defined on the CLOCK_TRACING objects
* [ ] The constraint period and waveform matches the <CLOCK_GROUP> definition accounting for clock modifying blocks.
* [ ] The clock name is referenced by exceptions elsewhere in the constraints file
* **Action**: 
* [ ] Create a `create_generated_clock` constraint on the <NETLIST_ELEMENT> using the existing name.
* [ ] Remove the existing constraint

**CASE 5:**
* **Type**: Auto Resolution
* **Condfidence**: High
* **Usefulness**: High
* **When**: 
* [ ] There are returned objects from CLOCK_TRACING
* [ ] A constraint is defined on the CLOCK_TRACING objects
* [ ] The constraint period and waveform matches the <CLOCK_GROUP> definition accounting for clock modifying blocks.
* [ ] The clock name is not referenced by exceptions elsewhere in the constraints file
* **Action**: 
* [ ] Remove the existing constraint

**CASE 6:**
* **Type**: User Resolution
* **Condfidence**: High
* **Usefulness**: Low
* **When**: 
* [ ] There are returned objects from CLOCK_TRACING
* [ ] A constraint is defined on the CLOCK_TRACING objects
* [ ] The constraint period and waveform oes not match the <CLOCK_GROUP> definition accounting for clock modifying blocks.
* [ ] The clock name is referenced by exceptions elsewhere in the constraints file
* **Action**: 
* [ ] Ask the user if they want to remove or waive the constraint. Print the clock differences so they can see what is expected v defined.



**Use** 
See [./REPORTING_CLOCK_DIFFERENCES](./REPORTING_CLOCK_DIFFERENCES.md)


### Examples

*To be documented with real-world examples. As required*

## References

- UltraFast Design Methodology Guide (UG949)
- Vivado Design Suite User Guide: Design Analysis and Closure Techniques (UG906)
- Vivado Design Suite User Guide: Using Constraints (UG903)
- [Clock Tracing Methodology](./CLOCK_TRACING.md)
