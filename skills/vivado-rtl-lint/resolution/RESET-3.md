<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# RESET-3: Incomplete Synchronous Reset Coverage

**Rule:** RESET-3 | **Severity:** CRITICAL WARNING | **Standard:** UG901 Ch.2

## Vivado Message
```
CRITICAL WARNING: [Synth 8-XXX] [RESET-3] Register 'out2' lacks reset assignment
while other registers in same block are reset. File 'top.v', Line 25.
```

## Root Cause

Register in synchronous reset block lacks reset assignment while other registers in the same block are reset — causing unpredictable initial state and non-deterministic post-reset behavior.

**Common mistakes:** Copy-paste without updating reset, **missing `begin...end`** (only first statement runs), incremental signal additions

## Fix: Add Reset for All Registers

```verilog
// Before: RESET-3 violation
always @(posedge clk) begin
   if(rst)
      out1 <= 0;        // out1 reset
   else if (en) begin
      out1 <= in1;
      out2 <= in2;      // out2 NOT reset
   end 
end

// After: All registers reset (note begin...end)
always @(posedge clk) begin
   if(rst) begin         // begin...end for multiple statements!
      out1 <= 0;
      out2 <= 0;         // FIXED: out2 now reset
   end
   else if (en) begin
      out1 <= in1;
      out2 <= in2;
   end 
end
```

**Critical pitfall:** Without `begin...end`, only the first statement after `if(rst)` executes — remaining registers silently miss reset.

## VHDL Pattern

```vhdl
-- Before: out2 not reset
process(clk) begin
   if rising_edge(clk) then
      if rst = '1' then
         out1 <= '0';     -- Only out1 reset
      elsif en = '1' then
         out1 <= in1;
         out2 <= in2;     -- out2 NOT reset
      end if;
   end if;
end process;

-- After: Both registers reset
process(clk) begin
   if rising_edge(clk) then
      if rst = '1' then
         out1 <= '0';
         out2 <= '0';     -- FIXED
      elsif en = '1' then
         out1 <= in1;
         out2 <= in2;
      end if;
   end if;
end process;
```

## Validation

```tcl
synth_design -top <module> -part <part> -lint
# Verify: RESET-3 = 0
```

## References
**Xilinx:** UG901 Ch.2 "Synchronous Reset Coding", UG906 Appendix B, AR# 52988  
**Related:** RESET-1 (multiple async resets), RESET-2 (incomplete async reset)
