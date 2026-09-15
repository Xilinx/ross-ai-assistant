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

**Example 1:** Tracing back to pins without a timing arc or port from an output pin
```tcl
proc trace_clock_back_to_source {pin} {
    set debug 1
    set pin [get_pins $pin]
    if {[get_property DIRECTION $pin] eq "OUT"} {
        set output_pins $pin
        if {$debug==1} {puts "-D: Starting trace from output pin $output_pins"}
    } else {
        set output_pins [get_pins -leaf -of [get_nets -of $pin] -filter {DIRECTION==OUT}]
        if {[llength $output_pins] == 0} {return [list $pin pin]}
    }
    
    set visited_pins {}
    set max_visits 5
    set pin_visit_count [dict create]
    
    while {1} {
        if {[llength [set arcs [get_timing_arcs -filter {TYPE=="Reg Clk to Q"||TYPE=="combinational"} -to $output_pins]]] == 0} {return [list $output_pins pin]}
        
        # Check if pin has been visited too many times
        if {[dict exists $pin_visit_count $output_pins]} {
            set count [dict get $pin_visit_count $output_pins]
            if {$count >= $max_visits} {return [list $output_pin pin]}
            dict set pin_visit_count $output_pins [expr {$count + 1}]
        } else {
            dict set pin_visit_count $output_pins 1
        }
        
        set input_pins [get_pins [get_property FROM_PIN $arcs]]
        set net [get_nets -parent -of $input_pins]
        set output_pins [get_pins -leaf -filter {DIRECTION==OUT} -of $net]
        if {[llength $output_pins] == 0} {
            if {[llength [set ports [get_ports -of $net]]] > 0} {return [list $ports port]}
        }
    }
}

set output_pin [get_pins ios_0/mmcm_0/CLKOUT2]
set sp [trace_clock_back_to_source $output_pin]

```

### DO:
✅ Consider there could be multiple timing arcs per pin 
✅ Consider there could be multiple clocks per pin

### DO NOT:
❌ Run indefinitely in a loop. Break after iterating on the same object

## References

### Xilinx Documentation 
- **UG906**: Vivado Design Suite User Guide: Design Analysis and Closure Techniques
  - [Invalid Primary Clock on CMB](https://docs.xilinx.com/r/en-US/ug906-vivado-design-analysis/Example)
