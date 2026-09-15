---
name: hls-array-to-stream
description: 'Analyze whether a given C/C++ code snippet follows the recommended coding style for array-to-stream conversion in Vitis HLS. Keywords: array, stream, HLS, pragma, ap_fifo, axis, interface'
argument-hint: "[<TOP_FUNCTION — top-level HLS function name e.g. 'Kernel'>]"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
You are focused on analyzing whether code follows the recommended coding style for array-to-stream conversion.

#### Concepts:
- **Array-to-stream conversion**: Converting arrays to streaming interfaces for efficient data transfer
- **Stream pragma**: `#pragma HLS stream variable=A type=T depth=D`
- **Interface pragma**: `#pragma HLS interface mode=ap_fifo port=A` or `#pragma HLS interface mode=axis port=A`
- **Top function**: the top-level HLS function, get it via argument `<TOP_FUNCTION>`, if not provided, call `/hls-component-basic-info` skill to get top_function which is the top-level function name
- **Top function arguments**: Parameters of the top-level function

#### Scope:
- Strictly follow rules 1–6; do not extend
- Must get `<TOP_FUNCTION>` value which is the top-level function name (e.g. `Kernel`)

---

#### Analysis Steps:

**1. Pragma Presence**
   Validate that the code must contain an array-to-stream pragma:
   - Stream pragma: `#pragma HLS stream variable=A type=T depth=D`
   - Interface pragma: `#pragma HLS interface mode=ap_fifo port=A` or `#pragma HLS interface mode=axis port=A`

**2. Stream Pragma Rules** (check if `#pragma HLS stream variable=<VAR> [type=<T>] [depth=<D>]` is used)
   - **2.1** It must be a legal HLS stream pragma with the form `#pragma HLS stream variable=<VAR> [type=<T>] [depth=<D>]`
   - **2.2** `<VAR>` can be:
     - An array (single or multi-dimensional)
     - A scalar variable
   - **2.3** `<VAR>` must be a local variable or a global variable
   - **2.4** `<VAR>` cannot be a top-level function argument
   - **2.5** `type=<T>` is optional:
     - If omitted, defaults to "fifo"
     - If provided, `<T>` must be "fifo"
   - **2.6** `depth=<D>` is optional:
     - If omitted, uses default depth
     - If provided, `<D>` must be a positive integer literal or const expression that evaluates to a positive integer

**3. Interface Pragma Rules** (check if `#pragma HLS interface mode=ap_fifo port=A` or `#pragma HLS interface mode=axis port=A` is used)
   - **3.1** `A` must be an array (possibly multi-dimensional) or a scalar
   - **3.2** `A` must be a top function argument
   - **3.3** `mode` must be either `ap_fifo` or `axis`

**4. Struct Element Type Rules** (skip if array element type is not a struct type)
   If the array element type is a struct type, ensure that the whole struct is prepared in a separate struct variable and then stored at once in the array.

**5. Struct Copy Assignment Rules** (skip if array element type is not a struct type)
   If the array element type is a struct type, ensure that no custom copy assignment operator is defined (such as `A& operator=(const A& a)`) for that struct type.

**6. Stream Pragma Access Pattern Rules** (check only if stream pragma `#pragma HLS stream variable=A type=T depth=D` is used)
   Ensure that each accessed array element is read exactly once, written exactly once, and that the read/write order is consistent:
   - **6.1** Ensure that each element is accessed exactly once and not more than once (no repeated access to any element)
   - **6.2** Ensure that the array is accessed in the same order and range on both reading and writing sides
   - **6.3** Ensure the iteration order must be the same (e.g., both ascending or both descending)
   - **6.4** Each side must access the same number of elements (e.g., if write accesses N-1 elements, read must also access N-1 elements, not N)

---

#### Verdict:
If **all** applicable criteria are met, the code follows the **recommended coding style for array-to-stream conversion**. Otherwise, it does **not** follow the recommended coding style.
