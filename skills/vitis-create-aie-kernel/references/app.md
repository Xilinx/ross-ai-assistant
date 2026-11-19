<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Create Kernel App

This reference describes how to generate the app file for an AI Engine kernel

## Workflow Steps

1. Create Default App File
2. Create Top-Level Ports
3. Initialize Top-Level Ports
4. Connect Top-Level Ports

## Create Default App File

* User may specify the top level graph instance name (assume INSTANCE below).
* Create the default app file named `KERNEL_app.cpp` using the following template:

```
#include "KERNEL_graph.h"

class dummy_graph : public graph {
public:
   KERNEL_graph dut;
   dummy_graph(void) {
   }
};

dummy_graph INSTANCE;

int main(void)
{
  INSTANCE.init();
  INSTANCE.run(1);
  INSTANCE.end();
  return 0;
}
```

## Create Top-Level Ports

* Each port on the kernel graph must be connected to a port on the dummy graph.
* Each dummy graph port has a direction "input" or "output".
* Each dummy graph port has a type "gmio" or "plio".
* Each port has a unique name.
* Name each dummy graph port exactly as the kernel graph port it is connected to.
* Each port must be connected to a port on the kernel graph.
* Input ports on the dummy graph must be connected to input ports on the kernel graph.
* Output ports on the dummy graph must be connected to output ports on the kernel graph.
* The table below shows how each dummy port is declared:

| Dummy Port Type | Dummy Port Direction | Port Name (dummy and kernel) | Dummy Port Declaration |
| -- | -- | -- | -- |
| plio | input | `sig_i` | `input_plio sig_i` |
| plio | output | `sig_o` | `output_plio sig_o` |
| gmio | input | `sig_i` | `input_gmio sig_i` |
| gmio | output | `sig_o` | `output_gmio sig_o` |

Example: 
   * Assume the `KERNEL_graph.h` contains two input ports `sig_i_0` and `sig_i_1` and one output port `sig_o`.
   * Assume dummy graph input `sig_i_0` will be a gmio port.
   * Assume dummy graph input `sig_i_1` will be a plio port.
   * Assume dummy graph output `sig_o` will be a plio port.
   * Assume the `KERNEL_graph.h` looks like this:
```
class KERNEL_graph : public graph {
public:
  input_port  sig_i_0;
  input_port  sig_i_1;
  output_port sig_o;
  ...
};
```
Before adding top-level ports, the dummy graph appears like this:
```
class dummy_graph : public graph {
public:
   KERNEL_graph dut;
   dummy_graph(void) {
   }
};
```
After adding top-level ports, the dummy graph appears like this:
```
class dummy_graph : public graph {
public:
   input_gmio  sig_i_0;
   input_plio  sig_i_1;
   output_plio sig_o;
   KERNEL_graph dut;
   dummy_graph(void) {
   }
};
```

## Initialize Top-Level Ports

* Top-level input plio ports must be initialized in the dummy graph constructor with  `input_plio::create()`.
* Top-level output plio ports must be initialized in the dummy graph constructor with  `output_plio::create()`.
* Top-level gmio input ports must be initialized in the dummy graph constructor with `input_gmio::create()`.
* Top-level gmio output ports must be initialized in the dummy graph constructor with `output_gmio::create()`.
* Each `plio` port is created as `create("PLIO_<port>", plio_64_bits, "data/<port>.txt")`, where `<port>` is the port name (ie. `sig_i_1`).
* Each `gmio` port is created as `create("GMIO_<port>", 64, 5000)`, where `<port>` is the port name. The values are the burst length in bytes and the bandwidth in MB/s.
* Each created name is unique because it includes the unique port name.
Example:
  * Assume we continue with the previous port definitions.

Before initializing top-level ports, the dummy graph appears like this:
```
class dummy_graph : public graph {
public:
   input_gmio  sig_i_0;
   input_plio  sig_i_1;
   output_plio sig_o;
   KERNEL_graph dut;
   dummy_graph(void) {
   }
};
```
After initializing top-level ports, the dummy graph appears like this:
```
class dummy_graph : public graph {
public:
   input_gmio  sig_i_0;
   input_plio  sig_i_1;
   output_plio sig_o;
   KERNEL_graph dut;
   dummy_graph(void) {
      sig_i_0 = input_gmio::create("GMIO_sig_i_0", 64, 5000);
      sig_i_1 = input_plio::create("PLIO_sig_i_1",plio_64_bits,"data/sig_i_1.txt");
      sig_o   = output_plio::create("PLIO_sig_o",plio_64_bits,"data/sig_o.txt");
   }
};
```

## Connect Top-Level Ports

Top-level dummy graph ports must be connected to the kernel graph ports in the dummy graph constructor.

Before connecting top-level ports, the dummy graph appears like this:
```
class dummy_graph : public graph {
public:
   input_gmio  sig_i_0;
   input_plio  sig_i_1;
   output_plio sig_o;
   KERNEL_graph dut;
   dummy_graph(void) {
      sig_i_0 = input_gmio::create("GMIO_sig_i_0", 64, 5000);
      sig_i_1 = input_plio::create("PLIO_sig_i_1",plio_64_bits,"data/sig_i_1.txt");
      sig_o   = output_plio::create("PLIO_sig_o",plio_64_bits,"data/sig_o.txt");
   }
};
```
After connecting top-level ports, the dummy graph appears like this:
```
class dummy_graph : public graph {
public:
   input_gmio  sig_i_0;
   input_plio  sig_i_1;
   output_plio sig_o;
   KERNEL_graph dut;
   dummy_graph(void) {
      sig_i_0 = input_gmio::create("GMIO_sig_i_0", 64, 5000);
      sig_i_1 = input_plio::create("PLIO_sig_i_1",plio_64_bits,"data/sig_i_1.txt");
      sig_o   = output_plio::create("PLIO_sig_o",plio_64_bits,"data/sig_o.txt");
      connect<>( sig_i_0.out[0], dut.sig_i_0);
      connect<>( sig_i_1.out[0], dut.sig_i_1);
      connect<>( dut.sig_o, sig_o.in[0]);
   }
};
```


