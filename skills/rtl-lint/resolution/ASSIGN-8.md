<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-8: Array Dimension Mismatch in Comparison

**Rule:** ASSIGN-8 | **Severity:** CRITICAL WARNING | **Standard:** IEEE 1800-2017 §11.4.5

## Vivado Message
```
CRITICAL WARNING: [Synth 37-100] [ASSIGN-8]
Operator '<op>' in expression '(operand1 <op> operand2)' has arrays 
of different dimensions as operands.
```
Applies to: `==`, `!=`, `===`, `!==`, `>=`, `>`, `<`, `<=`

## Root Cause

Comparison operators applied to arrays with mismatched dimensional structure. Per IEEE 1800-2017: if operands have different dimensions, the result is undefined.

**Common sources:** Port declaration mismatch (1D vs 2D), copy-paste errors, packed array syntax confusion (`[M:0][N:0]` vs `[M:0]`)

## Fix Options

### Option 1: Match Operand Dimensions (Most common)

```verilog
// Before: 1D (16-bit) vs 2D (128-bit)
module test(input [15:0] in1, input [15:0][7:0] in2, output wire out1);
   assign out1 = in1 == in2;   // Undefined behavior

// After: both 1D
module test(input [15:0] in1, input [15:0] in2, output wire out1);
   assign out1 = in1 == in2;   // Valid: 16-bit == 16-bit
```

Update parent module to pass matching-dimension signal.

### Option 2: Flatten Array (When 2D structure needed elsewhere)

```verilog
wire [127:0] in2_flat;
genvar i;
generate
   for (i = 0; i < 16; i = i + 1) begin : flatten
      assign in2_flat[i*8 +: 8] = in2[i];
   end
endgenerate
assign out1 = in1 == in2_flat[15:0];  // Compare specific bits
```

### Option 3: Element-Wise Comparison

```verilog
wire [15:0] matches;
genvar i;
generate
   for (i = 0; i < 16; i = i + 1) begin : cmp
      assign matches[i] = (in1[i] == in2[i][0]);
   end
endgenerate
assign out1 = &matches;  // All elements match
```

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-8 count = 0
```

## References
**Xilinx:** UG901 Ch.2 "Arrays and Memory", UG906 Appendix B  
**IEEE:** 1800-2017 §11.4.4 (Relational), §11.4.5 (Equality), §7.4 (Packed arrays)
