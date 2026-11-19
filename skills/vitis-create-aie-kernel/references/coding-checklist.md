<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
<!-- Checklist based on UG1079 (AI Engine Kernel and Graph Programming Guide) coding practice. Items were
     checked against designs built with Vitis 2026.2 for AIE (xcvc1902) and AIE-ML (xcve2802). -->
# Self-Review Checklist

Run this on your own output before you declare success. You check every item by **reading the files**.
**M** = mandatory: fix it, or report it as a deviation. **R** = recommended: fix it, or say why not.

Report the result in the summary as `checklist: N/N mandatory passed, R items: <list of exceptions>`.

## A. Kernel computation (`KERNEL.cpp`)

| # | Item | Level | How to check |
|---|---|---|---|
| A1 | The main computation uses AIE API vector types and ops (`aie::vector`, `aie::mul/mac/add`, `sliding_mul`, `mmul`) | M | the hot loop body contains `aie::` calls on vectors, and no element-wise `x[i]` arithmetic |
| A2 | No scalar per-sample loop for the main computation | M | no `for` over samples whose body does scalar math on one element. Scalar code only outside the hot loop (setup, final `reduce_*`) |
| A3 | The hot loop has `chess_prepare_for_pipelining` and `chess_loop_range(min, max)` | M | the pragma follows the `for (...)` of the innermost hot loop |
| A4 | Short fixed inner loops are unrolled (`chess_unroll_loop`) so that the pipelined loop is the innermost | R | e.g. the K loop in an mmul |
| A5 | No division or modulo by a non power of two in the hot loop | M | grep the hot loop for `/` and `%`. Precompute reciprocals or use shifts |
| A6 | No branches on sample values in the hot loop | R | use `aie::lt/gt` + `aie::select`, `aie::max/min` |
| A7 | Lanes and accumulator match `device-rules.md` for the type pair and architecture | M | `LANES` and the tag are `static constexpr` / `using`. `#if __AIE_ARCH__` where they differ |
| A8 | No accumulator overflow under the worst-case input | M | bound written in the contract comment. `acc48` for int32 outputs on AIE-ML |
| A9 | Rounding and saturation are set explicitly in the constructor (every kernel that converts accumulators to integer or bfloat16; the `cpp.md` template always does it) | M | `aie::set_rounding(...)` and `aie::set_saturation(...)` in `KERNEL::KERNEL` |
| A10 | Shift/round/saturate happen where the contract says, in `to_vector<T>(SHIFT)` | M | one `SHIFT` constant, used in kernel and golden |
| A11 | Loop trip counts divide exactly: `NSAMP % LANES == 0` | M | `static_assert` or arithmetic in the header |
| A12 | Loop-invariant vectors (coefficients, constants) are loaded outside the loop | R | |
| A13 | Raw pointers that do not alias are `__restrict` | R | `const T* __restrict p = buf.data();` |
| A14 | Vector loads/stores are aligned: `load_v`/`store_v`/iterators only at multiples of the vector size, otherwise `load_unaligned_v` | M | check every offset added to `data()` |
| A15 | Shuffles (`interleave_*`, `filter_*`) are used only where needed | R | not on every iteration of a MAC-bound loop without a reason |

## B. Memory and state

| # | Item | Level | How to check |
|---|---|---|---|
| B1 | No dynamic allocation: no `new`, `malloc`, `std::vector`, `std::string`, `std::map` in kernel code | M | grep `KERNEL.cpp`/`KERNEL.hpp`. (`std::vector` belongs only in the graph, for LUT values) |
| B2 | No unsupported library calls in kernels: no `printf`/`iostream` in the final code, no `std::sort`, `<cmath>` scalar functions in hot loops, no exceptions or RTTI | M | grep includes and calls. Debug prints removed |
| B3 | Buffer sizes fit the tile: Σ(buffer bytes x 2 for ping-pong) + margins + LUTs + members + 1 KB stack ≤ 32 KB (AIE) / 64 KB (AIE-ML). One buffer ≤ one module | M | compute from `NSAMP_*` x `sizeof(T)` |
| B4 | Buffers larger than half a module are `single_buffer()` or split | M | graph |
| B5 | LUTs are registered parameters, aligned (`alignas(32)` or `aie::vector_decl_align`), sized to a vector multiple and padded | M | `hpp.md` pattern, `REGISTER_PARAMETER` per LUT |
| B6 | State across iterations is a class member or a buffer margin, initialized in the constructor. No `static` locals, no globals | M | grep `static ` inside functions |
| B7 | Large scratch arrays are aligned class members, not stack arrays | R | stack is 1 KB by default |
| B8 | The margin size is ≥ the history needed and keeps vector loads aligned (32 B multiple) | M | `margin<M>` vs taps − 1 |

## C. Interfaces (hpp ↔ graph ↔ app)

| # | Item | Level | How to check |
|---|---|---|---|
| C1 | `run()` signature is identical in `KERNEL.hpp` and `KERNEL.cpp` | M | diff the two lines |
| C2 | Every kernel port is connected exactly once, with port indices in `run()` argument order (inputs and outputs counted separately) | M | `kk.in[i]` / `kk.out[j]` vs the signature |
| C3 | Every buffer port has `dimensions()` equal to its `NSAMP_*`. Stream and cascade ports have none | M | graph |
| C4 | Data types match across every connection: producer port `T` = consumer port `T`, PLIO file type = port type | M | walk each `connect<>` |
| C5 | Kernel-to-kernel buffers: both ends have equal sizes (with a `static_assert`) | M | graph |
| C6 | Graph port names = app port names. PLIO names are `PLIO_<port>`, files are `data/<port>.txt` | M | `app.md` |
| C7 | PLIO width can carry the type (cint32/cfloat need ≥ 64 bit) and matches the stimulus layout | M | `verify.md` §2 |
| C8 | Stream ports per kernel within the limits (AIE-ML: 1 in, 1 out) | M | count `input_stream`/`output_stream` |
| C9 | Cascade port tags match the accumulator tags written and read. Read and write counts per `run()` are equal | M | `device-rules.md`, `multi-kernel-graph.md` |
| C10 | Stream reads/writes per `run()` match the producer/consumer counts | M | count `readincr`/`writeincr` x loop trips |
| C11 | `runtime<ratio>` is set for every kernel. `source()` names the correct `.cpp` | M | graph |
| C12 | Reconvergent stream paths have `fifo_depth` | R | graph |
| C13 | No AIE API calls inline in headers. Kernel bodies are in `.cpp` | M | grep `aie::` in `.hpp` outside type declarations |

## D. Verification evidence

| # | Item | Level | How to check |
|---|---|---|---|
| D1 | The golden model implements the written contract (rounding, saturation, state, order) | M | read `golden.py` against the contract comment |
| D2 | Stimulus covers corner values (min, max, ±1, 0), several iterations, asymmetric coefficients | M | `golden.py` |
| D3 | x86sim ran: no ERROR, no "Detected deadlock", every output present, compare PASS with the stated tolerance | M | `log`, `plio.py compare` output |
| D4 | hw compile ran once without errors | M (full mode) | `log` after `make` |
| D5 | The reported max error and tolerance appear in the summary | M | summary |

## Quick grep pass

```bash
grep -nE "new |malloc|std::(vector|string|map)|printf|iostream" KERNEL*.cpp KERNEL*.hpp   # B1/B2 (vector OK only in *_graph.h)
grep -n "chess_prepare_for_pipelining" *.cpp                                               # A3
grep -nE "set_rounding|set_saturation" *.cpp                                               # A9
grep -nE "static [a-z].*=" *.cpp                                                           # B6
grep -n "dimensions" *_graph.h                                                             # C3
```
