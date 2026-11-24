<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Create Kernel CPP

This reference describes how to create or modify a kernel class source file. 

## Workflow Steps

1. Create Source Default
2. Add Kernel Ports
3. Add LUT Initialization

## Create Source Default

Create the source default using "KERNEL" as the user supplied name for the kernel.

Example template:
```
#include <adf.h>
#include <aie_api/aie.hpp>
#include <aie_api/utils.hpp>
#include "KERNEL.hpp"

// ------------------------------------------------------------
// Constructor
// ------------------------------------------------------------

KERNEL::KERNEL(void)
{
  aie::set_rounding(aie::rounding_mode::conv_even);
  aie::set_saturation(aie::saturation_mode::saturate);
}

// ------------------------------------------------------------
// Run
// ------------------------------------------------------------

void KERNEL::run(void)
{
}
```

## Add Kernel Ports

* Kernel ports are defined in the `KERNEL.hpp` file. 
* Replace the signature `run(void)` with the signature from `KERNEL.hpp`.

Before adding kernel ports the signature looks like this:
```
void KERNEL::run(void)
{
}
```
After adding kernel ports the signature looks like this:
```
void KERNEL::run( input_stream<int32>* sig_i, output_buffer<int16>& sig_o)
{
}
```
## Add LUT Initialization

* Kernel LUTs are defined in the `KERNEL.hpp` file. 
* Replace the constructor signature `KERNEL::KERNEL(void)` with the signature from `KERNEL.hpp`.

Before updating the LUT initialization the kernel constructor looks like this:
```
KERNEL::KERNEL( void )
```
After updating the LUT initialization the kernel constructor looks like this:
```
KERNEL::KERNEL( int32 (&LUT_A_i)[LUT_A_SIZE], int16 (&LUT_B_i)[LUT_B_SIZE] ) : LUT_A(LUT_A_i), LUT_B(LUT_B_i)
```


