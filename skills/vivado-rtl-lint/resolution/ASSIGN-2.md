<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-2: Mixed Signed/Unsigned Arithmetic

**Rule:** ASSIGN-2 | **Severity:** CRITICAL WARNING | **Standard:** IEEE 1364-2005 §4.1

## Vivado Message
```
CRITICAL WARNING: [Synth 37-81] [ASSIGN-2]
Operator '+' in expression '((a + b) + c)' has mixed signed and unsigned types 
on its operands, the result will be unsigned.
```

## Root Cause

Arithmetic combines signed and unsigned operands. Per Verilog: if ANY operand is unsigned, ALL promote to unsigned — signed values lose sign information.

**Common sources:** Port declaration inconsistency, implicit unsigned defaults, unsigned literals (`8'd5` vs `8'sd5`), bit-slicing (always strips signedness)

**Bit-slicing caveat:** `c[6:0]` is always unsigned even if `c` is signed. Cast explicitly: `$signed(c[6:0])`.

## Fix: Explicit `$signed()` Casting

Cast unsigned operands to signed. Zero hardware overhead (semantic-only).

```verilog
// Before: mixed signedness
input [6:0] a;            // Unsigned
input signed [6:0] b;     // Signed
result = a + b + c;       // All promote to unsigned — WRONG for negatives

// After: explicit casting
result = $signed(a) + b + c;  // All operands now signed — CORRECT
```

**Multiple unsigned operands:** Cast each one:
```verilog
result = $signed(a) + $signed(b) + c;
```

**Operator coverage:** Works for `+`, `-`, `*`, `>>>`, comparisons.

**Optional:** Declare outputs as signed for downstream correctness:
```verilog
output signed [7:0] y1;
```

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-2 count = 0
```

Test with negative values to verify correct arithmetic. `$signed()` adds zero LUT/FF overhead.

## References
**Xilinx:** UG901 "Signed and Unsigned Arithmetic", UG949  
**IEEE:** 1364-2005 §4.1-4.2, 1800-2017 §11.8
