<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Examples

Reference designs and walkthroughs for agentic FPGA workflows. Each example demonstrates a task or a comparison of skill-guided and direct tool use.

---

## Available examples

| Example | Input | Skills used | Target |
|---------|-------|-------------|--------|
| [`hls-intro-matmul/`](hls-intro-matmul/) | C++ triple-nested loop | `/hls-architect`, `/hls-optimize` | 15 GOPS @ 3.3 ns |
| [`edge-detection/`](edge-detection/) | MATLAB Sobel RGB edge detector | `/matlab-to-cpp`, `/hls-architect`, `/hls-optimize` | 4400 FPS @ 3.3 ns |
| [`globaltonemapping/`](globaltonemapping/) | C++ Vitis Vision L1 baseline | `/hls-optimize` | 2x latency reduction |
| [`vivado-ip-configurator/isp-skill-comparison/`](vivado-ip-configurator/isp-skill-comparison/) | High-level ISP configuration request | `vivado-ip-configurator`, compared with direct MCP use | 15 independent streaming inputs; recorded Luna and Sol results |

---

## hls-intro-matmul

**C++ → HLS, no MATLAB required.** A 64×64×64 matrix multiplication kernel optimized from a simple triple-nested loop to a pipelined HLS implementation.

```
cd hls-intro-matmul/
/hls-architect throughput=15 GOPS part=xczu9eg-ffvb1156-2-e clock=3.3
```

Good starting point if you have an existing C++ algorithm and want to accelerate it with HLS.

---

## edge-detection

**MATLAB → C++ → HLS end-to-end.** An RGB Sobel-style edge detector with three operating modes (luminance, per-channel, max-gradient). Exercises the full skill chain including MATLAB golden capture, C++ port, architecture design, and pragma optimization.

```
cd edge-detection/
/matlab-to-cpp  rgbEdgeDetector  4400 FPS  part=xczu9eg-ffvb1156-2-e  clock=3.3
```

Good second example if you are coming from a MATLAB algorithm and need the full conversion flow.

---

## globaltonemapping

**Optimize an existing HLS design.** A Vitis Vision L1 global tone mapping kernel (HDR → LDR). Starting point is the unoptimized NPPC=1 baseline; the goal is 2x latency reduction.

```
cd globaltonemapping/
/hls-optimize  xf_gtm_accel.cpp  Reduce true-latency by 2x while using no more than 1.5x the resources
```

Good example if you already have an HLS design and want to tune pragmas to meet a performance target.

---

## Loading skills

Skills live in [`../skills/`](../skills/). Load them with `--plugin-dir` when launching Claude Code:

```bash
claude --plugin-dir /path/to/hls-llm-skills/skills
```
