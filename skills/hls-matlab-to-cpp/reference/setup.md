---
description: Set up the matlab-to-cpp environment — verify Vitis, OpenCV, MATLAB. Delegates to hls-architect/setup for Vitis+OpenCV, then adds MATLAB on top.
argument-hint: "[mode=tooling-only]   # tooling-only skips design discovery"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Skill: setup (matlab-to-cpp)

Prepare the environment for a MATLAB-to-C++ design session. This setup:
1. **Delegates to `../hls-architect/reference/setup.md`** for Vitis + OpenCV + design discovery
2. **Adds MATLAB setup** on top

Run this:
- **From `/matlab-to-cpp`** at the very start, with `mode=tooling-only` (no design exists yet)
- **From `/csim`, `/csynth`, `/cosim` after matlab-to-cpp** with no args (full setup including design discovery)

---

## Step 0: Delegate to hls-architect setup

Call `../hls-architect/reference/setup.md` to handle Vitis, OpenCV, and design discovery.

Pass through the `mode=tooling-only` argument if present in `$ARGUMENTS`.

```bash
# Conceptually:
# Call ../hls-architect/reference/setup.md with same arguments
# This gets us: Vitis (Step 1), Design (Step 2), Build (Step 3), OpenCV (Step 4)
```

Print at the start:
```
─────────────────────────────────────────────────────
[matlab-to-cpp/setup]  Step 0 — delegating to hls-architect/setup
  → Calling ../hls-architect/reference/setup.md
  → This will verify: Vitis, OpenCV, Design (if not tooling-only)
─────────────────────────────────────────────────────
```

**Wait for hls-architect/setup to complete.** Then continue to Step 1 below.

After Step 0 completes, print:
```
─────────────────────────────────────────────────────
[matlab-to-cpp/setup]  Step 0 — done
  ✓ hls-architect/setup completed
  ← NEXT    Step 1 — MATLAB executable check
─────────────────────────────────────────────────────
```

---

## Step 1: MATLAB executable check

MATLAB is required by `/matlab-to-cpp` to run `.m` files and dump `matlab_input.bin` and `matlab_golden.bin` (the goldens every C++ refactor verifies against).

If the design has a `golden/` directory with `matlab_*.bin` files already present, mark this step `N/A` and skip.

Otherwise resolve the MATLAB binary in this order:

1. **Already in env?** If `$MATLAB_BIN` is set and executes, reuse it.
2. **On PATH?** `which matlab` — if found, set `MATLAB_BIN=$(which matlab)`.
3. **Otherwise prompt the user** with AskUserQuestion:
   - "Path to MATLAB executable (e.g. `/usr/local/MATLAB/R2024a/bin/matlab`):"

Then `export MATLAB_BIN=<resolved-path>`.

Verify the binary launches headless:

```bash
"$MATLAB_BIN" -batch "disp('matlab-ok')" -nodesktop -nosplash 2>&1 | tail -3
```

Output must contain `matlab-ok`. If MATLAB hangs at a license prompt, the user has a license problem — surface that to them and stop.

`$MATLAB_BIN` is inherited by downstream matlab-to-cpp steps — never re-resolve downstream.

> **Tip for repeat users:** add `export MATLAB_BIN=<path>` to your shell rc so you skip the prompt every session.

---

## Final summary print

After all steps complete, print:

```
─────────────────────────────────────────────────────
[matlab-to-cpp/setup]  done
  ✓ Step 0  Delegated  : hls-architect/setup completed
            ├─ Vitis   : <version>  (<path to vitis-run>)
            ├─ OpenCV  : <OPENCV_INCLUDE>   (or "N/A")
            ├─ Design  : top=<TOP_FUNCTION>  cfg=<CONFIG_FILE>   (or "skipped — tooling-only")
            └─ Build   : Build commands ready           (or "skipped — tooling-only")
  ✓ Step 1  MATLAB     : <MATLAB_BIN>   (or "N/A — golden/ already populated")
─────────────────────────────────────────────────────
```

**Environment variables exported:**
- `$XILINX_VITIS` — from hls-architect/setup Step 1
- `$OPENCV_INCLUDE`, `$OPENCV_LIB` — from hls-architect/setup Step 4 (if OpenCV needed)
- `$MATLAB_BIN` — from this setup Step 1
