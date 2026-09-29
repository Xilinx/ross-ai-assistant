# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# trace_clock_tree.tcl
# Purpose: Trace up a clock tree from a netlist element to find the primary clock source
# Searches for clock objects using get_timing_arcs to relate output pins to input pins
# Continues tracing until finding a clock with IS_GENERATED==0 and MASTER_CLOCK==""
# Outputs results to JSON file linking netlist element to clock source

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
