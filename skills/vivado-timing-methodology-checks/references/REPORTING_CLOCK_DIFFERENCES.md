<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Summary
This page captures reporting clock differences either between before and after constraint changes or for conflicting constraints

# Example
#### Before and After Comparison

| Aspect | Period | Waveform | Clock Definition Object | Constraint |
|--------|--------|----------|------------------------|------------|
| **Before** | 5.000 ns | {0 2.500} | GTX_CLK (primary) | /`create_clock -period 5.000 -name GTX_CLK [get_pins ios_0/mmcm_0/CLKOUT2]/` |
| **After** | 12.800 ns | {0 6.400} | GTX_CLK (generated) | /`create_generated_clock -name GTX_CLK -source [get_pins ios_0/mmcm_0/CLKIN1] -multiply_by 30 -divide_by 48 [get_pins ios_0/mmcm_0/CLKOUT2]/` |


#### Conflicting constraints

| Aspect | Period | Waveform | Clock Definition Object | Constraint |
|--------|--------|----------|------------------------|------------|
| **Primary** | 5.000 ns | {0 2.500} | GTX_CLK (primary) | /`create_clock -period 5.000 -name GTX_CLK [get_ports CLKIN]/` |
| **Redefinition#1** | 12.800 ns | {0 6.400} | NEW_CLK (generated) | /`create_clock -name NEW_CLK -period 12.800 [get_pins ios_0/mmcm_0/CLKOUT2]/` |
