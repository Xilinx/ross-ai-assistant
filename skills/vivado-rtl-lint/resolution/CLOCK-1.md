<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# CLOCK-1: Mixed Clock Edges

**Rule:** CLOCK-1 | **Severity:** CRITICAL WARNING | **Standard:** UG901 Ch.3

## Vivado Message
```
CRITICAL WARNING: [Synth 37-94] [CLOCK-1]
Module '(module_name)' has registers with different clock edges.
Clock name: '(clock_signal)'. First register: '(reg1)'. Second register: '(reg2)'.
```

## Root Cause

Module has registers clocked by both `posedge` and `negedge` of the same clock. Increases control sets, reduces placement flexibility, and complicates timing analysis.

**Common sources:** Unintentional mixing (99% of cases), copy-paste from different timing domain

## Fix: Change Edge (One-line change)

Change `negedge` → `posedge` on the offending always block. Do NOT merge blocks or change assignment styles — keep the fix minimal.

```diff
  always@(posedge clk)
    y1 = a;

- always@(negedge clk)      // Wrong edge
+ always@(posedge clk)      // FIXED: unified to posedge
    y2 <= b;                // Keep original assignment style
```

**Bit-slicing pattern:**
```diff
  always@(posedge clk) y[3:0] <= a[3:0];
- always@(negedge clk) y[7:4] <= a[7:4];
+ always@(posedge clk) y[7:4] <= a[7:4];  // FIXED
```

### True DDR Design (Rare)

If both edges are intentionally required for DDR I/O, use ODDR/IDDR primitives instead of mixed always blocks:
```verilog
ODDR #(.DDR_CLK_EDGE("OPPOSITE_EDGE")) ODDR_inst (
   .Q(ddr_out), .C(clk), .D1(posedge_data), .D2(negedge_data), ...);
```
See UG953 (7-Series) or UG974 (UltraScale) for primitive details.

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: CLOCK-1 count = 0
report_control_sets -verbose
# Before: 2 control sets (posedge + negedge). After: 1
```

## References
**Xilinx:** UG901 Ch.3 "Clocking Guidelines", UG949 "Control Set Optimization"  
**Related:** CDC-1 (different clocks, not different edges)
