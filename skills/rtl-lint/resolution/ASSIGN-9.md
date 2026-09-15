<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-9: Unassigned Output Port Bits

**Rule:** ASSIGN-9 | **Severity:** WARNING | **Standard:** UG901

## Vivado Message
```
WARNING: [Synth 37-126] [ASSIGN-9] Some bits in IO 'y1' are not set. 
First unset bit index is -8. RTL Name 'y1', Hierarchy 'top', File 'top.v', Line 8.
```

## Root Cause

Output port has no driver (or only partial bits driven). Creates undefined/floating values.

**Determine scenario first:** Check if ANY bits are assigned to this port.

## Fix Options

### Scenario A: Completely Unassigned Output → Remove Port

```verilog
// Before
module top(a, b, clk, unused_out);
   output [7:0] unused_out;  // No driver anywhere

// After — remove unused port
module top(a, b, clk);
   // Removed unused_out — update parent instantiations
```

### Scenario B: Partially Assigned → Complete the Assignment

Identify which bits ARE assigned, then fill missing bits:

```verilog
// B1: Lower assigned, upper missing
output [3:0] out;
always@(*) out[2:0] = a[2:0];     // Bit [3] missing
// Fix: always@(*) out = {1'b0, a[2:0]};

// B2: Upper assigned, lower missing
always@(*) out[3:1] = a[2:0];     // Bit [0] missing
// Fix: always@(*) out = {a[2:0], 1'b0};

// B3: Non-contiguous gaps
always@(*) begin
   out[7:5] = a[2:0];
   out[2:0] = b[2:0];             // Bits [4:3] missing
end
// Fix: Add out[4:3] = 2'b00;
```

**Padding value:** Zero (default), sign extension (signed arithmetic), or constant (protocol requirements).

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-9 count = 0
```

For Scenario A: update parent module instantiations to remove port connection.

## References
**Xilinx:** UG901 "I/O Assignment Best Practices", UG949  
**Related:** ASSIGN-10 (unused input port bits)
