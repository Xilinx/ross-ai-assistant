<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-10: Unused I/O Port Bits

**Rule:** ASSIGN-10 | **Severity:** WARNING | **Standard:** UG901 Ch.4, IEEE 1364-2005 §12.3

## Vivado Message
```
WARNING: [Synth 37-125] [ASSIGN-10]
Some bits in IO '(port_name)' are not read. First unread bit index is '(bit_index)'.
RTL Name '(port_name)', Hierarchy '(module_name)', File '(file.v)', Line (line_num).
```

## Root Cause

Port declared but never used (or only some bits used) in design logic. Wastes routing resources and adds unnecessary interface complexity.

**Common sources:** Leftover ports from earlier design iteration, copy-paste from reference design, port width too large for actual usage

## Fix Options

### Option 1: Remove Unused Port (When port serves no purpose)

```diff
 module data_processor(
    input clk,
    input [7:0] data_in,
-   input [7:0] control,      // Never read — remove
    output reg [7:0] result
 );
```

**Update all parent instantiations:**
```diff
-data_processor u1(.clk(clk), .data_in(din), .control(ctrl), .result(res));
+data_processor u1(.clk(clk), .data_in(din), .result(res));
```

### Option 2: Reduce Port Width (When only some bits used)

```diff
 module byte_processor(
-   input [7:0] config,       // Only bits [1:0] actually used
+   input [1:0] config,       // Reduced to actual usage
    input [7:0] data,
    output reg [7:0] result
 );
```

Update parent instantiations to pass only used bits: `.config(cfg[1:0])`.

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-10 count = 0
# Search for all instantiations of modified module:
grep -r "module_name" src/ --include="*.v" --include="*.sv"
```

## References
**Xilinx:** UG901 Ch.4 "Port Declarations", UG949 "Interface Optimization"  
**IEEE:** 1364-2005 §12.3, 1800-2017 §23.2.2  
**Related:** ASSIGN-6 (signal not read), ASSIGN-9 (unassigned output)
