<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# AMD Vitis HLS™ — LLM Skills for Algorithm → RTL

A collection of [Claude Code](https://claude.com/product/claude-code) skills that take an algorithm written in **MATLAB** or **C++** and walk it all the way to **synthesizable, optimized RTL** for AMD FPGAs using **Vitis HLS**.

Skills live under `.claude/skills/`. Each one owns a single, well-defined stage of the flow.

> **First time here?** Read top-to-bottom — sections are in the order you will actually use the skills.

---

## Software Requirements

### Required Software

- **Claude Code**: Required to invoke skills
  
- **AMD Vitis HLS**: Version 26.1 or 25.2
  - Verification command: `which vitis-run`
  
- **MATLAB**: 2025b (for `/hls-matlab-to-cpp` workflow)
  - Required for running `.m` scripts and generating golden reference files
  
- **OpenCV**: Version 4.x (conditional - only if testbench uses OpenCV)
  - Installation guide: https://adaptivesupport.amd.com/s/article/Vitis-Libraries-Compiling-and-Installing-OpenCV?language=en_US

### Environment Setup

**Set environment variables in your shell or make them permanent**

Export in your shell **before** launching Claude Code (recommended):
```bash
# In your terminal (bash/csh), BEFORE launching Claude Code:
export OPENCV_INCLUDE=/path/to/opencv/install/include/opencv4
export OPENCV_LIB=/path/to/opencv/install/lib
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$OPENCV_LIB
export MATLAB_BIN=/path/to/matlab/bin/matlab

# Then launch Claude Code from THIS shell
claude-code
```

Claude Code inherits environment variables from the shell it was launched from.

**Or make it permanent** (add to `.bashrc` or `.cshrc`):
```bash
# Add to ~/.bashrc (bash) or ~/.cshrc (csh):
export OPENCV_INCLUDE=/path/to/opencv/install/include/opencv4
export OPENCV_LIB=/path/to/opencv/install/lib
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$OPENCV_LIB
export MATLAB_BIN=/path/to/matlab/bin/matlab
```

Then every new shell (and Claude Code launched from it) will have these variables.

**What [`/setup`](.claude/skills/setup/SKILL.md) does**:
- **Vitis HLS**: Verifies `$XILINX_VITIS` is set and `vitis-run` is on `$PATH` (source your Vitis installation's `settings64.sh` beforehand if not already set)
- **OpenCV**: Uses `$OPENCV_INCLUDE` and `$OPENCV_LIB` from your environment
- **MATLAB**: Uses `$MATLAB_BIN` from your environment or finds `matlab` on `$PATH`

---

## Run Your Design

### Option 1: MATLAB → HLS C++

Start with MATLAB algorithm and go all the way to optimized RTL.

In Claude Code, `cd` to the folder containing your `<design_name>.m` and its testbench, then:

```
/hls-matlab-to-cpp  <design_name>  <throughput_target>  part=<fpga_part>  clock=<ns>
```

Where:
- `<design_name>` — the `.m` filename **without** the extension (e.g. `rgbEdgeDetector`, not `rgbEdgeDetector.m`)
- `<throughput_target>` — `<N> FPS` for image kernels, `<N> Msps` for sample-rate DSP, etc.
- `<fpga_part>` — full Xilinx part string (find via `lsdev -p` or AMD docs)
- `<ns>` — clock period in nanoseconds (e.g. `3.3` ≈ 303 MHz)

**Example:**
```
/hls-matlab-to-cpp  rgbEdgeDetector  4400 FPS  part=xczu9eg-ffvb1156-2-e  clock=3.3
```

This runs the entire pipeline: [`/setup`](.claude/skills/setup/SKILL.md) → [`/hls-matlab-to-cpp`](.claude/skills/hls-matlab-to-cpp/SKILL.md) → [`/architect`](.claude/skills/architect/SKILL.md) → [`/hls-optimize`](.claude/skills/hls-optimize/SKILL.md).

---

### Option 2: C++ → HLS C++

Start with existing C++ code (kernel + testbench) and optimize to meet performance targets.

In Claude Code, `cd` to the folder containing your `kernel.cpp`, `kernel.hpp`, and `main.cpp`:

```
/hls-optimize  <optimization_criteria>
```

**Optimization criteria:** Natural language describing your goal

Examples:
- `/hls-optimize minimize latency`
- `/hls-optimize minimize DSP usage while meeting timing`
- `/hls-optimize maximize throughput (minimize II)`
- `/hls-optimize reduce LUT count below 5000`

**Matrix Multiplication Example:**

```bash
cd hls-intro-matmul
```

Then in Claude Code:
```
/hls-optimize make it 8x faster than baseline
```

This runs: [`/hls-optimize`](.claude/skills/hls-optimize/SKILL.md).

The skill will:
1. Analyze your `kernel.cpp` (simple triple-nested matrix multiply)
2. Generate HLS DATAFLOW architecture
3. Optimize pragmas to meet the criteria (8x speedup)
4. Produce RTL ready for integration

### What success looks like

Expect ~30 minutes for a clean end-to-end run on a small kernel. At each stage the skill prints a banner; the final `/hls-optimize` summary should look like:

```
─────────────────────────────────────────────────────
[optimize]  done
  ✓ /perf-pragma  target_ti = 68847 cycles  (4400 FPS @ 303 MHz)
  ✓ /csim         PASS — max abs error = 0
  ✓ /csynth       II = 1   latency = 68843 cycles   target_ti met
  ✓ /cosim        post-RTL latency = 68847 cycles
  ✓ Resources     LUT 12,453   FF 9,872   BRAM 14   DSP 21
─────────────────────────────────────────────────────
```

If the printed `target_ti met = no` after `/hls-optimize`, the skill loops back into pragma tuning automatically; you'll see multiple `/csim → /csynth` cycles with the chosen pragma diff between iterations.

---

## The big picture

```
  ┌────────────┐  /hls-matlab-to-cpp  ┌──────────────┐  /architect   ┌──────────────────┐  /hls-optimize   ┌──────────────────┐
  │  MATLAB    │ ───────────────► │  frame_based │ ────────────► │  rearchitect/v1  │ ───────────► │   optimized RTL  │
  │ algorithm  │                  │  kernel.cpp  │               │   (load/compute  │              │  (target_ti met, │
  │     .m     │                  │   + tb.cpp   │               │   /store + DF)   │              │   pragmas tuned) │
  └────────────┘                  └──────────────┘               └──────────────────┘              └──────────────────┘
```

Three handoffs, each owned by one skill:

| Handoff | Skill | Input | Output |
|---|---|---|---|
| **MATLAB → C++** | `/hls-matlab-to-cpp` | `algorithm.m` + golden test | `frame_based/kernel.cpp` (functionally identical, fixed-point types) |
| **C++ → HLS macro-architecture** | `/architect` | `frame_based/kernel.cpp` | `rearchitect/v1/kernel_hls.cpp` (load → compute → store, DATAFLOW) |
| **Macro-arch → optimized RTL** | `/hls-optimize` | `kernel_hls.cpp` + throughput target | Pragma-tuned source + RTL meeting `target_ti` |

---

## Stage 1 — MATLAB → C++   (`/hls-matlab-to-cpp`)

Converts a sample-based MATLAB algorithm to frame-based C++ with **bit-exact fixed-point types**.

```
  Input (your files)              /hls-matlab-to-cpp does this              Output tree
  ──────────────────              ──────────────────────                ───────────
  ┌──────────────┐                ┌─────────────────────────┐           ┌──────────────────┐
  │ algorithm.m  │                │ 1. Run MATLAB → capture │           │ design/golden/        │
  │ testbench.m  │ ─────────────► │    matlab_input.bin     │ ────────► │ design/sample_based/  │
  │              │                │    matlab_golden.bin    │           │ design/frame_based/   │
  └──────────────┘                │ 2. Refactor 1: sample-  │           └──────────────────┘
                                  │    based C++ (line-by- │
                                  │    line port)          │
                                  │ 3. Range analysis →    │
                                  │    ap_fixed<W,I> types │
                                  │ 4. Refactor 2: frame-  │
                                  │    based C++           │
                                  │ 5. Verify vs golden    │
                                  └─────────────────────────┘
```

**Why two refactors?** The sample-based step preserves MATLAB semantics so divergence is caught early. The frame-based step is the shape HLS expects.

**Key artifact:** `frame_based/kernel.cpp` — passes the same golden as the MATLAB.

---

## Stage 2 — C++ → HLS macro-architecture   (`/architect`)

Converts the frame-based C++ into a producer-consumer HLS dataflow. The architect chooses **macro structure only** — pragmas like `PIPELINE`/`UNROLL` are deferred to `/hls-optimize`.

```
  ┌─────────────────────────────── #pragma HLS DATAFLOW ────────────────────────────────┐
  │                                                                                     │
  │   ┌──────────────┐         ┌──────────────┐          ┌──────────────┐               │
  │   │ load_input() │ stream  │   compute()  │ stream   │ store_output()│              │
  │   │              │ ──────► │              │ ───────► │               │              │
  │   │ m_axi → strm │         │  algorithmic │          │  strm → m_axi │              │
  │   └──────────────┘         │     work     │          └──────────────┘               │
  │                            └──────┬───────┘                                         │
  │                                   │                                                 │
  │                       (if decomposable, hierarchical:)                              │
  │                                   ▼                                                 │
  │   ┌────────────── inner #pragma HLS DATAFLOW ──────────────┐                        │
  │   │  compute_stage1 ──► compute_stage2 ──► … ──► stageN    │                        │
  │   └──────────────────────────────────────────────────────────┘                      │
  └─────────────────────────────────────────────────────────────────────────────────────┘
```

**The architect is allowed to write only:** `DATAFLOW`, `INTERFACE`, `STREAM depth=N`, `performance target_ti=N`.
**It must NOT write:** `PIPELINE`, `UNROLL`, `ARRAY_PARTITION`, `ARRAY_RESHAPE`, `INLINE`, `FLATTEN`. Those belong to `/hls-optimize`.

**Key artifact:** `rearchitect/v1/src/kernel_hls.cpp` + `hls_config.cfg`. After this stage the architect saves a snapshot to `architect_baseline/` before handing off.

---

## Stage 3 — Optimization loop   (`/hls-optimize`)

Closes the loop between **what you target** and **what HLS achieves**. Iterates pragmas until throughput target is met.

```
  throughput target (e.g. 140 FPS)
              │
              ▼
  ┌──────────────────────┐
  │  /perf-pragma        │  compute target_ti cascade
  │  top + per-loop      │
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐
  │  /csim               │  verify functional + profile loop trip counts
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐
  │  /csynth             │  → RTL + latency/II/resource report
  └──────────┬───────────┘
             │
       target met?
        │        │
       no        yes
        │         │
        ▼         ▼
  ┌─────────┐   ┌──────────┐    ┌──────────┐
  │ tune 1  │   │ /cosim   │ ─► │ /impl    │ ─► done
  │ pragma  │   │ true RTL │    │ P&R real │
  │ (loop ▲)│   │ latency  │    │   Fmax   │
  └────┬────┘   └──────────┘    └──────────┘
       └──────────► back to /csim
```

Each iteration changes **one thing** so cause-and-effect stays clean. Outcomes recorded in `perf_outcomes.md`.

---

## Directory layout per design

```
design_name/
├── golden/               ← MATLAB sources + matlab_input.bin + matlab_golden.bin
├── sample_based/         ← refactor 1: line-by-line C++ port
├── frame_based/          ← refactor 2: frame-based C++ (HLS-shaped)
└── rearchitect/
    └── v1/
        ├── src/                 ← kernel_hls.cpp (load → compute → store)
        ├── tb/                  ← testbench.cpp (copied from frame_based/)
        ├── hls_config.cfg
        └── architect_baseline/  ← snapshot saved before /hls-optimize
```

`design-layout` skill manages this tree — never `mkdir` manually.

---

## Skill catalog

| Skill | Purpose |
|---|---|
| [`/setup`](.claude/skills/setup/SKILL.md) | Verify Vitis / MATLAB / OpenCV; discover design + build commands |
| [`/hls-matlab-to-cpp`](.claude/skills/hls-matlab-to-cpp/SKILL.md) | MATLAB → frame-based C++ with `ap_fixed` types |
| [`/design-layout`](.claude/skills/design-layout/SKILL.md) | Create canonical directory tree per stage (`golden/`, `frame_based/`, `rearchitect/v1/` …) |
| [`/architect`](.claude/skills/architect/SKILL.md) | Frame-based C++ → HLS dataflow macro-architecture |
| [`/perf-pragma`](.claude/skills/perf-pragma/SKILL.md) | Cascade `target_ti` from top-level → loops |
| [`/hls-optimize`](.claude/skills/hls-optimize/SKILL.md) | Iterate pragmas until throughput target met |
| [`/vitis-libraries`](.claude/skills/vitis-libraries/SKILL.md) | Study production HLS coding patterns before writing |
| [`/hls-line-buffer`](.claude/skills/hls-line-buffer/SKILL.md) | Stencil II=1 line-buffer recipe |

**Validation skills (called by `/architect` to check the generated code):**

- [`/hls-dataflow`](.claude/skills/hls-dataflow/SKILL.md) — DATAFLOW canonical-form check
- [`/hls-synthesizable`](.claude/skills/hls-synthesizable/SKILL.md) — guards against unsynthesizable C++
- [`/hls-burst-inference`](.claude/skills/hls-burst-inference/SKILL.md) — verify AXI bursts will be inferred
- [`/hls-flattenable`](.claude/skills/hls-flattenable/SKILL.md) — loop-nest flatten eligibility
- [`/hls-array-to-stream`](.claude/skills/hls-array-to-stream/SKILL.md) — opportunities to convert PIPO → stream
- [`/hls-stencil-pattern`](.claude/skills/hls-stencil-pattern/SKILL.md) — stencil-shape recognition

---

## Example Designs

End-to-end examples under this repo:

**MATLAB → HLS Examples:**
- **[`test-demosaic/`](test-demosaic/)** — Bayer demosaicing (Malvar et al. linear interpolation)
- **[`test-edge-detection/`](test-edge-detection/)** — RGB edge detection (Sobel-style, 3 modes)

**C++ → HLS Examples:**
- **[`hls-intro-matmul/`](hls-intro-matmul/)** — Matrix multiplication (simple triple-nested loop baseline)

Each has its own README with the algorithm description and how to drive the LLM flow against it.

---

---

## License

MIT. Copyright © 2026 AMD.
