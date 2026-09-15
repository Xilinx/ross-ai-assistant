---
name: hls-dataflow
description: 'Analyze whether a given C/C++ code snippet contains a canonical dataflow region for Vitis HLS. Keywords: dataflow, HLS, pragma, canonical, stream, PIPO, hls::task'
argument-hint: "[<TOP_FUNCTION — top-level HLS function name e.g. 'Kernel'>]"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
You are focused on analyzing whether a dataflow region is canonical.

#### Concepts:
- **Dataflow pragma**: `#pragma HLS dataflow` or `#pragma HLS dataflow disable_start_propagation`
- **Function dataflow region**: A function that contains a dataflow pragma in its body
- **Loop dataflow region**: A loop that contains a dataflow pragma in its body
- **Dataflow function**: A function containing a dataflow loop or dataflow pragma
- **Dataflow function parameters**: Parameters of the function containing the dataflow region
- **Global variable**: A variable defined outside of all functions
- **m_axi type**: Marked with `#pragma HLS interface m_axi`
- **Top function**: the top-level HLS function, get it via argument `<TOP_FUNCTION>`, if not provided, call `/hls-component-basic-info` skill to get top_function which is the top-level function name
- **Top dataflow function**: The top-level function whose body contains a dataflow pragma directly (not only within a called function)
- **Read process**: A process (function/task/block) that reads/accesses a variable's value
- **Write process**: A process (function/task/block) that writes/modifies a variable's value
- **Read/write process**: A process that both reads and writes the same variable
- **Class instance rule**: If the dataflow region is defined within a class method and the corresponding class instance is created outside the dataflow region, treat that instance object (and its member variables accessed through `this`) as dataflow function parameters subject to rules 6.1–6.4

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
   - **3.2** The parent function of the for-loop contains only one for-loop, local variable declarations, or pragma statements.
   - **3.3** The loop counter is declared in the loop header and is of `int` type.
   - **3.4** The iterator must be declared as a non-negative integer in the loop header.
   - **3.5** The loop bounds must be a non-negative integer constant or a scalar argument of the function.
   - **3.6** The step of the iterator must be a positive integer constant.

**4. Dataflow Region Content Rules (check line-by-line)**
   - **4.1** The dataflow region must not contain any statements other than:
     - Dataflow pragma
     - Function call statements
     - Local variable declarations (of any kind)
     - Class object or `hls::task` instantiations
     - For-loop statements
     - Pragma statements
   - **4.2** If `hls::task` is present, it must be declared as `hls_thread_local`:
     ```cpp
     hls_thread_local hls::task t1(proc, arg1, arg2, arg3);
     ```
   - **4.3** If `hls::task` is present, `hls::stream` and `hls::stream_of_blocks` within it must be declared as `hls_thread_local`.
   - **4.4** All local variables within the dataflow region must be declared as non-static.
   - **4.5** Local variables with default constructors (e.g., `std::complex`) must not be redefined. To avoid initialization, use the `no_ctor` attribute:
     ```cpp
     std::complex<float> arr[SIZE] __attribute__((no_ctor));
     ```
   - **4.6** Function calls must not pass array element values (e.g., `arg_mem[offset_var]`, `mem[i]`, `array[index]`) as arguments. Only pass the entire array/pointer variable itself.
   - **4.7** Function calls must not perform arithmetic or logical operations on arguments at the call site (e.g., use `i`, not `i + 1`; use `ptr`, not `ptr + offset`). Array indexing (`mem[idx]`) and type conversion are excluded from this rule.
   - **4.8** List all function parameters and explicitly verify type compatibility between function parameters and arguments. No type conversion is allowed during function calls, except value-to-reference and reference-to-value conversions.

**5. Multiple Invocation Rules**
   If a function is invoked multiple times within a dataflow region, ensure static variables follow these rules:
   - **5.1** If the function is invoked by a loop within the dataflow region:
     - **5.1.a** If the same function is called in each iteration (repeatedly across all iterations), skip rules 5, 5.1, 5.2.
     - **5.1.b** If the function is called multiple times within a single iteration, ensure no static variables within that function are accessed.
   - **5.2** If the function is invoked multiple times by a non-loop within the dataflow region, ensure no static variables within that function are accessed.

**6. Dataflow Function Parameter Rules**
   List dataflow function parameters before checking. If parameters are accessed within a loop, analyze only a single iteration.
   - **6.1** If the parameter is of type `hls::stream`, ensure it is accessed (read or written) by only one dataflow process.
   - **6.2** If the parameter is an array of m_axi type, it must be accessed by only a single process, OR if accessed by both a read process and a write process, the write process must appear after the read process.
   - **6.3** If the parameter is an array and not of m_axi type, ensure:
     - Not written by more than one process
     - Not read by more than two processes
     - Not read and written simultaneously by different processes
   - **6.4** If a scalar parameter (passed by address or reference) is accessed by multiple different processes within a single iteration of a loop dataflow region, ensure its final write process occurs before its first read process. If final write and first read occur in the same process, skip this rule.

**7. Top Dataflow Function Global Scalar Rule**
  If `<TOP_FUNCTION>` function is a top dataflow function, ensure all scalar-type global variables accessed within it are written for the final time before their first read operation. Otherwise, skip this rule.

**8. Loop Dataflow Region Global Scalar Rules**
   If a scalar-type global variable is used within a loop dataflow region:
   - **8.1** If the scalar global variable is accessed indirectly through function calls (not directly in the dataflow region), skip rules 8, 8.1, 8.2.
   - **8.2** If accessed directly in the dataflow region, ensure its last write occurs before its first read within that region.

**9. Global Array Rule**
   If only an array-type global variable is accessed within the dataflow region, validate that no more than one dataflow process reads or writes the global array in the dataflow region.

**10. Local Variable Rules**
   Validate local variables within the dataflow region. For loop dataflow regions, analyze a single iteration only.
   - **10.1** Any shared local variable within a dataflow region must be a scalar, an array, `hls::stream`, `hls::stream_of_blocks`, or a class containing any of these types.
   - **10.2** If a local array is marked with `#pragma HLS bind_storage variable=... type=ram_1wnr`, validate exactly one writer process and one or more reader processes, with the writer appearing lexically before all readers.
   - **10.3** If a local array is not marked with `#pragma HLS bind_storage variable=... type=ram_1wnr`, validate exactly one writer process and one reader process, with the writer appearing lexically before the reader.
   - **10.4** If a local variable is of `hls::stream` type, validate exactly two processes: one writer and one reader.
   - **10.5** If PIPO memory is present, validate one process writes and another reads, with the write process appearing lexically before the read process.
   - **10.6** If `stream_of_blocks` is present, validate:
     - **a.** Defined as:
       ```cpp
       #include "hls_streamofblocks.h"
       ...
       hls::stream_of_blocks<block_type, depth> block;
       // or
       hls::stream_of_blocks<block_type> block;
       ```
     - **b.** Exactly two processes: one write process (`hls::write_lock lock(block)`) and one read process (`hls::read_lock lock(block)`).
     - **c.** The write process must appear before the read process.

---

#### Verdict:
If **all** criteria are met, the code forms a **canonical dataflow region**. Otherwise, it is **not canonical**.