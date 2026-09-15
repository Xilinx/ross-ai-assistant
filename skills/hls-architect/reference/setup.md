---
description: Set up the HLS design environment — verify Vitis, OpenCV, discover design config, detect build system. Run once at the start of any hls-architect, csim, csynth, cosim, or optimize session. For MATLAB setup, use matlab-to-cpp/reference/setup.md instead.
argument-hint: "[mode=tooling-only]   # tooling-only skips design discovery (use when no design exists yet)"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Skill: setup

Prepare the environment for an HLS design session. Run this:
- **From `/hls-architect`, `/hls-run-flow`, `/hls-optimize`** with no args (full setup including design discovery)
- **From other contexts** with `mode=tooling-only` (only Vitis / OpenCV checks, skip design discovery)

**Note:** This setup does NOT include MATLAB. If you need MATLAB (for `/matlab-to-cpp`), use `../matlab-to-cpp/reference/setup.md` instead — it delegates to this file and adds MATLAB on top.

If `$ARGUMENTS` contains `mode=tooling-only`, **skip Step 2 (design discovery) and Step 3 (build commands)**. Only Steps 1 and 4 run.

Print at the start:
```
─────────────────────────────────────────────────────
[hls-architect/setup]  required tooling
  Step 1  — Verify Vitis HLS         (sources Vitis, exports $XILINX_VITIS)
  Step 4  — Verify OpenCV            (only if testbench uses cv::imread)
[hls-architect/setup]  design configuration  (skipped in tooling-only mode)
  Step 2  — Discover the design
  Step 3  — Build commands
─────────────────────────────────────────────────────
```

---

## Step 1: Verify Vitis environment

```bash
which vitis-run
```

If not found, check if the environment variable XILINX_VITIS is set. If not, ask the user for the value of XILINX_VITIS. Then source the settings file:

```bash
source $XILINX_VITIS/settings64.sh && which vitis-run
```

Do not proceed until `vitis-run` is on PATH.

Print after Step 1:
```
─────────────────────────────────────────────────────
[hls-architect/setup]  Step 1 — done
  ✓ Step 1  Vitis <version> on PATH  (<path to vitis-run>)
  ← NEXT    Step 2 — Discover the design
─────────────────────────────────────────────────────
```

---

## Step 2: Discover the design

Ask the user for the design directory path. Once provided, find the HLS config file (name varies by design):

```bash
grep -rl "^\[hls\]" *.cfg 2>/dev/null | head -1
```

Store the result as `<config_file>`. Then read the key settings from it:

```bash
grep -E "^(syn\.top|clock|part|syn\.file)" <config_file>
```

Extract and hold for the rest of the session:
- `CONFIG_FILE` — the detected config filename (e.g. `PerfPragma.cfg`, `hls_config.cfg`)
- `TOP_FUNCTION` — top-level HLS function name, if missing use default 'top-function'
- `CLOCK_NS` — clock period in ns
- `XPART` — FPGA part
- `SRC_FILES` — all `syn.file=` entries

Print after Step 2:
```
─────────────────────────────────────────────────────
[hls-architect/setup]  Step 2 — done
  ✓ Step 1  Vitis on PATH
  ✓ Step 2  top=<TOP_FUNCTION>  cfg=<CONFIG_FILE>  part=<XPART>  clock=<CLOCK_NS> ns
  ← NEXT    Step 3 — Build commands
─────────────────────────────────────────────────────
```

---

## Step 3: Build commands

The canonical commands for all HLS flows are:

| Step | Command |
|---|---|
| C Simulation | `vitis-run --mode hls --csim --config <config_file> --work_dir <TOP_FUNCTION>` |
| C Synthesis | `v++ --compile --mode hls --config <config_file> --work_dir <TOP_FUNCTION>` |
| Co-Simulation | `vitis-run --mode hls --cosim --config <config_file> --work_dir <TOP_FUNCTION>` |

**Do not use** `vitis -s run.py`, `make csim`, `make csynth`, or `make cosim` — these either spawn a vitis-server (which hangs) or are not available in all designs.

Reports land under `hls/hls/syn/report/` (csynth) and `hls/hls/csim/report/` (csim).

Print after Step 3:
```
─────────────────────────────────────────────────────
[hls-architect/setup]  Step 3 — done
  ✓ Step 1  Vitis on PATH
  ✓ Step 2  Design discovered
  ✓ Step 3  Build commands ready (vitis-run / v++)
  ← NEXT    Step 4 — OpenCV dependency check
─────────────────────────────────────────────────────
```

---

## Step 4: OpenCV dependency check

Detect whether OpenCV is even needed:

```bash
grep -rl "opencv\|cv\.h\|highgui\|imgproc\|imgcodecs" . --include="*.cpp" --include="*.hpp" --include="*.h" 2>/dev/null | head -5
```

**If no matches → mark this step `N/A` and skip the rest of Step 4.**

**If matches exist**, OpenCV is required. Resolve paths in this order:

1. **Already in env?** If `$OPENCV_INCLUDE` and `$OPENCV_LIB` are set and the headers exist, reuse them.
2. **Otherwise prompt the user** with AskUserQuestion. Two questions:
   - "Path to OpenCV include directory (containing `opencv2/` subfolder):"
   - "Path to OpenCV lib directory (containing `libopencv_core.so`):"

   Then `export OPENCV_INCLUDE=<answer>` and `export OPENCV_LIB=<answer>`.

> **Tip for repeat users:** add `export OPENCV_INCLUDE=…` / `export OPENCV_LIB=…` to your shell rc so you skip the prompt every session.

In all paths, finally:

```bash
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$OPENCV_LIB
```

Verify the headers and `libopencv_core.so` are reachable:

```bash
ls "$OPENCV_INCLUDE/opencv2/core.hpp"
ls "$OPENCV_LIB"/libopencv_core.so*
```

These env vars are inherited by every downstream skill (`/hls-optimize`, `/csim`, `/csynth`) — never re-export downstream.

Also verify that `<config_file>` (the detected HLS config, if Step 2 ran) references `$(OPENCV_INCLUDE)` / `$(OPENCV_LIB)` — if the build system doesn't pick them up automatically, the compile step will fail with missing headers.

---

## Final summary print

After all required steps complete, print:

```
─────────────────────────────────────────────────────
[hls-architect/setup]  done
  ✓ Step 1  Vitis HLS  : <version>  (<path to vitis-run>)
  ✓ Step 4  OpenCV     : <OPENCV_INCLUDE>   (or "N/A — testbench has no OpenCV")
  ✓ Step 2  Design     : top=<TOP_FUNCTION>  cfg=<CONFIG_FILE>  part=<XPART>  clock=<CLOCK_NS> ns   (skipped if tooling-only)
  ✓ Step 3  Build      : Build commands ready                                              (skipped if tooling-only)
─────────────────────────────────────────────────────
```

**Note:** For MATLAB setup, see `../matlab-to-cpp/reference/setup.md`.
