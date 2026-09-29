<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# INFER-1: Latch Inference

**Rule:** INFER-1 | **Severity:** CRITICAL WARNING | **Standard:** UG901 Ch.4.3.1

## Vivado Message
```
CRITICAL WARNING: [Synth 8-327] [INFER-1] Latch inferred for signal 'out1_reg'.
RTL Name 'out1', Hierarchy 'top', File 'top.v', Line 11.
```
**Parse:** Strip `_reg` suffix from signal name for RTL correlation.

## Root Cause

Combinational `always@(*)` blocks with incomplete signal assignments create unintended latches — causing timing failures, glitch propagation, and simulation-synthesis mismatch.

## Pattern Recognition Table

| ID | Pattern | Root Cause |
|----|---------|------------|
| P1 | `if(c) s<=v;` (no else) | Missing else |
| P2 | `case(s) ... endcase` (no default) | Missing default |
| P3 | `else s=s;` | Self-reference |
| P4 | `if(e) begin r1=a; r2=b; end` | Variable assigned in one path only |
| P5 | `assign o=e?v:o;` | Continuous assign self-ref |
| P6 | `o<=e?r:i; r<=e?o:i2;` | Circular dependency |
| P7 | `o<=0; case(s)... endcase` | Pre-init false security |
| P8 | `output reg o=0; ... case` | Port init false security |
| P9 | `o[3:2]<=0; case ... o<=v` | Partial bits + incomplete |
| P10 | Comb block + unused clk | Sequential intent unclear |
| P11 | `<=` in `always@(*)` | Wrong assignment type |
| P12 | UDP with hold state (`-`) | Level-sensitive primitive latch |

**Critical notes:**
- **P7/P8:** Pre-initialization does NOT prevent latches — must have explicit `else`/`default`
- **P3/P5:** Self-reference (`sig=sig` or `?v:sig`) creates same latch as missing else
- **P12:** UDP hold state (`-`) is level-sensitive latch — change to constant or edge notation
- **P1/P2:** Cover ~80% of cases — check these first

## Fix Options

### Option 1: Complete Combinational Coverage (Default — no value retention needed)

```verilog
// P1 fix: Missing else
always@(*) begin
   if(en) out1 = in1;
   else   out1 = 1'b0;    // Add explicit else
end

// P2 fix: Missing default
always@(*) begin
   case(sel)
      2'b00: out = a;  2'b01: out = b;  2'b10: out = c;
      default: out = 1'b0;              // Add default
   endcase
end

// P3/P5 fix: Self-reference
// WRONG: else out = out;   ← still a latch!
// RIGHT: else out = 1'b0;  ← constant, no latch
```

**Rules:** Use blocking `=` (not `<=`) in combinational blocks. Never self-reference. Use `always_comb` (SV) for compile-time latch detection.

### Option 2: Convert to Sequential (Value retention intended)

```verilog
// BEFORE: always@(*) begin if(en) out1 <= in1; end    ← latch
// AFTER:
always@(posedge clk) begin
   if(en) out1 <= in1;  // Register holds when en=0
end
```

**Use when:** Signal must retain value between updates. Keep `<=` (non-blocking). Add reset if needed.

### P12 Fix: UDP Latch

```verilog
// Option A: Change hold to clear (minimal)
table
     0   ?  : ?   : 0  ;  // Force 0 instead of hold (-)
     1   0  : ?   : 0  ;
     1   1  : ?   : 1  ;
endtable

// Option B: Convert to edge-sensitive FF
table
    (01) 0  : ?   : 0  ;  // Rising edge triggered
    (01) 1  : ?   : 1  ;
    (0?) ?  : ?   : -  ;  // Non-edge hold OK in sequential
    (?0) ?  : ?   : -  ;
endtable
```
If latch is intentional, use `create_waiver` instead.

## Validation

```tcl
synth_design -top <module> -part <part> -lint
# Verify: INFER-1 count = 0
get_cells -hierarchical -filter {REF_NAME =~ LD*}
# Expected: {} (no LD/LDC/LDE/LDCE primitives)
```

## References
**Xilinx:** UG901 Ch.4.3.1, UG949 RTL guidelines, UG906 TIMING-10, Answer 41851  
**IEEE:** 1364-2005 §9.5, 1800-2017 §9.2.2  
**Related:** INFER-2 (incomplete case), ASSIGN-5 (undriven signal)
