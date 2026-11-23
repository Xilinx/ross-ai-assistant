<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Create Kernel Graph

This reference describes how to create the graph file for a kernel

## Workflow Steps

1. Create Default Graph
2. Define Graph Ports
3. Connect Graph Ports
4. Define Graph Port Dimensions
5. Define LUT Initialization

## Create Default Graph

Create the graph default file `KERNEL_graph.h` using "KERNEL" as the user supplied name for the kernel.

Example template:
```
#pragma once
#include <adf.h>
#include "KERNEL.hpp"
using namespace adf;

class KERNEL_graph : public graph {
private:
  kernel kk;
public:
  KERNEL_graph( void )
  {
    kk = kernel::create_object<KERNEL>();
    source(kk) = "KERNEL.cpp";
    runtime<ratio>(kk) = 0.9;
  }
};
```

## Define Graph Ports

* The kernel ports are defined in the `run()` signature of the `KERNEL.hpp` file.
* Define one graph `input_port` for each kernel input port.
* Define one graph `output_port` for each kernel output port.

Before adding graph ports, the graph class looks like this:
```
class KERNEL_graph : public graph {
private:
  kernel kk;
public:
  KERNEL_graph( void )
  {
    ...
  }
};
```
After adding graph ports, the graph class looks like this:
```
class KERNEL_graph : public graph {
private:
  kernel kk;
public:
  input_port  sig_i;
  output_port sig_o_0;
  output_port sig_o_1;
  KERNEL_graph( void )
  {
    ...
  }
};
```
## Connect Graph Ports

* Each graph port must be connected to its associated kernel ports.
* Kernel input ports are numbered sequentially (ie. 0, 1, 2) using the order they appear in the kernel `run()` signature.
* Kernel output ports are numbered sequentially (ie. 0, 1, 2) using the order they appear in the kernel `run()` signature.

Example: Assume the following kernel run signature in `KERNEL.hpp`:
```
void run(output_buffer<int32>& sig_o_0, input_stream<int32>* sig_i, output_buffer<int32>& sig_o_1 );
```
Before connecting graph ports, the graph class looks like this:
```
class KERNEL_graph : public graph {
private:
  kernel kk;
public:
  input_port  sig_i;
  output_port sig_o_0;
  output_port sig_o_1;
  KERNEL_graph( void )
  {
    ...
  }
};
```
After connecting the graph ports, the graph class looks like this:
```
class KERNEL_graph : public graph {
private:
  kernel kk;
public:
  input_port  sig_i;
  output_port sig_o_0;
  output_port sig_o_1;
  KERNEL_graph( void )
  {
    ...
    connect<>(sig_i,kk.in[0]);
    connect<>(kk.out[0], sig_o_0);
    connect<>(kk.out[1], sig_o_1);
  }
};
```

## Define Graph Port Dimensions

* All kernel ports using buffers require the graph to define the sizes of those buffers in terms of # of samples.
* Kernel stream ports do not require graph port dimensions.
* Kernel buffer port sizes have been defined previously in `KERNEL.hpp` using `static constexpr unsigned NSAMP_<portname>` identifiers.

Example: Assume the following kernel run signature in `KERNEL.hpp`:
```
void run(output_buffer<int32>& sig_o_0, input_stream<int32>* sig_i, output_buffer<int32>& sig_o_1 );
```
Before defining graph port buffer dimensions, the graph class looks like this:
```
class KERNEL_graph : public graph {
private:
  kernel kk;
public:
  input_port  sig_i;
  output_port sig_o_0;
  output_port sig_o_1;
  KERNEL_graph( void )
  {
    ...
    connect<>(sig_i,kk.in[0]);
    connect<>(kk.out[0], sig_o_0);
    connect<>(kk.out[1], sig_o_1);
  }
};
```
After defining graph port buffer dimensions, the graph class looks like this:
```
class KERNEL_graph : public graph {
private:
  kernel kk;
public:
  input_port  sig_i;
  output_port sig_o_0;
  output_port sig_o_1;
  KERNEL_graph( void )
  {
    ...
    connect<>(sig_i,kk.in[0]);
    connect<>(kk.out[0], sig_o_0);
    connect<>(kk.out[1], sig_o_1);
    dimensions(kk.out[0]) = { KERNEL::NSAMP_SIG_O_0 };
    dimensions(kk.out[1]) = { KERNEL::NSAMP_SIG_O_1 };
  }
};
```

## Define LUT Initialization

* Any kernel LUTs must be initialized by the graph.
* Declare a `std::vector<data-type>` as a graph class private member for each kernel LUT according to its data-type.
* Name each vector exactly as the corresponding parameter of the kernel constructor, including letter case (ie. `TAPS_i`, not `TAPS` or `TAPS_I`).
* Pass the vectors to `kernel::create_object` in the same order as the kernel constructor parameters.
* Initialize each private member as part of the graph constructor using the LUT size defined in `KERNEL.hpp`.

Example: Assume the kernel has two LUTs defined in the kernel constructor `KERNEL.cpp`:
```
KERNEL::KERNEL( float (&TAPS_i)[TAPS_SIZE], int32 (&COEFF_i)[COEFF_SIZE] )
```
Before defining LUT initialization, the graph class looks like this:
```
class KERNEL_graph : public graph {
private:
  kernel kk;
public:
  input_port  sig_i;
  output_port sig_o_0;
  output_port sig_o_1;
  KERNEL_graph( void )
  {
    kk = kernel::create_object<KERNEL>();
    ...
  }
};
```
After defining LUT initialization, the graph class looks like this:
```
class KERNEL_graph : public graph {
private:
  kernel kk;
  std::vector<int32> COEFF_i;
  std::vector<float> TAPS_i;
public:
  input_port  sig_i;
  output_port sig_o_0;
  output_port sig_o_1;
  KERNEL_graph( void ) : COEFF_i(KERNEL::COEFF_SIZE), TAPS_i(KERNEL::TAPS_SIZE)
  {
    kk = kernel::create_object<KERNEL>(TAPS_i, COEFF_i);
    ...
  }
};
```






