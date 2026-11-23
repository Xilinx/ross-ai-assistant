<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Host profiling–related `xrt.ini` keys (`[Debug]`)

Values are read via `xrt_core::config` (see `config_reader.h`). Booleans accept typical
ini boolean forms (`true`/`false`, `1`/`0`, etc.).

**Run targets:** **`sw_emu` (`-t sw_emu`) is no longer supported** — new designs cannot
be built targeting software emulation. Host profiling ini guidance assumes **hardware**
(or **`hw_emu`** where still applicable).

## Host trace selection (pick what matches the app)

| Key | Default | Role |
|-----|---------|------|
| `host_trace` | `false` | **Single-layer** host trace: first stack that asks wins
  (OpenCL vs native XRT vs HAL). Use when you want “whatever API I use” behavior. |
| `opencl_trace` | `false` | **OpenCL** host API trace → `opencl_trace.csv`. OpenCL
  loader also ties into counters when this path loads. |
| `native_xrt_trace` | `false` | **Native XRT** host API trace → `native_trace.csv`. |
| `xrt_trace` | `false` | **HAL**-level host trace (driver/shim path) →
  `hal_host_trace.csv` in HAL plugin. |
| `lop_trace` | `false` | **Low-overhead OpenCL** host trace → `lop_trace.csv`. See
  **`lop_trace` (low-overhead OpenCL)** below — **not** for native XRT apps. |

**Guidance:** If the API layer is known, prefer **`opencl_trace`** or
**`native_xrt_trace`** (or **`xrt_trace`** for HAL-focused work) instead of relying only
on **`host_trace`**, so behavior is explicit. **Do not** recommend **`lop_trace`** for
**native XRT** hosts (`xrt::` APIs) — use **`native_xrt_trace`** instead.

## `lop_trace` (low-overhead OpenCL)

**Product reference:** UG1700 (*Enabling Low Overhead Profiling*).

`lop_trace=true` enables **reduced-overhead host profiling aimed at OpenCL
applications**. It captures **minimal OpenCL event** information and writes
**`lop_trace.csv`**, viewable in Vitis Analyzer via **`xrt.run_summary`**. Supported on
**hardware** and **hardware emulation** — not on **`sw_emu`** (no longer supported for
new builds).

```ini
[Debug]
lop_trace=true
profiling_directory=/tmp/host_profile
```

### API family — when it applies

| Host API family | Use `lop_trace`? | Use instead |
|-----------------|------------------|-------------|
| **OpenCL** (`cl*`, XCL/OpenCL runtime) | **Optional** — when full `opencl_trace` overhead is too high and **reduced** OpenCL timeline is enough | `opencl_trace=true` for full detail; `lop_trace=true` for lighter OpenCL capture |
| **Native XRT** (`xrt::`, `xrtRun*`, BO APIs) | **No** — wrong layer; will not trace `xrt::bo::sync`, kernel launches, graph APIs | **`native_xrt_trace=true`** |
| **HAL / driver** | **No** | **`xrt_trace=true`** |

**Native XRT designs** (e.g. embedded Versal apps using `xrt::device`, `xrt::kernel`,
`xrt::graph`) must **not** use `lop_trace` expecting useful host visibility — same class
of mismatch as **`opencl_trace=true`** on a native app. Enabling it may still create
**`lop_trace.csv`** or a sparse run summary entry, but **application API spans will be
missing or uninformative**.

### What `lop_trace` intentionally omits (vs full OpenCL trace)

To keep overhead low, the following are **not** available in the low-overhead trace
(UG1700):

- **Device events** — compute-unit execution, kernel memory transfers on device timeline
- **Memory transfer detail** — destination address, size, full read/write metadata
- **Kernel enqueue detail** — kernel name, NDRange sizes, rich enqueue parameters
- **Dependencies** — ordering between buffer transfers and kernel enqueues

So `lop_trace` is **host-side OpenCL only**, with **less** signal than
**`opencl_trace`** — not a lighter substitute for **`native_xrt_trace`**.

### Conflicts and combinations

- **Do not** combine **`lop_trace=true`** with **`opencl_trace=true`** or other **full
  OpenCL profiling** options — use one OpenCL host trace mode.
- **Do not** combine with **`native_xrt_trace`** expecting merged timelines — pick the
  flag that matches the **actual API family**.
- For **H2D/D2H timing**, **kernel overlap**, or **pipelining** on native XRT hosts,
  **`native_xrt_trace`** (+ optional **`device_trace`**) is the correct path; **`lop_trace`**
  will not answer those questions on a native app.

### When to suggest `lop_trace`

Suggest **`lop_trace=true`** only when **all** of the following hold:

1. Host code uses the **OpenCL API** (not native XRT).
2. User wants **some** host timeline with **lower overhead** than **`opencl_trace`**.
3. User accepts **omitted** device events, memory details, enqueue metadata, and
   transfer↔kernel dependencies (see list above).

Otherwise recommend **`native_xrt_trace`**, **`opencl_trace`**, or **`xrt_trace`** per
API family.

## Device visibility (needed for overlap / pipelining / “device busy”)

| Key | Default | Role |
|-----|---------|------|
| `device_trace` | `off` | Device timeline / offload trace. Use a non-`off` value
  supported by your build (commonly `fine` or `coarse`; see `vitis-pl-profile`) when you need kernel /
  compute overlap. |
| `device_counters` | `false` | Summary / counter style device profiling (pairs with
  device offload path). |

## Compile-time vs `xrt.ini` (PL / AIE)

Setting **`device_trace`**, **`device_counters`**, **`aie_trace`**, or AIE trace settings
in `xrt.ini` **does not create** PL profiling monitors, PL trace fabric, or AIE event
trace in hardware by itself. If the **design was not compiled** with the corresponding
instrumentation for your flow, those signals **will not be available at runtime**—empty
or uninformative device/AIE artifacts are expected.

**This skill may** recommend that the user **recompile / rebuild** to obtain PL profiling,
PL trace, or AIE event trace when their questions require it. **This skill must not**
specify **`v++` options**, **link flags**, **HLS / implementation directives**, or other
build commands; route that work to a **separate Vitis / v++ / platform build skill**.

## Supporting keys

| Key | Default | Role |
|-----|---------|------|
| `profiling_directory` | *(empty)* | Directory for profiling outputs when set. |
| `profile_api` | `false` | **Special / rarely suggest.** When `true`, exposes paths for
  the **host application to probe PL profiling monitors directly** and read counter
  values at runtime (e.g. for **control decisions** based on live PL state). **Do not**
  enable for ordinary host performance work: **PL counter information for offline
  analysis** is already obtained through **`device_trace`** and other standard flags.
  This skill focuses on **host** behavior and CSV interpretation, **not** live hardware
  sampling from application code. |
| `continuous_trace` | `false` | Periodic flushing for some trace writers (e.g.
  OpenCL trace path checks this). |
| `trace_file_dump_interval_s` | `5` | Dump interval when continuous trace is on. |
| `trace_buffer_offload_interval_ms` | `10` | Offload cadence for trace buffers. |
| `trace_buffer_size` | `1M` | Trace buffer size string. |

## JSON / alternate config

| Key | Default | Role |
|-----|---------|------|
| `profile_settings` | *(empty)* | Path to XDP JSON settings when used instead of or
  alongside ini (advanced). |

## Host code instrumentation (User events and ranges)

When augmenting application code to mark phases or measure host-side intervals, use
**XDP User events and ranges** — not **`std::chrono::steady_clock::now()`** or other
standalone timers.

| API | Header | Use |
|-----|--------|-----|
| `xrt::profile::user_range` | `xrt/experimental/xrt_profile.h` | Labeled **duration** on the timeline (`start()` / `end()`, or RAII with label at construction) |
| `xrt::profile::user_event` | same | **Instant marker** at current time (`mark(label)`) |
| `xrtURStart` / `xrtUREnd` | same (C) | Range by numeric id |
| `xrtUEMark` | same (C) | Instant marker |

**Why not `std::chrono`?** Host trace events (native XRT, OpenCL, HAL) timestamp with
**`xrt_core::time_ns()`**. User events and ranges use the **same clock**, so they are
written to **`user_events.csv`** and rendered on the **same Vitis Analyzer waveform**
as other host events — making it easy to see how much host time each application phase
consumes relative to buffer transfers, enqueues, and waits. Ad-hoc `chrono` readings are
off the XDP timebase and do not integrate into the profile timeline without manual
conversion.

**Prerequisite:** Enable a **host trace** flag (`native_xrt_trace`, `opencl_trace`, or
`host_trace` as appropriate) so XDP captures the run; user markers are emitted whenever
the user-events plugin is active.

**Example (native XRT)**

```cpp
#include "xrt/experimental/xrt_profile.h"

void run_phase() {
  xrt::profile::user_range prep("Prepare buffers", "Host setup before H2D");
  // ... buffer allocation / mapping ...
  prep.end();

  xrt::profile::user_event evt;
  evt.mark("Start compute loop");

  xrt::profile::user_range compute("Compute iteration", nullptr);
  // ... enqueue kernels ...
}
```

Prefer **`user_range`** for “how long did this phase take?” and **`user_event::mark()`**
for single points in time. Avoid logging `steady_clock` deltas unless the user only
needs console diagnostics outside Vitis Analyzer.

## Interactions (short)

- **Host + device:** For “kernels in parallel” and “device vs host pipelining,” enable
  **both** a host trace flag and **`device_trace` / `device_counters`** as appropriate.
- **`host_trace`:** Only the **first** layer to consult `load_host_trace()` “wins”; do
  not assume all three layers log simultaneously from `host_trace` alone.
- **`lop_trace` vs `opencl_trace`:** Mutually exclusive OpenCL modes — pick one. Neither
  applies to **native XRT** hosts; use **`native_xrt_trace`**.

## Host trace contamination (PL / AIE trace / continuous offload)

When **host** profiling is on **together with** any of the following, **data movement
for profiling itself** is often visible in the same host API / buffer records the user
cares about. That traffic **does not originate from the user’s application** — it is
the runtime moving **trace buffers** (PL device trace, AIE event trace, etc.) to the
host (or between stages).

| Source | Typical `xrt.ini` knobs | Effect on host profiling |
|--------|-------------------------|---------------------------|
| **PL (device) trace** | `[Debug]` **`device_trace`** not `off` | Device-side trace is **offloaded**; host-side APIs and BO traffic used to read or map that data appear in host traces. |
| **AIE event trace** | `[Debug]` **`aie_trace=true`** | AIE trace packets are moved to the host like other device→host data; host profiling can show **bursts** tied to trace collection. |
| **Continuous offload (generic / OpenCL path)** | `[Debug]` **`continuous_trace=true`**, **`trace_file_dump_interval_s`**, **`trace_buffer_offload_interval_ms`**, … | **Periodic** host activity to flush trace buffers — often **many** transfers **not** driven by user buffer calls. |
| **AIE trace periodic offload** | `[AIE_trace_settings]` **`periodic_offload`**, **`buffer_offload_interval_us`**, **`buffer_size`**, … | Repeating offload of AIE trace buffers → **high volume** of host-visible transfers **uncorrelated** with user H2D patterns. |

**How to advise users**

1. **Disclose** whenever these flags are enabled that “H2D” in host CSVs is **not only**
   application data.
2. Suggest a **comparison run**: same app with host profiling but **without** PL/AIE
   trace or **without** continuous offload, when the question is strictly about user
   buffers.
3. If they must run combined, help them **correlate** offload cadence (intervals from
   ini) with spikes in host trace, or use **device** / **summary** artifacts to separate
   trace DMA from application DMA where possible.

## Timestamp alignment (host vs PL vs AIE)

- **Host trace CSVs** (`native_trace.csv`, `opencl_trace.csv`, `hal_host_trace.csv`, …)
  and **PL device trace** (`device_trace_*.csv` from `device_trace` not `off`) use
  **mutually aligned** timestamps in XDP output, so correlation for this skill’s
  workflows is **direct**.
- **AIE event trace** (`aie_trace`) is **not** aligned to the host trace timebase in the
  raw capture. For unified timeline, set **`enable_system_timeline=true`** in
  **`[AIE_trace_settings]`** at runtime (**`vitis-aie-profile`**) — no extra compile
  flags beyond normal trace; small polling overhead; alignment in post-processing.

For the scope of this skill, **prefer** ini recommendations that support **host-only**
or **host + limited PL** analysis; defer deep **host + AIE** timeline correlation unless
the user commits to post-alignment.
