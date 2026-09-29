<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# RESET-1: Multiple Asynchronous Resets

**Rule:** RESET-1 | **Severity:** CRITICAL WARNING | **Standard:** UG901 Ch.2 | **Correlation:** ROAS-1

## Vivado Message
```
CRITICAL WARNING: [Synth 8-XXX] [RESET-1] Multiple asynchronous resets specified
for register 'reg_name'. Hierarchy 'top', File 'top.v', Line 42.
```

## Root Cause

Multiple async controls (reset/set) in sensitivity list create race conditions and unpredictable behavior. FPGA flip-flops support a single async reset; dual async set+reset is not recommended.

**Key rule:** Sequential sensitivity list should contain **only** clock + **one** async reset (if needed).

## RESET-1 / ROAS-1 Correlation

| Rule | Tool | Level | Message |
|------|------|-------|---------|
| RESET-1 | `synth_design -lint` | RTL sensitivity list | "Multiple async resets" |
| ROAS-1 | `report_methodology` | Synthesized primitive | "Both async set and reset" |

Fix both simultaneously: single async reset.

## Fix: Single Async Reset

```verilog
// Before: Multiple async controls
always @(posedge clk or posedge rst or posedge en1) begin  // Both rst, en1 async
   if(rst) out1 <= 4'b0;
   else if(en1) out1 <= 4'hF;     // Race if rst=1, en1=1
   else out1 <= in1;
end

// After: Single async reset
always @(posedge clk or posedge rst) begin                 // Only rst async
   if(rst) out1 <= 4'b0;
   else if(en1) out1 <= 4'hF;     // Now synchronous
   else out1 <= in1;
end
```

### Active-Low Variant
```verilog
always @(posedge clk or negedge rstn) begin
   if(!rstn) out1 <= 4'b0;
   else if(en1) out1 <= 4'hF;     // Synchronous
   else out1 <= in1;
end
```

## VHDL Pattern

```vhdl
-- Before: Multiple async controls
process (rst, clk, en1) begin
   if(rst = '1') then out1 <= (others => '0');
   elsif(en1 = '1') then out1 <= (others => '1');  -- Async set
   elsif rising_edge(clk) then out1 <= in1;
   end if;
end process;

-- After: Single async reset
process (clk, rst) begin
   if(rst = '1') then out1 <= (others => '0');
   elsif rising_edge(clk) then
      if(en1 = '1') then out1 <= (others => '1');  -- Now synchronous
      else out1 <= in1;
      end if;
   end if;
end process;
```

## Validation

```tcl
synth_design -top <module> -part <part> -lint
# Verify: RESET-1 = 0
synth_design -top <module> -part <part> -rtl
report_methodology
# Verify: ROAS-1 = 0
report_utilization -hierarchical
# Expect: FDRE/FDCE (not FDPE — async preset indicates async set still present)
```

## References
**Xilinx:** UG901 Ch.2 "Async Reset Coding", UG906 Appendix B, UG949 "Reset Strategies", AR# 52988  
**IEEE:** 1364-2005 §9.2, 1800-2017 §9.4  
**Related:** RESET-2 (incomplete async coverage), RESET-3 (incomplete sync coverage), ROAS-1
