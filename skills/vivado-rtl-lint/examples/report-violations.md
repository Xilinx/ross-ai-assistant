<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# RTL Lint Report — Example (Violations Found)

```markdown
# RTL Lint Report

## Summary
- **Project:** my_design
- **Top Module:** top
- **Part Number:** xc7k70tfbg676-2
- **Lint Report File:** [lint_report.rpt](./lint_report.rpt)
- **Analysis Status:** ⚠️ **ISSUES FOUND**
- **Total Critical Warnings:** 1
- **Total Warnings:** 2
- **Total Info:** 0

## Vivado Lint Results

**Vivado Output:** "Total of 3 linter message(s) generated"

## Violations

### Arithmetic Overflow — ASSIGN-1

**Location**: [top.sv:42](../../../top.sv#L42)

**Issue Description**: Expression `a + b` could yield maximum size of 10 bits, but only width of 8 is being used.

**Problematic Code**:
` ` `diff
 module top(
     input  [8:0] a,
     input  [8:0] b,
-    output [7:0] result  // ERROR: 8-bit output truncates 10-bit sum
 );
-    assign result = a + b;  // ERROR: Sum of two 9-bit values needs 10 bits
` ` `

**Recommended Fix**:
` ` `diff
 module top(
     input  [8:0] a,
     input  [8:0] b,
+    output [9:0] result  // FIXED: Widened to 10 bits for full precision
 );
+    assign result = a + b;  // FIXED: No truncation
` ` `

**Rationale**: Sum of two N-bit values requires N+1 bits to avoid overflow. UG901 recommends matching output width to expression result width.

**Vivado Documentation**: UG901 Chapter 4 — Arithmetic Operations

### Latch Inference — ASSIGN-10

**Location**: [control.sv:78](../../../control.sv#L78)

**Issue Description**: Latch inferred for signal `state` due to incomplete conditional assignment.

*[... additional violations follow same template ...]*

## Recommendations

1. Address all CRITICAL WARNINGS first
2. Review latch inference issues carefully
3. Check for unused signals and optimize
4. Verify arithmetic operations for correct bit widths

## Next Steps

- Fix critical warnings before synthesis
- Re-run rtl-lint after fixes to verify resolution
```
