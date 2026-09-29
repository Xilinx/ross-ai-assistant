<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# RESET-2: Incomplete Asynchronous Reset Coverage

**Rule:** RESET-2 | **Severity:** WARNING | **Standard:** UG901 Ch.2

## Vivado Message
```
WARNING: [Synth 8-XXX] [RESET-2] Register 'temp' in always/process block
lacks asynchronous reset while other registers have it. File 'top.v', Line 30.
```

## Root Cause

Register(s) in async reset block lack reset assignment while other registers in the same block are reset — causing inconsistent reset behavior, X-states in simulation, and unpredictable power-on values.

**Common mistakes:** Copy-paste without updating reset, incremental signal additions, partial bit reset

**Rule:** All registers in same block must have **consistent reset behavior** (all async OR all sync, never mixed).

## Fix: Complete Async Reset Coverage

```verilog
// Before: RESET-2 violation
always @(posedge clk or posedge rst) begin
   if(rst) begin
      out1 <= 1'b0;     // Has async reset
      out2 <= 1'b0;     // Has async reset
                        // temp missing!
   end else begin
      out1 <= in1;
      out2 <= temp;
      temp <= in2;      // Assigned but not reset
   end
end

// After: Complete coverage
always @(posedge clk or posedge rst) begin
   if(rst) begin
      out1 <= 1'b0;
      out2 <= 1'b0;
      temp <= 1'b0;     // FIXED: Added async reset
   end else begin
      out1 <= in1;
      out2 <= temp;
      temp <= in2;
   end
end
```

### Multi-bit Registers
```verilog
// WRONG: Partial reset
if(rst) counter[7:4] <= 4'b0;  // Upper bits only!

// RIGHT: Complete reset
if(rst) counter <= 8'b0;       // Full width
```

## VHDL Pattern

```vhdl
-- Before: q1_r missing from reset
process(clk, rst) begin
   if(rst = '1') then
      q <= '0'; q1 <= '0';           -- q1_r missing!
   elsif rising_edge(clk) then
      q <= d; q1 <= q1_r; q1_r <= not d;
   end if;
end process;

-- After: Complete coverage
process(clk, rst) begin
   if(rst = '1') then
      q <= '0'; q1 <= '0'; q1_r <= '0';  -- All registers reset
   elsif rising_edge(clk) then
      q <= d; q1 <= q1_r; q1_r <= not d;
   end if;
end process;
```

## Validation

```tcl
synth_design -top <module> -part <part> -lint
# Verify: RESET-2 = 0
report_utilization -hierarchical
# Expect: All same FF type (FDRE/FDCE) — no mixed primitives
```

## References
**Xilinx:** UG901 Ch.2 "Reset Coding Styles", UG906 Appendix B, UG949 "Reset Distribution", AR# 53273  
**IEEE:** 1364-2005 §9.2.2, 1800-2017 §9.4.2, 1076-2008 §8.1  
**Related:** RESET-1 (multiple async resets), RESET-3 (incomplete sync reset)
