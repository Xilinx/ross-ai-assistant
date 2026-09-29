<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-7: Multiple Drivers / Overlapping Bit Ranges

**Rule:** ASSIGN-7 | **Severity:** CRITICAL WARNING | **Standard:** UG901 Ch.4

## Vivado Message
```
CRITICAL WARNING: [Synth 37-90] [ASSIGN-7] Multiple assignments detected on array y1, 
first multi-driven bit index is 3. RTL Name 'y1', Hierarchy 'top', File 'top.v', Line 8.
```

## Root Cause

Multiple always blocks assign to overlapping bits of the same signal. Each register bit must have exactly ONE driver — multiple drivers create race conditions and non-deterministic behavior.

## Fix: Adjust to Non-Overlapping Bit Ranges

Identify the overlapping bit from the message, then adjust one range to eliminate overlap.

```verilog
// Before: bit 3 driven by both blocks
always@(posedge clk) signal[3:0] <= source_a[3:0];
always@(posedge clk) signal[7:3] <= source_b[7:3];  // Overlap at bit 3

// After (Option A: adjust lower range)
always@(posedge clk) signal[2:0] <= source_a[2:0];  // Bits 2:0
always@(posedge clk) signal[7:3] <= source_b[7:3];  // Bits 7:3

// After (Option B: adjust upper range)
always@(posedge clk) signal[3:0] <= source_a[3:0];  // Bits 3:0
always@(posedge clk) signal[7:4] <= source_b[7:4];  // Bits 7:4
```

Choose boundary based on natural field alignment (nibble/byte) and minimal changes.

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-7 count = 0
```

## References
**Xilinx:** UG901 Ch.4 "Sequential Logic Coding — One driver per bit"
