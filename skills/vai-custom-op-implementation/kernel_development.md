<!--- Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.--->
# AIE Kernel Programming

This guide provides an overview on writing AIE kernel code for [custom operations in VAIML](README.md).
The kernel is the core computational component that executes on AIE hardware and defines the actual operation logic.

## Table of Contents

- [Overview](#overview)
- [Kernel Fundamentals](#kernel-fundamentals)
- [Basic Kernel Examples](#basic-kernel-examples)
- [Performance Optimization](#performance-optimization)
- [Advanced Techniques](#advanced-techniques)
- [Reference Links](#reference-links)

## Overview

The AIE kernel is a C++ function that executes on AIE cores and performs the actual computation for your custom operation.
The kernel code runs on every AIE core of the overlay in parallel. Each AIE core has its own
memory space containing the L1 buffers, heap and stack.
The kernel function is called whenever all synchronous buffers are ready (i.e. input buffers
have received their data and output buffers have free space available).

## Kernel Fundamentals

### Namespace

The kernel must be implemented within the `custom_ops` namespace.
### Basic Kernel Structure

Every AIE kernel follows a standard pattern:

```cpp
#include <adf.h>

namespace custom_ops {

// Define the size of layer parameters array
static constexpr int my_kernel_lp_size = 1;

template <typename dtype_input, typename dtype_output>
__attribute__((noinline)) void my_kernel(
    adf::input_buffer_conf<dtype_input, adf::bpc_sync_0d> &__restrict input,
    adf::output_buffer_conf<dtype_output, adf::bpc_sync_0d> &__restrict output,
    const uint32_t (&lp_params)[my_kernel_lp_size]
) {
    // Kernel implementation
}

} // namespace custom_ops
```

### Kernel Signature Components

#### Template Parameters
The template arguments correspond exactly to the data types of each buffer argument in order, ignoring the `lp_params` parameter.

For a kernel with two inputs and one output, the signature is:
```cpp
template <typename dtype_a, typename dtype_b, typename dtype_c>
void my_kernel(
    adf::input_buffer_conf<dtype_a, adf::bpc_sync_0d> &__restrict input_a,
    adf::input_buffer_conf<dtype_b, adf::bpc_sync_0d> &__restrict input_b,
    adf::output_buffer_conf<dtype_c, bpc_sync_0d> &__restrict output_c,
    const uint32_t (&lp_params)[lp_size],
);
```

The template parameters are:
- `dtype_a`: Data type of first buffer (`input_a`)
- `dtype_b`: Data type of second buffer (`input_b`)
- `dtype_c`: Data type of third buffer (`output_c`)

#### Function Parameters
1. **Input Buffers**: ADF buffer configurations for input data
2. **Layer Parameters**: Array of runtime parameters from tiler (does not affect template signature)
3. **Output Buffers**: ADF buffer configurations for output data

#### Buffer Synchronization Modes
- `adf::bpc_sync_0d`: Synchronous access (use this for most cases)
- `adf::bpc_async_0d`: Asynchronous access

More details on alternative options for synchronization besides `bpc_sync_0d` are [here](https://docs.amd.com/r/en-US/ug1079-ai-engine-kernel-coding/Asynchronous-Buffer-Port-Access).

Refer to the [tiling section on asynchronous buffers](tiling.md#keeping-buffers-across-kernel-calls-asynchronous-buffers) for information on how to use them.

### Layer Parameters (LPs)

Layer parameters are how the tiler passes runtime configuration to your kernel. Think of them as function arguments that tell your kernel how much data to process and how it's organized.

```python
# In tiler (Python) - this is handled by the tiling component
lp_params = [total_elements, tile_height, tile_width]
tiling.set_kernel(lp_params)
```

```cpp
// In kernel (C++) - your kernel receives these values
static constexpr int my_kernel_lp_size = 3;  // Must match number of parameters
// ...
int total_elements = (int)lp_params[0];  // How many elements to process
int tile_height = (int)lp_params[1];     // Height of data tile
int tile_width = (int)lp_params[2];      // Width of data tile
```

**Important**: The `lp_size` constant must exactly match the number of parameters your tiler sends.

## Basic Kernel Examples

### Example 1: Simple Unary Operation (Negate)

This example shows a basic kernel that negates each input element. This is a good starting point for understanding kernel structure.

```cpp
#include <adf.h>

namespace custom_ops {

// Define how many layer parameters this kernel expects
static constexpr int negate_kernel_lp_size = 1;

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void negate_kernel(
    adf::input_buffer_conf<dtype_ifm, adf::bpc_sync_0d> &__restrict ifm,
    adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[negate_kernel_lp_size]
) {
    // Get total number of elements from tiler
    int trip_count = (int)lp_params[0];

    // Get raw data pointers from ADF buffers
    dtype_ifm *in = ifm.data();
    dtype_ofm *out = ofm.data();

    // Process elements one by one (scalar approach)
    for (int i = 0; i < trip_count; i++) {
        *out = -(*in);  // Negate the input value
        in++;           // Move to next input element
        out++;          // Move to next output element
    }
}

} // namespace custom_ops
```

**Key points for beginners**:
- Template parameters `dtype_ifm` and `dtype_ofm` will be the actual data types (e.g., `bfloat16`)
- `trip_count` tells you how many elements to process
- Use `.data()` to get raw pointers from ADF buffers
- This is a scalar implementation - we'll show vectorized versions later

### Example 2: Binary Operation (Element-wise Multiplication)

This example shows how to handle two input buffers for operations like element-wise multiplication.

```cpp
#include <adf.h>

namespace custom_ops {

static constexpr int mul_kernel_lp_size = 1;

template <typename dtype_ifm, typename dtype_wts, typename dtype_ofm>
__attribute__((noinline)) void mul_kernel(
    adf::input_buffer_conf<dtype_ifm, adf::bpc_sync_0d> &__restrict a,
    adf::input_buffer_conf<dtype_wts, adf::bpc_sync_0d> &__restrict b,
    adf::output_buffer_conf<dtype_ofm, adf::bpc_sync_0d> &__restrict out,
    const uint32_t (&lp_params)[mul_kernel_lp_size]
) {
    dtype_ifm * a_data = a.data();
    dtype_wts * b_data = b.data();
    dtype_ofm * out_data = out.data();

    uint32_t size = lp_params[0];

    // Element-wise multiplication: out[i] = a[i] * b[i]
    for (uint32_t i = 0; i < size; ++i) {
        out_data[i] = a_data[i] * b_data[i];
    }
}

} // namespace custom_ops
```

## Performance Optimization

### Vectorization

Transform scalar operations into vector operations to perform multiple operations within the same cycle.
The [AIE high level API](https://download.amd.com/docnav/aiengine/xilinx2025_1/aiengine_api/aie_api/doc/topics.html) simplifies the task of implementing vector algorithms on AIE.
In particular, several arithmetic operations have built-in vector support; they are documented on [this page](https://download.amd.com/docnav/aiengine/xilinx2025_1/aiengine_api/aie_api/doc/group__group__arithmetic.html).

In the example below, we use the following steps:
1) Create the vector iterators `in` and `out`. `aie::begin_restrict_vector<16>(ptr)` interprets `ptr` as a pointer to an array of vectors with `16` elements each.
2) Reduce the loop iteration count by a factor of 16 because each iteration now processes 16 elements.
3) Use `*in` to load a vector of 16 elements from the input buffer.
4) Use `in++` to advance the input iterator by 16 elements to the next vector.
5) Use `aie::neg` to negate all 16 elements in the vector.
6) Use `*out` to store a vector of 16 elements to the output buffer.
7) Use `out++` to advance the output iterator by 16 elements to the next vector.

```cpp
#include <adf.h>
#include "aie_api/aie.hpp"

namespace custom_ops {

static constexpr int negate_kernel_lp_size = 1;

template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void negate_kernel_vectorized(
    adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
    adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[negate_kernel_lp_size]
) {
    int trip_count = (int)lp_params[0];

    // Create vector iterators. Addresses must be aligned to 32 bytes.
    auto in = aie::cbegin_restrict_vector<16>(ifm.data());
    auto out = aie::begin_restrict_vector<16>(ofm.data());

    // Process 16 elements at a time
    for (int i = 0; i < trip_count / 16; i++) {
        aie::vector<dtype_ifm, 16> vin = *in++;
        aie::vector<dtype_ofm, 16> negated = aie::neg(vin);
        *out++ = negated;
    }
}

} // namespace custom_ops
```

Always ensure that the addresses for vector loads/stores are aligned to at least the vector size. Otherwise they will silently produce wrong results.
See [here](https://download.amd.com/docnav/aiengine/xilinx2025_1/aiengine_api/aie_api/doc/group__group__memory.html) for details on alignment requirements.
Alternatively, `aie::vector::load_unaligned` and `aie::vector::store_unaligned` allow to load/store from unaligned addresses
but with a performance penalty.

### Loop Pipelining

Use compiler hints to optimize loop performance. `AIE_PIPELINE` enables hardware
loop pipelining, making use of the AIE zero-overhead hardware loops.
`AIE_LOOP_MIN_ITERATION_COUNT(x)` is used to promise that the loop will always
perform at least `x` iterations, which is necessary for the compiler to
produce optimized code for the loop.

```cpp
// Compiler-specific macros for loop optimization
#define AIE_LOOP_MIN_ITERATION_COUNT(x) \
    _Pragma(__STRINGIFY(clang loop min_iteration_count(x)))
#define AIE_PIPELINE _Pragma("clang loop pipeline(disable)")

// Optimized loop with pipelining hints
AIE_LOOP_MIN_ITERATION_COUNT(16)
AIE_PIPELINE
for (int i = 0; i < trip_count; i++) {
    aie::vector<bfloat16, negate_vec_size> vin = *in++;
    aie::vector<bfloat16, negate_vec_size> negated = aie::neg(vin);
    *out++ = negated;
}
```

The compiler might fail to pipeline if the loop condition or loop expression
are complicated.

### Aliasing

**Critical for Performance**: The computational code must be separated into a dedicated function with `__restrict` on its pointer arguments. This allows the single-core compiler to see correct aliasing information and perform optimal vectorization and loop optimizations.

```cpp
#include <adf.h>
#include "aie_api/aie.hpp"

namespace custom_ops {

static constexpr int negate_kernel_lp_size = 1;

// Separate computational function with __restrict pointers
template <typename dtype_ifm, typename dtype_ofm>
static void compute(
    dtype_ifm *__restrict input_ptr,
    dtype_ofm *__restrict output_ptr,
    int size
) {
    constexpr int vec_size = 16;

    auto in = aie::cbegin_restrict_vector<vec_size>(input_ptr);
    auto out = aie::begin_restrict_vector<vec_size>(output_ptr);

    AIE_LOOP_MIN_ITERATION_COUNT(16)
    AIE_PIPELINE
    for (int i = 0; i < size / vec_size; i++) {
        aie::vector<dtype_ifm, vec_size> vin = *in++;
        aie::vector<dtype_ofm, vec_size> negated = aie::neg(vin);
        *out++ = negated;
    }
}

// Main kernel function calls the computational function
template <typename dtype_ifm, typename dtype_ofm>
__attribute__((noinline)) void negate_kernel_optimized(
    adf::input_buffer_conf<dtype_ifm, bpc_sync_0d> &__restrict ifm,
    adf::output_buffer_conf<dtype_ofm, bpc_sync_0d> &__restrict ofm,
    const uint32_t (&lp_params)[negate_kernel_lp_size]
) {
    int trip_count = (int)lp_params[0];

    // Extract pointers and call computational function
    dtype_ifm *input_ptr = ifm.data();
    dtype_ofm *output_ptr = ofm.data();

    compute(input_ptr, output_ptr, trip_count);
}
}
```

### Banking

The AIE Core contains two load units which allows it to load two vectors from memory in the same cycle
provided that both pointers are in different banks.
On AIE2P and AIE2PS, the banks span the following addresses in L1:
| Bank Name                | Address Range       | Size   |
|--------------------------|---------------------|--------|
| `__aie_dm_resource_a`    | `0x0000 - 0x3FFFF`  | 16 KB  |
| `__aie_dm_resource_b`    | `0x4000 - 0x7FFFF`  | 16 KB  |
| `__aie_dm_resource_c`    | `0x8000 - 0xBFFFF`  | 16 KB  |
| `__aie_dm_resource_d`    | `0xC000 - 0xFFFFF`  | 16 KB  |

Each bank is 16 KB, for a total of 64 KB. We can help the compiler to generate more
efficient code by adding the `__aie_dm_resource_a` annotations where known:

```cpp
static void compute(
    bfloat16 __aie_dm_resource_a *__restrict input_ptr,
    // Signal that input2_ptr is in the same bank as input_ptr
    bfloat16 __aie_dm_resource_a *__restrict input2_ptr,
    bfloat16 *__restrict output_ptr,
    int size
) {
 ...
}
```
On pointers that are not annotated, the compiler will assume that those never conflict
with any other loads and schedule them in the same cycle.
This is always safe; the hardware will detect conflicts at runtime and stall
one of the loads if necessary.
The bank annotations don't affect stores as there is only a single store unit.

## Advanced Techniques

### Determining version of the AI Engine
Use the `__AIE_ARCH__` macro or the constexpr `aie::arch::version` variable to determine the target version of the AIE during
compilation.
| `__AIE_ARCH__` Value | `aie::arch::version` value | Architecture Name | Devices   |
|----------------------|----------------------------|-------------------|-----------|
| 21                   | `aie::arch::XDNA2`         | AIE2P             | Strix     |
| 22                   | `aie::arch::AIE_MLv2`      | AIE2PS            | Telluride |

## Reference Links

### AIE API Documentation
- [AIE API User Guide](https://download.amd.com/docnav/aiengine/xilinx2025_1/aiengine_api/aie_api/doc/index.html) - Complete API reference
- [Basic Types](https://download.amd.com/docnav/aiengine/xilinx2025_1/aiengine_api/aie_api/doc/group__group__basic__types.html) - Vector and accumulator types
- [Arithmetic Operations](https://download.amd.com/docnav/aiengine/xilinx2025_1/aiengine_api/aie_api/doc/group__group__arithmetic.html) - Mathematical operations
- [Memory Operations](https://download.amd.com/docnav/aiengine/xilinx2025_1/aiengine_api/aie_api/doc/group__group__memory.html) - Load/store operations

### AIE Programming Resources
- [AIE Kernel Programming User Guide](https://docs.amd.com/r/en-US/ug1079-ai-engine-kernel-coding/Introduction-to-Scalar-and-Vector-Programming) - Programming guide

### Custom Ops Resources
- [Custom Ops Main Documentation](README.md) - Overview and configuration
- [Custom Ops Tutorial Examples](tutorial/README.md) - Practical examples
