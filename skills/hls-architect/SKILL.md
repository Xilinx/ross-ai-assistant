---
name: hls-architect
description: HLS Design Architecture — convert input code to multi-stage HLS dataflow, validate with hls* skills, then hand off to optimize.
argument-hint: "[<throughput-target e.g. '140 FPS'>] [part=<fpga-part>] [clock=<ns>]"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# HLS Design Architecture

> Sources: "Parallel Programming for FPGAs" (Kastner et al.) + AMD UG1399 (Vitis HLS User Guide)

---

## Preamble — Parse Arguments and Print Summary

### Vitis environment

The Step 2e g++ verification depends on `$XILINX_VITIS` to locate `ap_fixed.h`.

- **When called by `/matlab-to-cpp`** (the normal flow): the environment was sourced
  in that skill's preamble — do NOT source it again, just verify it is set.
- **When called standalone**: source Vitis first via `./reference/setup.md` Step 1 or ensure `$XILINX_VITIS` is set

Verify before proceeding:
```bash
echo "XILINX_VITIS=$XILINX_VITIS" && ls $XILINX_VITIS/include/ap_fixed.h
```

### Parse arguments

Parse `$ARGUMENTS` at the very start:
- Everything before `part=` is the `THROUGHPUT_TARGET` (may be empty)
- `part=<value>` → `XPART`
- `clock=<value>` → `CLOCK_NS`

If `XPART` or `CLOCK_NS` are missing from `$ARGUMENTS`, ask the user for the missing values before proceeding.

### Input Discovery — Find or Create Workspace

The architect looks for an existing workspace, or creates one if needed. Detection is based solely on filesystem state (not context variables).

**Step 1: Check for existing frame_based/ workspace**

```bash
# Search for existing frame_based/kernel.cpp (from matlab-to-cpp or previous run)
EXISTING_KERNEL=$(find . -maxdepth 3 -path "*/frame_based/kernel.cpp" -type f | head -1)

if [ -n "$EXISTING_KERNEL" ]; then
  # Found existing workspace - extract design_name from path
  design_name=$(echo "$EXISTING_KERNEL" | cut -d'/' -f2)
  MODE=existing
else
  # No workspace found - need to create one
  MODE=new
fi
```

**Step 2a: If MODE=existing (workspace found)**

```bash
# Verify testbench also exists
if [ ! -f "$design_name/frame_based/testbench.cpp" ]; then
  ERROR: "Found frame_based/kernel.cpp but testbench.cpp is missing"
fi

# Print confirmation
echo "Found existing workspace: $design_name/frame_based/"
echo "  ✓ kernel.cpp"
echo "  ✓ testbench.cpp"
```

Proceed to Interface Selection.

**Step 2b: If MODE=new (no workspace found)**

1. Ask user for input file paths:
   - "Where is your C++ kernel source file?" → `USER_KERNEL_PATH`
   - "Where is your testbench file?" → `USER_TESTBENCH_PATH`

2. Ask user for design name (or derive from kernel filename):
   - "What should the design workspace be named?" → `design_name`
   - Default suggestion: extract basename without extension from kernel path

3. Create workspace and copy files:
   ```bash
   # Create frame_based/ directory
   mkdir -p "$design_name/frame_based"
   
   # Copy user files into canonical locations
   cp "$USER_KERNEL_PATH" "$design_name/frame_based/kernel.cpp"
   cp "$USER_TESTBENCH_PATH" "$design_name/frame_based/testbench.cpp"
   ```

4. Print confirmation:
   ```
   ─────────────────────────────────────────────────────
   [architect]  New Workspace Created
     Design name    : <design_name>
     Kernel copied  : <USER_KERNEL_PATH>
                    → <design_name>/frame_based/kernel.cpp
     TB copied      : <USER_TESTBENCH_PATH>
                    → <design_name>/frame_based/testbench.cpp
   ─────────────────────────────────────────────────────
   ```

**After Input Discovery completes (both modes):**
- `design_name/frame_based/kernel.cpp` exists ← INPUT to architect
- `design_name/frame_based/testbench.cpp` exists

Proceed to Interface Selection.

### Interface Selection

**In interactive mode**: ask the user which top-level interface the kernel should use.
Present the options clearly:

```
[architect]  Interface Selection — choose one:

  1. m_axi          Data in/out via AXI4 burst master (arrays in DDR/HBM).
                    Best for: frame buffers, large lookup tables, batch processing.
                    load_input() reads a full frame from memory before compute starts.
                    Example: peakPicker reading xcorr[] and threshold[] arrays.

  2. axis            Data in/out via AXI4-Stream (pixel/sample streaming, no buffering).
                    Best for: real-time pipelines, video, DSP chains.
                    load_input() forwards stream samples directly into compute.
                    Example: demosaic receiving a pixel stream from a camera sensor.

  3. m_axi + axis    Mixed — one port streams in, another bursts from memory (or vice versa).
                    Best for: kernels that stream output but need a lookup table from DDR.

  4. m_axi + s_axilite  Data arrays via m_axi; scalar parameters (thresholds, sizes) via
                         AXI-Lite control register. Most common for standalone Vitis kernels.

Which interface fits your design?
```

**If `autoconfirm=true` is in `$ARGUMENTS`**: do NOT ask — infer `IFACE_TYPE`
automatically from the top-level function signature:
- Pointer/array arguments (`float*`, `int[]`) with no `hls::stream` args → `m_axi`
- `hls::stream` arguments only → `axis`
- Mix of pointer and `hls::stream` → `m_axi + axis`
- Pointer args alongside scalar-only control args → `m_axi + s_axilite`

Print the inferred choice:
```
[architect]  Interface inferred: m_axi  (autoconfirm; pointer args detected)
```

Store the answer as `IFACE_TYPE`. Use it when writing `#pragma HLS INTERFACE` pragmas and when deciding whether `load_input` / `store_output` use `hls::stream` ports or `m_axi` pointer arguments.

Print confirmation:
```
─────────────────────────────────────────────────────
[architect]  Starting — <design_name>
  Throughput target : <THROUGHPUT_TARGET or "none — will derive from II=1 floor">
  FPGA part         : <XPART>
  Clock             : <CLOCK_NS> ns
  Interface         : <IFACE_TYPE>
─────────────────────────────────────────────────────
```

---

## Directory Layout

After Input Discovery, the canonical workspace structure is:

```
design_name/
├── frame_based/          ← INPUT (always exists after Input Discovery)
│   ├── kernel.cpp        ← Plain C++ kernel (created by matlab-to-cpp OR copied in standalone mode)
│   └── testbench.cpp     ← Testbench (created by matlab-to-cpp OR copied in standalone mode)
└── rearchitect/
    └── v1/               ← OUTPUT (created by architect via design-layout.md)
        ├── src/
        │   └── kernel_hls.cpp    ← HLS dataflow architecture
        ├── tb/
        │   └── testbench.cpp     ← Copied from frame_based/ unchanged
        └── hls_config.cfg         ← Generated config file
        └── vitis-comp.json        ← Generated hls component file
```

**Optional directories** (only present if called from /matlab-to-cpp):
```
design_name/
├── golden/               ← MATLAB source files + golden bins
├── sample_based/         ← Refactor 1 (line-by-line MATLAB → C++)
```

All directories under `rearchitect/` are created via `./reference/design-layout.md` — **never create directories manually**.

---

## Handoff Protocol

Every step ends with a `→ HANDOFF` block. **No step may begin until the previous step's Condition is satisfied.**

```
→ HANDOFF to Step X
  Produces : <artifact>
  Condition: <what must be true>
  Blocked if: <what causes a loop-back or stop>
```

---

## Flow Overview

Print this immediately after the confirmation banner so the user can track progress:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[architect]  Pipeline — <design_name>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Step 1   Study domain patterns (./reference/vitis-libraries.md)
  Step 2a  Draw activity timeline + choose paradigm
  Step 2b  Generate fixed top-level (load → compute → store)
  Step 2c  Decompose compute() — hierarchical or monolithic
  Step 2d  Write to rearchitect/ + pragma audit
  Step 2e  G++ functional verification
  Step 3a  Validate with hls* skills
  Step 3b  Architecture review (user approval)
  Step 3c  Write to perf_outcomes.md
  Step 3d-1  Apply performance pragmas (if throughput)
  Step 3d-2  Save baseline snapshot
  Step 3e  Hand off to /hls-optimize
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Re-print this after each step with `✓` on completed steps and `← NEXT` on the upcoming one.

---

## Workflow

### Step 1 / 3 — Study Domain Patterns

```
─────────────────────────────────────────────────────
[architect]  Step 1 / 3 — Study Domain Patterns (./reference/vitis-libraries.md)
─────────────────────────────────────────────────────
```

Invoke `./reference/vitis-libraries.md` to study coding patterns from production HLS implementations before writing any code. Do NOT include or link Vitis Library headers in user designs — study, then write your own.

Print before proceeding:
```
─────────────────────────────────────────────────────
[architect]  Step 1 done — Domain Patterns
  Patterns found : <list patterns identified from ./reference/vitis-libraries.md>
  Applies to     : <how each pattern maps to this algorithm>
  Gap            : <any applicable pattern NOT found — or "none">
─────────────────────────────────────────────────────
✓ Step 1   Study domain patterns (./reference/vitis-libraries.md)
  Step 2a  Draw activity timeline + choose paradigm     ← NEXT
  Step 2b  Generate fixed top-level (load → compute → store)
  Step 2c  Decompose compute() — hierarchical or monolithic
  Step 2d  Write to rearchitect/ + pragma audit
  Step 2e  G++ functional verification
  Step 3a  Validate with hls* skills
  Step 3b  Architecture review (user approval)
  Step 3c  Write to perf_outcomes.md
  Step 3d-1  Apply performance pragmas (if throughput)
  Step 3d-2  Save baseline snapshot
  Step 3e  Hand off to /hls-optimize
```

```
→ HANDOFF to Step 2
  Produces : Domain pattern notes (in-context — coding patterns from ./reference/vitis-libraries.md)
  Condition: At least one relevant HLS pattern identified for the input algorithm
  Blocked if: No applicable pattern found → document why, proceed to Step 2 anyway
```

---

### Step 2 / 3 — Generate Architecture

```
─────────────────────────────────────────────────────
[architect]  Step 2 / 3 — Generate Architecture
─────────────────────────────────────────────────────
```

Apply the **two paradigms** when converting input code to HLS:

| Paradigm | What it means | HLS mapping |
|---|---|---|
| **Producer-Consumer** | N tasks, each with one responsibility, communicating through channels | One `static` function per stage; top-level with `#pragma HLS DATAFLOW` |
| **Streaming Data** | Tasks exchange data via FIFOs (in-order, max overlap) or PIPOs (random-order, safer) | `hls::stream` for sequential access; PIPO for random access; avoid wide channels |

**Draw the activity timeline before coding** — shows when each stage executes across multiple invocations; reveals expected parallelism and bottlenecks.

> **Note:** Loop-level pipelining (`#pragma HLS PIPELINE`) is NOT applied by the architect — see Pragma Rules below. The performance pragma handles throughput targets.

```
→ HANDOFF to Fixed Top-Level Structure
  Produces : Activity timeline drawn; paradigm (producer-consumer / streaming) identified
  Condition: At least one activity timeline sketched showing stage overlap across invocations
  Blocked if: Timeline cannot be drawn → ask user for clarification on algorithm boundaries
```

---

#### Fixed Top-Level Structure

The top level **always** has exactly three functions connected by one `#pragma HLS DATAFLOW`:

```
load_input()  →  compute()  →  store_output()
```

- `load_input()` — reads from AXI master port(s), writes to stream(s) or PIPO(s)
- `compute()` — all algorithmic work; see decomposition rules below
- `store_output()` — reads from stream(s) or PIPO(s), writes to AXI master port(s)
- Channels: `hls::stream` for sequential access, local arrays (PIPO) for random access
- All `#pragma HLS INTERFACE` pragmas on top-level function args only

Invoke `/hls-dataflow` on the top-level function to confirm the `{load_input, compute, store_output}` region is canonical before proceeding.

Print before proceeding:
```
─────────────────────────────────────────────────────
[architect]  Fixed Top-Level — done
  Structure    : load_input() → compute() → store_output()
  Interface    : <IFACE_TYPE — pragmas applied>
  Channels     : <stream or PIPO — and why>
  /hls-dataflow: PASS
─────────────────────────────────────────────────────
✓ Step 1   Study domain patterns (./reference/vitis-libraries.md)
✓ Step 2a  Draw activity timeline + choose paradigm
✓ Step 2b  Generate fixed top-level (load → compute → store)
  Step 2c  Decompose compute() — hierarchical or monolithic  ← NEXT
  Step 2d  Write to rearchitect/ + pragma audit
  Step 2e  G++ functional verification
  Step 3a  Validate with hls* skills
  Step 3b  Architecture review (user approval)
  Step 3c  Write to perf_outcomes.md
  Step 3d-1  Apply performance pragmas (if throughput)
  Step 3d-2  Save baseline snapshot
  Step 3e  Hand off to /hls-optimize
```

```
→ HANDOFF to compute() Decomposition
  Produces : Top-level code (load_input + compute + store_output) with one #pragma HLS DATAFLOW
  Condition: /hls-dataflow passes on the top-level region
  Blocked if: /hls-dataflow fails → fix top-level structure; do NOT proceed to compute() Decomposition
```

---

#### compute() Decomposition

Analyze whether `compute()` can be internally divided into sequential sub-stages by invoking `/hls-dataflow` on the proposed decomposition.

**If `/hls-dataflow` passes → Hierarchical compute:**
```
compute():
  #pragma HLS DATAFLOW
    compute_stage1()  →  compute_stage2()  →  …  →  compute_stageN()
```
- Decompose into N `static void compute_stageX(...)` functions, split at natural algorithmic boundaries
- Add `#pragma HLS DATAFLOW` **inside** `compute()` over {compute_stage1, …, compute_stageN}
- N=1 requires no internal DATAFLOW — treat as monolithic

**If `/hls-dataflow` fails → Monolithic compute:**
- `compute()` contains sequential loops only — no internal `#pragma HLS DATAFLOW`
- Report the specific rule(s) flagged by `/hls-dataflow`

Print before proceeding:
```
─────────────────────────────────────────────────────
[architect]  compute() Decomposition — done
  Structure    : Hierarchical (<N> stages: <names>) / Monolithic (<reason>)
  Bottleneck   : <estimated stage> (~<N> cycles)
  /hls-dataflow: PASS / FAIL (<reason if fail>)
─────────────────────────────────────────────────────
✓ Step 1   Study domain patterns (./reference/vitis-libraries.md)
✓ Step 2a  Draw activity timeline + choose paradigm
✓ Step 2b  Generate fixed top-level (load → compute → store)
✓ Step 2c  Decompose compute() — hierarchical or monolithic
  Step 2d  Write to rearchitect/ + pragma audit          ← NEXT
  Step 2e  G++ functional verification
  Step 3a  Validate with hls* skills
  Step 3b  Architecture review (user approval)
  Step 3c  Write to perf_outcomes.md
  Step 3d-1  Apply performance pragmas (if throughput)
  Step 3d-2  Save baseline snapshot
  Step 3e  Hand off to /hls-optimize
```

```
→ HANDOFF to Write to rearchitect/
  Produces : compute() implementation — hierarchical (with internal DATAFLOW) or monolithic (reason documented);
             compute() code presented to user
  Condition: /hls-dataflow result recorded; compute() code is complete
  Blocked if: /hls-dataflow fails on decomposition and no valid monolithic fallback → ask user before proceeding
```

---

#### Write to rearchitect/

Call `./reference/design-layout.md  design_name=<name>  stage=rearchitect_v1`

This creates `design_name/rearchitect/v1/src/`, `design_name/rearchitect/v1/tb/`, a template `hls_config.cfg` and a template `vitis-comp.json`.

Write into `design_name/rearchitect/v1/`:
- `src/kernel_hls.cpp` — kernel source (load_input + compute + store_output)
- `tb/testbench.cpp`   — copied from `design_name/frame_based/testbench.cpp` unchanged (Input Discovery ensures this file exists)
- `hls_config.cfg`     — use this exact template (substituting `XPART`, `CLOCK_NS`, `TOP_FUNCTION`):

```ini
# ⚠ part= MUST be at the TOP LEVEL — never write syn.part= or put part inside [hls]
# ⚠ clock= uses ns period — never write syn.clkPeriod=
part=<XPART>

[hls]
syn.top=<TOP_FUNCTION>
clock=<CLOCK_NS>
syn.file=src/kernel_hls.cpp
tb.file=tb/testbench.cpp
```

**For testbenches that use OpenCV**, add these keys — use the exact key names:
```ini
tb.cflags=-I<OPENCV_INCLUDE>
csim.ldflags=-L<OPENCV_LIB> -lopencv_core -lopencv_imgcodecs -lopencv_imgproc -Wl,-rpath,<OPENCV_LIB>
```

**FORBIDDEN cfg patterns — never generate these:**
```ini
[hls]
syn.part=xczu9eg-...     ← not a valid field; v++ ignores or rejects it
syn.clkPeriod=3.5        ← wrong field name; use clock=
```


- `vitis-comp.json`     — use this exact template (substituting `TOP_FUNCTION` `design_name`):

```ini
{
   "name": <design_name>,
   "type": "HLS",
   "configuration": {
    "componentType": "HLS",
    "configFiles": ["./hls_config.cfg"],
    "work_dir": <TOP_FUNCTION>
   }
}
```


Present the generated code to the user.

```
→ HANDOFF to Pragma Rules
  Produces : kernel_hls.cpp written to design_name/rearchitect/v1/src/; directories confirmed by ./reference/design-layout.md
  Condition: top-level has exactly one #pragma HLS DATAFLOW over {load_input, compute, store_output};
             compute() is either hierarchical (with internal DATAFLOW) or monolithic (documented reason)
  Blocked if: code cannot be written (unresolvable dependency) → document reason, ask user before proceeding
```

---

### Pragma Rules — Self-Audit Gate

After the code is written, **scan every pragma** before proceeding to validation.

**Allowed in architect-generated code:**
```cpp
#pragma HLS performance target_ti=N
#pragma HLS INTERFACE   // m_axi, axis, s_axilite
#pragma HLS DATAFLOW
#pragma HLS STREAM variable=<stream_var> depth=<N>   // set FIFO depth on hls::stream channels
```

**FORBIDDEN — must not appear:**
```cpp
#pragma HLS PIPELINE
#pragma HLS UNROLL
#pragma HLS ARRAY_PARTITION
#pragma HLS ARRAY_RESHAPE
#pragma HLS INLINE
#pragma HLS FLATTEN
```

If any forbidden pragma is found → **remove it immediately** before proceeding.

Print this confirmation before moving on:
```
Pragma audit passed — code contains only: DATAFLOW, INTERFACE, performance.
```

> **Rationale:** `#pragma HLS performance` gives HLS the throughput target and lets it
> infer micro-optimizations automatically. The architect controls macro-architecture only.
> Fine-grained pragmas are the responsibility of `/hls-optimize`.

Print before proceeding:
```
─────────────────────────────────────────────────────
[architect]  Pragma audit — done
  Pragmas present : <list all #pragma HLS lines in the file>
  Forbidden found : none
─────────────────────────────────────────────────────
✓ Step 1   Study domain patterns (./reference/vitis-libraries.md)
✓ Step 2a  Draw activity timeline + choose paradigm
✓ Step 2b  Generate fixed top-level (load → compute → store)
✓ Step 2c  Decompose compute() — hierarchical or monolithic
✓ Step 2d  Write to rearchitect/ + pragma audit
  Step 2e  G++ functional verification                  ← NEXT
  Step 3a  Validate with hls* skills
  Step 3b  Architecture review (user approval)
  Step 3c  Write to perf_outcomes.md
  Step 3d-1  Apply performance pragmas (if throughput)
  Step 3d-2  Save baseline snapshot
  Step 3e  Hand off to /hls-optimize
```

```
→ HANDOFF to Step 2e
  Produces : Pragma-clean source file; audit confirmation line printed
  Condition: Zero forbidden pragmas
  Blocked if: Forbidden pragma cannot be cleanly removed → ask user before deleting
```

---

### Step 2e — G++ Functional Verification

```
─────────────────────────────────────────────────────
[architect]  Step 2e — G++ Functional Verification
─────────────────────────────────────────────────────
```

Before invoking the hls* validation battery, prove the rearchitected code is
**functionally correct** by compiling with g++ and running against the MATLAB golden.
This catches regressions introduced by the architecture transformation (load_input/
compute/store_output split, channel insertion, hierarchical decomposition) BEFORE
the expensive hls* validation cycle.

#### Compile

```bash
cd <design_name>/rearchitect/v1/

# ap_fixed/ap_int/ap_uint headers ship with Vitis at $XILINX_VITIS/include
g++ -std=c++14 \
    -I$XILINX_VITIS/include \
    -o verify_arch src/kernel_hls.cpp tb/testbench.cpp \
&& ./verify_arch
```

For OpenCV designs (when the testbench uses `cv::imread`):

```bash
g++ -std=c++14 \
    -I$XILINX_VITIS/include \
    -I${OPENCV_INCLUDE} \
    -o verify_arch src/kernel_hls.cpp tb/testbench.cpp \
    -L${OPENCV_LIB} -lopencv_core -lopencv_imgcodecs -lopencv_imgproc \
    -Wl,-rpath,${OPENCV_LIB} \
&& ./verify_arch
```

#### Pass criteria

The executable must print `PASS` on both calls (the testbench runs the kernel
twice for II measurement). If FAIL:

- Compare against the `frame_based/` verification — it passed there
- Common regressions:
  - channel/stream byte order swap in `load_input` → `store_output`
  - `compute_stage` decomposition split a dependency
  - pragmas were stripped that the algorithm needs (e.g., `DATAFLOW`)
- Fix `rearchitect/src/kernel_hls.cpp`; do NOT proceed to Step 3a

Print before proceeding:
```
─────────────────────────────────────────────────────
[architect]  Step 2e done — G++ functional verification
  Compile      : OK / FAIL (<error if any>)
  Run result   : PASS / FAIL (<mismatch count if FAIL>)
  Status       : <ok to proceed to hls* validation / blocked>
─────────────────────────────────────────────────────
✓ Step 1   Study domain patterns (./reference/vitis-libraries.md)
✓ Step 2a  Draw activity timeline + choose paradigm
✓ Step 2b  Generate fixed top-level (load → compute → store)
✓ Step 2c  Decompose compute() — hierarchical or monolithic
✓ Step 2d  Write to rearchitect/ + pragma audit
✓ Step 2e  G++ functional verification
  Step 3a  Validate with hls* skills                    ← NEXT
  Step 3b  Architecture review (user approval)
  Step 3c  Write to perf_outcomes.md
  Step 3d-1  Apply performance pragmas (if throughput)
  Step 3d-2  Save baseline snapshot
  Step 3e  Hand off to /hls-optimize
```

```
→ HANDOFF to Step 3a (hls* validation)
  Produces : verify_arch executable, both calls PASS
  Condition: g++ compiles cleanly AND ./verify_arch prints PASS twice
  Blocked if: compile error or mismatch → fix kernel_hls.cpp before Step 3a
```

---

### Step 3 / 3 — Validate, Record, and Optimize

```
─────────────────────────────────────────────────────
[architect]  Step 3 / 3 — Validate → Record → Optimize
─────────────────────────────────────────────────────
```

#### 3a — Validate with hls* Skills

Run **all** of the following. Fix any violations before proceeding.

```
╔══════════════════════════════════════════════════════════════╗
║     HLS Validation Report — <design name>                    ║
╠══════════════════╦══════════════════════════╦═══════════════╣
║ Skill            ║ What it checks           ║ Result        ║
╠══════════════════╬══════════════════════════╬═══════════════╣
║ hls-synthesizable║ Synthesizable code       ║ ✅ PASS       ║
║ hls-dataflow-info║ Process/channel topology ║ ✅ PASS       ║
║ hls-array-stream ║ Array→stream opportunity ║ ⚠️  N/A       ║
║ hls-burst-infer  ║ AXI burst inference      ║ ❌ FAIL       ║
║ hls-flattenable  ║ Loop nest flattenable    ║ ✅ PASS       ║
║ hls-stencil      ║ Stencil optimization     ║ ⚠️  N/A       ║
║ hls-line-buffer  ║ Line buffer II=1 recipe  ║ ⚠️  N/A       ║
╠══════════════════╩══════════════════════════╩═══════════════╣
║ Overall: X/7 passed   Violations: <list skills that failed> ║
╚══════════════════════════════════════════════════════════════╝
```

**Result key:** `✅ PASS` — ok | `❌ FAIL` — fix before proceeding | `⚠️  N/A` — not applicable

Reference skills for fixes:

| Violation in | Fix with |
|---|---|
| `hls-synthesizable` | `/hls-synthesizable`|
| `hls-dataflow` | `/hls-dataflow`|
| `hls-burst-inference` | `/hls-burst-inference`|
| `hls-flattenable` | `/hls-flattenable`|
| `hls-line-buffer` | `/hls-line-buffer` → re-check all 8 steps |

If any `❌ FAIL` → fix violation, re-run that specific skill, reprint the table; repeat until clean.

---

#### 3b — Architecture Review Gate

Check `$ARGUMENTS` for `autoconfirm=true`. If present, skip the approval wait and auto-proceed.

Present the architecture summary:

```
─────────────────────────────────────────────────────
[architect]  Architecture Review — <design_name>
─────────────────────────────────────────────────────
  Top-level  : load_input() → compute() → store_output()
  Interface  : <IFACE_TYPE>
  compute()  : <Hierarchical — N stages>  OR  <Monolithic — reason>
  Bottleneck : <stage_name>  (~N cycles)
  Est. rate  : ~X FPS @ Y MHz
  hls* checks: all ✅ PASS
─────────────────────────────────────────────────────
✓ Step 1   Study domain patterns (./reference/vitis-libraries.md)
✓ Step 2a  Draw activity timeline + choose paradigm
✓ Step 2b  Generate fixed top-level (load → compute → store)
✓ Step 2c  Decompose compute() — hierarchical or monolithic
✓ Step 2d  Write to rearchitect/ + pragma audit
✓ Step 2e  G++ functional verification
✓ Step 3a  Validate with hls* skills
  Step 3b  Architecture review                           ← HERE
  Step 3c  Write to perf_outcomes.md
  Step 3d-1  Apply performance pragmas (if throughput)
  Step 3d-2  Save baseline snapshot
  Step 3e  Hand off to /hls-optimize
```

**If `autoconfirm=true` is in `$ARGUMENTS`**: print `[architect] autoconfirm=true — auto-proceeding` and continue immediately. Do NOT wait for input.

**Otherwise**: ask the user:

> **Does this architecture match your expectations?**
> - Reply **yes** to proceed to perf_outcomes.md and /hls-optimize
> - Reply with feedback to revise the architecture

Wait for an explicit "yes" before continuing. If the user gives feedback:
1. Acknowledge — summarise what will change
2. Return to Step 2 (Fixed Top-Level Structure or compute() Decomposition as appropriate)
3. Re-run Step 3a validation on the revised code
4. Re-present this review gate
5. Repeat until the user says yes

If the user gives feedback:
1. Acknowledge — summarise what will change
2. Return to Step 2 (Fixed Top-Level Structure or compute() Decomposition as appropriate)
3. Re-run Step 3a validation on the revised code
4. Re-present this review gate
5. Repeat until the user says yes

```
→ HANDOFF to Write Architecture to perf_outcomes.md
  Produces : Explicit "yes" approval from user
  Condition: User has confirmed the architecture matches expectations
  Blocked if: User gives revision feedback → loop back to Step 2 → Step 3a → re-present review
```

---

#### 3c — Write Architecture to perf_outcomes.md

Write the design configuration to a repo-local memory file. Resolve the repo root first so the path works regardless of where the skill was invoked:

```bash
REPO_ROOT="$(git -C . rev-parse --show-toplevel 2>/dev/null || pwd)"
PERF_LOG="$REPO_ROOT/.claude/memory/perf_outcomes.md"
mkdir -p "$REPO_ROOT/.claude/memory"
```

Then:

**If `$PERF_LOG` does not exist** — create it with the full template (header, All Designs Summary table, Design section).
**If `$PERF_LOG` exists** — append a new Design section; add one row to the All Designs Summary table. Never overwrite existing content.

```markdown
## Design: <name>

### Configuration

| Field | Value |
|---|---|
| Description | <one-line description of what the kernel does> |
| Part | <XPART from hls_config.cfg> |
| Clock | <CLOCK_NS> ns = <MHz> MHz |
| Target | <throughput target if given by user, else "TBD"> |
| Architecture | Hierarchical (compute internally staged)  OR  Monolithic (compute atomic) |
| Working dir | <path to design directory> |
| Source file | <path to kernel source file> |
| Build command | `make csynth`  or  `python run.py csynth` |

### Architecture

#### Stage Breakdown

| Stage | Function | Input | Output | Loop trip count | Est. cycles |
|---|---|---|---|---|---|
| 1 | load_input()     | <m_axi port> | stream / PIPO | <N> | <N> |
| 2 | compute()        | stream / PIPO | stream / PIPO | <N> | <N> |
| N | store_output()   | stream / PIPO | <m_axi port>  | <N> | <N> |

#### compute() Internal Stages (if hierarchical)

| Sub-stage | Function | Input | Output | Loop trip count | Est. cycles |
|---|---|---|---|---|---|
| 1 | compute_stage1() | stream / PIPO | stream / PIPO | <N> | <N> |
| … | compute_stage2() | … | … | … | … |

#### Bottleneck

| Bottleneck stage | Est. cycles | Est. throughput @ <MHz> MHz |
|---|---|---|
| <stage_name> | <N> cycles | ~<X> FPS  /  ~<Y> Msps |

### Target TI Calculation

(To be filled in by /hls-optimize Step 1b after throughput target is confirmed)

### Attempt History

| # | target_ti | Achieved TI | Achieved Rate | Outcome |
|---|---|---|---|---|
| — | — | — | — | Architecture written by /hls-architect — optimization not started |
```

---

#### 3d-1 — Apply Performance Pragmas (if throughput target)

**IF THROUGHPUT_TARGET is EMPTY**: Skip to Step 3d-2

**IF THROUGHPUT_TARGET is NON-EMPTY**:

Call `/hls-perf-pragma` to:
1. Calculate target_ti from throughput
2. Cascade through architecture/loops
3. Apply pragmas to source (Position 1: top-level, Position 2: all loops)
4. Record cascade table in perf_outcomes.md

```bash
cd <design_name>/rearchitect/v1/
/hls-perf-pragma $THROUGHPUT_TARGET
```

hls-perf-pragma will:
- Modify src/*.cpp (add pragmas)
- Write cascade table to perf_outcomes.md

**DO NOT git commit yet** — commit happens after baseline snapshot.

Print:
```
─────────────────────────────────────────────────────
[architect]  Step 3d-1 ✓ Pragmas Applied
  Throughput     : $THROUGHPUT_TARGET
  Target TI      : <value> cycles
  Applied to     : Top-level function + <N> loops
  Recorded in    : perf_outcomes.md
─────────────────────────────────────────────────────
```
#### 3d-2 — Save Baseline Snapshot

Before handing off to `/hls-optimize`, save a clean copy of the architect-generated code:

```bash
cd <design_name>
cp -r rearchitect/v1 architect_baseline
echo "Baseline saved to architect_baseline/"
```

This preserves the architecture-stage output before `/hls-optimize` begins iterating, allowing you to:
- Compare optimize iterations against the architect baseline
- Revert to the clean architecture if optimization goes off-track
- Archive the macro-architecture separately from micro-optimizations

Print confirmation:
```
─────────────────────────────────────────────────────
[architect]  Baseline snapshot saved
  Source      : <design_name>/rearchitect/v1/
  Snapshot    : <design_name>/architect_baseline/
  Contents    : src/*.cpp, tb/*.cpp, hls_config.cfg, vitis-comp.json
─────────────────────────────────────────────────────
```

---

#### 3e — Hand Off to Optimize

```
─────────────────────────────────────────────────────
[architect]  Handing off to /hls-optimize
─────────────────────────────────────────────────────
✓ Step 1   Study domain patterns
✓ Step 2a  Draw activity timeline + choose paradigm
✓ Step 2b  Generate fixed top-level (load → compute → store)
✓ Step 2c  Decompose compute() — hierarchical or monolithic
✓ Step 2d  Write to rearchitect/ + pragma audit
✓ Step 2e  G++ functional verification
✓ Step 3a  Validate with hls* skills
✓ Step 3b  Architecture review (user approval)
✓ Step 3c  Write to perf_outcomes.md
✓ Step 3d-1  Apply performance pragmas (if throughput)
✓ Step 3d-2  Save baseline snapshot
  Step 3e  Hand off to /hls-optimize                       ← NOW
```

Invoke `/hls-optimize` passing `THROUGHPUT_TARGET` parsed from `$ARGUMENTS`:

**If THROUGHPUT_TARGET is non-empty:**
```
/hls-optimize <THROUGHPUT_TARGET>
```

**If THROUGHPUT_TARGET is empty** — ask the user:

> "What is your throughput target? (e.g. 140 FPS, 500 Msps, 1 GFLOPS)"

Wait for the response, then invoke:
```
/hls-optimize <user-provided target>
```

> **Note on pragma scope:** `/hls-optimize` may internally use `#pragma HLS PIPELINE`, `UNROLL`, `ARRAY_PARTITION`, etc. — the Pragma Rules restriction above applies **only to architect-generated code**, not to the optimize skill.

The optimize skill runs: hls-perf-pragma → csim → csynth → iterate → cosim.

---

## Reference

### §1 — AMD UG1399 Prescription (Checklist)

> "Software written for CPUs and software written for FPGAs is fundamentally different. Embrace this."

1. **Establish verification first** — testbench with golden reference before any optimization
2. **Focus on macro-architecture first** — model with producer-consumer paradigm
3. **Draw the activity timeline** — identify parallelism and bottlenecks across multiple invocations
4. **Only code after macro-architecture is set** — no premature micro-optimization
5. **HLS infers task-level parallelism only from function calls** — concurrent blocks must be separate functions
6. **Decompose into small modular components** — smaller components can be replicated for parallelism
7. **Aim for a single loop nest per function** — simplifies throughput measurement
8. **Avoid wide channels** — decompose into narrower ones
9. **Avoid large inlined functions** — complex control paths harm tool QoR
10. **Size streams correctly** — FIFO depth must prevent deadlock
11. **Use HLS reports to guide optimization** — never optimize blind
