<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Violation Handler Reference

This file contains per-violation-type instructions for generating fix recommendations
in the RTL lint report. For **every** linter violation detected, follow the handler below
that matches the rule ID. If no handler exists, fall back to vivado_doc_search + general knowledge.

> **Mandatory**: Always load the corresponding `resolution/<RULE-ID>.md` guide with `read_file`
> before generating the fix section. The guide contains validated fix templates, rationale,
> and verification steps.

---

## Dispatch Table

| Rule ID   | Category   | Handler Summary |
|-----------|------------|-----------------|
| ASSIGN-1  | Width      | Widen output to required bit width, or apply saturation logic |
| ASSIGN-2  | Signedness | Add `$signed()` cast to unsigned operand(s) |
| ASSIGN-3  | Shift      | Correct shift parameter or add clipping validation |
| ASSIGN-5  | Unset      | Add missing assignment (wire/reg/logic) |
| ASSIGN-6  | Unused     | Remove signal declaration and all assignments |
| ASSIGN-7  | Overlap    | Adjust bit ranges to non-overlapping |
| ASSIGN-8  | Dimension  | Match array operand dimensions in comparison |
| ASSIGN-9  | Port       | Complete unassigned output port bits or remove port |
| ASSIGN-10 | Port       | Remove or reduce width of unused I/O port bits |
| ASSIGN-12 | Tri-state  | Tie port to constant or use generate loop |
| ASSIGN-14 | Case       | Remove duplicate case branches or correct values |
| INFER-1   | Latch      | Complete all branches in combinational logic |
| INFER-2   | Case       | Add default clause or complete all case branches |
| INFER-3   | Equality   | Replace `===` with `==` or document intent |
| CLOCK-1   | Clock      | Unify to single clock edge (posedge) |
| QOR-1     | Precision  | Widen intermediate signal or direct merge |
| RESET-1   | Reset      | Use single async signal (reset OR set) |
| RESET-2   | Reset      | Add async reset assignments for all registers |
| RESET-3   | Reset      | Remove conflicting reset/set — choose one |

---

## Detailed Handlers

### ASSIGN-1 — Arithmetic Overflow / Result Truncation

1. Load `resolution/ASSIGN-1.md`
2. Extract required vs current bit width from message:
   `"could yield maximum size of N, but only width of M is being used"`
3. **Primary fix** (recommended): Widen output to required bit width
   - Example: `output [7:0] result;` → `output [9:0] result;`
   - Preserves full arithmetic precision
4. **Alternative fix** (constrained interface): Apply saturation logic (Option 3 from guide)
5. Include operand analysis table showing bit widths and worst-case calculation
6. Use exact markdown formatting from resolution guide's "Fix Template for Analysis Reports"

### ASSIGN-2 — Mixed Signed/Unsigned Arithmetic

1. Load `resolution/ASSIGN-2.md`
2. Extract operator type, expression, and operand signedness
3. **Primary fix**: Add `$signed()` cast to unsigned operand(s)
   - Example: `result = a + b;` → `result = $signed(a) + b;`
   - Zero hardware overhead (semantic-only)
4. **Optional enhancement**: Declare output/reg as `signed`
5. Use exact markdown formatting from resolution guide

### ASSIGN-3 — Shift Overflow

1. Load `resolution/ASSIGN-3.md`
2. Extract shift operator (`>>` / `<<`), signal width, shift amount from message:
   `"operator >> in expression ... exceeds range. LHS width: N, RHS value: M"`
3. Valid shift range: 0 to (signal_width − 1)
4. **Primary fix** (most common): Correct shift amount parameter
   - Example: `parameter SHIFTSIZE = 30;` → `parameter SHIFTSIZE = 2;`
5. **Alternative fix**: Add parameter validation and clipping
   - `localparam SAFE_SHIFT = (SHIFT > MAX) ? MAX : SHIFT;`
6. Reference IEEE 1364-2005 Section 5.1.13
7. Use "Example Agent Output Template" from resolution guide

### ASSIGN-5 — Variable Used But Not Set

1. Load `resolution/ASSIGN-5.md`
2. Extract signal name, type, and usage location
3. **Primary fix**: Add missing assignment
   - Wire → continuous assignment; Reg → procedural in always block; Logic → always_comb/ff
   - Ensure ALL conditional paths assign the signal
4. **Priority**: Fix ASSIGN-5 **before** ASSIGN-6 (functional > code quality)

### ASSIGN-6 — Signal Assigned But Not Read

1. Load `resolution/ASSIGN-6.md`
2. **Primary fix**: Remove signal declaration and all assignments
3. Clean up empty logic blocks if they only assigned this signal

### ASSIGN-7 — Multiple Assignments to Same Bit

1. Load `resolution/ASSIGN-7.md`
2. Extract signal name, bit ranges, overlap info
3. **Primary fix**: Adjust bit ranges to non-overlapping
   - Example: `[3:0]` and `[7:3]` overlap at bit 3 → fix to `[2:0]` and `[7:3]`

### ASSIGN-8 — Array Dimension Mismatch in Comparison

1. Load `resolution/ASSIGN-8.md`
2. Extract operator, operand dimensions, and port declarations from message
3. **Primary fix**: Match operand dimensions (both 1D or both 2D)
4. **Alternative**: Flatten multidimensional array or use element-wise comparison
5. Reference IEEE 1800-2017 §11.4.5

### ASSIGN-9 — Unassigned Output Port Bits

1. Load `resolution/ASSIGN-9.md`
2. Determine scenario: completely unassigned (Scenario A) vs partially assigned (Scenario B)
3. **Scenario A** (no bits assigned): Remove port from module and update parent instantiations
4. **Scenario B** (some bits assigned): Complete the assignment — fill missing bit ranges with zero, sign-extension, or protocol-required constants

### ASSIGN-10 — Unused I/O Port Bits

1. Load `resolution/ASSIGN-10.md`
2. Identify which port bits are actually read vs unused
3. **Primary fix** (port serves no purpose): Remove unused port from module interface
4. **Alternative** (some bits used): Reduce port width to only the bits actually consumed
5. Update all parent instantiations after port changes

### ASSIGN-12 — Unconnected/Tri-State Port

1. Load `resolution/ASSIGN-12.md`
2. **Primary fix** (recommended): Tie to constant
   - `.port_name(1'bz)` → `.port_name(1'b0)` or `.port_name(1'b1)`
3. **Alternative fix**: Generate loop for multi-instance designs
4. Reference IEEE 1364-2005 Section 12.3.3

### ASSIGN-14 — Duplicate Case Branches

1. Load `resolution/ASSIGN-14.md`
2. Identify which case items share the same selector value
3. **Primary fix** (first match correct): Remove duplicate branch
4. **Alternative** (copy-paste error): Correct state values to unique, sequential encoding
5. **Alternative** (conditional logic intended): Convert duplicate to `if-else` inside single case item
6. Always add `default` clause when modifying (prevents INFER-2)

### INFER-1 — Latch Inference

1. Load `resolution/INFER-1.md`
2. Identify pattern from the Pattern Recognition Table (P1–P12)
3. **Primary fix** (no value retention needed): Complete combinational coverage — add `else`/`default` branch
   - Use blocking `=` (not `<=`) in combinational blocks
   - Never self-reference (`sig = sig` still creates a latch)
4. **Alternative** (value retention intended): Convert to sequential logic with `posedge clk`
5. **P7/P8 note**: Pre-initialization does NOT prevent latches — explicit `else`/`default` required
6. Cross-check: INFER-1 often pairs with INFER-2 — fixing the case statement may resolve both

### INFER-2 — Incomplete Case Statement

1. Load `resolution/INFER-2.md`
2. Identify pattern from Pattern Recognition Table (C1–C11)
3. **Primary fix**: Add `default` clause with safe output value
4. **Alternative** (full coverage needed): Enumerate all case branches explicitly
5. Cross-check: Fixing INFER-2 typically resolves paired INFER-1 violations on the same signals
6. **C9 note**: `full_case`/`parallel_case` pragmas do NOT substitute for `default`

### INFER-3 — Case Equality Conversion

1. Load `resolution/INFER-3.md`
2. `===` (4-state case equality) is converted to `==` for synthesis
3. **Primary fix** (X/Z not needed): Replace `===` with `==` explicitly to match synthesis behavior
4. **Alternative** (simulation debug): Keep `===` and wrap in `translate_off`/`translate_on` guard
5. **Concern**: May cause simulation-synthesis mismatch if design relies on X/Z comparison
6. Reference IEEE 1800-2017 §11.4.5 (Equality operators)

### CLOCK-1 — Mixed Clock Edges

1. Load `resolution/CLOCK-1.md`
2. Identify which always blocks use `negedge` vs `posedge` of the same clock
3. **Primary fix**: Change `negedge` → `posedge` on the minority block (minimal change)
   - Do NOT merge always blocks or change assignment styles
4. **DDR exception** (rare): If both edges are intentional for DDR I/O, use ODDR/IDDR primitives instead
5. Verify: After fix, `report_control_sets` should show reduced control set count

### QOR-1 — Arithmetic Precision Loss

1. Load `resolution/QOR-1.md`
2. Trace arithmetic chain to identify precision loss point
3. **Primary fix** (recommended): Widen intermediate signal
   - Allows Vivado to merge operators for optimal QoR
4. **Alternative**: Direct merge (bypass intermediate signal)
5. **Alternative**: Explicit truncation with intent comment
6. Reference IEEE 1364-2005 Section 4.1.14

### RESET-1 — Multiple Async Reset/Set

1. Load `resolution/RESET-1.md`
2. **Primary fix**: Use single async signal (reset OR set, not both)

### RESET-2 — Incomplete Async Reset Coverage

1. Load `resolution/RESET-2.md`
2. **Primary fix**: Add async reset assignments for all registers in the process

### RESET-3 — Async Reset/Set Conflict

1. Load `resolution/RESET-3.md`
2. **Primary fix**: Remove conflict — choose reset OR set based on design intent
   - Standard practice: async reset to 0 is most common

---

## Resolution Guide Workflow (Generic)

```
For each violation with rule ID <RULE-ID>:
  1. Load: resolution/<RULE-ID>.md
  2. Parse linter message → extract signal, width, file:line, expression
  3. Read source code at file:line for context
  4. Select fix option from guide (Primary recommended)
  5. Generate markdown section using guide's fix template
  6. Include rationale and verification steps from guide
  7. Add UG901/IEEE documentation references
```

## Handling Unknown Violations

If no resolution guide exists for a rule ID:
1. Use vivado_doc_search to query UG901 for the violation type
2. Note in report: "No resolution guide available for [RULE-ID]"
3. Provide best-effort fix based on Vivado documentation
4. Consider creating a new `resolution/<RULE-ID>.md` for future use

## Future Extensibility

- Add new guides to `resolution/` following naming convention: `<RULE-ID>.md`
- Each guide should include: Problem Statement, Detection Pattern, Standard Resolution, Fix Template
- Current categories: ASSIGN-*, INFER-*, CLOCK-*, QOR-*, RESET-*
- Potential future categories: TIMING-*, CDC-*, SYNTH-*
