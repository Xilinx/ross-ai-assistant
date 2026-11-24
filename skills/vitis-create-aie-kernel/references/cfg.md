<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Create Kernel CFG

This reference describes how to create the compiler config file for a kernel

## Workflow Steps

1. Create Config File

## Create Config File

The config file should be named `aie.cfg`.

Use the following template for the config file:
```
[aie]
kernel-linting=true
xlopt=1
verbose=true
pl-freq=625
Xmapper=BufferOptLevel9
#stacksize=2048
#Xchess="main:backend.mist2.maxfoldk=500"
```
