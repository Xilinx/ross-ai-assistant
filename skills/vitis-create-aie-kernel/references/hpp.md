<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Create Kernel HPP

This reference describes how to create or modify a kernel class header file. 

## Workflow Steps

1. Create Header Default
2. Create Kernel Ports
3. Create Port Sizing Definitions
4. Create LUT Definitions

## Create Header Default

Create the header default using "KERNEL" as the user supplied name for the kernel.

Example template:
```
#pragma once
#include <adf.h>
#include <aie_api/aie.hpp>
#include <aie_api/utils.hpp>
using namespace adf;

class KERNEL {
public:
  
  // Constructor:
  KERNEL(void);

  // Kernel Signature:
  void run(void);

  // Register Kernel:
  static void registerKernelClass( void )
  {
    REGISTER_FUNCTION( KERNEL::run );
  }
};
```
## Create Kernel Ports

* Each port has a direction, "input" or "output".
* Each kernel may have zero or more input ports and one or more output ports.
* Each port requires a data type such as `int32`, `float`, `cfloat`, `int16`, etc. 
* Each port type uses "stream" or "buffer".
* Each port has a unique name.

The table below shows how each port is declared:
| Port Type | Data Type | Port Direction | Port Name | Port Declaration |
| -- | -- | -- | -- | -- |
| stream | `int32` | input | `sig_i` | `input_stream<int32>* sig_i` |
| stream | `float` | output | `sig_o` | `output_stream<float>* sig_o` |
| buffer | `int16` | input | `val_i` | `input_buffer<int16>& val_i` |
| buffer | `cint32` | output | `val_o` | `output_buffer<cint32>& val_o` |

Kernel ports are added as arguments to the kernel `run()` signature.

Before adding ports the run signature looks like this:
```
void run(void);
```
After adding ports the run signature looks like this:
```
void run( input_stream<int32>* sig_i, output_buffer<float>& sig_o);
```
## Create Port Sizing Definitions

* Each port processes a number of samples per graph iteration of the kernel. This is the port size.
* We define these as `static constexpr unsigned` data members of the kernel class.
* Ask the user to supply the port sizes for each named port.
* Prepend "NSAMP_" to each named port and use upper case for all definitions.

Before adding port sizes the kernel class looks like this:
```
class KERNEL {
public:
    ...
};
```
After adding port sizes the kernel class looks like this:
```
class KERNEL {
    static constexpr unsigned NSAMP_SIG_I   = 256;
    static constexpr unsigned NSAMP_SIG_O_0 = 128;
    static constexpr unsigned NSAMP_SIG_O_1 = 64;
    ...
}
```

## Create LUT Definitions

* The kernel may use internal lookup tables (LUTs) to support compute workloads. 
* Each LUT must be declared in the kernel class as an array reference with memory alignment, data type, and array sizing.
* Each LUT must be registered using `REGISTER_PARAMETER` to support full placement control by the user. 
* Each LUT must be initialized with an array reference on the kernel constructor.
* User must supply the data type and array sizing for each LUT.

Before adding LUTs the kernel class looks like this:
```
class KERNEL {
public:
    ...
    KERNEL(void);
    void run( ... );
    static void registerKernelClass( void )
    {
       REGISTER_FUNCTION( KERNEL::run );
    }
};
```
After adding LUTs the kernel class looks like this:
```
class KERNEL {
public:
    static constexpr unsigned LUT_A_SIZE = 512;
    static constexpr unsigned LUT_B_SIZE = 128;
    alignas(16) float (&LUT_A)[LUT_A_SIZE];
    alignas(16) cfloat (&LUT_B)[LUT_B_SIZE];
    ...
    KERNEL( float (&LUT_A_i)[LUT_A_SIZE], cfloat (&LUT_B_i)[LUT_B_SIZE] );
    void run( ... );
    static void registerKernelClass( void )
    {
       REGISTER_FUNCTION( KERNEL::run );
       REGISTER_PARAMETER( LUT_A );
       REGISTER_PARAMETER( LUT_B );
    }
};
```



