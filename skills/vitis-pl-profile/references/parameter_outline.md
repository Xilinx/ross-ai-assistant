<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# PL profiling–related `xrt.ini` keys (`[Debug]`)

**Product reference:** AMD **UG1702** (*Vitis Accelerated Flow Reference*) —
[xrt.ini File](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/xrt.ini-File)
(authoritative key list and valid values). End-to-end profiling context: **UG1700**
*Enabling Profiling in Your Application*. See [user_guides.md](user_guides.md) for
when to point users at other guides.

Values are read via `xrt_core::config` (`config_reader.h`). Booleans accept typical
ini forms (`true`/`false`, `1`/`0`).

**Prerequisite:** AM/AIM/ASM (and trace offload IP when timelines are needed) must
appear in the xclbin `DEBUG_IP_LAYOUT` section. Verify with xclbinutil before
recommending flags. If missing → **`vitis-pl-profile`**.

**Run targets:** **`sw_emu` (`-t sw_emu`) is no longer supported** — new designs cannot
be built targeting software emulation. PL runtime profiling assumes **hardware** (or
**`hw_emu`** where still applicable).

## Contents

- [Primary PL keys](#primary-pl-keys)
- [Trace buffer / offload tuning](#trace-buffer--offload-tuning)
- [Related keys](#related-use-when-question-scope-includes-them)
- [Power profiling (Alveo only)](#power-profiling-alveo-only)
- [`device_trace` allowed values](#device_trace-allowed-values)
- [`device_trace` and `stall_trace` interaction](#device_trace-and-stall_trace-interaction-implementation)
- [`device_trace` vs `device_counters`](#device_trace-vs-device_counters)
- [Recommendation patterns](#recommendation-patterns)
- [Out of scope for this file](#out-of-scope-for-this-file)

## Primary PL keys

| Key | Default | Role |
|-----|---------|------|
| `device_trace` | `off` | Collect PL monitor **trace** and add counter totals to **`summary.csv`** and timeline outputs. **Valid values only:** `off`, `fine`, `coarse`, `accel`. **`all` is invalid** — never recommend or emit it. See table below. |
| `device_counters` | `false` | Sample PL monitor **counters** into **`summary.csv`** when **`device_trace=off`**. **Redundant when `device_trace` is enabled** — the offload plugin reads counters for the summary automatically. |
| `stall_trace` | `off` (forced `off` when `device_trace=off`) | Emit CU **stall** events in device trace: `pipe`, `dataflow`, `memory`, or `all`. Requires AM with stall property (`m_properties & 0x4`). |
| `pl_deadlock_detection` | `false` | Enable runtime polling of **`ACCEL_DEADLOCK_DETECTOR`** IP when compiled into the xclbin. Not used in hw_emu with `XCL_EMULATION_MODE` set. |
| `profiling_directory` | *(empty)* | Output directory for profiling artifacts. |

## Trace buffer / offload tuning

| Key | Default | Role |
|-----|---------|------|
| `continuous_trace` | `false` | Periodic trace buffer flush to host during long runs. |
| `trace_buffer_size` | `1M` | PL trace buffer size (string, e.g. `8M`). Increase if XRT warns about trace loss. |
| `trace_buffer_offload_interval_ms` | `10` | How often trace is offloaded from device (ms). |
| `trace_file_dump_interval_s` | `5` | Host-side trace file dump interval when continuous trace is on. |

## Related (use when question scope includes them)

| Key | Default | Role |
|-----|---------|------|
| `noc_profile` | `false` | Platform **AXI_NOC** counter polling (requires `AXI_NOC` entries in layout). |
| `noc_profile_interval_ms` | `20` | NOC poll interval. |
| `profile_api` | `false` | Expose APIs for **application code** to read PL counters at runtime. Rarely needed for offline analysis — prefer `device_counters` / `device_trace`. |
| `host_trace` / `opencl_trace` / `native_xrt_trace` | `false` | Host API trace — use with **`vitis-host-profile`** when correlating host submits with PL `device_trace_*.csv`. |

## Power profiling (Alveo only)

| Key | Default | Role |
|-----|---------|------|
| `power_profile` | `false` | Poll **power rails** on the card during the application run and record a **running summary** of power usage. **Supported on Alveo (data center) cards only** — **not supported on edge boards** (XRT edge plugin loader does not load the power profiling plugin). |
| `power_profile_interval_ms` | `20` | Poll interval (milliseconds) when `power_profile=true`. |

**Platform guidance**

- **Alveo** — `power_profile=true` enables the power profiling plugin to sample rails
  while the app runs. Output typically includes **`power_profile_<device>.csv`** under
  `profiling_directory` (or the working directory). Useful for correlating **power
  draw** with kernel activity when combined with PL/host trace, but **orthogonal** to
  `device_trace` / `debug_ip_layout` — no compile-time profile IP required.
- **Edge** (Versal edge, MPSoC, etc.) — **do not recommend** `power_profile`; the flag
  has no effect or is not loaded on edge XRT builds.

**Example (Alveo hw run)**

```ini
[Debug]
power_profile=true
power_profile_interval_ms=20
profiling_directory=/path/to/profile_output
device_trace=fine
```

## `device_trace` allowed values

| Value | Meaning |
|-------|---------|
| `off` | No PL device trace offload; use with `device_counters=true` for counters-only runs. |
| `fine` | Default full-granularity PL device trace and timeline (`device_trace_*.csv`). **Use this** when recommending trace unless the user needs lower AIM volume. |
| `coarse` | Device trace with reduced AIM granularity — lower trace volume; use when `fine` causes loss warnings or disk pressure. |
| `accel` | Specialized mode: summary/counter path without the standard device-trace hardware collection bit. Rare; only when the user's workflow explicitly calls for it. |

**Invalid:** `all` and any other string. Do not confuse with **`stall_trace=all`**, which is a separate key.

## `device_trace` and `stall_trace` interaction (implementation)

From the PL device offload plugin (`configureTraceIP`):

- `device_trace=coarse` sets coarse AIM mode when the platform supports it.
- `device_trace=fine` enables device trace collection (coarse AIM bit clear).
- `device_trace=accel` does **not** set the standard device-trace collection bit.
- `stall_trace=pipe` → pipe stall bit; `dataflow` → dataflow stall bit;
  `memory` → memory stall bit; `all` → all three.

If `device_trace=off`, `stall_trace` is effectively off regardless of ini value.

## `device_trace` vs `device_counters`

When **`device_trace`** is `fine`, `coarse`, or `accel`:

- **Do not** recommend **`device_counters=true`** — it is unnecessary.
- XRT still reads PL monitor counters and writes them to **`summary.csv`** as part of
  the device trace / offload shutdown path (same counter totals `device_counters`
  would produce).

Use **`device_counters=true`** only when **`device_trace=off`** — counters-only builds
or when the user wants post-run totals **without** trace timeline overhead.

| `device_trace` | `device_counters` | Result |
|----------------|-------------------|--------|
| `fine` / `coarse` / `accel` | `false` (default) | `summary.csv` counters + `device_trace_*.csv` timeline (when trace fabric present) |
| `fine` / `coarse` / `accel` | `true` | **Redundant** — same counter data; omit `device_counters` |
| `off` | `true` | `summary.csv` counters only (lowest PL profiling overhead) |
| `off` | `false` | No PL counter or trace data |

## Recommendation patterns

| Goal | Suggested ini |
|------|---------------|
| Post-run byte counts per port | `device_counters=true`, `device_trace=off` |
| Kernel overlap / pipelining timeline | `device_trace=fine`, `stall_trace=all` (if AM stall compiled in); omit `device_counters` |
| Stream transaction volumes | `device_trace=fine` for summary + timeline; or `device_counters=true` with `device_trace=off` for totals only |
| Hang / deadlock | `device_trace=fine`, `stall_trace=all`, `pl_deadlock_detection=true` if ADD in layout |
| Long run, trace loss warnings | Increase `trace_buffer_size`, enable `continuous_trace`, or `device_trace=coarse` |
| Power usage during run (Alveo) | `power_profile=true`, optional `power_profile_interval_ms` — **not on edge** |

## Out of scope for this file

- **`v++` / Vitis compile flags** → `references/profile_options.md` (Workflow §3)
- **AIE** `[AIE_trace_settings]` / `[AIE_profile_settings]` → AIE-focused skills
- **Host-only** tuning → **`vitis-host-profile`**
