---
name: hls-perf-pragma
description: Vitis HLS Performance Pragma — calculate target_ti from a throughput target, cascade through architecture and loops, and present the pragma placement table for user confirmation. Run this before placing any #pragma HLS performance.
argument-hint: <throughput-target e.g. "140 FPS" | "500 Msps" | "1 GFLOPS" | "minimize II">
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Skill: perf-pragma

Calculate `target_ti` from the user's throughput target and produce a cascade table for review. This skill is a pure calculator — it does not run synthesis or interpret reports.

**Throughput target:** $ARGUMENTS

Print at the start:
```
─────────────────────────────────────────────────────
[perf-pragma]  flow overview
  Mode detect — Initial (Steps 0 → 5)  |  Post-synthesis re-cascade (Mode 2)
  Step 0  — Gather inputs (clock, architecture, loop structure)
  Step 1  — Calculate top-level target_ti
  Step 2  — Cascade through architecture
  Step 3  — Cascade through loops → build table
  Step 4  — Present table for review
  Step 5  — Apply pragmas to source code
─────────────────────────────────────────────────────
```

---

## Mode detection — do this first

This skill is called in two distinct modes. Identify which applies before proceeding:

| Mode | How to recognize | What to do |
|---|---|---|
| **Mode 1 — Initial** | $ARGUMENTS contains a throughput target (e.g. "140 FPS", "1 GFLOPS"). No synthesis has run yet, or top-level pragma has not been placed yet. | Run Steps 0 → 4 in full. |
| **Mode 2 — Post-synthesis re-cascade** | Called from `/csynth` after a Case 1 cascade miss. The top-level pragma already exists in the kernel source. The caller provides: the existing `target_ti` value, the stage names, and their current Target TI values from the report. | **Skip Steps 0 and 1.** Go directly to [Mode 2 procedure](#mode-2-post-synthesis-re-cascade). |

---

## Mode 2: Post-synthesis re-cascade

**Called when:** csynth Case 1 detected that one or more dataflow stages have Target TI > top-level target_ti.

**Rule:** In a dataflow pipeline, throughput = max(TI across all stages). Every stage's pragma must equal the top-level `target_ti` — not a fraction of it, not a budgeter-derived allocation. A stage pragma that is larger than `target_ti` makes that stage the throughput bottleneck.

**Procedure:**

1. Read the top-level `target_ti` from the existing pragma in the kernel source (do not recalculate).
2. From the csynth report, list every stage and its current Target TI:
   ```
   Stage                   Current Target TI   Action needed
   ─────────────────────────────────────────────────────────
   <stage_name>            <N> cycles          pragma needed if N > target_ti
   ```
3. For each stage where Current Target TI > top-level target_ti:
   - Add `#pragma HLS performance target_ti=<top_level_target_ti>` as the **first line inside that stage's function body**.
   - Use the exact same integer as the top-level pragma. Do not compute a per-stage fraction.
4. For stages where Current Target TI ≤ top-level target_ti — no pragma needed; they are already within budget.
5. After placing per-stage pragmas: go to Step 3 to cascade into loops within each newly-constrained stage (using that stage's loop trip counts).
6. Present the complete pragma placement table (stages + loops) to the user and wait for confirmation before writing any pragma.

> **Why not fractions?** Sequential architecture (Case B) splits the budget: stage1_ti + stage2_ti = total. Dataflow (Case C) does not split — it takes the max. Giving erode `target_ti=6,260,297` when the top-level is `2,164,502` means erode sets the design throughput at 6.26M cycles — a 2.88× miss.

---

## Step 0: Gather inputs

*(Mode 1 only — skip if called post-synthesis)*

Clock frequency is already known from `/setup` (use `CLOCK_NS` → `clock_hz = 1e9 / CLOCK_NS`).

Ask the user for:

1. **Architecture type** — single pipelined function, sequential functions, or dataflow pipeline?
2. **Loop structure** — how many levels of nesting, and what are the trip counts at each level?

The throughput requirement comes from $ARGUMENTS.

Print after Step 0:
```
─────────────────────────────────────────────────────
[perf-pragma]  Step 0 — done
  ✓ Step 0  clock=<CLOCK_NS> ns  arch=<single/sequential/dataflow>  loops=<N levels>
  ← NEXT    Step 1 — Calculate top-level target_ti
─────────────────────────────────────────────────────
```

---

## Step 1: Calculate top-level target_ti

### Case 1 — Rate (FPS, samples/s, MB/s, txn/s)

```
target_ti = clock_hz / throughput_rate

Examples:
  140 FPS    @ 300 MHz  →  300,000,000 / 140         = 2,142,857 cycles
  10k txn/s  @ 200 MHz  →  200,000,000 / 10,000      = 20,000 cycles
  1 GB/s     @ 250 MHz  →  (250M × 4B) / (1G)        = 1 cycle/word → II=1
```

### Case 2 — GFLOPS or MFLOPS

Requires knowing the exact **FLOPs per invocation** — do not use asymptotic formulas.

```
target_ti = FLOPs_per_invocation × clock_hz / (GFLOPS_target × 1e9)
```

**Count FLOPs from source (exact trace):**
- Count: multiply, divide, add, subtract, sqrt — each = 1 FLOP
- Do not count: loads, stores, casts, bit shifts, index arithmetic
- Trace through the algorithm once with the actual input dimensions; never use N³/3 or similar for small N

**Back-calculate to verify:**
```
FLOPs × clock_hz / target_ti  =?=  GFLOPS_target × 1e9
```
If it doesn't round-trip, the FLOPs count is wrong — recount before continuing.

Print after Step 1:
```
─────────────────────────────────────────────────────
[perf-pragma]  Step 1 — done
  ✓ Step 0  Inputs gathered
  ✓ Step 1  top-level target_ti = <N> cycles  (<throughput> @ <CLOCK_NS> ns)
  ← NEXT    Step 2 — Cascade through architecture
─────────────────────────────────────────────────────
```

---

## Step 2: Cascade through architecture

### Case A — Single function

The full `target_ti` applies to the one function. Go to Step 3.

### Case B — Sequential functions

Functions execute one after the other — total latency is the **sum**:
```
target_ti_total = target_ti_fn1 + target_ti_fn2 + ...
```
Allocate per-function budget by trip-count ratio. Apply the top-level pragma first; add function- or loop-scope pragmas only after synthesis confirms the bottleneck.

### Case C — Dataflow pipeline (including `hls::task`)

Throughput = **max** across all stages — the slowest stage sets the rate:
```
Design Throughput = max(TI_stage1, TI_stage2, ...)
```

`target_ti` is a **ceiling** that applies equally to every stage. A stage whose budgeter-allocated Target TI exceeds the top-level target_ti will dominate throughput and cause a miss — even if that stage shows "yes" for its own allocated target.

**Cascading strategy (Mode 1 — first synthesis):**
1. Place the top-level pragma only. Run synthesis.
2. Check per-stage Target TI values in the csynth report.
   - If any stage has Target TI > top-level `target_ti` → **cascade miss**. Return to this skill in Mode 2.
   - If all stage Target TIs ≤ top-level `target_ti` but TI not met → scheduling violation. Proceed to `/csynth` Case 2.
3. For each over-budget stage (Mode 2): add per-stage pragma = top-level `target_ti` (same value, not a fraction), then cascade into that stage's loops:
   ```
   stage_loop_II = ceil(target_ti / stage_loop_trip_count)
   ```

Print after Step 2:
```
─────────────────────────────────────────────────────
[perf-pragma]  Step 2 — done
  ✓ Step 0  Inputs gathered
  ✓ Step 1  top-level target_ti = <N> cycles
  ✓ Step 2  Architecture: <single/sequential/dataflow>  budget allocated
  ← NEXT    Step 3 — Cascade through loops
─────────────────────────────────────────────────────
```

---

## Step 3: Cascade through loops — build the table

### 3a. Classify trip counts before cascading

Inspect the source for each loop. If any trip count is variable (runtime argument or input-dependent), **stop here** and return to the caller (`/hls-optimize` Step 1d) to run csim first. Do not guess or estimate variable trip counts.

| Type | Example | Status |
|---|---|---|
| Compile-time constant | `for (int i = 0; i < 64; i++)` | ✓ Proceed |
| Fixed template parameter | `for (int i = 0; i < N; i++)` — N known at instantiation | ✓ Proceed |
| Runtime parameter | `for (int i = 0; i < height; i++)` — height is a function arg | ✗ Run csim first |
| Input-dependent | count driven by data | ✗ Run csim first |

### 3b. Propagate the budget

Propagate the budget downward through each loop level:

```
target_ti_outer = target_ti_function      (overhead usually negligible)
target_ti_inner = target_ti_outer / outer_trip_count
Required_II     = target_ti_inner / inner_trip_count
```

Fill this table using the known or profiled trip counts:

| Level | Formula | Value |
|---|---|---|
| Function target_ti | clock_hz / throughput_rate | ? cycles |
| Outer loop target_ti | function_ti / outer_trip_count | ? cycles |
| Inner loop target_ti | outer_ti / inner_trip_count | ? cycles |
| Required II | inner_ti / innermost_trip_count | ? |

### 3c. Detect scope types (function vs loop)

**Rule:** Only the top-level function gets a function-level pragma. All other pragmas target loops.

**Detection logic:**

1. **Extract TOP function** from `hls_config.cfg`:
   ```bash
   TOP_FUNCTION=$(grep "^top=" hls_config.cfg | cut -d'=' -f2)
   ```

2. **Find loop labels** in source files:
   ```bash
   grep -Pn "^\s*\w+:\s*for\s*\(" <source_files>
   ```
   This captures patterns like:
   - `Row_Loop: for (int r = 0; r < ROWS; r++)`
   - `Col_Loop: for (int c = 0; c < COLS; c++)`

3. **Build enhanced table** with Type column:

   | Target          | Type     | target_ti | Pragma Position      |
   |-----------------|----------|-----------|----------------------|
   | <TOP_FUNCTION>  | function | <value>   | Position 1 (function body) |
   | Row_Loop        | loop     | <value>   | Position 2 (loop body)     |
   | Col_Loop        | loop     | <value>   | Position 2 (loop body)     |

**Helper script** (optional): Use `../scripts/detect_loops.sh` to extract loop labels:
```bash
#!/bin/bash
# Usage: detect_loops.sh <source_file1> <source_file2> ...
grep -Phn "^\s*(\w+):\s*for\s*\(" "$@" | awk -F: '{print $3}' | sed 's/:.*//'
```

Print after Step 3:
```
─────────────────────────────────────────────────────
[perf-pragma]  Step 3 — done
  ✓ Step 0  Inputs gathered
  ✓ Step 1  top-level target_ti = <N> cycles
  ✓ Step 2  Architecture cascaded
  ✓ Step 3  Loop table built
  ← NEXT    Step 4 — Present table and wait for confirmation
─────────────────────────────────────────────────────
```

---

## Step 4: Present cascade table and wait for confirmation

Show the completed table with **Type** and **Pragma Location** columns to clearly distinguish function-level vs loop-level pragmas:

**Enhanced Cascade Table Format:**

| Target          | Type     | target_ti | Pragma Location                    |
|-----------------|----------|-----------|-------------------------------------|
| <TOP_FUNCTION>  | function | <value>   | Position 1: inside function body   |
| Row_Loop        | loop     | <value>   | Position 2: inside loop body       |
| Col_Loop        | loop     | <value>   | Position 2: inside loop body       |

**Placement guidance:**
- **Top-level function**: Place pragma at Position 1 (top of function body):
  ```cpp
  void <TOP_FUNCTION>(...) {
      #pragma HLS performance target_ti=<value>
      // function body
  }
  ```

- **Loop targets**: Place pragma at Position 2 (top of loop body):
  ```cpp
  Row_Loop: for (int r = 0; r < ROWS; r++) {
      #pragma HLS performance target_ti=<value>
      // loop body
  }
  ```

Do not place any pragma until the gate above resolves (auto in demo, user-confirmed in interactive).

Print after presenting the table:
```
─────────────────────────────────────────────────────
[perf-pragma]  Step 4 Complete — Cascade Table Presented
  ✓ Step 0  Inputs gathered
  ✓ Step 1  top-level target_ti = <N> cycles
  ✓ Step 2  Architecture cascaded
  ✓ Step 3  Loop table built with Type detection
  ✓ Step 4  Cascade table presented
  ← NEXT    Step 5 — Apply pragmas to source code
─────────────────────────────────────────────────────
```

---

## Step 5: Apply performance pragmas to source code

**This step applies ALL pragmas from the cascade table built in Steps 1-3.**

### Step 5a: Locate source file

Find the file containing the top-level function:

```bash
TOP_FUNCTION=$(grep "^top=" hls_config.cfg | cut -d'=' -f2)
SRC_FILE=$(grep -l "void $TOP_FUNCTION" src/*.cpp)
echo "Top function: $TOP_FUNCTION"
echo "Source file: $SRC_FILE"
```

Read the file to understand its current structure before editing.

### Step 5b: Apply Position 1 pragma (function-level)

**Target**: Top-level function only (from cascade table where Type="function")

**Location**: Immediately after the opening brace `{` of the top-level function

**Pragma to add**:
```cpp
    #pragma HLS performance target_ti=<TOP_LEVEL_TARGET_TI>
```

**Example** (if TOP_FUNCTION=colordetect_accel, TOP_LEVEL_TARGET_TI=68863):

**BEFORE**:
```cpp
void colordetect_accel(...) {
    // Load stage
    xf::cv::Mat<XF_8UC3, HEIGHT, WIDTH, XF_NPPC1> imgInput(rows, cols);
```

**AFTER**:
```cpp
void colordetect_accel(...) {
    #pragma HLS performance target_ti=68863
    
    // Load stage
    xf::cv::Mat<XF_8UC3, HEIGHT, WIDTH, XF_NPPC1> imgInput(rows, cols);
```

### Step 5c: Apply Position 2 pragmas (loop-level)

**Target**: All loops from cascade table where Type="loop"

**Location**: Between loop label and `for` keyword

**Pragma to add**:
```cpp
    #pragma HLS performance target_ti=<LOOP_TARGET_TI>
```

**Example** (if Row_Loop has target_ti=63):

**BEFORE**:
```cpp
    Row_Loop: for (int r = 0; r < rows; r++) {
        // loop body
    }
```

**AFTER**:
```cpp
    Row_Loop:
    #pragma HLS performance target_ti=63
    for (int r = 0; r < rows; r++) {
        // loop body
    }
```

**Repeat for ALL loops** in the cascade table.

### Step 5d: Verify pragmas applied correctly

Read the modified source file and confirm:
- ✓ Position 1 pragma after function opening brace
- ✓ All Position 2 pragmas between loop label and `for`
- ✓ Syntax: `#pragma HLS performance target_ti=<number>`
- ✓ No typos (target_ti not target_ii)
- ✓ Indentation matches surrounding code

### Step 5e: Return to caller

**DO NOT commit here.** The calling skill (architect/hls-optimize) will commit.

Return the following information to the caller:
- TOP_FUNCTION
- TOP_LEVEL_TARGET_TI
- List of modified files (src/*.cpp)
- Cascade table (for commit message)

Print completion:
```
─────────────────────────────────────────────────────
[perf-pragma]  Step 5 Complete — Pragmas Applied to Source
  Function       : $TOP_FUNCTION
    Pragma       : #pragma HLS performance target_ti=$TOP_LEVEL_TARGET_TI
    Location     : Position 1 (function body)
  
  Loops          : <N> loops
    Pragma       : target_ti values from cascade table
    Location     : Position 2 (loop bodies)
  
  Modified files : $SRC_FILE
  Status         : Pragmas applied — ready for commit by caller
─────────────────────────────────────────────────────
```

**The calling skill is responsible for**:
- Running `git add src/*.cpp`
- Creating commit with cascade table in message
- Proceeding with next steps

---

### Valid pragma positions

`#pragma HLS performance target_ti=N` is **syntactically valid** in all three positions below. Do not revert a pragma based on a "may not meet" warning — that is a performance warning, not a syntax error.

**IMPORTANT PLACEMENT RULE:**
- **Position 1** is for the **top-level function only** (extracted from `top=` in hls_config.cfg)
- **Positions 2 or 3** are for **all loop-level pragmas** (cascade targets below top-level)

```cpp
// Position 1 — top of top-level function body ONLY
void top_level_function(...) {  // This is the "top=" function from cfg
    #pragma HLS performance target_ti=2164502
    ...
}

// Position 2 — top of a loop body (PREFERRED for loop pragmas)
Row_Loop: for (int r = 0; r < 1080; r++) {
    #pragma HLS performance target_ti=2002
    ...
}

// Position 3 — immediately before a labeled loop (alternative to Position 2)
#pragma HLS performance target_ti=2002
Row_Loop: for (int r = 0; r < 1080; r++) { ... }
```

**Common mistake to avoid:** Do NOT place loop-level pragmas at the top of stage function bodies. For example, in a DATAFLOW design with stages `Array2xfMat`, `bgr2hsv`, `erode_0_0`, only the top-level `colordetect_accel` (if it's the TOP) gets a function-level pragma. All other pragmas target loops within those stages.

**Warning messages that do NOT mean invalid placement:**

| Message | Meaning | Action |
|---|---|---|
| `[HLS 214-394] May not meet target_ti=N` | Target may be unachievable with current structure | Add implementation pragma (ARRAY_PARTITION, UNROLL, etc.) — do NOT remove the performance pragma |
| `[HLS 214-395] May not meet target_ti=N ... function` | Same as above for function scope | Same |
| `[HLS 214-346] dataflow pragma takes precedence` | Performance pragma on the function that *itself* contains `#pragma HLS dataflow` is overridden at that level | Move the pragma to stage function bodies or their inner loops — do NOT remove loop-level pragmas |

Once confirmed, place pragmas using the computed integer values. Always write the **actual computed integer** — never a symbolic expression:

---

## Worked example: Dataflow FPS design @ 140 FPS, 303 MHz (Mode 1 → Mode 2)

**Step 1 — top-level target_ti:**
```
target_ti = 303,000,000 / 140 = 2,164,285 cycles  (round to 2,164,502 matching HLS period)
```

**Mode 1 — Place top-level pragma, run synthesis. Report shows:**
```
colordetect_accel   target=2,164,502  achieved=6,244,091  NO  ← 2.88×
  Array2xfMat         target=4,148,682  achieved=2,073,618  yes  ← WRONG: 4.1M > 2.16M
  bgr2hsv             target=2,097,365  achieved=2,088,721  yes  ← OK (≈ top-level)
  erode_0_0           target=6,260,297  achieved=6,244,090  yes  ← WRONG: 6.2M > 2.16M
```

Array2xfMat (4.1M) and erode_0_0 (6.2M) have Target TI > top-level 2,164,502 → **cascade miss → invoke Mode 2**.

**Mode 2 — Per-stage pragma table:**

| Stage | Current Target TI | Action | Pragma to add |
|---|---|---|---|
| Array2xfMat | 4,148,682 | > top-level → add pragma | `target_ti=2164502` |
| bgr2hsv | 2,097,365 | ≈ top-level → OK | none |
| erode_0_0 | 6,260,297 | > top-level → add pragma | `target_ti=2164502` |

Per-stage pragmas use the **same value** as the top-level (2,164,502) — not fractions.

After per-stage pragmas confirmed, cascade into each stage's loops using Step 3b.

---

## Worked example: Cholesky 3×3 @ 8.33 MFLOPS, 300 MHz

**Step 1 — Exact FLOPs trace (N=3):**
```
L[0][0] = sqrt(A[0][0])                           → 1 sqrt
L[1][0] = A[1][0] / L[0][0]                       → 1 div
L[2][0] = A[2][0] / L[0][0]                       → 1 div
L[1][1] = sqrt(A[1][1] - L[1][0]²)                → 1 mul + 1 sub + 1 sqrt
L[2][1] = (A[2][1] - L[2][0]·L[1][0]) / L[1][1]  → 1 mul + 1 sub + 1 div
L[2][2] = sqrt(A[2][2] - L[2][0]² - L[2][1]²)    → 2 mul + 2 sub + 1 sqrt
                                                            Total = 14 FLOPs
```
Note: N³/3 = 9 for N=3 — **wrong by 56%**. Always trace exactly.

**Back-calculate:**
```
14 × 300,000,000 / target_ti = 8,330,000
target_ti = 14 × 300,000,000 / 8,330,000 = 504 cycles  ✓
```

**Cascade table (dataflow, row_loop trip count = 3):**

| Level | Formula | Value |
|---|---|---|
| Function target_ti | 300 MHz / 8.33 MFLOPS | 504 cycles |
| row_loop target_ti | (504 − 28 stream overhead) / 3 | 158 cycles |
| Required II | 158 / innermost_trip_count | depends on N |

**Pragmas after user confirmation:**
```cpp
void kernel_cholesky_0(...) {
    #pragma HLS performance target_ti=504
    ...
}

row_loop: for (int i = 0; i < N; i++) {
    #pragma HLS performance target_ti=158
    ...
}
```
