<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-3: Shift Overflow

**Rule:** ASSIGN-3 | **Severity:** CRITICAL WARNING | **Standard:** IEEE 1364-2005 §5.1.13

## Vivado Message
```
CRITICAL WARNING: [Synth 37-92] [ASSIGN-3]
Right-hand side of operator >> in expression (signal >> SHIFT_AMOUNT) 
exceeds the range of left-hand side. LHS width: N, RHS value: M.
```

## Root Cause

Shift amount ≥ signal bit width. Per IEEE 1364-2005 §5.1.13, logical shift by amount ≥ width always produces zero — creating dead logic or incorrect conditionals.

**Common sources:** Parameter misconfiguration, copy-paste from different-width design, missing width validation

## Fix Options

### Option 1: Correct Shift Parameter

```verilog
// Before
parameter SHIFT_AMOUNT = 30;          // Exceeds 13-bit width
if (data_in >> SHIFT_AMOUNT) ...      // Always 0

// After
parameter SHIFT_AMOUNT = 2;           // Valid range: 0 to width-1
if (data_in >> SHIFT_AMOUNT) ...      // Functionally correct
```

### Option 2: Add Validation (Parameterized designs)

```verilog
localparam WIDTH = $bits(din);
localparam MAX_SHIFT = WIDTH - 1;
localparam SAFE_SHIFT = (SHIFT > MAX_SHIFT) ? MAX_SHIFT : SHIFT;

assign dout = din >> SAFE_SHIFT;

initial if (SHIFT > MAX_SHIFT)
   $warning("SHIFT (%0d) exceeds MAX (%0d), clipped", SHIFT, MAX_SHIFT);
```

**Runtime variable shifts:**
```verilog
assign dout = (shift_amt >= WIDTH) ? 0 : (din >> shift_amt);
```

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-3 count = 0
```

## References
**Xilinx:** UG901 Ch.3 "Shift Operators", UG906 Appendix B  
**IEEE:** 1364-2005 §5.1.13, 1800-2017 §11.4.10
