<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# C++ API Reference

The VFS C++ API runs HLS kernels (`vfs::hlsKernel`) and AI Engine graphs (`vfs::aieGraph`) from a C++ testbench. It builds and simulates them the same way the Python and MATLAB APIs do, and it exchanges data as `std::vector` of `varray` types.

In Vitis 2026.2 the C++ library provides only these two classes. Use Python or MATLAB for:

- HDL IP models such as xFFT, vrfFFT, firCompiler and the channelizers. The upstream C++ guide lists IP wrappers, but `vfs.h` and `libamdvfs.so` do not include them.
- AXI-Stream kernels that rely on TLAST or other sidebands. C++ has no `axis_config()`, so the kernel sees no packet boundaries.
- Packet-switched AIE graphs. C++ has no `getPacketIds()` or packet helpers.

---

## Building a Testbench

Include `varray.h` and `vfs.h`, and link against `libamdvfs.so` and `libamdvarray.so` in `$XILINX_VITIS/functional_suite/lib/cpp`. The libraries need the libstdc++ from GCC 9 or later, so the GCC 8 that ships with RHEL 8 cannot link them. Use the GCC bundled with Vitis, and set the runtime library path (rpath) to its libstdc++:

```bash
source <Vitis_install>/settings64.sh
CXX=$XILINX_VITIS/tps/lnx64/gcc-12.2.0/bin/g++
FS=$XILINX_VITIS/functional_suite
$CXX -std=c++17 tb.cpp -I$FS/include -L$FS/lib/cpp \
    -Wl,-rpath-link,$FS/data/ip/floating_point_v7_2 \
    -lamdvfs -lamdvarray \
    -Wl,-rpath,$FS/lib/cpp:$(dirname $($CXX -print-file-name=libstdc++.so)) \
    -o tb
./tb
```

- The testbench must build with exactly this command. Do not make the source depend on extra `-D` defines such as `-DWORKDIR=...`: anyone who builds it with the plain command, including CI, gets `'WORKDIR' was not declared in this scope`. Write source paths relative to the working directory instead (see the note below).
- If the program fails at start-up with ``version `GLIBCXX_3.4.29' not found``, it loaded the system libstdc++. Fix the `-rpath`.
- The `-rpath-link` option only silences a link-time warning about `libgmp.so.11`.
- With GCC 12, the system linker may print `unable to initialize decompress status for section .debug_info`, both for your link and when VFS builds an HLS kernel. The warning is harmless. Adding `-B$XILINX_VITIS/tps/lnx64/binutils-2.44/bin` to your own link uses the bundled linker, which does not print it.

---

## vfs::hlsKernel

The constructor takes a list of `{key, value...}` entries. A key with several values lists them after the key.

```cpp
#include <cstdio>
#include <vector>
#include "varray.h"
#include "vfs.h"

int main() {
    vfs::hlsKernel kernel({
        {"input_files", "stream_kernel/kernels.cpp"},
        {"hls_function", "scale_offset"},
        {"part", "xcvc1902-vsva2197-2MP-e-S"},   // optional
    });

    std::vector<varray::int32> in = {5, -2, 7}, out;
    kernel.run(in, out);          // inputs first, then outputs
    for (auto v : out) std::printf("%d\n", int(v));
    return 0;
}
```

- `run()` takes one vector per port: all inputs first, then all outputs. VFS fills the output vectors.
- When every port has the same type, you can instead pass one `std::vector<std::vector<T>>` for the inputs and one for the outputs, with one inner vector per port.
- If the number of vectors does not match the ports, `run()` throws `std::runtime_error`: `Number of arguments (3) does not match number of ports (inputs: 1, outputs: 1)`.
- A missing source file throws `Error: File does not exist: <path>`.
- `vfs::hlsKernel` has no `reset()`. For a new independent test, create a new instance.
- `setNumKernelRuns(n)` sets how many times a kernel with no input ports runs per `run()`. Call it before `run()`, and pass an empty 2D input: `std::vector<std::vector<varray::int32>> none, outs; kernel.run(none, outs);`.
- Relative paths are resolved from the working directory. VFS keeps its builds in `vfs_work/` there. Unlike the Python and MATLAB scripts, a C++ testbench needs no absolute paths: run it from the directory that holds the sources.

### Complete Stream Testbench

A single-file testbench that checks every output, reports mismatches and returns non-zero on failure. The kernel under test, `gain/sat_gain.cpp`, reads one `hls::stream<int32_t>` sample, multiplies it by 4 and saturates the result to ±1500, so it returns one output per input:

```cpp
// gain/sat_gain.cpp
#include "hls_stream.h"
#include <stdint.h>

void sat_gain(hls::stream<int32_t> &in, hls::stream<int32_t> &out) {
    int32_t y = 4 * in.read();
    out.write(y > 1500 ? 1500 : (y < -1500 ? -1500 : y));
}
```

The testbench keeps its own reference model, `golden()`, and compares every output against it:

```cpp
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <random>
#include <stdexcept>
#include <vector>
#include "varray.h"
#include "vfs.h"

static int32_t golden(int32_t x) { return std::clamp(4 * x, -1500, 1500); }

int main() {
    const int N = 48;
    std::mt19937 rng(2026);
    std::uniform_int_distribution<int32_t> dist(-600, 600);   // reaches both saturation limits

    std::vector<int32_t> samples;
    std::vector<varray::int32> in, out;
    for (int i = 0; i < N; ++i) {
        samples.push_back(dist(rng));
        in.emplace_back(samples.back());
    }

    try {
        vfs::hlsKernel kernel({
            {"input_files", "gain/sat_gain.cpp"},
            {"hls_function", "sat_gain"},
        });
        kernel.run(in, out);
    } catch (const std::exception &e) {
        std::fprintf(stderr, "VFS error: %s\n", e.what());
        return 1;
    }

    int mismatches = 0;
    if (out.size() != samples.size()) {
        std::printf("Expected %zu outputs, got %zu\n", samples.size(), out.size());
        mismatches += 1;
    }
    for (size_t i = 0; i < samples.size() && i < out.size(); ++i) {
        int32_t expected = golden(samples[i]);
        if (int32_t(out[i]) != expected) {
            if (mismatches < 10)
                std::printf("Mismatch at %zu: got %d, expected %d\n", i, int(out[i]), int(expected));
            ++mismatches;
        }
    }
    std::printf("Mismatches: %d\n", mismatches);
    std::printf(mismatches == 0 ? "PASS\n" : "FAIL\n");
    return mismatches == 0 ? 0 : 1;
}
```

- The source path is relative to the working directory, so the file builds with the command in [Building a Testbench](#building-a-testbench) and runs from the directory that holds `gain/`. No `-D` defines are needed.
- Keep the reference values in a plain `std::vector<int32_t>`, not in the `varray` vector you pass to `run()`.
- Check the output count before comparing values: a short output must fail, not pass with fewer comparisons.
- Return non-zero from `main` on any failure. Printing `FAIL` alone does not fail the run.

---

## vfs::aieGraph

```cpp
vfs::aieGraph graph({
    {"input_file", "scale_graph/graph.cpp"},
    {"include_paths", "scale_graph", "scale_graph/src"},   // several values after one key
    {"Xpreproc", "-DGAIN=3"},                              // optional preprocessor defines
});

std::vector<varray::int32> in = {1, 2, 3, 4}, out;
graph.run(in, out);
```

- `run()` and the 2D form work as for `hlsKernel`. Calling `run()` again continues the same simulation. `vfs::aieGraph` has no `reset()`; create a new instance for each independent test.
- `setPortDump("name")` before `run()`, then `writePortDump()` after it, writes text dumps of every kernel port to `aiePortDump/name/dump/`. Each dump starts with the port name, direction, type and data type. The name is optional and defaults to `aieGraph`. Call it once per graph object, and give each graph in the same process its own name. A relative name is placed under `aiePortDump/`; an absolute path is used as given.
- `setNumIterations(n)` stops each `run()` after `n` graph iterations. A graph that writes one sample per iteration then returns `n` outputs, however much input you pass.

---

## Inspecting Ports

`getInputSpec()` and `getOutputSpec()` fill two vectors: `labels` holds the field names (`Name`, `Type`, `DataType`, `Size`, `Interface`), and `values[i]` holds that field for every port.

```cpp
std::vector<std::string> labels;
std::vector<std::vector<std::string>> values;
graph.getInputSpec(labels, values);
for (size_t i = 0; i < labels.size(); ++i)
    std::printf("%s: %s\n", labels[i].c_str(), values[i][0].c_str());   // port 0
// Name: in / Type: stream / DataType: int32 / Size: N/A / Interface: PLIO(32)
```

`getNumInputPorts()` and `getNumOutputPorts()` return the port counts.

---

## Build Options and Reuse

Both classes accept `config_file` or `vfs_build_dir` as their only entry:

```cpp
vfs::hlsKernel fromCfg({{"config_file", "kernel.cfg"}});   // same .cfg format as Python
vfs::aieGraph reused({{"vfs_build_dir", "vfs_work/graph_49db03b7/vfsBuildDir"}});
```

- The hash in the build directory name depends on the constructor arguments. List `vfs_work/` to find it.
- A build loaded with `vfs_build_dir` starts in well under a second.
- Mixing either key with other entries throws `Error: If a .cfg file is specified, it should be the only argument.` or `Error: If vfsBuildDir is specified, it should be the only argument.`
- A missing build directory throws `Error: The specified build directory ... does not exist.`
- Creating a component again with the same arguments also reuses its earlier build automatically.

---

## Chaining

An output vector can feed the next component directly, as long as the types match:

```cpp
vfs::hlsKernel hls({{"input_files", "stream_kernel/kernels.cpp"}, {"hls_function", "scale_offset"}});
vfs::aieGraph aie({{"input_file", "scale_graph/graph.cpp"}, {"include_paths", "scale_graph"}});

std::vector<varray::int32> in = {1, 2, 3}, mid, out;
hls.run(in, mid);    // 3 * x + 1
aie.run(mid, out);   // 2 * x - 5
```

---

## varray Types in C++

- `varray::int8` … `varray::uint64`, `float`, `double`, `bfloat16`, `half` and the other scalar aliases map to the kernel's C++ types.
- `varray::cint16` and the other complex aliases are `std::complex` types. Use `.real()` and `.imag()`.
- `varray::fi<Sign, Width, Fraction>` is a fixed-point value. For example, `varray::fi<1, 16, 8> x(1.5f)` stores `0x0180`. `varray::hex(x)` returns `"0180"`, and `static_cast<float>(x)` returns `1.5`.
- Passing a vector of a type that is not a `varray` type fails at compile time with a `DRC Error` static assertion.
