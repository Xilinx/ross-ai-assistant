<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Vitis build flows for PL profiling

## Two-phase `v++` flow (command line)

Typical accelerated application:

1. **Compile** each kernel → `.xo`
2. **Link** kernels + platform → `.xclbin`

```bash
# Phase 1 — compile (add --profile.stall when stall trace is needed)
v++ -c -t hw --platform <platform.xpfm> \
  --profile.stall all:all \
  --config hls_config.cfg \
  -k <kernel_name> \
  -o <kernel>.xo <kernel_source>.cpp

# Phase 2 — link (profile.data, profile.stall, trace_memory; omit profile.exec when data trace is on)
v++ -l -t hw --platform <platform.xpfm> \
  --profile.data all:all:all \
  --profile.stall all:all \
  --profile.trace_memory DDR[0] \
  -o binary_container.xclbin <kernels>.xo
```

Use a trace memory name **valid for the platform** — `DDR[0]` is example syntax only.

Pass **`--config`** files to merge options:

```bash
v++ -l -t hw --platform <platform> --config link.cfg -o design.xclbin *.xo
```

### Example `link.cfg`

```ini
[profile]
data=all:all:all
stall=all:all
trace_memory=DDR[0]
```

For an `hw_emu` deadlock, `compiler.deadlockDetection` goes on the **compile** step, not in
`link.cfg` (UG1702 lists it for `v++ --compile` / Vitis HLS `config_export`):

```ini
# compile.cfg  (v++ -c -t hw_emu --config compile.cfg ...)
[advanced]
param=compiler.deadlockDetection=true
```

Set `trace_memory` to a bank name that exists on the target platform (see
`profile_options.md`). See **`profile_options.md`** § Config file format for the
command-line ↔ `[profile]` mapping rule.

### Example `hls_config.cfg` (stall ports)

```ini
[profile]
stall=all:all

[hls]
syn.rtl.kernel_profile=1
```

## Vitis IDE System Project

1. Open the System Project → **hw_link** / `binary_container-link.cfg`.
2. **Kernel Data** (per PL kernel): enable **Data Transfer** (includes execution trace when trace is on), **Stall Profiling** — enable **Execute Profiling** only when execution trace is needed **without** data trace. These emit the same `profile.*` commands.
3. **Trace Memory** in link settings → `--profile.trace_memory` with a **platform-valid**
   memory name (resolve outside this skill via `platforminfo` / platform docs).
4. HLS component → `syn.rtl.kernel_profile=1` for stall support.

Re-export or rebuild after changing profile settings; profile IP is fixed at link time.

## OpenCL / `.cl` kernel flow

XRT tests historically use:

```makefile
v++ --platform $(DSA) -t $(MODE) -c -o kernel.xo kernel.cl
v++ --platform $(DSA) -t $(MODE) -l -o kernel.xclbin kernel.xo
```

Add the same `--profile.*` flags to **link** (and **compile** for stall). Kernel
names match the OpenCL kernel identifier.

## Emulation vs hardware

**`sw_emu` (`-t sw_emu`) is no longer supported** — new designs **cannot** be built
targeting software emulation. Do not recommend `sw_emu` for profiling flows.

| Target | Profile IP in xclbin | `trace_memory` | Notes |
|--------|----------------------|----------------|-------|
| `hw_emu` | Yes | Do not use | `compiler.deadlockDetection=true` at kernel compile for deadlock messages |
| `hw` | Yes | **Use** | Production profiling; longest link time |

Tell the user to profile on **`hw`** (or their deployment target) when numbers must
match silicon. Use **`hw_emu`** to validate deadlock detection behavior before hw runs
when an hw_emu flow is still in use.

## Packaging / `vitis-run`

If the flow uses **`v++ -p`** packaging or **`vitis-run`**, profile options still
belong on the **link** step that produces the xclbin consumed by XRT. Trace the user's
artifact: the file passed to `xrt::device::load_xclbin()` must be the profiled xclbin.

## What this skill does not cover

- **`--profile.aie`** — discouraged; prefer **`vitis-aie-profile`** /
  **`vitis-aie-profile`** (see `profile_options.md`)
- **`--profile.memory`** — DDR controller aggregate counters; isolated tests only
- **Chipscope / ILA** (`--debug` options) unless needed for unrelated debug
- **xrt.ini** runtime configuration → **`vitis-pl-profile`**
- **Interpreting CSV output** → **`vitis-pl-profile`**
