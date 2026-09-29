<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# XSim Error Signatures

A signature identifies the stage and the first hypothesis. It does not
identify the root cause. Pair every signature with the failing construct, then
research it using
[known-issue-research.md](known-issue-research.md).

## Signature table

| Signature | Stage | First hypothesis | Research query seed |
|---|---|---|---|
| `[XSIM 43-3316] Signal SIGSEGV received` | xelab or runtime | front-end or kernel defect on a specific construct | signature + construct + `mixed language` |
| `[XSIM 43-3294] Signal EXCEPTION_ACCESS_VIOLATION received` | xelab or runtime, Windows | Windows-only path of the same class; often stack or memory limit | signature + `Windows` + construct |
| `[XSIM 43-3312] Signal SIGABRT received` | xelab or runtime | assertion inside the tool, often VHDL type or bound handling | signature + VHDL construct |
| `FATAL_ERROR: Vivado Simulator kernel has discovered an exceptional condition` | runtime | kernel defect reached through legal stimulus | message + construct + version |
| `[XSIM 43-4187] ... is not supported yet for simulation` | xvlog or xvhdl | genuine language-support gap, not a DUT defect | exact construct name + `not supported` |
| `[XSIM 43-4452] Linking failed for "xsim.dir/.../dpi.so"` | xsc | host toolchain, gcc version, or missing dev package | signature + `xsc` + distribution |
| `[XSIM 43-3254] Could not find hier sig handle for expression` | runtime or logging | hierarchical reference unsupported or optimized away | signature + `hierarchical reference` |
| `[XSIM 43-3918] Unable to determine HDL language type of design hierarchy in SDF` | post-implementation timing | SDF scope or language mix at the annotation point | signature + `SDF annotation` |
| `[USF-XSim-62] 'elaborate' step failed with error(s)` | xelab | wrapper-level failure; the real cause is in `elaborate.log` | read the log first, then research the inner message |
| `error while loading shared libraries: libxv_simulator_kernel.so` | runtime launch | environment, not design | run the environment doctor first |
| `[VRFC 10-xxxx]` | xvlog or xvhdl | front-end parse, type, or binding issue | exact VRFC id + construct |

`43-3316`, `43-3294`, `43-3312`, and the kernel fatal are the same family seen
from different host platforms and stages. Treat them identically: capture the
stack text, identify the construct, arbitrate across simulators, reduce, and
research.

## Handling rules

A crash is always classified `environment` for the purposes of this skill's
verdict, because the tool failed rather than the DUT. Never report a crash as
a design FAIL, and never edit RTL to dodge one without saying so.

An unsupported-construct message is also `environment`. The source may be
fully legal. Confirm with the LRM or another simulator before suggesting any
source change.

`USF-XSim-62` and similar wrapper errors carry no diagnostic content. Open the
stage log named in the message and quote the first inner error instead.

## Workflow from a signature

1. Capture the complete stage log, tool version banner, and host OS.
2. Identify the construct at the reported location.
3. Run [cross-simulator arbitration](cross-simulator-arbitration.md) when other
   backends are available.
4. Reduce with [minimal-reproducer.md](minimal-reproducer.md).
5. Research with [known-issue-research.md](known-issue-research.md).
6. If the failure is new in this release, run
   [version-bisect.md](version-bisect.md).
7. Report cause, workaround, verification result, and residual risk.

## Official sources

- [UG900 Logic Simulation](https://docs.amd.com/r/en-US/ug900-vivado-logic-simulation)
- [Xilinx Simulation Solution Center (AR 58795)](https://adaptivesupport.amd.com/s/article/58795?language=en_US)
