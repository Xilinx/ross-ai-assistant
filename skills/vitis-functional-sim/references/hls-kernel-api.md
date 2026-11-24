<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# HLS Kernel API Reference

## Constructor

```python
# Python
kernel = vfs.hlsKernel(
    input_files="path/to/kernels.cpp",      # Required: source file(s)
    hls_function="my_function",             # Required: top-level function name
    include_paths=["include/", "src/"],     # Optional: additional include directories
    part="xcvc1902-vsva2197-2MP-e-S",       # Optional: target device
    platform="path/to/platform.xpfm",      # Optional: platform file
)
```

```matlab
% MATLAB
kernel = vfs.hlsKernel( ...
    input_files="path/to/kernels.cpp", ...
    hls_function="my_function", ...
    include_paths="include/");
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `input_files` | Yes | Path to C/C++ source file(s) containing the HLS kernel |
| `hls_function` | Yes | Name of the top-level function to simulate |
| `include_paths` | No | Additional directories for `#include` resolution |
| `part` | No | Target device part string |
| `platform` | No | Path to `.xpfm` platform file |
| `vfs_build_dir` | No | Load an existing build instead of compiling. Pass it on its own (see [Reusing a build](./aie-graph-api.md#reusing-a-build)) |
| `config_file` | No | Path to a Vitis HLS configuration file. It replaces the source, function and part arguments (example below) |

A minimal configuration file for `config_file`:

```text
part = xcvc1902-vsva2197-1LP-e-S
[hls]
syn.file = kernels.cpp
syn.top = my_function
```

---

## Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `run(*inputs)` | varray (single output) or list of varrays (multiple outputs) | Execute the kernel with given inputs |
| `getInputSpec()` | table | Input port metadata: index, name, type, DataType, size |
| `getOutputSpec()` | table | Output port metadata |
| `setNumKernelRuns(n)` | None | For zero-input kernels: set number of iterations |

Resources are released when the object is deleted (`del kernel` in Python, `clear kernel` in MATLAB).

`hlsKernel` has no `reset()` in Python, MATLAB or C++. A kernel with `static` variables keeps them across `run()` calls, so create a new instance for each independent test vector.

### run() Signatures

```python
# Single input, single output
output = kernel.run(input_data)

# Multiple inputs, multiple outputs
out1, out2 = kernel.run(in1, in2)

# List form (same result as the comma form)
outputs = kernel.run([in1, in2])

# With output reshape (Python only)
output = kernel.run(input_data, shape=(4, 4))

# One shape per output
out1, out2 = kernel.run(in1, in2, shape=[(3,), (4,)])

# One 2D varray for a two-input kernel: column 0 feeds port 0, column 1 feeds port 1
output = kernel.run(va.array(np.column_stack([a, b]), va.int32))
```

```matlab
% Single
output = kernel.run(input_data);

% Multiple
[out1, out2] = kernel.run(in1, in2);

% Cell form
outputs = kernel.run({in1, in2});

% One 2D varray for a two-input kernel: one column per port
outputs = kernel.run(varray.int32([a(:) b(:)]));
output = outputs{1};
```

- Python returns 1D outputs by default. Pass `shape=` to reshape them; a shape that does not fit the element count raises `ValueError`.
- MATLAB returns column vectors and has no `shape` argument. Call `reshape` on the result instead.
- A single 2D input feeds one column to each input port, but only when the kernel has more than one input port. The column count must match the port count.
- In MATLAB, the 2D input form returns a cell array even when the kernel has one output.

---

## Interface Behavior

How much output `run()` returns depends on the kernel's interface type. Check it in the C++ signature before choosing the input length.

### Stream Interfaces (`hls::stream<T>`)

A stream port appears as `hls::stream<T>&` in the signature. VFS calls the kernel once for each complete set of inputs and returns the outputs of those calls. The output count depends on what one call writes: `sum_of_four`, for example, reads 4 samples and writes 1.

| Input Size vs Samples Read per Call | Output | Behavior |
|-------------------------------------|--------|----------|
| N samples, kernel reads K per call | Output of ⌊N/K⌋ kernel calls | Auto-iterates ⌊N/K⌋ times |
| Leftover samples (fewer than K) | Nothing for them yet | Kept and used by the next `run()` call |

```cpp
// C++ kernel with stream interface
void sum_of_four(hls::stream<int32_t> &in, hls::stream<int32_t> &out) {
    int32_t sum = 0;
    for (int i = 0; i < 4; i++)
        sum += in.read();
    out.write(sum);
}
```

### Buffer/Array Interfaces (`int arr[N]`, pointers)

An array or pointer port appears as a fixed-size array or pointer in the signature. VFS waits until it has a complete block, then calls the kernel. Until then, `run()` returns an empty output and keeps the samples.

| Input Size vs Buffer Size | Output | Behavior |
|---------------------------|--------|----------|
| < buffer size | Empty | Samples kept for the next call |
| = buffer size | Complete output | Processes one block |
| 2× buffer size | Double output | Calls the kernel twice |

This is a common surprise. If you send 4 samples to a kernel with an 8-sample buffer, the first `run()` returns nothing. A second `run()` with 4 more samples completes the block and returns all 8 outputs.

```cpp
// C++ kernel with buffer interface
void double_array(int in[8], int out[8]) {
    for (int i = 0; i < 8; i++)
        out[i] = in[i] * 2;
}
```

### Mixed Interfaces (Stream + Scalar/Array)

- Non-stream inputs limit the number of kernel iterations
- Unused stream samples are buffered for the next `run()` call

```cpp
// Mixed: stream + scalar
void scale(hls::stream<int> &in, hls::stream<int> &out, int factor) {
    for (int i = 0; i < 8; i++)
        out.write(in.read() * factor);
}
// Only runs once per scalar value, even if stream has more data
```

```python
# 16 stream samples and one factor: 8 outputs; the other 8 samples wait
stream_data = va.array(np.arange(16, dtype=np.int32))
factor = va.array(np.array([3], dtype=np.int32))
out = kernel.run(stream_data, factor)

# The next factor processes the 8 waiting samples
no_new_data = va.array(np.array([], dtype=np.int32), va.int32)
factor = va.array(np.array([2], dtype=np.int32))
out = kernel.run(no_new_data, factor)
```

---

## AXI-Stream Sideband (ap_axis)

When the kernel uses `hls::stream<ap_axis<W,U,T,D>>` or `hls::stream<ap_axiu<W,U,T,D>>`, inputs must include sideband signals.

### Python

```python
import vfs
import varray as va
import numpy as np

kernel = vfs.hlsKernel(
    input_files="../src/pl_func.cpp",
    hls_function="pl_func"
)

# Create input with axis sideband
in1 = np.arange(1, 6, dtype=np.int32)
in1_arr = va.array(in1, dtype=va.int32, axis=va.axis_config())
in1_arr.axis.tlast[-1] = 1  # Mark last sample in packet

# Run
out1 = kernel.run(in1_arr)
result = np.asarray(out1)
```

### MATLAB

```matlab
kernel = vfs.hlsKernel( ...
    input_files="../src/pl_func.cpp", ...
    hls_function="pl_func");

in1 = int32(1:5);
in1_arr = varray.int32(in1, axis=varray.axis_config());
in1_arr.axis.tlast(end) = 1;

out1 = kernel.run(in1_arr);
result = int32(out1);
```

### Sideband Fields

| Field | Width | Description |
|-------|-------|-------------|
| `tlast` | 1 bit | Marks end of packet (set to 1 on final sample) |
| `tkeep` | W/8 bits | Byte validity mask |
| `tstrb` | W/8 bits | Byte position qualifier |
| `tuser` | U bits | User-defined sideband |
| `tid` | T bits | Stream identifier |
| `tdest` | D bits | Routing destination |

---

## Complete Python Example: Stream Kernel

```python
"""VFS functional verification: HLS stream kernel (sumOfFour)"""
import vfs
import varray as va
import numpy as np

# --- Component instantiation ---
kernel = vfs.hlsKernel(
    input_files="./src/kernels.cpp",
    hls_function="sum_of_four"
)

# --- Inspect ports ---
print("Input spec:", kernel.getInputSpec())
print("Output spec:", kernel.getOutputSpec())

# --- Prepare stimulus ---
# sum_of_four reads 4 int32 values and writes their sum
# Send 8 values → expect 2 output sums
input_data = va.array(np.arange(1, 9, dtype=np.int32))

# --- Execute ---
output = kernel.run(input_data)
result = np.asarray(output)

# --- Verify ---
# Expected: sum(1..4)=10, sum(5..8)=26
expected = np.array([10, 26], dtype=np.int32)
assert np.array_equal(result, expected), \
    f"FAIL: got {result}, expected {expected}"
print(f"PASS: {result}")
```

## Complete Python Example: Buffer Kernel

```python
"""VFS functional verification: HLS buffer kernel (double_array)"""
import vfs
import varray as va
import numpy as np

kernel = vfs.hlsKernel(
    input_files="./src/array_kernels.cpp",
    hls_function="double_array"
)

# Buffer size is 8 — must send exactly 8 (or multiples of 8)
input_data = va.array(np.arange(1, 9, dtype=np.int32))
output = kernel.run(input_data)
result = np.asarray(output)

expected = np.arange(1, 9) * 2
assert np.array_equal(result, expected), \
    f"FAIL: got {result}, expected {expected}"
print(f"PASS: {result}")

# IMPORTANT: Sending partial data (< 8) returns empty!
partial = va.array(np.array([1, 2, 3, 4], dtype=np.int32))
empty_output = kernel.run(partial)
assert len(np.asarray(empty_output)) == 0, "Expected empty output for partial buffer"
```

## Complete MATLAB Example: Stream Kernel

```matlab
%% VFS functional verification: HLS stream kernel
kernel = vfs.hlsKernel( ...
    input_files="./src/kernels.cpp", ...
    hls_function="sum_of_four");

% Inspect ports
disp(kernel.getInputSpec());
disp(kernel.getOutputSpec());

% Prepare stimulus: 8 int32 values
input_data = varray.int32(int32(1:8));

% Execute
output = kernel.run(input_data);
result = int32(output);

% Verify: sum(1:4)=10, sum(5:8)=26
expected = int32([10; 26]);  % VFS outputs are columns in MATLAB
assert(isequal(result, expected), ...
    sprintf('FAIL: got %s, expected %s', mat2str(result'), mat2str(expected')));
fprintf('PASS: %s\n', mat2str(result'));
```

## Complete MATLAB Example: AXI-Stream Kernel

```matlab
%% VFS functional verification: HLS kernel with AXI-Stream sideband
kernel = vfs.hlsKernel( ...
    input_files="../src/pl_func.cpp", ...
    hls_function="pl_func");

% Create inputs with axis sideband
in1 = varray.int32(int32(1:5), axis=varray.axis_config());
in1.axis.tlast(end) = 1;  % Mark end of packet

in2 = varray.uint32(uint32(11:15), axis=varray.axis_config());
in2.axis.tlast(end) = 1;

% Run
[out1, out2] = kernel.run(in1, in2);

% Verify
assert(isequal(double(out1(:)), (1:5)' * 2), 'Output 1 mismatch');
assert(isequal(double(out2(:)), (11:15)' + 10), 'Output 2 mismatch');
fprintf('PASS: All outputs match\n');
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `run()` returns empty array | Buffer interface, partial input | Size input to match kernel's buffer size exactly |
| `ValueError: The number of input vitis arrays (N) does not match the number of input ports (M).` | Wrong number of inputs | Call `getInputSpec()` to check port count |
| `TypeError: Input port 0 expects AXI-Stream axis sidebands` | Missing sideband | Create the input with `axis=va.axis_config()` and set `arr.axis.tlast[-1] = 1` |
| `TypeError: Input port 0 is not an AXI-Stream port ... but the provided varray has an AXI-Stream axis configuration attached.` | Sideband on a plain stream port | Create the input without `axis=` |
| `AttributeError: 'tlast' is not accessible directly. Use a.axis.tlast` | Sidebands are reached through `arr.axis` | Use `arr.axis.tlast` (and `arr.axis.tkeep`, ...) |
| `TypeError: varray.bfloat16 is not a supported data type for HLS Kernels.` | Used AIE-only type | Use `float`, `half`, or `fi` for HLS kernels |
| Output values are wrong/clipped | Integer overflow or wrong dtype | Match varray dtype to kernel's C++ type exactly |
