<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
<!-- Signatures marked [R] were reproduced with Vitis 2026.2 on xcvc1902-vsva2197-2MP-e-S (AIE) and/or
     xcve2802-vsvh1760-2MP-e-S (AIE-ML). Other rows are from general AIE debug experience: confirm them
     against your log. -->
# Troubleshooting: Error Signature → Cause → Fix

Read the **first** error in `./log` (the Makefile output), then `Work/AIECompiler.log` for the x86 or hw
compile and `x86simulator_output/` or `aiesimulator_output/` for the simulators. Skip the lines with
`Executing Cmd`. They echo command lines that contain the word "error".

```bash
grep -nE "ERROR|error:" log | grep -v "Executing Cmd" | head
```

## Compile (x86com or hw)

| Signature | Cause | Fix |
|---|---|---|
| `error: no matching function for call to 'mul'` with `acc80` on AIE-ML [R] | accumulator tag not available on the architecture | `acc64` on AIE-ML (`device-rules.md` §3) |
| `error: no matching function for call to 'add'` (accum + accum) on AIE [R] | accumulator + accumulator add exists only on AIE-ML | accumulate with `mac`, or `add(acc, vector)` |
| `no viable conversion from 'accum<acc32, …>' to 'accum<acc48, …>'` on AIE-ML [R] | `aie::mul(a, b)` returns the default `acc32` | `aie::mul<acc48>(a, b)` |
| `no matching function for call to 'writeincr'` … `output_cascade<acc48>` vs `output_stream_acc64` [R] | cascade port tag ≠ the accumulator's tag (mmul `acc48` is `acc64` on AIE-ML) | port tag `acc64` on AIE-ML (`#if __AIE_ARCH__ >= 20`) |
| `no matching function for call to 'sliding_mul_sym'` on AIE-ML [R] | no symmetric pre-add on AIE-ML for this shape | `sliding_mul` with full taps |
| `constraints not satisfied for class template 'vector' [with T = bfloat16]` on AIE (hw) [R] | bfloat16 does not exist on AIE | refuse, or offer float / AIE-ML (`device-rules.md`) |
| aiecompiler `Floating point exception (core dumped)` in "x86Sim Graph Analysis" [R] | bfloat16 port/vector on AIE (x86sim) | same as above |
| `error: no viable overloaded '='` on `*po++ = …` [R] | vector type/size ≠ iterator type (`begin_vector<L>` on the wrong `T`, LANES mismatch, int32 into an int16 buffer) | match `T` and `L` on both sides. Convert with `to_vector<T>()` |
| `error: no matching function for call to 'bit_and'` [R] | scalar operand in second position | `aie::bit_and(scalar, vec)` |
| `no member named 'rounding_mode' in namespace 'aie'` in `KERNEL.hpp` [R] | AIE API code inline in the header (the header is also compiled for the graph) | move constructor and method bodies into `KERNEL.cpp` |
| `ERROR: [aiecompiler 77-6317] … has inherited size, but there is no 'adf::dimensions' constraint` [R] | buffer port without `dimensions()` | add `dimensions(k.in[i]) = { NSAMP }` for every buffer port, both ends |
| `fatal error: '<name>.cpp' file not found` (`wrap_<name>.cpp`) [R] | `source(k)` names a missing file | fix `source(k) = "KERNEL.cpp"` |
| `ERROR: [aiecompiler 77-974] Out of bounds access to adf::vector using index:0, vector size:0` [R] | `registerKernelClass` missing `REGISTER_FUNCTION(KERNEL::run)` | add it (`hpp.md`) |
| `The AI Engine kernel has 2 input stream ports, but the maximum number…` (AIE-ML hw) [R] | AIE-ML core has 1 input / 1 output stream | use a buffer port for the second input, or a merge kernel |
| `DMA ping-pong buffer pair: … total size 49152 exceeds maximum memory group size 32768` followed by `UDM Placer Did NOT Finish Successfully` (hw) [R] | buffer x 2 (ping-pong) > memory module | fewer samples per `run()`, `single_buffer()`, or split the kernel. The placer message is a consequence here, not a tool bug |
| `DMA S2MM requires BD 32 : Limit 16 S2MM descriptors` (AIE hw) [R] | `write_access/read_access` tiling too complex for the AIE tile DMA | in-kernel or tiler-kernel tiling (`multi-kernel-graph.md` §GEMM) |
| `Program Memory Overflow` / `report_pm.txt` > 16 KB (hw) | too much unrolled or inlined code | fewer `chess_unroll_loop`, smaller templates, split the kernel |
| `Data Memory Overflow` / stack overflow in `report_stack.txt` | buffers + LUTs + members + stack > tile memory, or large local arrays | move large arrays to aligned class members, `stack_size(k)`, smaller buffers |
| `undefined reference to 'KERNEL::run…'` | `run()` signature differs between hpp and cpp | copy the signature exactly |
| `template argument deduction failed` in an aie:: call | mixed element types (int16 vector with an int32 scalar) | cast scalars: `(int16)3` |
| `use of undeclared identifier` [R] | typo / missing include | fix. `make` now exits non-zero |

`std::vector` or `new` inside a kernel compiles and even runs (heap) [R], but it is forbidden: see
`coding-checklist.md`.

## x86simulator

| Signature | Cause | Fix |
|---|---|---|
| `x86simulator: Detected deadlock` … `Node 'platformIn_…' is done (exhausted data file)` [R] | input file shorter than `ITER x NSAMP` | regenerate stimulus with the correct length |
| `Detected deadlock` … `blocked while reading port '…in[k]'` (no exhausted file) | kernel reads more than its producer writes (stream count, cascade read/write count, rate mismatch) | make the reads per `run()` equal the writes per `run()` on every edge |
| `Detected deadlock` … `blocked while writing` | consumer reads less than the producer writes, or a reconvergent stream path without a FIFO | fix counts. Add `fifo_depth(net)` |
| Output file shorter than expected, no deadlock | `NSAMP/LANES` not an integer, or the loop count is wrong | `static_assert(NSAMP % LANES == 0)` |
| Output file missing | sim never ran (check the compile log) or the plio file name is wrong in the app | check `output_plio::create(..., "data/<port>.txt")` |
| Results all zero | output port not written (iterator on the wrong buffer), or graph port not connected | trace `connect<>` from the app to the kernel |

## aiesimulator

| Signature | Cause | Fix |
|---|---|---|
| hangs at `Waiting for core(s) of graph top to finish execution ...` [R] | same as an x86sim deadlock (short input, count mismatch) | the Makefile's `--hang-detect-time` stops it. Fix counts and stimulus |
| `Error: The work directory './Work' is not generated for target hw` [R] | `make sim` without a hw compile | `make` (hw) first |
| Passes x86sim, fails aiesim | uninitialized state, alignment (`load_v` on an unaligned pointer), reading beyond a buffer, race on a shared member | check margins/offsets, use `load_unaligned_v`, initialize members in the constructor |
| Throughput far below expectation | PLIO-bound (64 bit at `pl-freq`), hot loop not pipelined, bank conflicts | wider plio (`plio_128_bits`), check the loop II in `Work/aie/<tile>/`, `chess_prepare_for_pipelining` |

## Mismatch (compare FAIL)

See the table in `verify.md` §5. These were seen while building the examples:
- AIE-ML only: `abs_square<int32>(v, shift)` ignored `shift` and wrapped [R]. Use `mul<acc48>`.
- Off by one at ties: the rounding mode was not set, so the power-up `floor` applies [R]. This also
  applies to `accfloat → bfloat16`.
- Symmetric test coefficients hide reversed-tap bugs. Test with asymmetric taps.

## When to stop

After 5 failed attempts, stop. Report the first error line, the files changed, and the remaining mismatch
(count and max error). Do not claim a pass, and do not loosen tolerances to hide the mismatch.
