---
name: hls-perf-pragma
description: Vitis HLS Performance Pragma — calculate target_ti from a throughput target, cascade through architecture and loops, and present the pragma placement table for user confirmation. Run this before placing any #pragma HLS performance.
license: MIT
argument-hint: <throughput-target e.g. "140 FPS" | "500 Msps" | "1 GFLOPS" | "minimize II">
metadata:
  author: "Sai Akhil Ayyagari"
  version: "1.0.0"
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
  Steps 0 → 5
  Step 0  — Gather inputs (clock, architecture, loop structure)
  Step 1  — Calculate top-level target_ti
  Step 2  — Cascade through architecture
  Step 3  — Cascade through loops → build table
  Step 4  — Present table for review
  Step 5  — Apply pragmas to source code
─────────────────────────────────────────────────────
```

---

---

## Step 0: Gather inputs

The throughput requirement comes from $ARGUMENTS.

**Read the configuration and source code to determine:**

1. **Clock frequency** — Read from the `.cfg` file:
   ```bash
   CFG_FILE=$(ls *.cfg)
   CLOCK_NS=$(grep "^clock=" $CFG_FILE | cut -d'=' -f2)
   clock_hz = 1e9 / CLOCK_NS
   ```

2. **Architecture type** — Inspect the top-level function:
   - Look for `#pragma HLS dataflow` or `hls::task` → dataflow pipeline
   - Multiple function calls in sequence → sequential functions
   - Single pipelined function otherwise

3. **Loop structure** — Identify loop nesting levels and trip counts:
   - Extract all labeled loops (e.g., `Row_Loop:`, `Col_Loop:`)
   - Determine nesting depth
   - **Trip counts:** Read loop bounds from source code   
   - **If trip counts are runtime parameters (not compile-time constants):**: **warn the user** : "Performance pragma requires loop trip counts. Please specify the trip count values for: [list loops with variable bounds]"

4. **Throughput conversion** — If throughput is given as data rate (MB/s, GB/s):

   **Read source code to determine input data size:**
   
   ```bash
   # Find top function
   CFG_FILE=$(ls *.cfg)
   TOP_FUNCTION=$(grep "^top=" $CFG_FILE | cut -d'=' -f2)
   
   # Find source file containing top function
   SRC_FILE=$(grep -l "void $TOP_FUNCTION" src/*.cpp)
   ```
   
   Read the function signature. The function will have multiple inputs/outputs. **Identify the input arrays** and calculate:
   
   ```
   bytes_per_invocation = total_input_array_size_in_bytes
   ```
   
   **Convert data rate to invocations per second:**
   
   ```
   data_rate_bytes_per_sec = throughput_value × (1,000,000 for MB/s, 1,000,000,000 for GB/s)
   invocations_per_sec = data_rate_bytes_per_sec / bytes_per_invocation
   ```
   
   Use this `invocations_per_sec` value in Step 1.

5. **Computational throughput** — If throughput is given as GOPS or GFLOPS:

   ```bash
   # Parse value (e.g., "100 GOPS" → 100e9 ops/sec)
   THROUGHPUT_VALUE=$(echo "$ARGUMENTS" | grep -oE '[0-9.]+')
   OPS_PER_SEC=$(echo "$THROUGHPUT_VALUE * 1e9" | bc)
   
   # Read source code to determine operations per invocation
   # Look for dimensions in defines or comments
   
   # Convert: invocations/sec = ops/sec / ops_per_invocation
   # Note: 1 OP = 1 MAC = 2 FLOPs
   invocations_per_sec=$(echo "$OPS_PER_SEC / $OPS_PER_INVOCATION" | bc)
   ```
   
   If operation count cannot be determined, warn the user to specify it or use FPS/MB/s instead.
   
   Use the calculated `invocations_per_sec` in Step 1.

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

**Formula:**
```
target_ti = clock_hz / throughput_rate
```

Where `throughput_rate` is the target invocations per second (e.g., FPS, invocations/s, samples/s).

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

**Cascading strategy:**
1. Place the top-level pragma only.
2. For dataflow stages that need per-stage constraints, add per-stage pragma = top-level `target_ti` (same value, not a fraction), then cascade into that stage's loops:
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

Propagate the budget downward through each loop level. The formula depends on loop nesting depth:

**General pattern:** Each nested loop divides its parent's target_ti by the parent's trip count.

**Case 1: Single loop in function**
```
Loop_target_ti = Function_target_ti      (assuming negligible function overhead)
```

**Case 2: Two-level nested loops**
```
Outer_loop_target_ti = Function_target_ti                    (overhead usually negligible)
Inner_loop_target_ti = Outer_loop_target_ti / outer_trip_count
```

**Case 3: Three-level nested loops**
```
Outer_loop_target_ti  = Function_target_ti
Middle_loop_target_ti = Outer_loop_target_ti / outer_trip_count
Inner_loop_target_ti  = Middle_loop_target_ti / middle_trip_count
```


Fill this table using the known or profiled trip counts (example shows 2-level nesting):

| Level | Formula | Value |
|---|---|---|
| Function target_ti | clock_hz / throughput_rate | ? cycles |
| Outer loop target_ti | function_ti (overhead ≈ 0) | ? cycles |
| Inner loop target_ti | outer_ti / outer_trip_count | ? cycles |

### 3c. Detect scope types (function vs loop)

**Rule:** Only the top-level function gets a function-level pragma. All other pragmas target loops.

**Detection logic:**

1. **Extract TOP function** from `*.cfg`:
   ```bash
   TOP_FUNCTION=$(grep -h -m1 "^syn.top=" -- *.cfg | cut -d'=' -f2)
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

**Helper script** (optional): Use `../hls-scripts/detect_loops.sh` to extract loop labels:
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
TOP_FUNCTION=$(grep "^top=" *.cfg | cut -d'=' -f2)
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
- TOP_FUNCTION_NAME
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
