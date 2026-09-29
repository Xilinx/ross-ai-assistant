<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# edge-detection — RGB edge detection reference

End-to-end test design for the LLM skill flow. The MATLAB algorithm here is an **RGB edge detector** with three operating modes (luminance, per-channel, max-gradient).

- **Environment:** Claude Code + `claude-opus-4.8` model, Vitis HLS 2026.1 / Vivado 2026.1

> **Audience:** first-time users of the skill flow. This design exercises the LLM pipeline against a Sobel-style stencil computation with multiple algorithmic variants.

---

## What edge detection does

```
  ┌────────────┐   gradient kernels     ┌────────────────────┐  threshold   ┌────────────────┐
  │ RGB image  │ ─────────────────────► │ Gradient magnitude │ ───────────► │ Binary edges   │
  │            │   (Sobel-style 3×3)    │  per pixel         │              │ (logical mask) │
  └────────────┘                        └────────────────────┘              └────────────────┘
```

Each pixel's edge response is computed from a **3×3 neighbourhood** — a stencil computation. The 3 modes differ in how they combine the R/G/B channels:

| Mode | What it does | Good for |
|---|---|---|
| `luminance` (default) | Convert RGB → grayscale, then 1× edge detection | Standard edges, fastest |
| `perChannel`          | Detect on each R/G/B separately, combine gradients | Color edges (red↔green, etc.) |
| `maxGradient`         | Take max gradient across channels per pixel | Most sensitive |

---

## Files

| File | Role |
|---|---|
| [`rgbEdgeDetector.m`](rgbEdgeDetector.m)         | Main edge detection function (all 3 modes) |
| [`testbench_edgeDetector.m`](testbench_edgeDetector.m) | Verification suite with test coverage for all modes |
| [`edge_detection_runme.m`](edge_detection_runme.m) | Entry-point script — runs the testbench end-to-end |

---

## How to run the design

**Design targets:**

| Parameter | Value |
|---|---|
| Throughput  | **4400 FPS** |
| Clock       | **3.3 ns** (≈ 303 MHz) |
| FPGA part   | `xczu9eg-ffvb1156-2-e` |

In Claude Code, from this design directory:

```
/matlab-to-cpp  rgbEdgeDetector  4400 FPS  part=xczu9eg-ffvb1156-2-e  clock=3.3
```

(`<design_name>` = the `.m` basename without extension.)

The skill chain will:

1. [`/matlab-to-cpp`](../../skills/matlab-to-cpp/SKILL.md) — generate `golden/`, `sample_based/`, `frame_based/` with C++ ports verified against the MATLAB golden.
2. [`/hls-architect`](../../skills/hls-architect/SKILL.md) — produce `rearchitect/v1/`. Likely a hierarchical `compute()`: gradient → magnitude → threshold as separate stages.
3. [`/hls-optimize`](../../skills/hls-optimize/SKILL.md) — iterate pragmas until 4400 FPS @ 303 MHz is met.

