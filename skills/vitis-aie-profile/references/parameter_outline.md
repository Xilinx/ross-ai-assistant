<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# AIE `xrt.ini` keys

Values read via `xrt_core::config` (`config_reader.h`). Booleans accept `true`/`false`,
`1`/`0`.

## Contents

- [Main switches (`[Debug]`)](#main-switches-debug)
- [`[AIE_profile_settings]`](#aie_profile_settings)
  - [Sampling](#sampling)
  - [Graph-based metrics](#graph-based-metrics-preferred-when-user-knows-graph-names)
  - [Tile-based metrics](#tile-based-metrics-coordinates-from-compile-map)
  - [Latency](#latency-kernel--graph-path-latency)
  - [Advanced](#advanced)
- [`[AIE_trace_settings]`](#aie_trace_settings)
  - [Graph-based trace](#graph-based-trace)
  - [Tile-based trace](#tile-based-trace-bounding-box-friendly)
  - [Trace control / offload](#trace-control--offload)
  - [`enable_system_timeline`](#enable_system_timeline-runtime-alignment)
- [Syntax reminders](#syntax-reminders)
  - [Last match wins](#last-match-wins-overlapping-tile-specs)
- [Compile vs runtime](#compile-vs-runtime)

## Main switches (`[Debug]`)

| Key | Default | Role |
|-----|---------|------|
| `aie_profile` | `false` | Enable **AIE profiling counters** — no compile-time trace required. |
| `aie_trace` | `false` | Enable **AIE event trace** — requires `--event-trace` at graph compile. |
| `profiling_directory` | *(empty)* | Output directory for profile/trace files. |

Do **not** enable `aie_trace` without verifying `AIE_TRACE_METADATA` / compile summary
shows event trace (see `tile_discovery.md`).

**Run targets:** **`sw_emu` (`-t sw_emu`) is no longer supported** — new designs cannot
be built targeting software emulation. AIE profile/trace ini guidance assumes
**hardware** (or **`hw_emu`** where still applicable).

---

## `[AIE_profile_settings]`

### Sampling

| Key | Default | Role |
|-----|---------|------|
| `interval_us` | `1000` | Counter sample period (microseconds). Lower → finer resolution, more overhead. |
| `start_type` | `time` | When profiling starts (`time`, `graph`, `iteration`, … per UG1076). |
| `start_iteration` | `1` | Graph iteration to start when `start_type=iteration`. |
| `config_one_partition` | `false` | Limit to one partition when applicable. |

### Graph-based metrics (preferred when user knows graph names)

| Key | Syntax | Module |
|-----|--------|--------|
| `graph_based_aie_metrics` | `<graph\|all>:<kernel\|all>:<metric>` | AI Engine **core** tiles |
| `graph_based_aie_memory_metrics` | `<graph\|all>:<kernel\|all>:<metric>` | AIE **memory** module |
| `graph_based_memory_tile_metrics` | `<graph\|all>:<buffer\|all>:<metric>[:ch0][:ch1]` | **Memory tile** (AIE-ML+) |
| `graph_based_interface_tile_metrics` | `<graph\|all>:<port\|all>:<metric>[:ch0][:ch1]` | **Interface / shim** tile |

**Core / memory metrics:** `heat_map`, `stalls`, `execution`, `floating_point`,
`write_throughputs`, `read_throughputs`, `stream_put_get`, `s2mm_throughputs`,
`mm2s_throughputs`, `conflicts`, `dma_locks`, `dma_stalls_s2mm`, `dma_stalls_mm2s`,
`off`.

**Interface metrics:** `s2mm_throughputs`, `mm2s_throughputs`, `packets`,
`input_throughputs`, `output_throughputs`, `input_stalls`, `output_stalls`,
`input_ports`, `output_ports`, `input_ports_stalls`, `output_ports_stalls`,
`input_output_ports_stalls`, …

### Tile-based metrics (coordinates from compile map)

| Key | Syntax |
|-----|--------|
| `tile_based_aie_metrics` | `<col\|all>:<row\|all>:<metric>` or `{c,r}:<metric>` or `{c1,r1}:{c2,r2}:<metric>` |
| `tile_based_aie_memory_metrics` | Same pattern |
| `tile_based_memory_tile_metrics` | Same + optional channels |
| `tile_based_interface_tile_metrics` | `<col\|all>:<metric>[:channel]` or range forms |

### Latency (kernel / graph path latency)

| Key | Syntax |
|-----|--------|
| `interface_tile_latency` | `<graph1>:<port1>:<graph2>:<port2>:<tranx_num>` |

Multiple specs separated by **`;`**. `tranx_num` selects which transaction is measured — the
latency of the nth transaction from input to output (UG1076); it defaults to 0 when omitted. The
result is one cycle count per port pair, not a sampled statistic.
Configures **interface tiles** on both ends of a graph port pair.

**Example**

```ini
interface_tile_latency=tx_graph:datain:rx_graph:dataout:100
```

### Advanced

| Key | Default | Role |
|-----|---------|------|
| `dtrace_debug` | `false` | Debug verbosity for profile driver path. |

---

## `[AIE_trace_settings]`

### Graph-based trace

| Key | Syntax |
|-----|--------|
| `graph_based_aie_tile_metrics` | `<graph\|all>:<kernel\|all>:<trace_metric>` |
| `graph_based_memory_tile_metrics` | `<graph\|all>:<buffer\|all>:<trace_metric>[:ch0][:ch1]` |
| `graph_based_interface_tile_metrics` | `<graph\|all>:<port\|all>:<trace_metric>[:ch0][:ch1]` |

### Tile-based trace (bounding-box friendly)

| Key | Syntax |
|-----|--------|
| `tile_based_aie_tile_metrics` | `all:all:<metric>` or `{c,r}:<metric>` or `{c1,r1}:{c2,r2}:<metric>` |
| `tile_based_memory_tile_metrics` | Same patterns |
| `tile_based_interface_tile_metrics` | Column / range forms per UG1076 |

**Trace metrics (common):** `functions`, `partial_stalls`, `all_stalls`, `all_dma`,
`all_stalls_dma`, `s2mm_channels`, `mm2s_channels`, `all_stalls_s2mm`,
`all_stalls_mm2s`, `s2mm_channels_stalls`, `mm2s_channels_stalls`, `off`.

Multiple entries: separate with **`;`**.

**Bounding-box example**

```ini
tile_based_aie_tile_metrics={4,1}:{6,2}:all_stalls; {4,1}:functions
```

### Trace control / offload

| Key | Default | Role |
|-----|---------|------|
| `buffer_size` | `8M` | Device buffer for trace packets. |
| `periodic_offload` | `true` | Offload trace during run (required for long captures). |
| `buffer_offload_interval_us` | `100` | Offload cadence. |
| `reuse_buffer` | `false` | Circular buffer — follow UG1076 size/stream limits if true. |
| `file_dump_interval_s` | `5` | Host file dump interval. |
| `start_type` | `time` | Delay trace start (`time`, `iteration`, `graph`, …). |
| `start_time` | `0` | Cycles or time per `start_type`. |
| `start_iteration` | `1` | Iteration-based start. |
| `start_layer` | max uint | Layer filter when applicable. |
| `end_type` | `disable_event` | Trace end condition. |
| `counter_scheme` | `es2` | Counter scheme for timer samples. |
| `poll_timers_interval_us` | `50` | Timer poll interval. |
| `max_timer_samples` | `2000000` | Cap timer samples. |
| `trace_start_broadcast` | `true` | Broadcast core trace start. |
| `enable_system_timeline` | `true` | **Runtime only** — no extra compile flags beyond normal AIE/host/PL trace. Polls AIE timestamps during the run (small overhead; tune `poll_timers_interval_us`, `max_timer_samples`) and emits alignment data for **post-processed** unified timeline in Vitis Analyzer. Requires the trace domains you want aligned to be captured in the same run (`aie_trace`, host trace, PL `device_trace` as needed). |
| `config_one_partition` | `false` | Single-partition trace config. |

### `enable_system_timeline` (runtime alignment)

**`enable_system_timeline`** is a **runtime-only** `[AIE_trace_settings]` switch. It
does **not** add compile-time requirements beyond whatever **normal trace flags** you
already need for the data you want on the unified timeline:

| Domain on unified view | Normal prerequisite (compile + runtime) |
|------------------------|----------------------------------------|
| AIE events | AIE graph built with `--event-trace`; `[Debug] aie_trace=true` |
| Host API | Host trace ini (`native_xrt_trace`, `opencl_trace`, …) |
| PL kernels | PL monitors/trace in xclbin; `[Debug] device_trace` not `off` |

When **`enable_system_timeline=true`** (default in many flows), XRT starts **polling
AIE timers** during the run and writes timestamp samples (e.g. **`aie_event_timestamps.bin`**
) used in **post-processing** to align AIE trace with host and PL in Vitis Analyzer.
Overhead is **small** but non-zero — controlled by **`poll_timers_interval_us`** and
**`max_timer_samples`**. Set **`enable_system_timeline=false`** if you only need AIE-only
trace with no cross-domain alignment.

```ini
[AIE_trace_settings]
enable_system_timeline=true
poll_timers_interval_us=50
max_timer_samples=2000000
```

---

## Syntax reminders

- **`;`** separates multiple metric specifications on one key.
- **`:``** separates graph / kernel / metric fields.
- **`{col,row}`** — single tile; **`{c1,r1}:{c2,r2}`** — inclusive range (bounding box).
- Graph/kernel names must match compile metadata or XRT warns and skips.

### Last match wins (overlapping tile specs)

When multiple metric specifications on the **same ini key** match the **same tile**,
**only the last matching spec takes effect** for that tile. Earlier specs are
overwritten — they do **not** combine or accumulate.

**Profile example — all tiles get only the last metric:**

```ini
graph_based_interface_tile_metrics=mygraph:all:input_ports_stalls:0;mygraph:all:output_ports_stalls:0
```

Both specs match all interface tiles in `mygraph`. Result: every tile is configured for
**`output_ports_stalls`**. To profile **both** input and output stalls on the same
tiles, use **`input_output_ports_stalls`** in a single spec instead.

**Profile example — non-overlapping tiles keep distinct metrics:**

```ini
graph_based_interface_tile_metrics=mygraph:{0:2}:input_ports_stalls:0;mygraph:1:output_ports_stalls:0
```

Interface tiles on **columns 0 and 2** → **`input_ports_stalls`**. Interface tile on
**column 1** → **`output_ports_stalls`**.

This rule applies to **all** graph-based and tile-based `*_metrics` keys in
`[AIE_profile_settings]` and `[AIE_trace_settings]` whenever a single tile is matched by
more than one `;`-separated specification. Prefer **combined metric sets** over
semicolon-chained specs that target the same tiles.

## Compile vs runtime

| Capability | Recompile needed? |
|------------|-------------------|
| Change profile metric / tile | **No** |
| Change trace metric / tile (if `event-trace=runtime` build) | **No** |
| Enable trace on graph never built with `--event-trace` | **Yes** → `vitis-aie-profile` |
