<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# INFER-2: Incomplete Case Statement

**Rule:** INFER-2 | **Severity:** CRITICAL WARNING | **Standard:** UG901 Ch.4.3.5

## Vivado Message
```
CRITICAL WARNING: [Synth 37-75] [INFER-2] Case statement items not fully specified
and does not have a default item. Hierarchy 'top_rtl', File 'top.sv', Line 40.
```

## Root Cause

Case statements without complete value coverage and no `default` clause create undefined behavior — leading to latch inference (triggers INFER-1) and simulation-synthesis mismatch.

**Common sources:** Missing default, FSM with unused states, commented-out branches, sparse encoding, parameter-based case exceeding branches

## Pattern Recognition Table

| ID | Pattern | Root Cause |
|----|---------|------------|
| C1 | `case(sel) 2'b0:...; endcase` (no default) | Missing default clause |
| C2 | FSM with N-1 states only | Incomplete state coverage |
| C3 | `case(state) p1:...; // p16: ...; endcase` | Commented branch (debug) |
| C4 | 3-bit selector, 5 branches, no default | Sparse encoding incomplete |
| C5 | `typedef enum {A,B,C}; case(e) A:...; B:...; endcase` | Enum subset handling |
| C6 | `case(WIDTH_PARAM[3:0]) 4'd8:...; 4'd16:...; endcase` | Parameter can exceed branches |
| C7 | One-hot `case(1'b1) state[0]:...; state[3]:...; endcase` | Assumes exclusivity, no error case |
| C8 | `case(bus[7:0]) 8'h0A:...; 8'hFF:...; endcase` | Bus too wide for branches |
| C9 | `case(sel) /* synthesis full_case */` | Pragma without actual coverage |
| C10 | Casez with don't-cares, gaps in coverage | Don't-care logic incomplete |
| C11 | Case in generate block with parameter | Generate-time incomplete |

**Critical notes:**
- **C9:** `full_case`/`parallel_case` pragmas are synthesis directives only — still need explicit `default`
- **C7:** One-hot case should add `default` to catch multi-hot errors
- **C3:** Commented branches during debug leave incomplete coverage

## Fix Options

### Option 1: Add Default Clause (Most common)

```verilog
// BEFORE
always@(*) begin
   case(sel)
      2'b00: out = a;
      2'b01: out = b;
   endcase  // Missing 2'b10, 2'b11
end

// AFTER
always@(*) begin
   case(sel)
      2'b00: out = a;
      2'b01: out = b;
      default: out = 8'h00;  // Safe value for unspecified encodings
   endcase
end
```

### Option 2: Complete All Branches (Full coverage needed)

```verilog
// FSM: enumerate all states + safety default
always@(*) begin
   case(state)
      p1:  nextState = p2;
      p2:  nextState = p3;
      p15: nextState = p1;
      p16: nextState = p2;      // Explicitly handle all states
      default: nextState = p1;  // Safety net (should never reach)
   endcase
end
```

### Option 3: SystemVerilog `unique case`

```verilog
always@(*) begin
   unique case(state)        // Compiler verifies completeness
      p1:  nextState = p2;
      p15: nextState = p1;
      default: nextState = p1;  // Still required
   endcase
end
```

## INFER-1 Relationship

INFER-2 often causes INFER-1 on signals assigned within case branches. Fixing INFER-2 by adding `default` typically resolves both simultaneously. Always cross-check for paired INFER-1 violations.

## Validation

```tcl
synth_design -top <module> -part <part> -lint
# Verify: INFER-2 count = 0, check INFER-1 also resolved
get_cells -hierarchical -filter {REF_NAME =~ LD*}
# Expected: {} (no latch primitives)
```

## References
**Xilinx:** UG901 Ch.4.3.5, UG949 RTL practices, UG906 SYNTH-8-155, Answer 41851  
**IEEE:** 1364-2005 §9.5, 1800-2017 §12.5 (unique/priority case)  
**Related:** INFER-1 (latch inference), ASSIGN-14 (duplicate case branches)
