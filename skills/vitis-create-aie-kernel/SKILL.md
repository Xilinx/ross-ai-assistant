---
name: vitis-create-aie-kernel
description: >-
  Write a new AI Engine (AIE) kernel and its ADF graph from a plain-English description: kernel
  header and source with a vectorized AIE API implementation, aie.cfg, floorplan constraints, graph,
  test application and Makefile, then build, check in x86sim against a golden model and self-review
  against UG1079 coding practice. Covers single- and multi-kernel graphs (pipelines, cascade, tiled
  matrix multiply, widget chains) for AIE (VCK190) and AIE-ML (VEK280). Also creates or updates any
  one of those files on its own. Use for custom AIE functionality, not for instantiating DSP Library IP.
license: MIT
compatibility: Requires Vitis 2026.1+ (v++, x86simulator, aiesimulator) and Python 3.8+. AIE and AIE-ML devices.
metadata:
  author: Mark Rollins, Michal Lechtanski
  version: "1.0.0"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Create an AI Engine Kernel

Turns a design description into a complete, buildable AI Engine project and proves that it works.
Each file type has a reference in `references/` with the exact template and naming rules. Follow the
reference for every file you write.

## Choose the mode first

| Request | Mode | Steps |
|---|---|---|
| Describes an algorithm or computation ("a kernel that…", "moving average", "FIR", "matrix multiply") | **Full** | 1 → 11 |
| Asks for all the kernel files but gives no computation (ports, sizes, LUTs only) | **Scaffold** | 1–4, 6–9, with an empty `run()` body. No build |
| Asks for one file or one change ("write the aie.cfg", "add a port to the header", "create the app file") | **Single file** | Only the matching reference. Do not build unless asked |
| Another skill sends you here for one step (e.g. "application step", "graph pattern") | **Delegated** | Only the named reference, with the caller's names. Do not run the rest of the workflow |

If the request says not to build or simulate, skip steps 10–11 and say so in your summary.

## DO

- Check device legality (step 2) **before** writing any file.
- Use the AIE API (`aie::vector`, `aie::mul/mac`, iterators) for the main computation, and keep the hot loop free of scalar per-sample arithmetic.
- Pin the numeric contract (types, shift, rounding, saturation, state across iterations) before you write code. Implement the golden model from the same contract.
- Keep names consistent across files: kernel class `KERNEL`, `NSAMP_<PORT>` sizes, graph port names = app port names, plio files `data/<port>.txt`.
- Build and run x86sim, and fix the errors yourself (see `references/troubleshooting.md`) before you report success.

## DON'T

- Don't generate code for a data type or operation that the target device doesn't support. Refuse and propose the nearest legal alternative.
- Don't claim a build or simulation passed unless you ran it and read the log.
- Don't edit the compiler config or Makefile templates beyond what the references allow.
- Don't use dynamic allocation, `std::` containers, or division in kernel hot loops.
- Don't call AIE API functions (for example `aie::set_rounding`) inline in the `.hpp`. The header is also compiled for the graph, so they belong in the `.cpp` constructor or `run()`.

## Architecture gotchas (verified with Vitis 2026.2)

| Gotcha | What to do |
|---|---|
| Rounding/saturation at power-up is floor, no saturation | Always set `conv_even`/`saturate` in the constructor (`references/cpp.md`) |
| AIE-ML: `acc32` → `to_vector<int32>(shift)` is a bit-cast (no shift, round or saturate) | Shift into a narrower type, or use a wider accumulator (`acc48`/`acc64`) when a shifted 32-bit result is needed |
| AIE-ML: `aie::abs_square<int32>(v, shift)` gives wrong results | Use `aie::mul<acc48>(v, aie::conj(v))` or real/imag `mac`s |
| AIE-ML: `aie::mul(int16, int16)` returns `acc32`. An `acc48` mmul is held as `acc64` | Write `aie::mul<acc48>` explicitly. Declare cascade ports `output_cascade<acc64>` on AIE-ML and `<acc48>` on AIE |
| `sliding_mul_sym` and `aie::sincos` exist on AIE only. `aie::add(acc, acc)` exists on AIE-ML only | Use the generic forms in `references/aie-api-cheatsheet.md` |
| "UDM Placer Did NOT Finish" | A buffer is larger than half a memory module: reduce samples per iteration |
| AIE-ML: at most 1 input and 1 output stream per kernel | Use buffer ports, or split across kernels |
| AIE: tile-DMA `tiling_parameters` for matrix tiles fail the hw compile ("DMA S2MM requires BD 32 : Limit 16"), and x86sim does not catch it | Tile inside the kernel on AIE (`references/multi-kernel-graph.md`) |
| AIE: bfloat16 is not supported | Refuse at step 2 |
| AIE-ML: float16 (IEEE half) and the int8 `mul8`/`mul16` intrinsics are not supported. cfloat is supported (multiplies emulated) | Refuse at step 2, or use bfloat16/cint16 |
| `x86simulator` exits 0 on a deadlock and truncates outputs | The Makefile `x86sim` target checks the log; always compare output lengths |

## Mandatory Workflow (Full mode)

### 1. Capture the specification
Record the following, and ask only for what is missing and cannot be defaulted:
- kernel (or design) name
- target part
- every port: direction, stream/buffer, data type, samples per iteration, top-level plio/gmio
- LUTs
- number of graph iterations
- the algorithm, with its numeric contract

Defaults: buffer ports, `plio_64_bits`, round to nearest with ties to even (`conv_even`), saturate, one graph iteration.

| Part | Board | Architecture |
|---|---|---|
| `xcvc1902-vsva2197-2MP-e-S` | VCK190 | AIE |
| `xcve2802-vsvh1760-2MP-e-S` | VEK280 | AIE-ML |

### 2. Check legality, before any code (`references/device-rules.md`)
Check the data types, accumulator needs, intrinsics, memory per tile and buffer sizes against the
target architecture. If the request can't be built as stated, **stop**. Write no files, explain the
rule that blocks it, and propose a legal alternative (another type, another device, a tiled or
multi-kernel split). Open the reply with one sentence "`<item>` is not supported on `<device>`"
(`references/device-rules.md` §Refusal procedure). Requests for int8 MAC intrinsic tuning on AIE belong to the `vitis-aie-int8-mac` skill.

### 3. Kernel header (`references/hpp.md`)
Write the class, `run()` signature, `NSAMP_*` sizes, LUT members and registration. For multi-kernel
designs, write one header per kernel class.

### 4. Kernel source scaffold (`references/cpp.md`)
Write the includes, the constructor with rounding/saturation set up, the `run()` signature and LUT
initialization.

### 5. Implement the algorithm (`references/algorithm.md`, `references/aie-api-cheatsheet.md`)
Fill `run()` with a vectorized implementation:
1. Choose the lanes and accumulator for the type pair and architecture.
2. Choose the I/O access (buffer iterators or stream reads).
3. Handle state across iterations (margin or internal delay line).
4. Write the main loop with pipelining pragmas.
5. Apply shift/round/saturate exactly as the contract says.

### 6. Compiler config (`references/cfg.md`)
Write `aie.cfg` from the template.

### 7. Floorplan constraints (`references/aiecst.md`)
Write `KERNEL_app.aiecst` from the template.

### 8. Graph (`references/graph.md`, `references/multi-kernel-graph.md`)
- **One kernel:** graph ports, connections, buffer `dimensions`, LUT vectors.
- **Several kernels:** kernel-to-kernel buffer/stream connections with matching types and sizes, FIFO depths, cascade chains, DMA tiling for matrix data, widget stages (stream↔buffer, real→complex).

### 9. Application and Makefile (`references/app.md`, `references/makefile.md`)
Write the dummy top graph with plio/gmio ports, `run(<iterations>)`, and the Makefile for the user's part.

### 10. Verify (`references/verify.md`, `scripts/plio.py`)
1. Write a golden model from the numeric contract.
2. Generate stimulus into `data/<port>.txt`.
3. Run `make x86com x86sim`, and compare `x86simulator_output/data/<port>.txt` against the golden with the contract's tolerance.
4. On a failure, read the log, fix and rerun, at most 5 attempts. If it still fails, report the remaining failure.
5. Run `make` (hw target) once to confirm the design compiles for the device.

### 11. Self-review (`references/coding-checklist.md`)
Go through the checklist on your own output and fix every mandatory item before you finish. In the
summary, report:
- the files written
- the build and x86sim results, with the max error
- the checklist result
- any deviation from the request

## Failure handling

| Situation | Action |
|---|---|
| Illegal type/operation for the device | Stop at step 2: refuse with the reason and an alternative |
| Compile error | `references/troubleshooting.md`. Fix and rebuild |
| x86sim mismatch | Check the numeric contract (shift, rounding, saturation, iteration state), then the data format (`scripts/plio.py`) |
| Buffer does not fit tile memory | Reduce samples per iteration, or split across kernels (`references/multi-kernel-graph.md`) |
| Tools not found | Report that Vitis is not set up. Do not fabricate results |

## References

| File | Use for |
|---|---|
| `references/device-rules.md` | AIE vs AIE-ML types, accumulators, lanes, memory limits, refusal procedure |
| `references/hpp.md` | Kernel class header template, ports, sizes, LUTs |
| `references/cpp.md` | Kernel source template, constructor, `run()` signature |
| `references/algorithm.md` | Turning an algorithm into vectorized AIE API code (UG1079 practice) |
| `references/aie-api-cheatsheet.md` | AIE API calls with per-architecture notes |
| `references/cfg.md` | `aie.cfg` template |
| `references/aiecst.md` | Floorplan constraints template |
| `references/graph.md` | Single-kernel graph template |
| `references/multi-kernel-graph.md` | Pipelines, cascade, tiled GEMM, widget chains |
| `references/app.md` | Top-level application with plio/gmio ports |
| `references/makefile.md` | Makefile template |
| `references/verify.md` | Golden model, PLIO file formats, x86sim, comparison |
| `references/coding-checklist.md` | UG1079 self-review checklist |
| `references/troubleshooting.md` | Compile and simulation errors → fixes |
| `scripts/plio.py` | Write/read/compare PLIO text files |

Further reading: AI Engine Kernel and Graph Programming Guide (UG1079), AI Engine Tools and Flows (UG1076),
the AIE API reference, and the open-source Vitis DSP Library (github.com/Xilinx/Vitis_Libraries, `dsp/`).
