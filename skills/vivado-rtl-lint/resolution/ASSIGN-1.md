<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-1: Arithmetic Overflow / Result Truncation

**Rule:** ASSIGN-1 | **Severity:** WARNING | **Standard:** UG901 Ch.4

## Vivado Message
```
WARNING: [Synth 37-78] [ASSIGN-1]
Input operands(of sizes 7 8 7) of add/sub cluster ending with expression 
'((a - b) - c)' could yield maximum size of 10, but only width of 8 is being used.
```

## Root Cause

Arithmetic operations produce results requiring more bits than the assigned target width. Result is silently truncated.

**Width rules:** Addition: `max(widths) + 1`. Subtraction: `max(widths) + sign_bits`. Multi-operand: cascading accumulation.

**Common sources:** Insufficient output width, mixed operand widths, cascaded operations, bit-slicing (strips carry info)

**Bit-slicing caveat:** Reducing operand width via slicing doesn't reduce result precision needs. `a[5:0] - b - c` with 8-bit b,c still needs 10 bits. Often triggers ASSIGN-2 as well (unsigned slice).

## Fix Options

### Option 1: Widen Output (Recommended)

Read required width directly from Vivado message ("maximum size of N").

```verilog
// Before
output [7:0] y1;
y1 = a - b - c;       // 10-bit result truncated to 8

// After
output [9:0] y1;      // Widened to 10 bits
y1 = a - b - c;       // Full precision preserved
```

Update parent module connections to match new width.

**Width calculation helper:**
```verilog
localparam RESULT_W = $clog2(2**A_W + 2**B_W);  // Auto-calculate
```

### Option 2: Saturation/Clamping (When interface is fixed)

Compute at full width internally, then clamp to output range.

```verilog
reg [9:0] result_full;

always @(posedge clk) begin
   result_full = a - b - c;           // Full 10-bit calculation
   if (result_full[9])                // Negative (MSB set)
      y1 <= 8'h00;                    // Clamp to 0
   else if (result_full > 255)        // Exceeds 8-bit max
      y1 <= 8'hFF;                    // Clamp to 255
   else
      y1 <= result_full[7:0];         // Use exact value
end
```

**Signed saturation variant:**
```verilog
if (result_full > 127)       y1 <= 8'h7F;
else if (result_full < -128) y1 <= 8'h80;
else                         y1 <= result_full[7:0];
```

### Decision Guide

| Scenario | Fix |
|----------|-----|
| Full precision needed / interface changeable | Option 1: Widen |
| Fixed interface (IP, standard) | Option 2: Saturate |
| Intentional modulo arithmetic | Suppress with comment |

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-1 count = 0
report_utilization  # Widened: ~same LUTs. Saturate: +5-10 LUTs for comparators
```

## References
**Xilinx:** UG901 Ch.4 "Arithmetic Operators", UG949 "RTL Coding Best Practices"  
**IEEE:** 1364-2005 §5.5, 1800-2017 §11.6 (Expression bit-width determination)
