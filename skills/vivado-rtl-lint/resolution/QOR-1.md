<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# QOR-1: Arithmetic Precision Loss

**Rule:** QOR-1 | **Severity:** WARNING | **Standard:** UG901 Ch.4

## Vivado Message
```
WARNING: [Synth 37-74] [QOR-1]
Arithmetic Operator '(in1 + in2)' resulted in loss of precision. 
This prevented merging optimization to its load operator '(temp + in3)' 
from happening and may degrade QoR. 
Full precision width of preceding operator is 11, however only 10 bits are selected.
```

## Root Cause

Intermediate arithmetic result truncated before the next operation, preventing the synthesizer from merging operator trees. Results in separate adder stages instead of a single merged tree — increased logic depth, larger area, worse timing.

**Common sources:** Intermediate signal sized for final output (not for computation), copy-paste from different-width design

## Fix Options

### Option 1: Preserve Full Precision (When intermediate signal needed)

```verilog
// Before: truncates carry bit
wire [P1-1:0] w1;              // 10 bits — too narrow
assign w1 = in1 + in2;        // 11-bit result truncated
assign out = w1 + in3;        // Cannot merge with previous

// After: full precision intermediate
wire [P1:0] w1_full;           // 11 bits — preserves carry
assign w1_full = in1 + in2;   // Full result
assign out = w1_full + in3;   // Can now merge operators
```

**Auto-calculate width:**
```verilog
localparam W1_WIDTH = P1 + 1;
wire [W1_WIDTH-1:0] w1_full;
```

### Option 2: Remove Intermediate (When signal not needed elsewhere)

```verilog
// Before
wire [9:0] w1 = in1 + in2;      // Unnecessary intermediate
wire [10:0] w2 = w1 + in3;

// After: single merged expression
wire [10:0] w2 = (in1 + in2) + in3;  // Single operator tree
```

### Decision Guide

| Scenario | Fix |
|----------|-----|
| Intermediate used elsewhere | Option 1: Widen intermediate |
| Intermediate only feeds next op | Option 2: Remove intermediate |
| Truncation architecturally required | Keep but use full precision for next op |

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: QOR-1 count = 0
report_utilization -hierarchical       # Expect LUT reduction
report_design_analysis -logic_level_distribution  # Expect reduced logic levels
```

## References
**Xilinx:** UG901 Ch.4 "Arithmetic Operators", UG906 Appendix B, UG949 "Operator Merging"  
**IEEE:** 1364-2005 §5.5, 1800-2017 §11.4.4  
**Related:** ASSIGN-1 (arithmetic overflow), ASSIGN-2 (width mismatch)
