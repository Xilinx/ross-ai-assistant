<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Request

```text
Run hardware emulation for my VCK190 design. Use input/hwemu_inputs_vck190.md. The design is pure AI Engine with GMIO ports only: no PLIO ports and no programmable-logic kernels. Confirm that every path in the inputs file exists. If a path is still a placeholder or does not exist, stop and name that path. When every path exists, build the required artifacts, start QEMU, run the host program, and write the hardware-emulation report in this directory.
```
