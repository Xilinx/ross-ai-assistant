<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# AIE profiling counters vs event trace

Use this table before recommending compile-time `--event-trace`. If the row's
**Counters sufficient?** column is **Yes**, stop at runtime `aie_profile` — no graph
recompile for trace.

## Quick decision

```
Need time-ordered events or per-call timeline?
  YES → compile with --event-trace (this skill)
  NO  → runtime aie_profile counters only (below)
```

## Question → recommendation

| User question | Counters sufficient? | Runtime approach | Compile-time trace? |
|---------------|---------------------|------------------|---------------------|
| Average PLIO/GMIO input or output throughput | **Yes** | `aie_profile=true`; `tile_based_interface_tile_metrics=all:s2mm_throughputs` or `mm2s_throughputs` (often **one channel per run**) | No |
| DDR / shim bandwidth | **Yes** | `read_bandwidth` / `write_bandwidth` / `ddr_bandwidth` interface metrics | No |
| Tile utilization / heat map | **Yes** | `graph_based_aie_metrics=all:all:heat_map` | No |
| Execution vs stall cycle breakdown (aggregated) | **Yes** | `heat_map`, `stalls`, or `execution` metric sets | No |
| Stall time **by cause** — memory vs stream vs lock vs cascade (aggregated) | **Yes** | `stalls` metric set (`graph_based_aie_metrics=all:all:stalls` or `tile_based_aie_metrics=...:stalls`) — four separate stall-time counters (UG1076) | No |
| Memory tile / DMA lock conflicts | **Yes** | `tile_based_aie_memory_metrics=all:conflicts` or `dma_locks` | No |
| Floating-point operation mix (statistical) | **Yes** | `floating_point` metric set | No |
| **When** did kernel A finish vs kernel B start? | **No** | `aie_trace=true` + graph/tile trace metrics | **Yes** (`--event-trace`) |
| Timeline of **function** entry/return | **No** | `graph_based_aie_tile_metrics=...:functions` | **Yes** |
| **Sequence** of stall types during a frame | **No** | `...:all_stalls` or `partial_stalls` trace metrics | **Yes** |
| Debug **hang** — last active kernel / function | **No** | Event trace + Vitis Analyzer timeline | **Yes** |
| DMA channel activity **over time** | **No** | `all_dma`, `s2mm_channels`, etc. trace metrics | **Yes** |
| Unified timeline with **host + PL + AIE** | **No** (for alignment flag) | AIE + host + PL **normal trace** ini; **`enable_system_timeline=true`** at runtime | **Yes** for AIE `--event-trace`; PL/host use their usual trace builds — **no extra compile option for system timeline** |

## Runtime counters example (no compile-time trace)

```ini
[Debug]
aie_profile=true

[AIE_profile_settings]
interval_us=1000
graph_based_aie_metrics=all:all:heat_map
tile_based_aie_memory_metrics=all:conflicts
tile_based_interface_tile_metrics=all:s2mm_throughputs:0
```

Run the application; open `xrt.run_summary` in Vitis Analyzer. Point the compile
directory to `Work/<graph>.aiecompile_summary` for source correlation.

**Limitations (counters)**

- **One interface channel** per run is typical for throughput — multiple channels need
  multiple runs and report consolidation (UG1076).
- Counters show **aggregates over the sample interval**, not individual invocations.
- Limited hardware counters per module — not all metric sets combine on one run.

## Runtime trace example (requires compile-time `--event-trace`)

Build graph with `event-trace=runtime` first (see main SKILL.md), then:

```ini
[Debug]
aie_trace=true

[AIE_trace_settings]
graph_based_aie_tile_metrics=all:all:all_stalls
buffer_size=16M
periodic_offload=true
buffer_offload_interval_us=100
```

## When users say "profile" generically

Clarify intent with one question:

- **"Do you need a timeline (event-by-event), or summary statistics (averages/totals)?"**

Timelines → trace build skill. Statistics → counters only.

## Product references

- **UG1076** — AI Engine Tools and Flows (event trace, profile metrics, XRT flow)
- **UG1702** — `xrt.ini` keys (`aie_profile`, `aie_trace`, AIE settings sections)
- **UG1700** — profiling flow and Profile Summary / Analyzer overview
- System Profiling tutorial (XD100) — `aie_profile` XRT examples

Full guide links and question→guide mapping: **`references/user_guides.md`**.
