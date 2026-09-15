<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ASSIGN-6: Dead Code / Unused Signal

**Rule:** ASSIGN-6 | **Severity:** WARNING | **Standard:** UG901

## Vivado Message
```
WARNING: [Synth 37-124] [ASSIGN-6] Signal 'unread_wire' was assigned but not read. 
First unread bit index is 0. RTL Name 'unread_wire', Hierarchy 'top', File 'top.v', Line 12.
```

## Root Cause

Signal is assigned but never read. Dead code with no functional impact — indicates incomplete implementation, refactoring artifacts, or leftover debug logic.

## Fix: Remove Unused Signal

1. Verify signal is truly unused (search entire file for read references)
2. Remove all assignments to the signal
3. Remove signal declaration
4. Clean up empty blocks left behind
5. Check for **signal chains**: if this signal was the only reader of another signal, that upstream signal may now trigger ASSIGN-6 too

```diff
-wire [7:0] wire1;
-assign wire1 = b[0];      // Dead code — never read

 always@(posedge clk) begin
    result <= a + b;
 end
```

**Chain cleanup example:**
```verilog
// If temp3 is unused, and temp2 feeds only temp3, and temp1 feeds only temp2:
// Remove entire chain: temp1, temp2, temp3
```

If downstream signal has other readers, stop cleanup at that point.

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: ASSIGN-6 count = 0
# Check: No new ASSIGN-5 violations from broken chains
```

## References
**Xilinx:** UG901 "Unused Logic Optimization", UG949  
**Related:** ASSIGN-5 (opposite: used but not assigned)
