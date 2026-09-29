<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# INFER-3: Case Equality Converted for Synthesis

**Rule:** INFER-3 | **Severity:** INFO | **Standard:** IEEE 1800-2017 §11.4.5

## Vivado Message
```
INFO: [Synth 37-XX] [INFER-3]
'===' is converted to '==' for synthesis.
RTL Name '(signal)', Hierarchy '(module)', File '(file.sv)', Line (line).
```

## Root Cause

The `===` (case equality) and `!==` (case inequality) operators compare all four logic
values (0, 1, X, Z). Since synthesis targets real hardware (only 0 and 1), Vivado converts
`===` → `==` and `!==` → `!=`. This is usually harmless, but can cause simulation-synthesis
mismatch if the design intentionally tests for X or Z states.

**Common sources:** Copy-paste from testbench code, overly cautious coding style,
leftover assertions or debug comparisons in RTL

## Fix Options

### Option 1: Replace with `==` (Recommended — No X/Z intent)

Most RTL should use `==` since synthesis cannot represent X/Z. Explicit replacement
documents the intent and eliminates the info message.

```verilog
// Before
always_comb begin
   if (data === 8'hFF)        // 4-state comparison — unnecessary in synthesis
      valid = 1'b1;
end

// After
always_comb begin
   if (data == 8'hFF)         // 2-state comparison — matches synthesis behavior
      valid = 1'b1;
end
```

### Option 2: Keep `===` with Intent Comment (Simulation debug code)

If `===` is deliberately used for simulation-only X-detection (e.g., checking for
uninitialized values), keep it but document the purpose:

```verilog
// Intentional: detect uninitialized bus in simulation
// synthesis translate_off
always @(posedge clk) begin
   if (data_bus === 8'hxx)
      $warning("Uninitialized data_bus at time %0t", $time);
end
// synthesis translate_on
```

**Best practice:** Wrap simulation-only `===` checks in `translate_off`/`translate_on`
or `` `ifdef SIMULATION`` guards so they don't appear in synthesis at all.

### Option 3: Use `inside` for Value Matching (SystemVerilog)

For matching against multiple valid values, `inside` is cleaner and has well-defined
synthesis behavior:

```verilog
// Before
if (state === IDLE || state === DONE)

// After
if (state inside {IDLE, DONE})    // No X/Z ambiguity
```

## Decision Guide

| Scenario | Fix |
|----------|-----|
| Normal RTL comparison | Option 1: Replace with `==` |
| Simulation debug / X-detection | Option 2: Keep with `translate_off` guard |
| Multi-value matching | Option 3: Use `inside` |
| Intentional and must stay | Suppress with comment, accept INFO |

## Simulation-Synthesis Mismatch Risk

| Operator | Simulation (X input) | Synthesis | Mismatch? |
|----------|---------------------|-----------|-----------|
| `===`    | Returns 0 (X ≠ val) | `==` behavior | Possible if X expected |
| `==`     | Returns X           | Returns 0 or 1 | Possible if X expected |

In practice, for signals that are always 0 or 1 in hardware, `===` and `==` produce
identical results. The risk only exists when X/Z states are meaningful (testbench,
uninitialized memory, tri-state buses).

## Validation

```tcl
synth_design -top <module> -part <part> -lint -file lint_recheck.rpt
# Verify: INFER-3 count = 0 (or accepted with documentation)
# Verify simulation still passes after replacement:
#   launch_simulation -mode behavioral
```

## References
**Xilinx:** UG901 Ch.4 "Operators", UG906 "Synthesis Report Messages"
**IEEE:** 1800-2017 §11.4.5 (Case equality), §11.4.6 (Wildcard equality)
**Related:** INFER-1 (latch inference), INFER-2 (incomplete case)
