---
name: hls-dataflow
description: 'Analyze whether a given C/C++ code snippet contains a canonical dataflow region for Vitis HLS. Keywords: dataflow, HLS, pragma, canonical, stream, PIPO, hls::task'
license: MIT
argument-hint: "[<TOP_FUNCTION — top-level HLS function name e.g. 'Kernel'>]"
metadata:
  stage: beta
  author: "Alexandre Isoard"
  version: "1.0.1"
---
<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
You are focused on analyzing whether a dataflow region follows the canonical dataflow coding style. A dataflow region defines a network of processes communicating through channels. The canonical coding style results in a dataflow network that directly matches the function calls given in the source code. It is thus more predictable for the user, in terms of both performance and structure. Unlike canonical regions, non-canonical dataflow regions are transformed by Vitis HLS by grouping instructions and control so that they become canonical regions. In particular, a loop in a dataflow region is extracted as a function call.

#### Concepts:
- **Dataflow pragma**: `#pragma HLS dataflow` or `#pragma HLS dataflow disable_start_propagation`
- **Dataflow region**: A region that contains directly a dataflow pragma
- **Function dataflow region**: A function whose body is a dataflow region
- **Loop dataflow region**: A loop whose body is a dataflow region
- **Dataflow function**: A function that is either a function dataflow region or that contains only a loop dataflow region
- **Dataflow function arguments**: Arguments of a dataflow function
- **Global variable**: A variable defined outside of all functions
- **Static variable**: A variable declared as `static` within a function
- **Flow target**: `vivado` (the default) or `vitis`, given in the prompt or by `flow_target=` in the HLS configuration file. If it is not specified, assume `vivado`
- **m_axi type**: An array or pointer argument of the top function (possibly passed as an argument to subfunctions) that accesses external memory through the m_axi protocol. This is the case when the argument is marked by `#pragma HLS interface m_axi` or, by default, when `syn.interface.default_axi` is true (which is the case if the flow target is `vitis`), unless a `#pragma HLS interface` selects another protocol (e.g., `ap_memory`, `bram`)
- **Top function**: The top-level HLS function, get it via argument `<TOP_FUNCTION>`, if not provided, call `/hls-component-basic-info` skill to get top_function which is the top-level function name
- **Top dataflow function**: The top function if it is a dataflow function
- **Overlapping dataflow region**: A dataflow region that is a top dataflow function, or the body of a loop dataflow region, or a function dataflow region called directly as a process of an overlapping dataflow region. An intermediate non-dataflow region is a sequential region and thus prevents overlapping
- **Read process**: A process (function/task/block) that reads a variable's value
- **Write process**: A process (function/task/block) that writes a variable's value
- **Read/write process**: A process that both reads and writes the same variable
- **Class instance rule**: If the dataflow region is defined within a non-static class method, treat `this` (and its member variables) as dataflow function arguments

#### Scope:
- Strictly follow rules 1–10; each rule should only be checked within that specific rule—do not extend
- Follow the literal text of each rule strictly; do not extend rules based on general design principles
- For loop dataflow regions, analyze only a single iteration

---

#### Analysis Steps:

**1. Pragma Presence**
   Validate that the code must contain `#pragma HLS dataflow` or `#pragma HLS dataflow disable_start_propagation`.

**2. Pragma Location**
   Validate that the dataflow pragma is in a loop body or function body.

**3. Loop Dataflow Region Rules (skip if pragma is not inside a loop body)**
   - **3.1** The loop must be a `for`-loop.
   - **3.2** The parent function of the for-loop contains only this for-loop and no other code except local variable declarations and pragma statements.
   - **3.3** The loop counter is declared in the loop header and is of `int` type.
   - **3.4** The iterator must be initialized to a non-negative integer constant in the loop header.
   - **3.5** The loop bounds must be a non-negative integer constant or a scalar argument of the function.
   - **3.6** The step of the iterator must be a positive integer constant.
   - **3.7** The loop must have no early exit such as return, continue, break.

**4. Dataflow Region Content Rules (check line-by-line)**
   - **4.1** The dataflow region must not contain any statements other than:
     - Pragma statements
     - Local variable declarations (of any kind, including class objects instantiations), which become dataflow channels
     - Function call statements (including `hls::task` instantiations and class method calls), which become dataflow processes
     - Assignment of a local scalar variable from the return of a function is allowed.
   - **4.2** If `hls::task` is present, it must be declared as `hls_thread_local`:
     ```cpp
     hls_thread_local hls::task t1(proc, arg1, arg2, arg3);
     ```
   - **4.3** If `hls::task` is present, `hls::stream` and `hls::stream_of_blocks` within it must be declared as `hls_thread_local`.
   - **4.4** All local variables within the dataflow region must be declared as non-static.
   - **4.5** Local variables with initializing default constructors (e.g., `std::complex`) lead to implicit write processes. To avoid this, use the `no_ctor` attribute:
     ```cpp
     std::complex<float> arr[SIZE] __attribute__((no_ctor));
     ```
   - **4.6** Function calls must not pass array element values (e.g., `arg_mem[offset_var]`, `mem[i]`, `array[index]`) as arguments. Only pass the entire array/pointer variable itself.
   - **4.7** Function calls must not perform arithmetic or logical operations on arguments at the call site (e.g., use `i`, not `i + 1`; use `ptr`, not `ptr + offset`).
   - **4.8** List all function parameters and explicitly verify type compatibility between function parameters and arguments. No type conversion is allowed during function calls, except value-to-reference conversions.

**5. Multiple Invocation Rules**
   If a function is invoked multiple times within a dataflow region, ensure static variables follow these rules:
   - **5.1** If the function is invoked by a loop within the dataflow region:
     - **5.1.a** If the same function is called in each iteration (repeatedly across all iterations), skip rules 5, 5.1, 5.2.
     - **5.1.b** If the function is called multiple times within a single iteration, ensure no static variables within that function are accessed.
   - **5.2** If the function is invoked multiple times by a non-loop within the dataflow region, ensure no static variables within that function are accessed.

**6. Dataflow Function Argument Rules**
   List dataflow function arguments before checking. If arguments are accessed within a loop, analyze only a single iteration.
   - **6.1** If the argument is of type `hls::stream`, ensure it is accessed (read or written) by only one dataflow process.
   - **6.2** If the argument is an array of m_axi type, it may be read by at most one process and written by at most one process (a process that both reads and writes it counts as both a reader and a writer). The access pattern must be one of:
     - a single process accesses it (reading, writing, or both)
     - one process only reads it and a different process only writes it, and the writing process appears textually after the reading process
   - **6.3** If the argument is an array and not of m_axi type, ensure:
     - If written, it is accessed (read or written) by only one process
     - If read-only, it is read by at most two processes
   - **6.4** *Scalar carried over successive invocations of an overlapping region between two different processes:* if a scalar argument (passed by address or reference) of an OVERLAPPING dataflow region is read in a process P that appears textually before a *different* process Q that writes it, ensure that this scalar is always written in the region before it is read. When applying this rule, consider that a conditional write corresponds to a read followed by a write (in the same process). This rule is thus not triggered when no such pair (P, Q) exists, or if the same single process performs both the read and the write of the scalar, or if the dataflow region is not an overlapping dataflow region. The reason for this rule is that a flow "loop-carried" dependence within dataflow is supported only within a single process.
     **6.5** A dataflow function must not have a return value: outputs must be passed as arguments. The rule does not apply for a top dataflow function. 

**7. Dataflow Function Global/Static Scalar Rule**
  Ensure all scalar-type global/static variables accessed within an overlapping dataflow region follow the rule 6.4 (substituting scalar argument by global/static scalar variable).

**8. Top Dataflow Function Scalar Argument Rules**
   If `<TOP_FUNCTION>` function is a top dataflow function, and a scalar argument of the top function is used in a dataflow region, recursively included in dataflow regions up to the top, ensure that if written, it is accessed (read or written) by only one process.

**9. Global/Static Array Rule**
   If an array-type global/static variable is accessed within the dataflow region, validate that no more than one dataflow process reads or writes the global/static array in the dataflow region.

**10. Local Variable Rules**
   Validate local variables within the dataflow region. For loop dataflow regions, analyze a single iteration only.
   Ignore local variables that are unused (pragmas are not considered uses either).
   - **10.1** Any local variable within a dataflow region must be a scalar, an array, `hls::stream`, `hls::stream_of_blocks`, or a class containing any of these types. A class object is not checked as a whole: each of its members is checked according to its own type. A reference member is checked as the variable it refers to.
   - **10.2** If a local array is marked with `#pragma HLS bind_storage variable=... type=ram_1wnr`, validate exactly one writer process and one or more reader processes, with the writer appearing lexically before all readers.
   - **10.3** If a local array is not marked with `#pragma HLS bind_storage variable=... type=ram_1wnr`, validate exactly one writer process and one reader process (a process that both reads and writes it only counts as a writer), with the writer appearing lexically before the reader.
   - **10.4** If a local variable is of `hls::stream` type, validate exactly one writer process and one reader process.
   - **10.5** If a local variable is of `hls::stream_of_blocks` type, validate:
     - **a.** It is declared as:
       ```cpp
       #include "hls_streamofblocks.h"
       ...
       hls::stream_of_blocks<block_type, depth> block;
       // or
       hls::stream_of_blocks<block_type> block;
       ```
     - **b.** Exactly one write process, and it is using `hls::write_lock lock(block)` in the region that writes
     - **c.** Exactly one read process, and it is using `hls::read_lock lock(block)` in the region that reads
   - **10.6** If a local variable is a scalar, there are no writer/reader limits.

---

#### Verdict:
If **all** criteria are met, the code forms a **canonical dataflow region**. Otherwise, it is **not canonical**.
