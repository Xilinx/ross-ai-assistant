<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Clock Tree Tracing Skill

## Summary
This skill captures how to trace a clock tree from an output pin back to where the source clock constraint should be defined.

## Key Concepts

### Valid Clock Definition Objects
1. A port
2. An output pin that has no timing arcs


# Timing arc relationship

## Essential Tcl Commands

### 1. Trace using timing arcs 
**Preferred:** Trace using timing arcs is preferred inside Vivado

The tracing proc is `methodology_check::trace_clock_back_to_source` (source:
[<skill_dir>/scripts/run_methodology_setup.tcl](../scripts/run_methodology_setup.tcl)). It is loaded in Step 1 by
sourcing that same file, so **do not source it and do not copy the proc body**. Just call it with its
namespace-qualified name (the unqualified name `trace_clock_back_to_source` does not exist).

**Example 1:** Tracing back to pins without a timing arc or port from an output pin
```tcl
set output_pin [get_pins ios_0/mmcm_0/CLKOUT2]
lassign [methodology_check::trace_clock_back_to_source $output_pin] objs type
# $type is "port" or "pin"; $objs is the port / pin object(s) where the clock should be defined
```

**Arguments:** one pin (an output pin, or an input pin which is resolved to the net's driver).

**Returns:** a 2-element list `{<objects> port|pin}`:
* `port` - the trace ended at a top-level port
* `pin` - the trace ended at an output pin with no timing arc (or hit the visit limit)

The design must be open. Prints a `-D:` debug line for the start pin.

### DO:
✅ Consider there could be multiple timing arcs per pin 
✅ Consider there could be multiple clocks per pin
✅ Call it as `methodology_check::trace_clock_back_to_source`

### DO NOT:
❌ Run indefinitely in a loop. The proc stops after visiting the same object 5 times
❌ Redefine or copy the proc into the session; use the loaded one

## References

### Xilinx Documentation 
- **UG906**: Vivado Design Suite User Guide: Design Analysis and Closure Techniques
  - [Invalid Primary Clock on CMB](https://docs.xilinx.com/r/en-US/ug906-vivado-design-analysis/Example)
