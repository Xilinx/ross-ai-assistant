<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# NoC Write Timeout Error — Quick Start Prompts

The example ships as source only (RTL + Tcl). Build the PDI first, then debug it.

Unlike the `write-decode-error` and `axsize-violation` examples, a NoC **timeout**
is not latched until NoC timeouts are **explicitly enabled** on the NMU at debug
time. This tutorial therefore has a **three-step** flow: build, an initial scan
(which is expected to be clean), then enable timeouts and re-scan.

## Step 1 — Build the PDI from source

Either run it yourself:

```bash
cd input
vivado -mode batch -source create_project.tcl
# then, in the same session or a new -mode tcl session:
#   launch_runs impl_1 -to_step write_device_image -jobs 8
#   wait_on_run impl_1
```

or ask the agent:

```
Build this example: source input/create_project.tcl in Vivado, then run
implementation to write_device_image to produce the PDI.
```

Resulting PDI:

```
input/noc_timeout_error/noc_timeout_error.runs/impl_1/noc_timeout_wrapper.pdi
```

## Step 2 — Program and take a baseline scan

```
Use /hw-noc-debug to program input/noc_timeout_error/noc_timeout_error.runs/impl_1/noc_timeout_wrapper.pdi onto the board and scan the NoC for errors.
```

The initial `sysdbg_noc analyze` is expected to report **0 findings**: the
master's write is already outstanding at the NMU, but NoC timeout detection is
**disabled by default**, so nothing has latched yet.

## Step 3 — Enable NoC timeouts, then re-scan

```
Enable NoC write timeouts on the NMUs (a short timebase), then re-run the NoC
scan and root-cause the resulting timeout error.
```

The skill enables timeout detection via `sysdbg_noc_timeout` (e.g. `set` a short
timebase index on the NMU sites). Once enabled, the outstanding write exceeds the
timeout window and the NMU latches `REG_ISR.timeout_wr`. A second
`sysdbg_noc analyze` then reports exactly one timeout finding.

> **Note — NPI register lock.** On some setups the NMU timeout control registers
> are NPI-locked, so the first `sysdbg_noc_timeout set` writes do not verify
> (the written value differs from the read-back). The workaround is to unlock each
> NMU by writing the key `0xF9E8D7C6` to `<nmu_base> + 0xC` before re-issuing the
> `set`. The `hw-noc-debug` skill applies this unlock when it detects unverified
> timeout writes.
