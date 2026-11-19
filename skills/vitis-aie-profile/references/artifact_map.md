<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# AIE profiling and trace artifacts

Outputs appear under the process working directory or **`Debug.profiling_directory`**.

## Common files

| File | Source | Content |
|------|--------|---------|
| `xrt.run_summary` | XDP summary writer | **Primary** — open in Vitis Analyzer |
| `aie_profile.csv` | AIE profile plugin | Counter samples per tile/metric |
| `summary.csv` | Database summary | May include AIE-related summary rows |
| `aie_trace_*.txt` / `aie_trace_plio_*` / `aie_trace_gmio_*` | AIE trace offload | Raw event trace packets |
| `aie_event_runtime_config.json` | Trace config snapshot | Metrics active for the run |
| `aie_event_timestamps.bin` | AIE trace plugin when `enable_system_timeline=true` | Polled AIE timer samples for **post-process** alignment with host / PL |

## Vitis Analyzer workflow

```bash
vitis_analyzer xrt.run_summary
```

1. **Set compile directory** → `Work/<graph>.aiecompile_summary` (kernel/source correlation).
2. **Profile view** — counter tables, heat maps, interface latency, throughput columns.
3. **Trace view** — per-tile event timelines (requires `aie_trace=true` run).
4. **Timeline (system)** — when **`enable_system_timeline=true`** (runtime-only; no extra
   compile flags) and host / PL / AIE trace were captured with their **normal** trace
   settings in the same run; Analyzer post-processes using polled AIE timestamps
   (e.g. `aie_event_timestamps.bin`).

## Answering the three canonical questions

### Latency

| Artifact | What to read |
|----------|--------------|
| Profile: Interface Latency table | Cycles between configured port pairs (`interface_tile_latency`) |
| Profile: `execution` per kernel | Compute time vs waiting |
| Trace: interface + core rows | Time from port event to kernel `functions` entry |

`interface_tile_latency` gives **one cycle count** for the selected transaction; trace gives timing
for **every** transaction.

### Where and when stalls occur

| Stage | Where | When |
|-------|-------|------|
| Profile `stalls` / `conflicts` / `*_stalls` | Tile or port with highest counts | Not available — counts only |
| Trace `all_stalls`, `*_channels*` | Same tiles in trace legend | Event timestamps — zoom timeline |

Black bars or gaps in trace → buffer overflow; increase `buffer_size` or narrow
`tile_based_*` range.

### Tile-to-tile transfer inefficiency

| Signal | Profile | Trace |
|--------|---------|-------|
| Imbalanced send/receive | Compare `write_throughputs` vs `read_throughputs` / `stream_put_get` | DMA/stream event ordering |
| Memory bottleneck | `conflicts`, `dma_locks` on bridge tiles | `all_stalls_dma` episodes |
| Interface limit | `s2mm_throughputs` / `packets` per channel | Channel stall metrics |

## Timestamp alignment

| Combination | Alignment |
|-------------|-----------|
| AIE trace only | Internal AIE timebase |
| AIE + host / PL raw CSVs | **Not** directly aligned without post-processing |
| `enable_system_timeline=true` + normal trace capture in same run | Unified Timeline in Analyzer (**runtime** flag; polls AIE timestamps; no extra compile options) |

See **`vitis-host-profile`** for host trace contamination when PL offload is on.

## Empty or missing data

| Symptom | Likely cause |
|---------|--------------|
| No trace view | `aie_trace=false`, or no compile-time `--event-trace` |
| Profile warnings, empty tiles | Wrong graph/kernel/tile names in ini |
| Trace for some tiles only | Compile routing partial (`Xrouter=skipunroutabletracenets`) |
| No latency row | Invalid `interface_tile_latency` ports or empty `stream_ids` |
| `aie_profile.csv` missing | `aie_profile=false` or no valid metric config |

## Multi-run consolidation

When profiling **multiple interface channels**, each run produces its own
`xrt.run_summary`. In Analyzer, use **report consolidation** (+ toolbar) to overlay
channel throughputs (UG1076). Same methodology applies to comparing stall profiles
across runs with different `tile_based_*` scopes.

## Handoff

| Gap | Skill |
|-----|-------|
| No `event_trace` in metadata | `vitis-aie-profile` |
| Need PL on unified timeline | `vitis-pl-profile` (normal PL trace build; separate from runtime `enable_system_timeline`) |
| Host submit correlation | `vitis-host-profile` |
