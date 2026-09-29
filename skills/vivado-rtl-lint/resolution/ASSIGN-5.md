<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-5: Undriven Signal

**Rule:** ASSIGN-5 | **Severity:** WARNING | **Impact:** Functional bug (X-propagation)

## Vivado Message
```
WARNING: [Synth 37-95] [ASSIGN-5] Some bits in '(signal_name)' are not set. 
First unset bit index is '(bit_index)'.
RTL Name '(signal_name)', Hierarchy '(module)', File '(file.v)', Line (N).
```

## Root Cause

Signal declared but never assigned a value. Produces X in simulation and undefined hardware behavior. **Higher priority than ASSIGN-6** — this is a functional bug, not just dead code.

**Common sources:** Missing driver, incomplete struct initialization, conditional assignment gaps (missing else/default)

## Fix Options

**Detection step (mandatory):** Check if signal is actually used:
```bash
grep -n "signal_name" file.v | wc -l
```
- **1 match** (declaration only) → Option 1: Remove
- **2+ matches** (declaration + usage) → Option 2: Add assignment

Show ONLY the applicable fix in the report.

### Option 1: Remove Unused Signal

```diff
-wire [7:0] w1;    // Never assigned or used — remove
```

### Option 2: Add Missing Assignment

```verilog
// Before
wire [8:0] sum;
assign y1 = sum[6:0];     // sum is undefined

// After
wire [8:0] sum;
assign sum = {2'b0, a} + {2'b0, b};    // Added driver
assign y1 = sum[6:0];
```

For sequential logic, ensure all paths assign the signal and add reset values.

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-5 count = 0
report_drc -ruledeck default_checks
```

## References
**Xilinx:** UG901 "Signal Assignment", UG949  
**Related:** ASSIGN-6 (opposite: assigned but not read), ASSIGN-10 (unused ports)
