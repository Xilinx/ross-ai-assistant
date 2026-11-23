---
name: vitis-functional-sim
description: >-
  Generate Python, MATLAB or C++ functional verification test scripts for AMD
  Versal hardware implementations using VFS (Vitis Functional Simulation).
  Supports HLS kernels, AI Engine graphs (including packet switching), LogiCORE
  IPs (xFFT, FIR Compiler), Versal RF Hard IPs (vrfFFT, vrfChannelizer, vrfADC,
  vrfDAC), Poly Block IPs (pbChannelizer, pbArbResampler), the Versal LDPC
  decoder (versalLDPC), and multi-component chained pipelines.
  Use when: user wants to functionally verify their implementation, build a test
  framework, simulate their kernel in Python/MATLAB/C++, compare against a
  reference model, create a VFS testbench, or functionally simulate their design.
  VFS is functional x86 simulation. For cycle-approximate PLIO handshaking
  between PL kernels and an AI Engine graph with an RTL testbench, use Vitis
  Subsystem Simulation instead.
compatibility: Requires Vitis 2026.2+ with VFS, and Python 3.9-3.14, MATLAB, or a C++17 compiler (GCC 9+)
license: MIT
metadata:
  stage: beta
  version: "1.0.0"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Vitis Functional Simulation (VFS)

Generate functional verification test scripts that wrap hardware implementations in Python, MATLAB or C++ using the VFS framework. VFS provides bit-accurate C-model simulation of HLS kernels, AI Engine graphs, and HDL IPs — no synthesis or place-and-route required.

VFS is functional simulation through x86sim and C models. It does not check cycle-approximate PLIO handshaking between PL kernels and an AI Engine graph. For that, use an RTL testbench with Vitis Subsystem Simulation (UG1701, "Simulation with the Vitis Subsystem"; the `vitis-vss-simulation` skill).

---

## Prerequisites

Before generating any test script, verify the following. Halt and inform the user if any check fails.

### 1. Vitis Environment

```bash
echo $XILINX_VITIS
```

- If `$XILINX_VITIS` is empty or the command fails: ask the user for the path to their Vitis 2026.2 install (the directory that contains `settings64.sh`), then source `<Vitis_install>/settings64.sh`. Do not guess the path.
- Confirm: `which v++` returns a valid path
- If the test machine has no full Vitis install, create a standalone VFS copy on a machine that has one: `$XILINX_VITIS/functional_suite/create_vfs_standalone.sh <dest>` (3–5 GB). Then source `<dest>/settings64.sh` instead of the Vitis one.
- This skill targets VFS 2026.2. If the user is on an earlier release, check `getInputSpec()` and the installed VFS docs before using the AXI-Stream or `vrfFFT` APIs.

### 2. Language-Specific Setup

#### Python

1. Ask the user: **"Do you have an existing Python virtual environment for this project, or should I create one?"**
   - If existing: `source <user_path>/bin/activate`
   - If new: `python3 -m venv vfs_env && source vfs_env/bin/activate`
2. Verify Python version is 3.9–3.14: `python3 --version`
3. Verify NumPy: `python3 -c "import numpy"` — if fails, run `pip install numpy`
4. Verify VFS: `python3 -c "import vfs; import varray as va; print('VFS OK')"`

#### MATLAB

1. Check: `which matlab`
   - If not available: ask the user for the MATLAB install directory (the one that contains `bin/matlab`), then add its `bin` directory to `PATH` for this shell. Do not guess the path. Then continue with step 2.
2. Launch MATLAB from a shell where Vitis `settings64.sh` has been sourced. That script adds the VFS MATLAB packages to `MATLABPATH`, so `vfs` and `varray` resolve with no further command at the MATLAB prompt
3. Verify VFS available: `matlab -batch "disp(class(varray.int32(1)))"` prints `varray.int32`

#### C++

1. The C++ API covers HLS kernels and AIE graphs only. For HDL IPs, AXI-Stream sidebands or packet switching, use Python or MATLAB.
2. Use GCC 9 or later. The GCC 8 on RHEL 8 cannot link the VFS libraries; use `$XILINX_VITIS/tps/lnx64/gcc-12.2.0/bin/g++`.
3. Build with the command in [cpp-api.md](./references/cpp-api.md#building-a-testbench), which sets the include path, libraries and rpath.

### 3. Source Files

- Confirm all user-specified source files (`.cpp`, `.h`, `.cfg`) exist and are accessible
- Confirm any specified include paths are valid directories

---

## Workflow

### Step 1: Detect Components

Read the user's source files and classify:

| Indicator | Component Type |
|-----------|---------------|
| `hls::stream<T>`, `ap_int`, `ap_fixed`, standalone C++ function with `#pragma HLS` | **HLS Kernel** |
| `adf::graph`, `input_port`, `output_port`, `aie::` namespace, `kernel::create` | **AIE Graph** (with `pktsplit`/`pktmerge`: packet switching) |
| User explicitly names vrfFFT, xFFT, firCompiler, vrfChannelizer, vrfADC, vrfDAC, pbChannelizer, pbArbResampler, versalLDPC | **HDL IP** |
| Multiple of the above | **Chained Pipeline** |

Load the relevant reference document:
- HLS → [./references/hls-kernel-api.md](./references/hls-kernel-api.md)
- AIE → [./references/aie-graph-api.md](./references/aie-graph-api.md)
- HDL IP → [./references/hdl-ip-api.md](./references/hdl-ip-api.md)
- Chained → [./references/chaining-patterns.md](./references/chaining-patterns.md)
- Data types → [./references/varray-types.md](./references/varray-types.md)
- C++ testbench (HLS and AIE) → [./references/cpp-api.md](./references/cpp-api.md)

### Step 2: Determine Test Strategy

Ask the user: **"Do you have an existing reference model (Python or MATLAB script) that produces golden output to compare against?"**

#### Path A: Existing Reference Model

1. Read the existing script to identify:
   - Input stimuli (variable names, data shapes, types)
   - Expected output (golden vectors)
   - Data flow and processing order
2. Inject VFS component creation into the same script (or a companion script if the user prefers)
3. Feed the SAME stimuli to the VFS component
4. Add comparison logic after both reference and VFS produce output

**Script ordering when integrating with existing reference:**
```
[reference model]   ← runs first, produces golden_output
[VFS section]       ← creates kernel, feeds same stimuli, runs
[comparison logic]  ← compares golden vs VFS output, reports error in LSBs
```

#### Path B: No Reference Model

1. Create a new test script from scratch
2. Use `getInputSpec()` on the VFS component to determine port names, types, and widths
3. Generate random stimuli with appropriate shape and dtype:
   - Integer ports: `np.random.randint(low, high, size=N)` bounded by bit-width
   - Float ports: `np.random.randn(N)` scaled appropriately
   - Complex ports: `np.random.randn(N) + 1j * np.random.randn(N)`
4. Run the VFS component and print/plot outputs for user inspection

### Step 3: Select Language

If not already obvious from context (e.g., user said "MATLAB" or has `.m` files), ask: **"Python or MATLAB for the test script?"** Offer C++ only when the user asks for it or the design is HLS kernels and AIE graphs without AXI-Stream sidebands or packet switching.

### Step 4: Generate Test Script

Follow this structure:

```
1. Imports (vfs, varray, numpy/scipy or MATLAB built-ins)
2. Path setup (use absolute paths — VFS compiles from a work directory)
3. Component instantiation (constructor with source paths)
4. Stimulus preparation (varray creation from golden data or random)
5. Execution (component.run())
6. Comparison (if reference exists):
   - Compute error in LSBs (1 LSB = 1 integer unit at output precision)
   - Report max mismatch in LSBs (0 = bit-exact)
   - Pass/fail assertion against LSB tolerance
7. Results display (print LSB mismatch, pass/fail)
```

**Path setup pattern** (always use absolute paths for source files):

```python
import os
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
kernel = vfs.hlsKernel(
    input_files=os.path.join(SCRIPT_DIR, "kernels.cpp"),
    hls_function="my_function"
)
```

```matlab
script_dir = fileparts(mfilename('fullpath'));
kernel = vfs.hlsKernel(input_files=fullfile(script_dir, 'kernels.cpp'), ...
    hls_function="my_function");
```

#### Comparison Logic Templates

Always report mismatch in **LSBs** (1 LSB = 1 integer unit at the output word's least significant bit position). For floating-point outputs, report max absolute error instead.

**When to expect bit-exact (0 LSBs):**
- Integer-only paths (e.g., int16 → int16) with identical quantization
- Reference model uses the same fixed-point arithmetic as the implementation

**When to expect small LSB mismatch (1–2 LSBs):**
- Same word widths but different rounding at intermediate stages

**When to expect larger mismatch (>2 LSBs):**
- Reference is floating-point, implementation is fixed-point
- Cascaded fixed-point operations with different intermediate precision

Convert both sides to whole LSBs of the output type, `round(x * 2^FRAC_BITS)`, and compare real and imaginary parts separately. Do not cast the outputs to an integer type directly: `np.asarray()` returns `fi` outputs as float64 and `cint`/`cfi` outputs as complex, so a direct cast drops the fraction or the imaginary part, and outputs that differ report 0 LSBs. Set `FRAC_BITS` to the output's fraction length: 0 for integer and `cint` outputs, `F` for `fi(s, W, F)`, `cfi(s, W, F)` and `ap_fixed<W, W-F>`. The VFS output always lands on whole LSBs, so the templates fail when it does not: a `FRAC_BITS` that is too small would otherwise round the fraction away and report a false PASS.

**Python — LSB comparison (integer/fixed-point paths, real or complex):**
```python
import numpy as np

MAX_LSB_TOLERANCE = 0  # 0 = require bit-exact; adjust per design
FRAC_BITS = 0          # output fraction length: 0 for int/cint, F for fi(s, W, F)

def to_lsb(x, check_whole=False):
    x = np.asarray(x).flatten().astype(np.complex128) * 2.0 ** FRAC_BITS
    if check_whole and not np.allclose(x, np.round(x), rtol=0, atol=1e-6):
        raise AssertionError("FAIL: output is not on whole LSBs; set FRAC_BITS to its fraction length")
    return np.round(x.real).astype(np.int64), np.round(x.imag).astype(np.int64)

ref_re, ref_im = to_lsb(golden_output)
act_re, act_im = to_lsb(vfs_output, check_whole=True)
if act_re.size == 0 or act_re.size != ref_re.size:
    raise AssertionError(f"FAIL: expected {ref_re.size} outputs, got {act_re.size}")
max_err_lsb = int(max(np.max(np.abs(act_re - ref_re)), np.max(np.abs(act_im - ref_im))))

print(f"Max mismatch: {max_err_lsb} LSBs")
if max_err_lsb == 0:
    print("PASS: Bit-exact match (0 LSBs)")
elif max_err_lsb <= MAX_LSB_TOLERANCE:
    print(f"PASS: {max_err_lsb} LSBs within tolerance ({MAX_LSB_TOLERANCE})")
else:
    raise AssertionError(
        f"FAIL: {max_err_lsb} LSBs exceeds tolerance ({MAX_LSB_TOLERANCE})")
```

**MATLAB — LSB comparison (integer/fixed-point paths, real or complex):**
```matlab
MAX_LSB_TOLERANCE = 0;  % 0 = require bit-exact
FRAC_BITS = 0;          % output fraction length: 0 for int/cint, F for fi(s, W, F)

scale = 2^FRAC_BITS;
reference = double(golden_output(:)) * scale;  % Column vector
actual = double(vfs_output(:)) * scale;        % Column vector — VFS outputs are columns
if isempty(actual) || numel(actual) ~= numel(reference)
    error('FAIL: expected %d outputs, got %d', numel(reference), numel(actual));
end
if any(abs(actual - round(actual)) > 1e-6)
    error('FAIL: output is not on whole LSBs; set FRAC_BITS to its fraction length');
end
err = [round(real(actual)) - round(real(reference)); ...
       round(imag(actual)) - round(imag(reference))];   % not "error": that hides error()
max_err_lsb = max(abs(err));

fprintf('Max mismatch: %d LSBs\n', max_err_lsb);
if max_err_lsb == 0
    fprintf('PASS: Bit-exact match (0 LSBs)\n');
elseif max_err_lsb <= MAX_LSB_TOLERANCE
    fprintf('PASS: %d LSBs within tolerance (%d)\n', max_err_lsb, MAX_LSB_TOLERANCE);
else
    error('FAIL: %d LSBs exceeds tolerance (%d)', max_err_lsb, MAX_LSB_TOLERANCE);
end
```

**Python — Floating-point paths (when LSB doesn't apply):**
```python
reference = np.asarray(golden_output).flatten()
actual = np.asarray(vfs_output).flatten()
max_err = np.max(np.abs(reference - actual))
print(f"Max absolute error: {max_err:.6e}")
assert max_err < TOLERANCE, f"FAIL: max error {max_err:.6e} exceeds {TOLERANCE}"
print("PASS")
```

### Step 5: Validate

After generating the script:

1. **Run it** — execute the generated script to confirm no import/syntax/runtime errors
2. **Check paths** — verify all referenced source files resolve
3. **Check types** — confirm varray dtype matches what the kernel port expects
4. **Warn about buffering** — if the kernel uses buffer/array interfaces, warn the user that partial inputs produce no output until the buffer fills (see [hls-kernel-api.md](./references/hls-kernel-api.md) for details)

---

## Component-Specific Patterns

### HLS Kernel (Quick Reference)

```python
import vfs
import varray as va
import numpy as np
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

kernel = vfs.hlsKernel(
    input_files=os.path.join(SCRIPT_DIR, "kernels.cpp"),
    hls_function="my_function",
    include_paths=[os.path.join(SCRIPT_DIR, "include/")],  # optional
)

# Inspect ports
print(kernel.getInputSpec())
print(kernel.getOutputSpec())

# Run
input_data = va.array(np.arange(1, 9, dtype=np.int32))
output = kernel.run(input_data)
result = np.asarray(output)
```

### AIE Graph (Quick Reference)

```python
import vfs
import varray as va
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

graph = vfs.aieGraph(
    input_file=os.path.join(SCRIPT_DIR, "graph.cpp"),
    include_paths=[os.path.join(SCRIPT_DIR, "src/")],
)

output = graph.run(va.array(input_data, dtype=va.cint16))
```

For a packet-switched graph (`pktsplit`/`pktmerge`), get the IDs from `graph.getPacketIds()`. Those IDs are the compiler-assigned x86sim values from `packet_ids_c.h`. On one `pktsplit` or `pktmerge`, the branches are numbered 0, 1, 2, and so on. A packet ID is 5 bits, so 31 is the maximum, not a range every graph uses. Build the input with `varray.aiePacketUtils.pack_packets`, and split the output with `unpack_packets`. Output packets can arrive in a different order from the input, so match each one to its expected result by packet ID. See [aie-graph-api.md](./references/aie-graph-api.md#packet-switched-ports-pktsplit--pktmerge).

### HDL IP (Quick Reference)

```python
import varray as va
from vfs.vrfFFT import vrfFFT

config = vrfFFT.getDefaultConfig()
config.data_width_in = 16
config.reorder_output = 1   # Config field; Control() rejects it
fft = vrfFFT(config)

ctrl = vrfFFT.Control(point_size=10, direction=1, scaling_factor=1/1024)
output, overflow = fft.run(va.array(data, va.cdouble), ctrl)
```

Create `pbChannelizer`, `pbArbResampler` and `versalLDPC` with `Config(field=value)`. The Poly Block IPs return the output and a status object. `versalLDPC` takes LLRs rather than bits and returns hard output, soft output and status. See [hdl-ip-api.md](./references/hdl-ip-api.md).

Several IPs can be changed between `run()` calls: `firCompiler` coefficient sets and reloads (`configSend`, `reloadSend`), `pbChannelizer` coefficients and gain in multi-channel filter mode (`updateCoeff`, `updateGain`, `flush`), `versalLDPC` custom codes (`generateLdpcSpec`, `addLdpcCode`), and `vrfADC`/`vrfDAC` settings (`configure`). Each needs a specific config flag or call order, described in [hdl-ip-api.md](./references/hdl-ip-api.md).

### Chained Pipeline (Quick Reference)

```python
# Components chain freely — output varray feeds next input
stage1_out = aie_graph.run(input_data)
stage2_out = hls_kernel.run(stage1_out)
final_out = hdl_ip.run(stage2_out, control)
```

### C++ Testbench (Quick Reference)

```cpp
#include <cstdint>
#include <cstdio>
#include <vector>
#include "varray.h"
#include "vfs.h"

int32_t golden(int32_t x);   // define as the kernel's reference model

int main() {
    std::vector<int32_t> samples = {1, -2, 3};
    std::vector<varray::int32> in(samples.begin(), samples.end()), out;
    vfs::hlsKernel kernel({{"input_files", "kernels.cpp"}, {"hls_function", "my_function"}});
    kernel.run(in, out);   // inputs first, then outputs; VFS fills `out`

    int mismatches = out.size() == samples.size() ? 0 : 1;
    for (size_t i = 0; i < samples.size() && i < out.size(); ++i)
        if (int32_t(out[i]) != golden(samples[i])) ++mismatches;
    std::printf("Mismatches: %d\n", mismatches);
    return mismatches == 0 ? 0 : 1;   // non-zero on any mismatch or missing output
}
```

Compare every output against a golden model, fail on a short output, and return non-zero from `main` on failure. Write source paths relative to the working directory, and keep the file buildable with the plain build command: no `-D` defines such as `-DWORKDIR`. `vfs::aieGraph` takes `{"input_file", ...}` and `{"include_paths", ...}` the same way. See [cpp-api.md](./references/cpp-api.md#complete-stream-testbench) for a complete testbench and the build command.

---

## Important Notes

- **Stream vs Buffer**: For `hls::stream<T>` inputs, VFS runs the kernel whenever enough samples are available for one call and saves the remainder for the next `run()`. Array/buffer inputs produce no output until a complete block is available. Match the input length to the kernel's read or block size.
- **vrfADC chunk length:** Each `vrfADC.run()` input length should be a multiple of `decim_rate`. The call still returns data when it is not, after the cmodel warning `Incorrect number of samples passed to DDC`, and later outputs can differ from one `run()` of the whole stream. Compare a chunked run with a single run only when every chunk is a multiple of the rate. See [hdl-ip-api.md](./references/hdl-ip-api.md).
- **varray is universal**: All VFS components consume and produce `varray` (vrfADC and vrfDAC wrap them in a per-channel dict). Use `va.array()` to create from NumPy, `np.asarray()` to convert back.
- **AXI-Stream sideband**: If a kernel port uses `ap_axis<>` or `hls::stream<ap_axis<>>`, create the input with `axis_config()` and set `tlast` on the final sample. In Python: `va.array(data, va.int32, axis=va.axis_config())`, then `arr.axis.tlast[-1] = 1`. In MATLAB: `varray.int32(data, axis=varray.axis_config())`, then `arr.axis.tlast(end) = 1`.
- **Components keep state**: HLS kernels with `static` variables, AIE graphs and HDL IPs all carry state across `run()` calls. Create a new instance for each independent test vector. `hlsKernel`, `aieGraph`, `xfft` and `vrfFFT` have no `reset()` in Python, MATLAB or C++, so a new instance is the only way to clear them. Only `firCompiler`, `vrfChannelizer`, `vrfADC`, `vrfDAC`, `pbChannelizer`, `pbArbResampler` and `versalLDPC` can instead be cleared with `reset()`. A `versalLDPC` created with `init_code` loses that code on `reset()`, so call `addLdpcCode(0, path)` again before the next decode (see [hdl-ip-api.md](./references/hdl-ip-api.md#custom-codes)). For `vrfChannelizer`, use `reset()` or keep every instance alive: replacing an instance can end the process (see [hdl-ip-api.md](./references/hdl-ip-api.md#vrfchannelizer-vrf-channelizer-v20)).
- **Builds are reused**: creating a component again with the same arguments loads the earlier build instead of recompiling. VFS still rebuilds when a source file changes. Builds are kept in `vfs_work/` in the working directory. To load one from elsewhere, pass only `vfs_build_dir`.
- **Port inspection**: Always call `getInputSpec()` / `getOutputSpec()` first if unsure about port names, types, or widths.
- **MATLAB vector orientation**: VFS outputs are column vectors in MATLAB. Always coerce to consistent orientation before comparison: `golden(:)` and `double(vfs_output(:))`.
- **Use absolute paths in Python and MATLAB**: VFS compiles from an internal work directory. Always use absolute paths for `input_files` and `include_paths` (use `os.path.abspath()` in Python, `fileparts(mfilename('fullpath'))` in MATLAB). In C++, paths are resolved from the working directory, so use relative paths and run the testbench from the source directory. Do not pass the directory in with a `-D` define.
