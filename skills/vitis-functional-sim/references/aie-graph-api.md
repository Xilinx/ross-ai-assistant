<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# AIE Graph API Reference

## Constructor

```python
# Python
graph = vfs.aieGraph(
    input_file="path/to/graph.cpp",           # Required: graph source file (.cpp)
    include_paths=["src/", "include/aie/"],   # Optional: include directories
    part="xcve2802-vsvh1760-2MP-e-S",         # Optional: target device
    Xpreproc=["-DFFT_SIZE=1024"],             # Optional: preprocessor defines
)
```

```matlab
% MATLAB
graph = vfs.aieGraph( ...
    input_file="path/to/graph.cpp", ...
    include_paths="src/");
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `input_file` | Yes | Path to the AIE graph source file (`.cpp`) that instantiates the graph and declares its `PLIO`/`GMIO` ports |
| `include_paths` | No | Additional directories for header resolution |
| `part` | No | Target device part, which sets the AIE architecture. Without `part` or `platform`, VFS uses `xcvc1902-vsva2197-1LP-e-S` and prints a warning |
| `Xpreproc` | No | Preprocessor flags passed to AIE compiler (list of strings) |
| `platform` | No | Path to a platform file (`.xpfm`), instead of `part` |
| `config_file` | No | A v++ `.cfg` file that lists the sources, includes and part (see below) |
| `vfs_build_dir` | No | Load an existing build instead of compiling. Pass it on its own |
| `attach_tkeep_tlast_to_plio_output` | No | `True` attaches `tkeep`/`tlast` to every PLIO output (Python and MATLAB). Packet-stream outputs always have them |

---

## Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `run(*inputs)` | varray or list of varrays | Execute graph with given inputs |
| `getInputSpec()` | table | Input port metadata (name, type, DataType, interface) |
| `getOutputSpec()` | table | Output port metadata |
| `getPacketIds()` | table | Packet IDs per PLIO and kernel; empty without packet switching |
| `setNumIterations(n)` | None | Stop each `run()` after `n` graph iterations |
| `setPortDump(dir_path)` | None | Enable dumping of all port data to files |
| `writePortDump()` | None | Flush port dump data to disk |

Resources are released when the object is deleted (`del graph` in Python, `clear graph` in MATLAB).

`aieGraph` has no `reset()` in Python, MATLAB or C++. Calling `run()` again continues the same simulation, so create a new instance for each independent test vector.

### Limiting Iterations

By default, `run()` lets the graph run until it has consumed all the input. `setNumIterations(n)` stops each `run()` after `n` graph iterations instead:

```python
graph = vfs.aieGraph(input_file="scale_graph/graph.cpp", include_paths=["scale_graph"])
graph.setNumIterations(5)                     # call before the first run()
y = graph.run(va.array(np.arange(16), va.int32))
assert np.asarray(y).size == 5                # one output per iteration here
```

- Call it before the first `run()`. Once a graph has run without a limit, setting one raises `ValueError`. Setting a limit of 0 or less after a positive one also raises `ValueError`.
- Input that a limited run does not consume stays queued. The next `run()` reads it before the new input, so pass exactly the samples that `n` iterations use, or create a new graph for each test vector.
- The limit applies to every later `run()`. You can change it to another positive value between runs.

### run() Signatures

```python
# Single input/output
output = graph.run(input_data)

# Multiple inputs/outputs
out1, out2 = graph.run(in1, in2)
```

```matlab
% Single
output = graph.run(input_data);

% Multiple
[out1, out2] = graph.run(in1, in2);
```

---

## Interface Behavior

AIE graphs follow the same stream vs buffer rules as HLS kernels:

### Stream Ports (`input_stream`, `output_stream`)

- Process complete PLIO beats as data arrives, so output is proportional to input
- Trailing samples that do not fill a beat (for example one `int32` on a 64-bit PLIO) wait for the next `run()`
- Common with PLIO-connected designs

### Window/Buffer Ports (`input_buffer`, `output_buffer`)

- Require complete windows before processing
- Partial input → empty output (buffered internally)
- Window size defined in the graph's `connect<>` declarations

### GMIO Ports

- Call `run()` on a GMIO design exactly as on a PLIO design; the testbench code is the same

### Packet-Switched Ports (`pktsplit` / `pktmerge`)

In a packet-switched graph, one PLIO carries data for several kernels through `pktsplit<N>` and `pktmerge<N>`. To test it, ask the graph which packet ID reaches each kernel. Then pack the input frames with those IDs, run the graph, and match each returned packet by its ID:

```python
import numpy as np
import vfs
from varray import aiePacketUtils

# DUT.cpp from the installed packet_switching example, which has four kernels
graph = vfs.aieGraph(input_file="DUT.cpp", include_paths=["."])

ids = graph.getPacketIds()   # table: PLIO, KernelInstance, PacketID
input_ids = [i for plio, i in zip(ids["PLIO"], ids["PacketID"]) if plio == "input"]

stream = aiePacketUtils.pack_packets([
    (input_ids[0], np.array([1, 2, 3], dtype=np.int32)),
    (input_ids[2], np.array([-5, 6], dtype=np.int32)),
])
for packet_id, data in aiePacketUtils.unpack_packets(graph.run(stream)):
    print(packet_id, np.asarray(data))
```

```matlab
graph = vfs.aieGraph(input_file="DUT.cpp", include_paths=".");
ids = graph.getPacketIds();
inputIds = ids.PacketID(ids.PLIO == "input");

stream = varray.aiePacketUtils.pack_packets( ...
    {{inputIds(1), int32([1 2 3])}, {inputIds(3), int32([-5 6])}});
received = varray.aiePacketUtils.unpack_packets(graph.run(stream));
packetId = received{1}{1};
data = received{1}{2};
```

- `unpack_packets` returns `(packet_id, data)` pairs in arrival order, which can differ from the input order. Match results by output packet ID, not by position.
- Read the input and output IDs separately from `getPacketIds()`. They need not be equal or consecutive.
- `getPacketIds()` returns the compiler-assigned x86sim packet IDs from `packet_ids_c.h`. On one `pktsplit` or `pktmerge`, the branches are numbered 0, 1, 2, and so on. A packet ID is 5 bits, so 31 is the maximum, not a range every graph uses.
- `getPacketIds()` returns an empty table for a graph without packet switching.
- The installed example is `$XILINX_VITIS/functional_suite/examples/aie/packet_switching`.

For a PLIO wider than 32 bits, pass `tdata_width=64` or `128` to `pack_packets`. To build or read a single header yourself, use `generate_packet_header(id)` and `get_packet_id(header)`.

---

## Build Options and Reuse

### Configuration file

Put the sources and settings in a v++ configuration file and pass only `config_file`:

```text
include = src
input_files = src/graph.cpp
part = xcvc1902-vsva2197-1LP-e-S
target = x86sim
aie.Xpreproc = -DFFT_SIZE=1024
```

```python
graph = vfs.aieGraph(config_file="graph.cfg")
```

### Reusing a build

Creating the graph again with the same arguments reuses the earlier build, so a rerun does not recompile. VFS still rebuilds when a source file changes. Builds are kept in `vfs_work/` in the working directory, one subdirectory per build.

To load a build from another directory or script, pass the build's `vfsBuildDir` folder on its own:

```python
graph = vfs.aieGraph(vfs_build_dir="/abs/path/vfs_work/graph_d6bc4007/vfsBuildDir")
```

Combining `vfs_build_dir` with any other argument raises `RuntimeError`. `hlsKernel` follows the same rule.

---

## Port Dump (Debugging)

Port dump records the data at every kernel port in the graph. Use it to find where an unexpected output starts.

`setPortDump()` takes a folder name, and the argument is optional. With no argument, or with `""`, the name is `aieGraph`. A relative name is written under `./aiePortDump/<name>/dump/`. An absolute path is used as given. Call it once per graph object; a second call on the same object fails. A second `aieGraph` in the same process needs a different name.

To attach GDB to the process running the simulation, call `vfs.getpid()` in MATLAB. In debug mode that is the mexHost process. In release mode it is the MATLAB process. In Python call `os.getpid()`. From the Vitis Unified IDE, use the VFS Application launch configuration to start the script under GDB or to attach to that process.

```python
import os
graph = vfs.aieGraph(input_file="./src/graph.cpp", include_paths=["src/"])

# Enable dump BEFORE running. An absolute path is used as is; a relative
# path is placed under ./aiePortDump/ (here ./aiePortDump/debug_dumps/).
dump_dir = os.path.abspath("debug_dumps")
graph.setPortDump(dump_dir)

# Run
output = graph.run(input_data)

# Write captured data to files
graph.writePortDump()

# Read dump files back for analysis. Files are written to <dir>/dump/,
# one per kernel port, named after the graph, kernel and port
# (for example dut_multiply_in_0.txt).
from vfs.readDumpFile import read_dump_file
metadata, data = read_dump_file(os.path.join(dump_dir, "dump", "dut_multiply_in_0.txt"))
print(f"Port: {metadata['port']}, Samples: {len(data)}")
```

`writePortDump()` prints where it wrote the files. Each port file holds every sample that crossed the port during the runs since `setPortDump()`; `metadata` gives `port`, `port_dir`, `port_type` and `data_type`.

```matlab
graph = vfs.aieGraph(input_file="./src/graph.cpp", include_paths="src/");

% Enable dump; files go to <dir>/dump/ (relative paths go under ./aiePortDump/)
graph.setPortDump(fullfile(pwd, 'debug_dumps'));

% Run
output = graph.run(input_data);

% Write dumps
graph.writePortDump();
```

---

## Complete Python Example: Complex Multiply Graph

```python
"""VFS functional verification: AIE graph with cint16 multiplication"""
import vfs
import varray as va
import numpy as np

# --- Component instantiation ---
graph = vfs.aieGraph(
    input_file="./src/multiplyByTwoComplex/vmc_model/graph.cpp",
    include_paths=["./src/multiplyByTwoComplex/vmc_model/"]
)

# --- Inspect ports ---
print("Inputs:", graph.getInputSpec())
print("Outputs:", graph.getOutputSpec())

# --- Prepare stimulus (cint16) ---
N = 256
real_part = np.random.randint(-10000, 10000, size=N)
imag_part = np.random.randint(-10000, 10000, size=N)
input_data = va.array(real_part + 1j * imag_part, va.cint16)

# --- Execute ---
output = graph.run(input_data)
result = np.asarray(output)  # complex NumPy values

# --- Verify: kernel multiplies real by 2, imag by 3 ---
assert np.array_equal(result.real, real_part * 2), "Real mismatch"
assert np.array_equal(result.imag, imag_part * 3), "Imag mismatch"

print(f"PASS: All {N} complex samples verified")
```

## Complete Python Example: AIE Graph with Port Dump Debugging

```python
"""VFS functional verification: AIE graph with debugging via port dump"""
import glob
import os
import vfs
import varray as va
import numpy as np
from vfs.readDumpFile import read_dump_file

graph = vfs.aieGraph(
    input_file="./src/my_graph.cpp",
    include_paths=["./src/"],
    Xpreproc=["-DWINDOW_SIZE=256"]
)

# Enable port dump for debugging; the absolute path makes VFS write to ./port_dumps/dump/
dump_dir = os.path.abspath("port_dumps")
graph.setPortDump(dump_dir)

# Prepare input
input_data = va.array(np.random.randn(256).astype(np.float32))

# Run
output = graph.run(input_data)

# Write dumps to disk
graph.writePortDump()

# Analyze results
result = np.asarray(output)
print(f"Output shape: {result.shape}")
print(f"Output range: [{result.min():.4f}, {result.max():.4f}]")

# If output looks wrong, inspect the kernel ports:
for path in sorted(glob.glob(os.path.join(dump_dir, "dump", "*.txt"))):
    if os.path.basename(path).startswith("x86sim_"):
        continue  # index file, not port data
    metadata, port_data = read_dump_file(path)
    print(f"{metadata['port']} ({metadata['port_dir']}): {len(port_data)} samples")
```

`read_dump_file` supports `int16`, `int32`, `cint32`, `bfloat16` and `half` ports. For other types, such as `cint16`, open the text dump file directly.

## Complete MATLAB Example: AIE Graph

```matlab
%% VFS functional verification: AIE graph (multiplyByTwo)
graph = vfs.aieGraph( ...
    input_file="./src/multiplyByTwo/vmc_model/graph.cpp", ...
    include_paths="./src/multiplyByTwo/vmc_model/");

% Inspect ports
disp(graph.getInputSpec());
disp(graph.getOutputSpec());

% Prepare input: 128 int32 values
N = 128;
stimulus = int32(randi([-1000, 1000], N, 1));
input_data = varray.int32(stimulus);

% Execute
output = graph.run(input_data);
result = int32(output);

% Verify: kernel multiplies by 2
assert(isequal(result(:), stimulus * 2), 'Output mismatch');
fprintf('PASS: %d output samples match\n', length(result));
```

## Complete MATLAB Example: AIE FIR Filter

```matlab
%% VFS functional verification: AIE FIR filter
graph = vfs.aieGraph( ...
    input_file="./src/firAsyncRTP/vmc_model/graph.cpp", ...
    include_paths="./src/firAsyncRTP/vmc_model/");

% Prepare input signal
N = 512;
fs = 1000;  % Sample rate
t = (0:N-1) / fs;
signal = int16(round(16000 * sin(2*pi*50*t)));  % 50 Hz tone
input_data = varray.int16(signal);

% Run the FIR graph
output = graph.run(input_data);
result = double(output);

% Basic sanity: output should be same length, attenuated
assert(length(result) == N, 'Output length mismatch');
fprintf('PASS: FIR output %d samples, peak amplitude %.1f\n', ...
    length(result), max(abs(result)));
```

---

## Common AIE Data Types

| C++ Type in Graph | varray Type (Python) | varray Type (MATLAB) |
|-------------------|---------------------|---------------------|
| `int16` | `va.int16` | `varray.int16` |
| `int32` | `va.int32` | `varray.int32` |
| `cint16` | `va.cint16` (from complex NumPy) | `varray.cint16(re, im)` |
| `cint32` | `va.cint32` (from complex NumPy) | `varray.cint32(re, im)` |
| `float` | `va.float` | `varray.float` |
| `cfloat` | `va.cfloat` | `varray.cfloat` |
| `bfloat16` | `va.bfloat16` | `varray.bfloat16` |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `run()` returns empty | Window/buffer not full | Send data = window size (check graph `connect<>`) |
| Compilation error on `aie::` | Missing include paths | Add path containing `aie_api/` headers |
| Wrong output values | Data type mismatch | Use `getInputSpec()` and match varray dtype exactly |
| `Xpreproc` defines not taking effect | Wrong format | Use list: `Xpreproc=["-DNAME=VALUE"]` |
| Port dump files empty | `writePortDump()` not called | Call after `run()` completes |
| Compiler picks up the wrong header | The compiler also searches the working directory, which holds a header with the same relative path (for example `src/kernels.h`) | Run the testbench from another directory |
