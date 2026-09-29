<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-14: Duplicate Case Branches

**Rule:** ASSIGN-14 | **Severity:** CRITICAL WARNING | **Standard:** IEEE 1800-2017 §12.5

## Vivado Message
```
CRITICAL WARNING: [Synth 37-140] [ASSIGN-14]
Duplicate case branches found. Hierarchy 'module_name', File 'design.sv', Line XX.
```

## Root Cause

Case statement contains duplicate case items (same selector value multiple times). Only first match executes; subsequent duplicates are unreachable dead code.

**Common sources:** Copy-paste errors during FSM development, typo in case item values, attempting conditional logic via duplicate items

## Fix Options

### Option 1: Remove Duplicates (First match is correct)

```verilog
// Before
case(state)
   4'b1000 : next = 4'b1001;  // Executes
   4'b1000 : next = 4'b1010;  // Dead code
endcase

// After
case(state)
   4'b1000 : next = 4'b1001;
   default : next = 4'b0000;
endcase
```

### Option 2: Correct State Values (Sequential FSM copy-paste error)

```verilog
// Before
4'b1000 : next = 4'b1001;  // State 8 → 9
4'b1000 : next = 4'b1010;  // Should be 4'b1001
4'b1000 : next = 4'b1011;  // Should be 4'b1010

// After
4'b1000 : next = 4'b1001;  // State 8  → 9
4'b1001 : next = 4'b1010;  // State 9  → 10
4'b1010 : next = 4'b1011;  // State 10 → 11
```

### Option 3: Add Conditional Logic (Transition depends on input)

```verilog
// Before: attempted conditional via duplicate
STATE_WAIT : next = STATE_PROC;  // Executes
STATE_WAIT : next = STATE_IDLE;  // Dead code

// After: nested if-else
STATE_WAIT : begin
   if (enable) next = STATE_PROC;
   else        next = STATE_IDLE;
end
```

Always add `default` clause when removing duplicates (prevents INFER-2).

## VHDL Pattern

```vhdl
-- Before: duplicate
when "1000" => nextState <= "1001";
when "1000" => nextState <= "1010";

-- After: corrected
when "1000" => nextState <= "1001";
when "1001" => nextState <= "1010";
when others => nextState <= "0000";
```

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-14 count = 0
```

## References
**Xilinx:** UG901 Ch.3, UG906 Appendix B  
**IEEE:** 1364-2005 §9.5, 1800-2017 §12.5  
**Related:** INFER-2 (incomplete case — add default when removing duplicates)
