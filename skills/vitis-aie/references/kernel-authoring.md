<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# AIE Kernel & Graph Authoring Reference

Ground-truth templates for authoring the source files of an AI Engine component.

## Recommended layout — mirror the Vitis examples (DEFAULT)

**Author the design in the same structure as the installed Vitis examples**
(`$XILINX_VITIS/installed_examples/aie_examples`). Start from the closest example
and adapt it. The canonical layout is:

| File | Role |
|------|------|
| `src/project.h` | the graph class (`adf::graph`) — kernels, PLIO, connects, dims |
| `src/project.cpp` | top: graph instance + `main()` (`init/run(n)/end`) |
| `src/kernels.h` | kernel **function** declarations |
| `src/kernels/kernels.cc` | kernel function implementations (`in.data()`/`out.data()`) |
| `src/kernels/include.h` | shared defines (e.g. `NUM_SAMPLES`) |
| `data/input.txt`, `data/golden.txt` | PLIO stimulus + expected output |

Build it with the driver's tree import:
`--src-dir <design>/src --data-dir <design>/data --top-file project.cpp`.
See the **"Canonical Vitis example idiom"** section at the end for the exact
templates (function kernels, `kernel::create(fn)`, `data/` PLIO, multi-kernel
chaining). This is the preferred output — it matches what Vitis users get from
"create from example" and validates against the shipped golden data.

> The class-based, flat single-kernel style below (§1–§7) is a simpler
> alternative for trivial one-kernel designs; both compile. Prefer
> the example-mirroring layout above for anything non-trivial.

---

## 1. Kernel header — `KERNEL.h`

```cpp
#pragma once
#include <adf.h>
#include <aie_api/aie.hpp>
#include <aie_api/utils.hpp>
using namespace adf;

class KERNEL {
public:
  KERNEL(void);                 // constructor (add LUT refs if used)
  void run(void);               // replace with the real port signature
  static void registerKernelClass(void) { REGISTER_FUNCTION(KERNEL::run); }
};
```

**Ports** are arguments of `run()`. Each port: direction (input/output), data type
(`int32`, `float`, `cfloat`, `int16`, `cint32`, …), and kind (stream/buffer):

| Kind | Dir | Declaration |
|------|-----|-------------|
| stream | input | `input_stream<int32>* sig_i` |
| stream | output | `output_stream<float>* sig_o` |
| buffer | input | `input_buffer<int16>& val_i` |
| buffer | output | `output_buffer<cint32>& val_o` |

e.g. `void run(input_stream<int32>* sig_i, output_buffer<float>& sig_o);`

**Buffer port sizes** (samples per iteration) as `static constexpr unsigned`,
prefixed `NSAMP_` (upper-case): `static constexpr unsigned NSAMP_SIG_O = 128;`
(stream ports need no size.)

**LUTs** (optional): declare as aligned array references, register with
`REGISTER_PARAMETER`, initialize via the constructor:
```cpp
static constexpr unsigned LUT_A_SIZE = 512;
alignas(16) float (&LUT_A)[LUT_A_SIZE];
KERNEL(float (&LUT_A_i)[LUT_A_SIZE]);
// in registerKernelClass: REGISTER_PARAMETER(LUT_A);
```

## 2. Kernel implementation — `KERNEL.cpp`

```cpp
#include <adf.h>
#include <aie_api/aie.hpp>
#include "KERNEL.h"

KERNEL::KERNEL(void) {
  aie::set_rounding(aie::rounding_mode::conv_even);
  aie::set_saturation(aie::saturation_mode::saturate);
}

void KERNEL::run( /* same signature as KERNEL.h */ ) {
  // compute; e.g. for streams: readincr(sig_i) / writeincr(sig_o, v)
}
```
The `run()` and constructor signatures must match `KERNEL.h` exactly. If LUTs are
used, the constructor takes the array refs and initializes the members.

## 3. Graph — `KERNEL_graph.h`

```cpp
#pragma once
#include <adf.h>
#include "KERNEL.h"
using namespace adf;

class KERNEL_graph : public graph {
private:
  kernel kk;
public:
  input_port  sig_i;            // one per kernel input port
  output_port sig_o;            // one per kernel output port
  KERNEL_graph(void) {
    kk = kernel::create_object<KERNEL>();   // add LUT vectors if used
    source(kk) = "KERNEL.cpp";
    runtime<ratio>(kk) = 0.9;
    connect<stream>(sig_i, kk.in[0]);       // kernel ports indexed by run() order
    connect<stream>(kk.out[0], sig_o);
    // dimensions(kk.out[0]) = { KERNEL::NSAMP_SIG_O };   // buffer ports only
  }
};
```
- Connect each graph port to its kernel port; **kernel in/out ports are numbered
  by their order in the `run()` signature** (in[0], in[1]; out[0], out[1]).
- Buffer ports need `dimensions(...)`; stream ports do not.
- For LUTs, declare `std::vector<T>` members and pass them to `create_object`.

## 4. Top-level app — `KERNEL_app.cpp`

The graph's `input_port`/`output_port` must reach **real PLIO/GMIO I/O** for the
compiler to accept them and for simulation to have a source/sink. Wrap the graph:

```cpp
#include "KERNEL_graph.h"

class dummy_graph : public graph {
public:
  input_plio  top_i;
  output_plio top_o;
  KERNEL_graph dut;
  dummy_graph(void) {
    top_i = input_plio::create("DataIn1", plio_32_bits, "data/input.txt");
    top_o = output_plio::create("DataOut1", plio_32_bits, "data/output.txt");
    connect<>(top_i.out[0], dut.sig_i);
    connect<>(dut.sig_o, top_o.in[0]);
  }
};

dummy_graph gr;

int main(void) {
  gr.init();
  gr.run(1);
  gr.end();
  return 0;
}
```
- `input_plio::create("<name>", plio_<width>_bits, "<file>")` — width matches the
  port data width (32-bit int → `plio_32_bits`); the file is PLIO stimulus. **The data file
  packs to the PLIO width, not the element width:** on `plio_32_bits` carrying `int16`, each text
  row holds **two** `int16` samples — match input and golden to that (see §7).
- `input_gmio::create("<name>", burst, bandwidth)` for GMIO instead of PLIO.
- A graph with `input_port`/`output_port` left **unconnected** fails to compile
  (`port should have one and only one valid connection`) — always wire to PLIO/GMIO.

**PLIO vs GMIO — prefer PLIO for file-based simulation.** PLIO reads/writes
`data/*.txt` automatically, so `main()` is just `init(); run(N); end();` and the
sim produces `output.txt` to compare against `golden.txt`. **GMIO is host-memory
driven**: it does NOT read `data/*.txt`. With GMIO you MUST move data yourself in
`main()` — allocate buffers and call `gmio.gm2aie_nb(inBuf, bytes)` /
`gmio.aie2gm_nb(outBuf, bytes)` then `gmio.wait()` — and verify the output buffer
in `main()` (there is no `output.txt`). A GMIO graph whose `main()` only does
`init/run/end` **deadlocks** (the kernel starves: "GMIO input is done"), and the
driver reports `deadlock:true, succeeded:false`. So unless the request
specifically needs GMIO/host-DMA, author the design with **PLIO**.

## 5. Config — `aie.cfg` (optional)

```
[aie]
kernel-linting=true
xlopt=1
verbose=true
Xmapper=BufferOptLevel9
```
NOTE: a Vitis AIE component already includes its own cfg; adding a second is
rejected. Only supply `aie.cfg` for the standalone v++/Makefile flow.

## 6. Constraints — `KERNEL_app.aiecst` (optional)

Floorplan constraints; default is empty:
```
{
}
```

## 7. Simulation data

PLIO data files (`data/*.txt`) are text, **one PLIO _word_ per line — not necessarily one logical
element per line.** The row width is the PLIO width, so a sub-word element type packs **multiple
elements per row**: a **32-bit PLIO carrying `int16` holds TWO `int16` per row** (a 32-bit PLIO
carrying `int32`/`float` holds one; a 128-bit PLIO carrying `int16` holds eight). Pack **both** the
input stimulus **and** the `golden.txt` reference to the PLIO width — writing one element per row on
a `plio_32_bits`/`int16` port injects a zero into every second lane and corrupts the result.
Provide enough samples for the number the kernel reads per run.

---

## Canonical Vitis example idiom (preferred reference)

Vitis ships ready AIE examples at **`$XILINX_VITIS/installed_examples/aie_examples`**
(17 designs: `simple`, `simple_chained`, `GMIO_bandwidth`, `template_window_class`,
`simple_param`, `simple_packet_split_merge`, `async_buffer`, …). **Start from the
closest example.** Their idiom (alongside the class-based style above) is:

- **Function kernels** (not classes): a free function declared in `kernels.h`,
  implemented in `kernels/kernels.cc`, instantiated with `kernel::create(<fn>)`:
  ```cpp
  // kernels.h
  void simple(adf::input_buffer<cint16>& in, adf::output_buffer<cint16>& out);
  // kernels/kernels.cc
  void simple(adf::input_buffer<cint16>& in, adf::output_buffer<cint16>& out) {
    cint16* inItr = in.data(); cint16* outItr = out.data();
    for (unsigned i=0;i<NUM_SAMPLES;i++) { *outItr++ = f(*inItr++); }
  }
  ```
- **Graph in `project.h`**, top + `main()` in `project.cpp`:
  ```cpp
  class simpleGraph : public adf::graph {
    kernel first, second;                       // kernels can be chained
  public:
    input_plio in; output_plio out;
    simpleGraph() {
      in  = input_plio::create(plio_32_bits, "data/input.txt");
      out = output_plio::create(plio_32_bits, "data/output.txt");
      first = kernel::create(simple); second = kernel::create(simple);
      adf::connect(in.out[0], first.in[0]);
      connect(first.out[0], second.in[0]);      // kernel-to-kernel
      connect(second.out[0], out.in[0]);
      dimensions(first.in[0]) = { NUM_SAMPLES };  // buffer ports need dims
      dimensions(first.out[0]) = { NUM_SAMPLES };
      source(first) = "kernels/kernels.cc";
      runtime<ratio>(first) = 0.1;
    }
  };
  // project.cpp:  simpleGraph mygraph;  int main(){ mygraph.init(); mygraph.run(4); mygraph.end(); }
  ```
- **`in.data()`/`out.data()`** pointer iteration for buffer kernels (simpler than
  `aie::begin`). Buffer comm is window-based; `dimensions()` sets the sample count.
- Put PLIO data under **`data/`** and keep a **`golden.txt`** to check outputs.
- These graphs build for both the functional (`x86sim`) and cycle-accurate (`hw`) targets.

**Driver note:** when importing an example that uses subdirs (`kernels/kernels.cc`,
`data/input.txt`), preserve those relative paths so the `source(...)` and PLIO
references resolve, and stage `data/` into the simulator's working dir.

---

## Naming PLIOs when the component feeds an integration / system link

The shipped `simple`/`simple_chained` examples create PLIOs **unnamed** (the 2-arg
`input_plio::create(plio_32_bits, "data/input.txt")`). That is fine for standalone
x86sim/aiesim, but an **integration** `stream_connect=` needs a stable port name to target. When
the component will be linked by `vitis-system`:

- **Name the PLIOs explicitly** (3-arg form) — a one-line edit to the shipped example:
  `input_plio::create("DataIn1", plio_32_bits, "data/input.txt")` and
  `output_plio::create("DataOut1", plio_32_bits, "data/output.txt")`.
- **Or read the generated name** from the built graph if you keep them unnamed — the compiler
  assigns names you can find under `build/**/Work/**` (e.g. `aie_control_xrt.cpp`) — then use
  that exact, case-sensitive name in the cfg.
- **Match PLIO count to the kernels the link will connect** — a `mm2s`+`s2mm` integration wants
  exactly one input and one output PLIO; don't ship extra PLIOs that would force extra kernels.
