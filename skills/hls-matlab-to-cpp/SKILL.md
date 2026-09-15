---
name: hls-matlab-to-cpp
description: Convert MATLAB sample-based code to plain C++ frame-based loops — analyze algorithm, generate C++ that compiles with g++, verify against MATLAB golden, then hand off to /hls-architect for HLS dataflow architecture.
argument-hint: <matlab-file.m> [part=<fpga-part>] [clock=<ns>] [throughput=<target>]
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# MATLAB to C++ Conversion

Convert MATLAB sample-based algorithms into frame-based plain C++ that compiles with g++, then hand off to `/hls-architect` for multi-stage HLS dataflow architecture.

---

## Preamble — Verify Tooling + Capture Required Parameters

### Step 0 — Run ./reference/setup.md in tooling-only mode

All required tooling (Vitis HLS, MATLAB, OpenCV) is verified by the `./reference/setup.md` skill.
At this point in the flow there is no design directory yet, so call `./reference/setup.md` with
`mode=tooling-only` — design discovery and build commands (Steps 2 & 3 of ./reference/setup.md)
are skipped.

```
./reference/setup.md mode=tooling-only
```

The `./reference/setup.md` skill will:
- Source Vitis (and export `$XILINX_VITIS`) — Step 1
- Detect & verify OpenCV install if the testbench will use `cv::imread` — Step 4
- Locate MATLAB and export `$MATLAB_BIN` — Step 5

If `./reference/setup.md` cannot resolve a tool (e.g. MATLAB not on PATH, OpenCV lib path missing),
it prompts the user with `AskUserQuestion`. **Do not proceed until `./reference/setup.md` reports
all three checks passed (or marked N/A).**

After `./reference/setup.md` returns, the following env vars are guaranteed to be set:

| Env var          | Set by         | Used by                                   |
|------------------|----------------|-------------------------------------------|
| `$XILINX_VITIS`  | `./reference/setup.md` Step 1| g++ compile commands in Step 2 & Step 4   |
| `$OPENCV_INCLUDE`| `./reference/setup.md` Step 4| g++ `-I` flag for testbench compile       |
| `$OPENCV_LIB`    | `./reference/setup.md` Step 4| g++ `-L` flag for testbench compile       |
| `$MATLAB_BIN`    | `./reference/setup.md` Step 5| Step 0 — running the user's `.m` files    |

Downstream skills (`/hls-architect`, `/hls-optimize`, `/csim`, `/csynth`, `/cosim`) inherit
this environment — **never re-verify or re-source**.

### Capture pipeline parameters

Parse parameters from `$ARGUMENTS`:

**Expected format:**
```
/matlab-to-cpp <file.m> [part=<fpga-part>] [clock=<ns>] [throughput=<target>]
```

**Examples:**
```
/matlab-to-cpp rgbEdgeDetector.m part=xczu9eg-ffvb1156-2-e clock=3.3 throughput=4400fps
/matlab-to-cpp demosaic.m part=xczu9eg-ffvb1156-2-e clock=3.5
/matlab-to-cpp filter.m
```

**Parse logic:**
1. Extract MATLAB file path (first non-key=value argument)
2. Extract `part=...` → store as `XPART`
3. Extract `clock=...` → store as `CLOCK_NS`
4. Extract `throughput=...` → store as `THROUGHPUT_TARGET`

**No silent defaults.** If any of `<matlab-file>`, `part=`, `clock=`, or `throughput=` is missing from `$ARGUMENTS`, prompt the user with `AskUserQuestion`:

| Missing arg | Question header | Question text |
|---|---|---|
| matlab file        | "MATLAB script" | "Which MATLAB script should I convert? (path to `.m` file)" |
| `part=`            | "FPGA part"     | "FPGA part to target? (e.g. `xczu9eg-ffvb1156-2-e`)" |
| `clock=`           | "Clock period"  | "Clock period in ns? (e.g. `3.3` for ~303 MHz)" |
| `throughput=`      | "Throughput"    | "Throughput target? (e.g. `4400 FPS`, `500 Msps`, `1 GFLOPS`)" |

**Why no defaults:** silent defaults (`xczu9eg`, `3.5 ns`) used to mask user typos and propagate the wrong values into `/hls-architect` and `/hls-optimize`. The prompt forces an explicit choice and surfaces parsing failures immediately.

After all four are resolved, print confirmation:
```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Pipeline parameters
  Source     : <matlab-file>
  Part       : <XPART>
  Clock      : <CLOCK_NS> ns  (≈ <MHz> MHz)
  Throughput : <THROUGHPUT_TARGET>
─────────────────────────────────────────────────────
```
  
## Flow Overview

Print this at the very start so the user can track progress throughout:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[matlab-to-cpp]  Pipeline — <design_name>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Step 0   Run MATLAB simulation → save golden I/O
  Step 0b  Range instrumentation → sim-measured types
  Step 1   Analyze MATLAB algorithm
  Step 2   Generate refactor_1 (plain C++) + verify
  Step 3   MATLAB → HLS construct mapping
  Step 4   Generate refactor_2 (frame-based C++)
  Step 5   Hand off to /hls-architect → /hls-optimize
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Re-print this overview after each step completes with a `✓` next to the completed step so the user always knows where the flow is.

---

## Design Workspace

Before running Step 0, derive `design_name` from the MATLAB script filename (snake_case, no `.m` extension).

Call `./reference/design-layout.md` to orient the workspace:
```
./reference/design-layout.md  design_name=<name>  stage=show
```
This prints the full expected directory tree without creating anything. Confirm the tree with the user, then proceed — each step calls `./reference/design-layout.md` to create its directory before writing files.

---

## Step 0 / 5 — Run MATLAB Simulation (Golden Reference)

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 0 / 5 — Run MATLAB Simulation (Golden Reference)
─────────────────────────────────────────────────────
```

Before converting anything, run the MATLAB simulation to capture golden inputs and outputs. These are used in Step 2 to verify the generated C++.

### Launch MATLAB CLI

Use `$MATLAB_BIN` — exported by `./reference/setup.md` Step 5. Do NOT hardcode a path here; that breaks for any user other than the one whose path was baked in.

```bash
"$MATLAB_BIN" -nodisplay -nodesktop
```

If `$MATLAB_BIN` is empty at this point, `./reference/setup.md mode=tooling-only` was not run (or it failed) — go back and re-run it before continuing.

### Run the Simulation

Once the MATLAB CLI is open, run the `*_runme.m` script in the design directory:

```matlab
>> run('path/to/design_runme.m')
```

**Important:**
- Run **only the simulation section** — do **not** run any MtoHDL Coder or code generation sections
- If the runme script contains both, stop before the HDL/HLS coder calls

### Save Inputs and Outputs

Save both the **input data** (stimulus) and **output data** (golden reference) so the C++ testbench can use the same files:

```matlab
% Save input
fid = fopen('matlab_input.bin', 'wb');
fwrite(fid, input_array, 'uint8');    % match the actual data type
fclose(fid);

% Save golden output
fid = fopen('matlab_golden.bin', 'wb');
fwrite(fid, output_array, 'uint8');
fclose(fid);
```

Keep note of: output dimensions, data type, and border pixels that are undefined (e.g., warmup edges for filter kernels).

### Write to golden/

Call `./reference/design-layout.md  design_name=<name>  stage=golden`

Then copy into `design_name/golden/`:
- All MATLAB source files (`*.m`) from the input design path
- `matlab_input.bin` — saved stimulus
- `matlab_golden.bin` — saved golden reference output

Print before proceeding:
```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 0 done — MATLAB Simulation
  Input saved  : golden/matlab_input.bin   (<N> bytes, <dtype>)
  Output saved : golden/matlab_golden.bin  (<N> bytes, <dtype>)
  Dimensions   : <ROWS × COLS or signal length>
  Border/warmup: <KSIZE/2 rows+cols invalid / N/A>
─────────────────────────────────────────────────────
✓ Step 0   Run MATLAB simulation → save golden I/O
  Step 0b  Range instrumentation → sim-measured types   ← NEXT
  Step 1   Analyze MATLAB algorithm
  Step 2   Generate refactor_1 (plain C++) + verify
  Step 3   MATLAB → HLS construct mapping
  Step 4   Generate refactor_2 (frame-based C++)
  Step 5   Hand off to /hls-architect → /hls-optimize
```

---

## Step 0b — Range Instrumentation

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 0b — Range Instrumentation
  0b.0  Detect design type (A: script / B: function-based)
  0b.1  Generate instrumented MATLAB files
  0b.2  Re-run MATLAB, collect RANGE lines
  0b.3  Build range table (filter constants/masks)
  0b.4  Derive W and I for each variable
  0b.4b Select Q and O modes (closest to MATLAB double)
  0b.4c Compute quantization error per variable
  0b.4d Size fixed-point multiply intermediates
  0b.5  Present table → user accept / refine loop
─────────────────────────────────────────────────────
```

Capture **min/max of every intermediate variable** from the MATLAB simulation. This drives exact-bitwidth type selection in Step 2 — tight widths reduce area and improve achievable II. Without this, type selection falls back to conservative static analysis.

### 0. Detect design type

Examine the design directory:

- **Type A — script-based:** the top-level runme calls no sub-function `.m` files; all computation is inline. The MATLAB `who` command can see all variables after the script runs.
- **Type B — function-based:** the design has multiple `.m` files that start with the `function` keyword. Sub-function internals are invisible to `who` — only the function's return values appear in the caller's workspace. These require per-function instrumentation.

**Detection rule:** scan the design directory for `.m` files. If more than one file begins with `function` (excluding the runme/tb scripts), it is **Type B**.

### 1. Generate instrumented MATLAB files

#### Type A — append snippet to runme

Add this block to the **end** of the `*_runme.m` script, after the main algorithm has already executed. Do not modify the algorithm itself.

```matlab
% --- Range instrumentation — append after main algorithm ---
fprintf('\n=== RANGE INSTRUMENTATION ===\n');
vars = who;
for k = 1:length(vars)
    v = eval(vars{k});
    if isnumeric(v) && ~isempty(v) && numel(v) > 1
        vmin   = double(min(v(:)));
        vmax   = double(max(v(:)));
        is_int = all(v(:) == floor(v(:)));
        fprintf('RANGE %-30s  min=%12.4f  max=%12.4f  integer=%d\n', ...
                vars{k}, vmin, vmax, double(is_int));
    end
end
fprintf('=== END RANGE INSTRUMENTATION ===\n');
```

#### Type B — auto-generate instrumented sub-functions

Use the two scripts shipped with this repo under `./scripts/`:

```bash
SCRIPTS_DIR="./scripts"
```

Both `gen_all_instr.py` and `gen_matlab_instr.py` ship with the repo — no external download required.

**Step 1: Generate `*_instr.m` for every sub-function:**

```bash
python3 $SCRIPTS_DIR/gen_all_instr.py --dir <design_dir>
```

This scans the design directory, finds all files starting with `function`, skips `*_instr`, `*_runme`, `*_tb`, `*_range`, `*_init`, and generates `<name>_instr.m` for each. The prefix embedded in every `RANGE` line equals the filename stem (e.g., `hb2_fir_i.m` → prefix `hb2_fir_i`).

Print the summary output from `gen_all_instr.py` — it lists every generated file and its tracked variable count.

**Step 2: Create or update an instrumented runner.**

Create `<design_name>_full_instr_runme.m` in the design directory that:
1. Calls `clear functions` at the top to reset all persistent variables
2. Calls the `*_instr` versions of every sub-function (not the originals)
3. Uses the same stimulus and parameters as the original runme
4. Prints any top-level coefficient/LUT ranges directly via `fprintf('RANGE ...')` before the pipeline loop

The RANGE lines are emitted by each `*_instr.m` function automatically at the end of their execution — no additional snippet is needed in the runner.

> **Note on loop-internal constants:** `gen_all_instr.py` tracks ALL named assignments inside the outer loop, including constants like `bit_start = 21` or `offset = fi(1025,...)`. These will appear in the RANGE output with trivial ranges (e.g., `[21,21]`). Ignore them when building the type table — they are not signal-path variables.

> **Note on output_direct temps:** assignments like `cout(i) = expr_with_no_named_intermediate` are captured via injected temp variables named `rng_<array>_N`. These appear as `RANGE prefix_rng_<array>_N` and represent the actual output signal value — keep them in the type table.

### 2. Re-run the simulation

**STOP — do not proceed until MATLAB has actually been re-run.**

The only valid source for the range table is the `RANGE` lines printed to the MATLAB console during this re-run. Any other source is forbidden:

| Forbidden source | Why it fails |
|---|---|
| Prior simulation output | Does not contain loop-internal variables |
| Code inspection / static analysis | Cannot see runtime values or conditional variables |
| Type inference from input ranges | Underestimates multiply/accumulate intermediates |
| Memory of a previous run | Stale — types may have changed |

Run the script:

```bash
# Type A
/path/to/matlab -nodisplay -nodesktop -r "run('path/to/design_runme.m'); exit"

# Type B
/path/to/matlab -nodisplay -nodesktop -r "run('path/to/design_full_instr_runme.m'); exit"
```

**How to verify:** the output must contain `RANGE` lines — one per tracked variable per function. For Type B, lines arrive interleaved as each `*_instr` function finishes its loop; collect all of them.

Collect every line beginning with `RANGE` from the output.

### 3. Build the range table

**First — print every `RANGE` line exactly as captured, unfiltered:**

```
RANGE <var1>   min=...  max=...  integer=...
RANGE <var2>   min=...  max=...  integer=...
...
```

Do not omit any line. This is the raw capture — show it all before making any decisions.

**Second — classify each variable:**

| Variable | Min | Max | Keep / Filter | Reason if filtered |
|---|---|---|---|---|
| `prefix_acc` | -1.2 | 1.1 | keep | signal-path accumulator |
| `prefix_rng_cout_1` | -1.0 | 1.0 | keep | output_direct temp — actual output value |
| `prefix_bit_start` | 21 | 21 | filter | loop-internal constant (trivial range) |
| `prefix_bit_end` | 32 | 32 | filter | loop-internal constant (trivial range) |
| `prefix_offset` | 1025 | 1025 | filter | loop-internal constant (trivial range) |

**Filter rules:**
- **Loop-internal constants**: min == max (or range is trivially the literal value) — these are assigned inside the loop but never change. Filter them — they have no useful range information for HLS type sizing.
- **Type A scalars**: loop counters, config constants, boolean scalars — filter.
- **Keep everything else**: signal-path variables, accumulators, intermediate products, output_direct temps (`rng_*`).

Build the type derivation table from the **kept** rows only:

| Variable | Min | Max | All-integer? | Chosen HLS type |
|---|---|---|---|---|
| `im1_pre` | -219 | 1107 | yes | `ap_int<12>` |
| `s_horiz` | 0 | 510 | yes | `ap_uint<9>` |
| `coeff_acc` | -0.125 | 0.5 | **no** | `ap_fixed<5,2>` |

### 4. Derive HLS type for each variable

Use the type that **matches MATLAB's numeric domain**. Apply these rules based on the OBSERVED ranges from instrumentation:

#### **ap_uint<N> — WHEN: Unsigned integers only**

**When to use:**
- Variable range has **NO fractional values** (all integers)
- Minimum ≥ 0 (unsigned)

**Why:**
- Matches MATLAB's integer arithmetic exactly
- Smallest hardware footprint for integer-only data

**Formula:**
```
N = ceil(log2(max + 1))
Example: max = 510 → ceil(log2(511)) = 9 → ap_uint<9>
```

**Do NOT use if:**
- ❌ MATLAB range is [0, 1] doubles → use `ap_fixed` instead
- ❌ Range has fractional values like 0.5, 0.125 → use `ap_fixed` instead

---

#### **ap_int<N> — WHEN: Signed integers only**

**When to use:**
- Variable range has **NO fractional values** (all integers)
- Minimum < 0 (signed)

**Why:**
- Matches MATLAB's signed integer arithmetic exactly
- Handles negative values efficiently

**Formula:**
```
N = ceil(log2(max(max + 1, abs(min) + 1))) + 1
Example: min = -219, max = 1107
  max(1108, 220) = 1108 → ceil(log2(1108)) + 1 = 12 → ap_int<12>
```

**Do NOT use if:**
- ❌ MATLAB range is [-1, 1] doubles → use `ap_fixed` instead
- ❌ Range has fractional values like -0.5, 1.25 → use `ap_fixed` instead

---

#### **ap_fixed<W,I> — WHEN: Fractional (floating-point) values**

**When to use:**
- Variable range has **ANY fractional values** (not all integers)
- Examples: [0, 1.0], [-0.125, 0.5], [-3.529, 3.459]

**Why:**
- Matches MATLAB's floating-point arithmetic
- Preserves MATLAB's numeric precision
- **CRITICAL:** Using `ap_int` when MATLAB uses doubles breaks bit-exact matching

**Formula:**
```
I = ceil(log2(max(abs(min), max) + 1)) + 1    (integer bits incl. sign)
F = ceil(-log2(min non-zero |v - floor(v)|))  (fractional bits)
W = I + F
Example: range [-0.125, 0.5] → I = 2, F = 3 → ap_fixed<5, 2>
```

**Common image processing ranges:**
```
gray in [0, 1.0]         → ap_fixed<12, 2>   (2 int bits for [0,1], 10 frac for precision)
gx/gy in [-4.0, 4.0]     → ap_fixed<16, 4>   (4 int bits incl sign, 12 frac)
gradMag in [0, 6.0]      → ap_fixed<16, 4>   (4 int bits, 12 frac)
```

**CRITICAL - Do NOT "optimize" by converting to integers:**

❌ **WRONG:**
```cpp
// MATLAB: gray = 0.299*R + 0.587*G + 0.114*B  (range [0, 1.0])
// Observed range: [0.027, 0.992]

// WRONG C++: "I'll convert to uint8 for hardware efficiency"
ap_uint<8> gray = (77*R + 150*G + 29*B) >> 8;  // [0, 255] range
// ❌ This changes the numeric domain → different values → mismatches
```

✅ **CORRECT:**
```cpp
// MATLAB: gray = 0.299*R + 0.587*G + 0.114*B  (range [0, 1.0])
// Observed range: [0.027, 0.992]

// CORRECT C++: Match MATLAB's domain
ap_fixed<12,2> gray = 0.299*R + 0.587*G + 0.114*B;  // [0, 1] range
// ✅ Same numeric domain → same values → bit-exact match
```

**Why this matters:**
- Changing from [0,1] to [0,255] changes ALL downstream computations
- gradMag = sqrt(gx² + gy²) will give **different results** in different domains
- Thresholds calibrated for [0,1] won't work in [0,255] domain
- Result: mismatches in csim, failed verification

**Rule:** If MATLAB uses floating-point, C++ uses `ap_fixed`. Period.

---

> **Margin rule:** Add 1 extra bit beyond strict minimum when observed range comes from a single small test image. Larger production images may push extremes slightly further.

### 4b. Select Q (quantization) and O (overflow) modes

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 0b.4b — Q/O mode selection
─────────────────────────────────────────────────────
```

The full fixed-point type is `ap_fixed<W, I, Q, O>`. Default is `AP_TRN, AP_WRAP`. Select Q and O to minimise deviation from MATLAB's double-precision result.

Reference — all valid modes (from `ap_decl.h`):

| Q mode | Behaviour |
|---|---|
| `AP_TRN` | Truncation toward −∞ (default) |
| `AP_TRN_ZERO` | Truncation toward zero |
| `AP_RND` | Round toward +∞ |
| `AP_RND_ZERO` | Round toward zero |
| `AP_RND_MIN_INF` | Round toward −∞ |
| `AP_RND_INF` | Round away from zero (matches MATLAB `round()`) |
| `AP_RND_CONV` | Convergent / banker's rounding — round half to even (matches IEEE 754, MATLAB's default double arithmetic) |

| O mode | Behaviour |
|---|---|
| `AP_WRAP` | Wrap-around (default) |
| `AP_SAT` | Saturate at ±max |
| `AP_SAT_ZERO` | Saturate to zero on overflow |
| `AP_SAT_SYM` | Symmetrical saturation |
| `AP_WRAP_SM` | Sign-magnitude wrap |

#### Overflow mode O — always derive from the range table

| Condition | O mode | Reason |
|---|---|---|
| Range table proves I bits cover min/max | `AP_WRAP` (default) | Overflow never occurs → no saturation hardware, no overhead |
| Intermediate result can temporarily exceed I bits | Widen I instead | AP_SAT masks bugs — fix the width, do not hide the overflow |
| Final output boundary (e.g. clip to [0, 255]) | Explicit `if/else` in C++ | More readable than AP_SAT and visible in code |

**Rule: size I correctly from the range table and use AP_WRAP. Never use AP_SAT to compensate for an undersized I.**

#### Quantization mode Q — derive from how MATLAB computes each variable

MATLAB computes in IEEE 754 double precision. Q determines how the HLS type handles fractional bits that are dropped when assigning to a narrower fixed-point type.

Inspect the MATLAB expression that produces each variable:

| MATLAB operation | Q mode | Reason |
|---|---|---|
| General arithmetic (`+`, `-`, `*`, `/N`) | `AP_RND_CONV` | IEEE 754 default is round-half-to-even — AP_RND_CONV matches exactly |
| Power-of-2 divide then store (`/8`, `/16`) | `AP_RND_CONV` | MATLAB rounds the double result before storing; plain `>> N` in C++ truncates (AP_TRN) — use AP_RND_CONV to recover 1 LSB accuracy |
| Explicit `floor(x)` | `AP_TRN` | MATLAB truncates toward −∞; AP_TRN does the same |
| Explicit `round(x)` | `AP_RND_INF` | MATLAB rounds half away from zero; AP_RND_INF matches |
| Explicit `fix(x)` / `int(x)` | `AP_TRN_ZERO` | MATLAB truncates toward zero; AP_TRN_ZERO matches |
| Explicit `ceil(x)` | implement as `floor(x) + 1` or negate-floor-negate in C++ | No direct HLS mode |
| Pure integer arithmetic (no fractional bits drop) | `AP_TRN` (default) | Q is irrelevant when F=0; leave at default |

**Default when unsure:** `AP_RND_CONV` — it is the IEEE 754-compatible choice and closest to MATLAB's double-precision arithmetic. It costs one extra adder per assignment versus `AP_TRN`.

> **Note:** `AP_RND` (round toward +∞) is **not** the same as round-to-nearest. It biases positive results upward. Do not use it as a substitute for `AP_RND_CONV`.

#### Full type examples

```cpp
// xcorr: fractional, general MATLAB arithmetic → AP_RND_CONV, sized from range table
ap_ufixed<34, 1, AP_RND_CONV, AP_WRAP>   xcorr_val;

// accumulator: fractional intermediate — widen I to avoid overflow
ap_fixed<20, 4, AP_RND_CONV, AP_WRAP>    acc;

// result of explicit floor() in MATLAB
ap_fixed<16, 8, AP_TRN, AP_WRAP>         floored_val;

// result of explicit round() in MATLAB
ap_fixed<16, 8, AP_RND_INF, AP_WRAP>     rounded_val;

// index: unsigned integer — Q/O irrelevant
ap_uint<13>                               idx;

// final output clamp [0, 255] — done explicitly, not via AP_SAT
ap_uint<8> out_px;
if      (result < 0)   out_px = 0;
else if (result > 255) out_px = 255;
else                   out_px = (ap_uint<8>)result;
```

Update the range table to include the full type with Q and O:

| Variable | Min | Max | All-integer? | HLS type (full) |
|---|---|---|---|---|
| `xcorr` | 3.14e-10 | 4.97e-3 | no | `ap_ufixed<34,1,AP_RND_CONV,AP_WRAP>` |
| `s_horiz` | 0 | 510 | yes | `ap_uint<9>` |
| `coeff_acc` | -0.125 | 0.5 | no | `ap_fixed<5,2,AP_RND_CONV,AP_WRAP>` |

### 4c. Compute quantization error per variable

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 0b.4c — Quantization error check
─────────────────────────────────────────────────────
```

For every fractional variable in the table, compute the LSB and the worst-case quantization error introduced by the chosen `ap_fixed` type versus MATLAB's double precision.

```
F            = W - I                          (fractional bits)
LSB          = 2^(-F)
max_q_error  = 0.5 × LSB   (AP_RND_CONV)
             = 1.0 × LSB   (AP_TRN / AP_TRN_ZERO)
rel_err_max  = max_q_error / |signal_max|     (error relative to peak signal)
rel_err_min  = max_q_error / |signal_min|     (error relative to smallest signal — worst case)
```

Present an extended table with the error column:

| Variable | HLS type | LSB | Max q-error | Rel err @ max | Rel err @ min | Status |
|---|---|---|---|---|---|---|
| `xcorr` | `ap_ufixed<34,1,AP_RND_CONV,AP_WRAP>` | 1.16e-10 | 5.8e-11 | 0.0012% | 18.5% | ⚠ warn |
| `coeff_acc` | `ap_fixed<5,2,AP_RND_CONV,AP_WRAP>` | 0.125 | 0.0625 | 12.5% | — | ✗ redo |

**Thresholds:**

| Rel err @ min signal | Action |
|---|---|
| < 1% | ✓ acceptable — proceed |
| 1% – 10% | ⚠ warn — ask user if acceptable; if unsatisfied go back to Step 0 |
| > 10% | ✗ redo — add fractional bits: increase F by `ceil(log2(rel_err / 0.01))`, then recompute |

When a variable fails (> 10%):
1. Add fractional bits: `F_new = F + ceil(log2(rel_err_min / 0.01))`
2. Set `W_new = I + F_new`
3. Recheck that the new type does not exceed 64 bits total (HLS synthesis limit for most operators)
4. Update the table and recompute error — iterate until all entries are ✓ or ⚠

When any entry is ⚠ (1–10%), present the table and ask:
```
Some variables have 1–10% quantization error at their minimum signal level.
Do you want to accept these types, or increase precision?
  [accept]  — proceed with current types
  [refine]  — go back to Step 0, re-instrument with the new types, verify the golden match improves
```
If the user says refine → return to Step 0 with the proposed wider types and re-run the full instrumentation.

---

### 4d. Fixed-point multiplication intermediates

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 0b.4d — Multiply intermediate sizing
─────────────────────────────────────────────────────
```

Fixed-point multiplication changes the value range: `a × b` produces a result with `W_a + W_b` total bits and `I_a + I_b` integer bits. If the result is immediately assigned to a narrower type, fractional bits are silently dropped — MATLAB never loses these bits (it stays in double throughout).

**Rule: any intermediate that is the result of a multiply must be declared at the full product width before any narrowing.**

For every multiply in the MATLAB algorithm, identify the operands and declare the intermediate:

```cpp
// MATLAB: result = a * b
// a: ap_fixed<Wa, Ia, Qa, Oa>
// b: ap_fixed<Wb, Ib, Qb, Ob>

ap_fixed<Wa+Wb, Ia+Ib, AP_RND_CONV, AP_WRAP> prod = a * b;

// Only then narrow — after the full-precision product is captured:
ap_fixed<W_out, I_out, AP_RND_CONV, AP_WRAP> result = prod;
```

For the peakPicker case — comparing `xcorr[i] >= threshold[i]`:
- This is a comparison, not a multiply — no intermediate needed
- If an accumulation or scale factor is added later (e.g. `xcorr * weight`), declare `prod` at full width first

Include a multiply-intermediate column in the range table where applicable:

| Multiply | Operand types | Intermediate type | Output type |
|---|---|---|---|
| `a * b` | `ap_fixed<16,4>` × `ap_fixed<16,4>` | `ap_fixed<32,8,AP_RND_CONV,AP_WRAP>` | `ap_fixed<16,4,AP_RND_CONV,AP_WRAP>` |

---

### 5. Present to user and validate

Print the completed range table with error column:
```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 0b — Range + Quantization Error
  Variables instrumented : <N>

  Variable         Sim range              HLS type                         LSB        Max q-err  Rel@max   Rel@min   Status
  ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  xcorr            [3.14e-10, 4.97e-3]   ap_ufixed<34,1,AP_RND_CONV,AP_WRAP>  1.16e-10   5.8e-11    0.001%    18.5%    ⚠
  threshold        [1.53e-5,  4.40e-3]   ap_ufixed<34,1,AP_RND_CONV,AP_WRAP>  1.16e-10   5.8e-11    0.001%     0.4%    ✓
  ...

  Multiply intermediates:
  <a> × <b>  →  ap_fixed<Wa+Wb, Ia+Ib, AP_RND_CONV, AP_WRAP>   (full product width)

─────────────────────────────────────────────────────
```

Then ask the user:
```
Quantization error summary:
  ✓ <N> variables within 1% at min signal
  ⚠ <N> variables 1–10% at min signal  ← listed above
  ✗ <N> variables > 10% — already widened and retried

Are you satisfied with the type precision?
  [accept]  — proceed to Step 1
  [refine]  — go back to Step 0, re-run MATLAB with wider types applied, re-verify golden match
```

Wait for the user's answer. If **refine**:
- Apply the wider types to the instrumented MATLAB run (Step 0 re-run)
- Re-collect RANGE lines, rebuild the table, recompute errors
- Re-present the updated table — repeat until user accepts

If **accept** (or all entries are ✓):

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 0b done — Range Instrumentation
✓ Step 0   Run MATLAB simulation → save golden I/O
✓ Step 0b  Range instrumentation → sim-measured types
  Step 1   Analyze MATLAB algorithm                     ← NEXT
  Step 2   Generate refactor_1 (plain C++) + verify
  Step 3   MATLAB → HLS construct mapping
  Step 4   Generate refactor_2 (frame-based C++)
  Step 5   Hand off to /hls-architect → /hls-optimize
─────────────────────────────────────────────────────
```

Save the accepted range table in memory — Step 2 references it for every intermediate type decision.

---

## Step 1 / 5 — Analyze the MATLAB Code

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 1 / 5 — Analyze MATLAB Code
─────────────────────────────────────────────────────
```

Read the MATLAB code and extract:

1. **Algorithm intent** — what does it compute? (filter, transform, color space, etc.)
2. **Inputs and outputs** — types, dimensions, value ranges
3. **Sample-based operations** — identify every operation that works on a single pixel/sample at a time vs. entire frame
4. **Neighborhood access** — does it use `imfilter`, `conv2`, sliding windows, or index expressions like `A(i-1:i+1, j-1:j+1)`?
5. **Filter coefficients** — are they fixed at compile time or runtime parameters?
6. **Control flow** — loops, conditionals, switch/case based on pixel position (Bayer phase, color channel, etc.)

---

Print before proceeding:
```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 1 done — MATLAB Analysis
  Algorithm    : <what it computes>
  Input        : <type, dimensions>
  Output       : <type, dimensions>
  Operations   : <sample-based ops identified>
  Neighborhood : <sliding window / none>
  Coefficients : <fixed at compile time / runtime>
  Assumptions  : <any assumptions made>
─────────────────────────────────────────────────────
✓ Step 0   Run MATLAB simulation → save golden I/O
✓ Step 0b  Range instrumentation → sim-measured types
✓ Step 1   Analyze MATLAB algorithm
  Step 2   Generate refactor_1 (plain C++) + verify    ← NEXT
  Step 3   MATLAB → HLS construct mapping
  Step 4   Generate refactor_2 (frame-based C++)
  Step 5   Hand off to /hls-architect → /hls-optimize
```

## Step 2 / 5 — Generate `refactor_1` (Plain C++) + Testbench, Verify Against MATLAB

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 2 / 5 — Generate refactor_1 (Plain C++) + Verify
─────────────────────────────────────────────────────
```

Before writing HLS code, generate **`refactor_1`** — a plain C++ implementation (no HLS pragmas, no `ap_int`, no streams) — and a testbench that mirrors the MATLAB simulation exactly. Verify correctness first.

### `refactor_1` — Plain C++ File

- Translate the MATLAB algorithm to HLS C++ using types chosen from the **Step 0b simulation range table**. For each intermediate variable, the simulation result drives the choice among exactly three candidates:
  - **`ap_uint<N>`** — simulation shows min ≥ 0 and all values are integer
  - **`ap_int<N>`** — simulation shows min < 0 and all values are integer
  - **`ap_fixed<W,I>`** — simulation shows fractional values, or the MATLAB path uses `double`/`float` (which are forbidden in synthesis and must be replaced)
  - For variables not captured by the range table (scalars, loop counters): apply the same three-way choice using static analysis of the MATLAB code

#### Generate Typedefs from Range Table

For **every variable** in the accepted Step 0b range table, generate a typedef:

```cpp
// Generate one typedef per unique type from range table
typedef ap_ufixed<W1, I1, AP_RND_CONV, AP_WRAP> input_t;
typedef ap_fixed<W2, I2, AP_RND_CONV, AP_WRAP>  intermediate_t;
typedef ap_ufixed<W3, I3, AP_RND_CONV, AP_WRAP> output_t;
// ... one typedef for each variable in the range table
```

**Naming convention**:
- Use the MATLAB variable name as the suffix: `<var>_t`
- For shared ranges (variables with identical min/max), use one typedef with a descriptive name

#### Apply Types to All Variables

Use the generated typedefs for:
1. **Output arrays**: Must match the derived output types from Step 0b
2. **Intermediate variables**: Loop-internal calculations use the range-table types
3. **Multiply products**: Declare at full product width before narrowing

**✅ CORRECT**: Use derived types from range table:

```cpp
static intermediate_t buffer[FRAME_PIXELS];
static result_t       results[FRAME_PIXELS];

void my_kernel(input_t input_arr[FRAME_PIXELS],
               output_t output_arr[FRAME_PIXELS])  // ← use derived output_t, not float
{
    for (int i = 0; i < FRAME_PIXELS; i++) {
        intermediate_t temp = buffer[i] * buffer[i];  // ← use typed intermediate
        output_arr[i] = process(temp);
    }
}
```

**❌ WRONG**: Don't fall back to float for fixed-point variables:

```cpp
// ❌ Defeats the purpose of Step 0b range instrumentation
float output_arr[FRAME_PIXELS];
float intermediate = ...;
```

#### Use hls::sqrt() and Other Math Functions

Per the HLS Math Library docs (https://docs.amd.com/r/en-US/ug1399-vitis-hls/HLS-Math-Library), use `hls::` math functions for fixed-point types:

```cpp
#include <ap_fixed.h>
#include <hls_math.h>

// Use hls:: functions for ap_fixed types
output_t result = hls::sqrt(input_val);      // sqrt
output_t result = hls::sin(angle);           // sin
output_t result = hls::exp(x);               // exp
```

**For g++ testbench compilation**:

The `hls_math.h` header is synthesis-only. For g++ verification, use conditional compilation:

```cpp
#ifdef __SYNTHESIS__
    #include <hls_math.h>
    #define SQRT(x) hls::sqrt(x)
#else
    #include <cmath>
    #define SQRT(x) decltype(x)(std::sqrt(float(x)))
#endif

// In kernel:
output_t result = SQRT(input_val);
```

Or use a wrapper function:

```cpp
#ifdef __SYNTHESIS__
    #include <hls_math.h>
    template<typename T>
    T sqrt_wrapper(T x) { return hls::sqrt(x); }
#else
    #include <cmath>
    template<typename T>
    T sqrt_wrapper(T x) { return T(std::sqrt(float(x))); }
#endif

// In kernel:
output_t result = sqrt_wrapper(input_val);
```

**Important**: Always use `hls::` functions from `<hls_math.h>`, not `std::` functions, when working with `ap_fixed` types in synthesizable code.

- Use the same logic, same coefficients, same normalization as the MATLAB code
- No HLS pragma constructs yet — this is a functional translation, but with HLS-compatible types
- **Always use row-major scan order** (row outer, col inner) — even if the MATLAB code uses column-major. Row-major is the HLS standard for image processing and determines line buffer sizing downstream.
- **Output dimensions must match input dimensions exactly** — read `ROWS` and `COLS` from the input image; do NOT add extra rows or columns for border padding. If the algorithm has a warmup border (e.g., filter kernels), exclude those border pixels from the error comparison — do not change the output array size.

```cpp
// Row-major flat loop — always use this pattern
for (int k = 0; k < ROWS * COLS; k++) {
    int row = k / COLS;   // line buffer depth = COLS
    int col = k % COLS;
    ...
}
```

> **Why row-major matters for HLS:** Line buffers for an N×N filter are sized by the number of pixels per line. In row-major, one line = COLS pixels → `lb[COLS]` with N-1 buffers. Column-major requires `lb[ROWS]` — non-standard and wrong when ROWS ≠ COLS.

### Testbench

The testbench must mirror the MATLAB `*_runme.m` structure — same input data, same comparison against the golden output.

**Shape mismatch warning:** MATLAB stores arrays in **column-major** order; C++ iterates in **row-major** order. A direct flat index comparison will fail even on a correct implementation. The comparison must map between the two orderings:

```
MATLAB flat index (column-major): j = col * ROWS + row
C++    flat index (row-major):    i = row * COLS + col
```

```cpp
int main() {
    // 1. Load input — MATLAB saves in column-major order.
    //    Reorder to row-major for C++ kernel.
    load_bin("matlab_input.bin", matlab_in, FRAME_PIXELS);  // column-major
    for (int row = 0; row < ROWS; row++)
        for (int col = 0; col < COLS; col++)
            in_buf[row * COLS + col] = matlab_in[col * ROWS + row];

    // 2. Load MATLAB golden output (column-major)
    load_bin("matlab_golden.bin", golden, OUT_PIXELS);

    // 3. Run the C++ function TWICE — mandatory.
    //    cosim measures Initiation Interval (II) from back-to-back calls.
    //    With only 1 call, cosim reports NA for Interval.
    int total_mismatches = 0;
    for (int call = 1; call <= 2; call++) {
        my_kernel(in_buf, out_buf);

        // 4. Compare: map C++ row-major index to MATLAB column-major index
        int mismatches = 0;
        for (int row = 0; row < ROWS; row++) {
            for (int col = 0; col < COLS; col++) {
                int cpp_idx    = row * COLS + col;   // C++ row-major
                int matlab_idx = col * ROWS + row;   // MATLAB column-major
                if (abs((int)out_buf[cpp_idx] - (int)golden[matlab_idx]) > TOLERANCE) {
                    printf("Call %d MISMATCH (%d,%d): got %d expected %d\n",
                           call, row, col, out_buf[cpp_idx], golden[matlab_idx]);
                    mismatches++;
                }
            }
        }
        printf("Call %d: %s\n", call, mismatches == 0 ? "PASS" : "FAIL");
        total_mismatches += mismatches;
    }
    return total_mismatches;
}
```

> **Note:** This testbench carries forward unchanged into the HLS design. `/hls-architect` uses the same testbench — only the kernel implementation changes, not the stimulus or comparison logic.

### Compare ap_fixed Outputs

When the kernel uses `ap_fixed` types for outputs, convert to `float` before comparison:

```cpp
// Load golden (double from MATLAB)
double matlab_golden[FRAME_PIXELS];
load_bin("matlab_golden.bin", matlab_golden, FRAME_PIXELS);

// Compare
for (int row = 0; row < ROWS; row++) {
    for (int col = 0; col < COLS; col++) {
        int cpp_idx    = row * COLS + col;
        int matlab_idx = col * ROWS + row;
        
        // Convert ap_fixed to float for comparison
        float cpp_val = output_arr[cpp_idx].to_float();
        float matlab_val = (float)matlab_golden[matlab_idx];
        
        if (fabs(cpp_val - matlab_val) > TOLERANCE) {
            printf("MISMATCH (%d,%d): got %.6f expected %.6f\n",
                   row, col, cpp_val, matlab_val);
            mismatches++;
        }
    }
}
```

**Note**: `to_float()` is an `ap_fixed` member function that converts to IEEE float. For `ap_uint`/`ap_int` types, use direct casts as shown in the earlier example.

### Image I/O Detection — PNG/Image Read/Write

**MANDATORY CHECK during Step 1:** Scan the MATLAB code for image I/O:

```matlab
% Detection patterns:
imread()    % reads image file
imwrite()   % writes image file
imshow()    % displays image (implies visual output)
rgb2gray()  % color conversion (implies image processing)
```

**Detection result determines testbench I/O:**

| MATLAB has | C++ testbench must |
|---|---|
| `imread()` + `imwrite()` | Read PNG input + write PNG output (OpenCV) |
| `imread()` only | Read PNG input + save `.bin` golden + write PNG output |
| Neither | Binary `.bin` I/O only |

**Rule: If MATLAB writes visual output (imwrite/imshow), C++ must write PNG for side-by-side comparison.**

---

### PNG I/O Implementation (when detected)

When image I/O is detected, the testbench must do **BOTH**:
1. Binary `.bin` comparison (numerical verification)
2. PNG write (visual comparison)

Generate a **hybrid testbench** that combines both approaches:

```cpp
#include <opencv2/opencv.hpp>

int main() {
    // ============================================================
    // HYBRID TESTBENCH — Binary verification + Visual output
    // ============================================================
    
    // Determine golden path - use absolute path if provided, else local
    const char *GOLDEN_DIR = getenv("GOLDEN_PATH");
    if (!GOLDEN_DIR) GOLDEN_DIR = "../golden";  // fallback to relative
    
    char input_bin_path[512], golden_bin_path[512], input_png_path[512];
    snprintf(input_bin_path, sizeof(input_bin_path), "%s/matlab_input.bin", GOLDEN_DIR);
    snprintf(golden_bin_path, sizeof(golden_bin_path), "%s/matlab_golden.bin", GOLDEN_DIR);
    snprintf(input_png_path, sizeof(input_png_path), "%s/<input>.png", GOLDEN_DIR);

    // 1. Load input from binary (same as MATLAB saves)
    load_bin(input_bin_path, matlab_in, FRAME_PIXELS);
    for (int row = 0; row < ROWS; row++)
        for (int col = 0; col < COLS; col++)
            in_buf[row * COLS + col] = matlab_in[col * ROWS + row];

    // 2. Load MATLAB golden from binary (numerical comparison)
    load_bin(golden_bin_path, golden, OUT_PIXELS);

    // 3. ALSO load input PNG for visual reference (optional, for debugging)
    cv::Mat input_png = cv::imread(input_png_path, cv::IMREAD_GRAYSCALE);
    if (input_png.empty()) {
        printf("Warning: input PNG not found — proceeding with binary-only input\n");
    }

    // 4. Run the C++ kernel TWICE — mandatory for cosim II measurement
    int total_mismatches = 0;
    for (int call = 1; call <= 2; call++) {
        my_kernel(in_buf, out_buf);

        // 5a. Binary comparison against MATLAB golden (numerical verification)
        int mismatches = 0;
        for (int row = 0; row < ROWS; row++) {
            for (int col = 0; col < COLS; col++) {
                int cpp_idx    = row * COLS + col;
                int matlab_idx = col * ROWS + row;
                if (abs((int)out_buf[cpp_idx] - (int)golden[matlab_idx]) > TOLERANCE) {
                    printf("Call %d MISMATCH (%d,%d): got %d expected %d\n",
                           call, row, col, out_buf[cpp_idx], golden[matlab_idx]);
                    mismatches++;
                }
            }
        }
        printf("Call %d (binary): %s\n", call, mismatches == 0 ? "PASS" : "FAIL");
        total_mismatches += mismatches;

        // 5b. Write C++ output as PNG (visual comparison)
        cv::Mat out_png(ROWS, COLS, CV_8UC1);
        for (int row = 0; row < ROWS; row++)
            for (int col = 0; col < COLS; col++)
                out_png.at<uint8_t>(row, col) = (uint8_t)out_buf[row * COLS + col];
        
        char filename[256];
        sprintf(filename, "cpp_output_call%d.png", call);
        cv::imwrite(filename, out_png);
        printf("           (visual): %s written\n", filename);
    }

    // 6. Summary
    printf("\n========================================\n");
    printf("Visual comparison:\n");
    printf("  Input      : %s\n", input_png_path);
    printf("  MATLAB gold: %s/<golden>.png\n", GOLDEN_DIR);
    printf("  C++ output : cpp_output_call2.png (written locally)\n");
    printf("========================================\n");
    printf("Open PNGs side-by-side to verify visually.\n");
    
    return total_mismatches;
}
```

**Hybrid testbench rules:**
- **Binary verification:** Uses `.bin` files for exact numerical comparison (catches precision errors)
- **Visual output:** Writes PNG for side-by-side visual inspection (catches algorithm errors)
- **Both must pass:** Binary PASS + visual inspection confirms correctness
- Use `cv::IMREAD_GRAYSCALE` or `cv::IMREAD_COLOR` to match MATLAB
- For RGB output, use `CV_8UC3` and interleave channels correctly
- Write PNG for **both calls** (call1 and call2) to verify consistency
- **All file paths must be absolute.** csim runs from `hls/hls/csim/build/` — derive absolute paths at generation time.

### Verify

```bash
# ap_fixed/ap_int/ap_uint headers ship with Vitis at $XILINX_VITIS/include
g++ -std=c++14 -I$XILINX_VITIS/include -o verify testbench.cpp kernel.cpp && ./verify
```

Must print `PASS` before proceeding.

### Write to sample_based/

Call `./reference/design-layout.md  design_name=<name>  stage=sample_based`

Write into `design_name/sample_based/`:
- `kernel.cpp`    — the refactor_1 plain C++ kernel
- `testbench.cpp` — the testbench (loads `.bin` files from `../golden/`)

If it fails:
- Check coefficient scaling (`/8` → `>> 3`, `/16` → `>> 4`)
- Check rounding vs truncation differences
- Exclude warmup border pixels from comparison for filter kernels (KSIZE/2 rows + cols)

---

Print before proceeding:
```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 2 done — refactor_1
  Data types   : <variable: sim range [min, max] → ap_uint<N> / ap_int<N> / ap_fixed<W,I> — list each>
  Border/warmup: <warmup = N rows/cols skipped in comparison / N/A>
  Index mapping: <row-major ↔ column-major note if applicable / N/A>
  Verify result: PASS / FAIL (<mismatch count if fail>)
─────────────────────────────────────────────────────
✓ Step 0   Run MATLAB simulation → save golden I/O
✓ Step 0b  Range instrumentation → sim-measured types
✓ Step 1   Analyze MATLAB algorithm
✓ Step 2   Generate refactor_1 (plain C++) + verify
  Step 3   MATLAB → HLS construct mapping               ← NEXT
  Step 4   Generate refactor_2 (frame-based C++)
  Step 5   Hand off to /hls-architect → /hls-optimize
```

## Step 3 / 5 — MATLAB → HLS Construct Mapping

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 3 / 5 — MATLAB → HLS Construct Mapping
─────────────────────────────────────────────────────
```

### Data Types

| MATLAB type | HLS equivalent |
|---|---|
| `uint8` | `ap_uint<8>` |
| `uint16` | `ap_uint<16>` |
| `int16` | `ap_int<16>` |
| `single` (float) | `float` (avoid in HLS — prefer fixed-point) |
| `double` | forbidden in synthesis — replace with `ap_fixed<W,I>` |
| logical / boolean | `bool` or `ap_uint<1>` |

See `/hls-bitwidth` for arithmetic bitwidth rules.

### Scalar Operations

| MATLAB | HLS C++ |
|---|---|
| `A + B` | `A + B` (watch bitwidth growth — see `/hls-bitwidth`) |
| `A * B` | `A * B` (result needs W_A + W_B bits) |
| `A / 8` | `A >> 3` (use shifts for power-of-2 division — no DSP cost) |
| `floor(x)` | right shift or `ap_fixed` truncation |
| `min(a, b)` | `std::min(a, b)` or ternary |
| `max(a, b)` | `std::max(a, b)` or ternary |
| `cast(x, 'uint8')` | `(ap_uint<8>)x` with explicit saturation if needed |
| `saturate to [0,255]` | `if(v<0) return 0; if(v>255) return 255; return v;` |

### Array / Matrix Access

| MATLAB | HLS C++ |
|---|---|
| `A(i, j)` (1-indexed) | `A[(i-1)*COLS + (j-1)]` (0-indexed flat array) |
| `A(:)` — all elements | `for` loop over `FRAME_PIXELS` |
| `A(r, c) = val` | `A[r*COLS + c] = val` |

### Neighborhood / Filter Operations

| MATLAB | HLS C++ equivalent |
|---|---|
| `imfilter(im, filt)` | Manual sliding window loop with line buffer (see `/hls-stencil-pattern`) |
| `conv2(A, B)` | Unrolled MAC loop over kernel coefficients |
| `A(i-k:i+k, j-k:j+k)` | 5×5 window extracted from `hls::LineBuffer` + `hls::Window` |
| `ndgrid` / meshgrid | Replaced by loop counters `(row, col)` tracking LSBs |

### Mask / Switch Logic

| MATLAB | HLS C++ |
|---|---|
| `mask .* value` | `mask ? value : 0` or `if (mask) out = value;` |
| `switch idx` | `switch ((int)idx)` with `case 0: ... break;` |
| `find(str == 'r') - 1` | Pre-encoded integer index passed as `ap_uint<2>` parameter |

---

Print before proceeding:
```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 3 done — HLS Construct Mapping
  Key mappings : <list the non-trivial mappings that apply to this design>
                 e.g. imfilter → line buffer + sliding window
                      double   → ap_fixed<W,I>
                      ndgrid   → row/col loop counters
  Any gaps     : <MATLAB constructs with no direct HLS equivalent — or "none">
─────────────────────────────────────────────────────
✓ Step 0   Run MATLAB simulation → save golden I/O
✓ Step 0b  Range instrumentation → sim-measured types
✓ Step 1   Analyze MATLAB algorithm
✓ Step 2   Generate refactor_1 (plain C++) + verify
✓ Step 3   MATLAB → HLS construct mapping
  Step 4   Generate refactor_2 (frame-based C++)        ← NEXT
  Step 5   Hand off to /hls-architect → /hls-optimize
```

---

## Step 4 / 5 — Generate `refactor_2`: Sample-Based to Frame-Based Conversion

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 4 / 5 — Generate refactor_2 (Frame-Based C++)
─────────────────────────────────────────────────────
```

Starting from `refactor_1`, convert to **`refactor_2`** — a frame-based C++ file that iterates pixel-by-pixel in raster scan order. This is the file handed to `/hls-architect` in Step 5.

MATLAB code is inherently **sample-based**: it operates on entire arrays/matrices at once using vectorized operations. HLS requires **frame-based** code: iterate over pixels one at a time in raster scan order.

> **Image size rule:** The frame-based kernel must produce exactly `ROWS × COLS` output pixels — the same dimensions as the input. Do NOT increase the output array size to accommodate border warmup. Border pixels (e.g., first `KSIZE/2` rows and columns for a filter) are invalid and must be skipped in the testbench comparison, not padded into the output.

> **Type consistency rule:** refactor_2 uses **the exact same types** as refactor_1. The only difference is the loop structure (flat pixel iteration instead of nested row/col loops). **Do NOT change types** when converting sample-based to frame-based. If refactor_1 uses `output_t` for output arrays, refactor_2 must also use `output_t`. Copy the typedef block verbatim from refactor_1 to refactor_2.

### Key Transformations

**1. Vectorized operation → pipelined loop**
```matlab
% MATLAB (sample-based — entire frame at once)
im1 = imfilter(im_in, filt1);
```
```cpp
// HLS (frame-based — one pixel per cycle)
for (int i = 0; i < FRAME_PIXELS; i++) {
    #pragma HLS pipeline II=1
    pixel_t px = window_center;
    int16_t im1 = apply_filt1(neighborhood_sums);
    out_stream.write(im1);
}
```

**2. 2D indexing → 1D raster scan with row/col counters**
```matlab
% MATLAB
[y, x] = ndgrid(0:ROWS-1, 0:COLS-1);
mask00 = and(not(bitget(y,1)), not(bitget(x,1)));
```
```cpp
// HLS — track row/col from loop counter
int row = i / COLS;
int col = i % COLS;         // or: maintain separate row/col counters
bool lsb_y = (row & 1);
bool lsb_x = (col & 1);
bool mask00 = !lsb_y && !lsb_x;
```

**3. imfilter → line buffer + sliding window**

MATLAB's `imfilter` applies a 2D kernel over the whole frame. In HLS, this becomes a **stateful sliding window** stage:
- A line buffer holds (KSIZE-1) previous rows
- A 2D window holds the current KSIZE×KSIZE neighborhood
- One new pixel enters per cycle; the window shifts; sums are computed
- Valid output is emitted after KSIZE/2 rows and KSIZE/2 columns of warmup

**4. Symmetric filter → pre-summed taps**

MATLAB filters with symmetric coefficients can be pre-summed before applying weights:
```cpp
// HLS — pre-sum symmetric pairs, then apply integer-scaled weights
ap_int<16> s_center   = win[2][2];
ap_int<16> s_horiz1   = win[2][1] + win[2][3];   // left + right
ap_int<16> s_vert1    = win[1][2] + win[3][2];   // above + below
// Apply coefficients as shifts+adds (no DSP multipliers needed)
ap_int<16> result = (s_center << 3) + (s_horiz1 << 2) - (s_vert1 << 1);
output = sat_u8(result >> 4);   // divide by 16 via right shift
```

**5. Filter denominator → right shift**

MATLAB divides filter outputs by a normalization constant (e.g., `/8`). In HLS, use a right shift:
- `/8` → `>> 3`
- `/16` → `>> 4`
- This eliminates division hardware; keep intermediate values in `ap_int<16>` or wider to avoid overflow before shifting.

---

### Critical Rule: Do NOT implement sliding windows in Step 4

Step 4 converts sample-based code to **frame-based loops** — it does NOT
implement streaming line buffers or sliding windows. That is /hls-architect's job.

**Correct approach for Step 4:**
- Use flat pixel loops with `row = k / COLS; col = k % COLS;`
- Access the grayscale buffer with clamped 2D indices: `gray[clamp(row±1) * COLS + clamp(col±1)]`
- Keep intermediate buffers (e.g., `gray[ROWS*COLS]`) between stages
- The structure should be: sequential loops over a shared buffer, NOT a single-pass pipeline

**Wrong approach (causes shift bugs):**
- Manual `line_buf[][]` + `win[][]` sliding window in a single loop
- Inline border replication that overwrites window state mid-shift
- Attempting to fuse grayscale + Sobel + threshold into one pass

The line buffer + sliding window transformation belongs in /hls-architect Step 2c
(compute decomposition), where /hls-dataflow and ./reference/hls-line-buffer.md skills
validate the implementation. Step 4's job is only to restructure array-level
MATLAB operations into pixel-level C++ loops.

---

### Write to frame_based/

Call `./reference/design-layout.md  design_name=<name>  stage=frame_based`

Write into `design_name/frame_based/`:
- `kernel.cpp`    — the refactor_2 frame-based C++ kernel
- `testbench.cpp` — same testbench as `sample_based/` (unchanged — same inputs, same golden comparison)

### Verify refactor_2 against MATLAB golden

Compile and run immediately after writing the files — same testbench, same golden `.bin` files:

```bash
cd design_name/frame_based/
# ap_fixed/ap_int/ap_uint headers ship with Vitis at $XILINX_VITIS/include
g++ -std=c++14 -I$XILINX_VITIS/include -o verify_frame kernel.cpp testbench.cpp && ./verify_frame
```

For OpenCV designs, add the include and link flags:

```bash
g++ -std=c++14 \
    -I$XILINX_VITIS/include \
    -I${OPENCV_INCLUDE} \
    -o verify_frame kernel.cpp testbench.cpp \
    -L${OPENCV_LIB} -lopencv_core -lopencv_imgcodecs -lopencv_imgproc \
    -Wl,-rpath,${OPENCV_LIB} \
&& ./verify_frame
```

Must print `PASS` before proceeding to Step 5. If it fails:
- A raster-order bug (scan direction, row/col swap) shows up here but not in refactor_1 — fix the loop structure
- A warmup border difference (filter edge pixels) — confirm the testbench skips the same border as in Step 2
- A type narrowing issue introduced during frame-based rewrite — check `ap_int`/`ap_fixed` intermediate widths match refactor_1

---

Print before proceeding:
```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 4 done — refactor_2 (frame-based)
  Key changes  : <vectorised→loop, 2D→1D scan, imfilter→line buffer, etc.>
  HLS types    : <ap_uint/ap_int/ap_fixed changes from refactor_1>
  Verify       : PASS / FAIL (<mismatch count if fail>)
  Handing off  : frame_based/kernel.cpp → /hls-architect
─────────────────────────────────────────────────────
✓ Step 0   Run MATLAB simulation → save golden I/O
✓ Step 0b  Range instrumentation → sim-measured types
✓ Step 1   Analyze MATLAB algorithm
✓ Step 2   Generate refactor_1 (plain C++) + verify
✓ Step 3   MATLAB → HLS construct mapping
✓ Step 4   Generate refactor_2 (frame-based C++)
  Step 5   Hand off to /hls-architect → /hls-optimize           ← NEXT
```

## Step 5 / 5 — Call `/hls-architect`

```
─────────────────────────────────────────────────────
[matlab-to-cpp]  Step 5 / 5 — Handing off to /hls-architect
─────────────────────────────────────────────────────
```

Once the plain C++ passes verification (Step 2), hand off to `/hls-architect`.

Always pass `THROUGHPUT_TARGET`, `XPART`, and `CLOCK_NS` collected in the Preamble:

```
/hls-architect <THROUGHPUT_TARGET>  part=<XPART>  clock=<CLOCK_NS>
```

If `THROUGHPUT_TARGET` is empty, pass an empty string — `/hls-architect` will derive the best achievable target from II=1 floor and pass it to `/hls-optimize` automatically:

```
/hls-architect  part=<XPART>  clock=<CLOCK_NS>
```

Provide as context:
- `design_name` — the workspace root (architect uses this to call `./reference/design-layout.md`)
- `design_name/frame_based/kernel.cpp` — the frame-based C++ kernel (input to architect)
- `design_name/frame_based/testbench.cpp` — the testbench (carried forward unchanged)
- The identified stages and their responsibilities (from Step 4)
- Input/output types, frame dimensions, filter coefficients

`/hls-architect` will:
- Convert the plain C++ into multi-stage HLS dataflow C++
- Reuse the **same testbench** from Step 2 (same inputs, same MATLAB golden comparison)
- Run the full hls* validation battery
- Run csim — a PASS confirms the HLS C++ matches the MATLAB golden reference
- Hand off to `/hls-optimize <throughput target>` (passing the target through directly)
