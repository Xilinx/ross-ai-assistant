<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Methodologies for AIE performance questions

Default order: **discover tiles** → **profile counters** → **event trace** (if build
supports it) → **Analyzer**.

---

## "What is the latency of my AIE kernel?"

### Phase 1 — Profiling counters (no recompile)

Measure **end-to-end or path latency** between graph ports using interface counters:

```ini
[Debug]
aie_profile=true

[AIE_profile_settings]
interval_us=1000
interface_tile_latency=src_graph:output_port:dst_graph:input_port:100
```

- Pick **graph:port** names from compile summary / graph source.
- `tranx_num` (last field) selects **which transaction** is measured: the latency of that nth
  transaction from input to output (UG1076). XRT reports **one cycle count** for the port pair
  (it also prints "Total latency between <g1>:<p1> and <g2>:<p2> is N clock cycles"). It is not a
  sample count, and the result is not a min/avg/max statistic.
- Add core `execution` on kernels along the path to see compute vs wait:

```ini
graph_based_aie_metrics=src_graph:all:execution; dst_graph:all:execution
```

**Read results:** Vitis Analyzer profile view → **Interface Latency** (or latency
table in `aie_profile.csv` / consolidated summary): one latency value in clock cycles for the
configured port pair. To see how latency changes across transactions, rerun with a different
`tranx_num` or use event trace (Phase 2).

### Phase 2 — Event trace (if Phase 1 insufficient)

Needed when latency **varies over time** or you must tie delay to **specific stalls**.

**Prerequisite:** `event_trace` in `AIE_TRACE_METADATA` → else **`vitis-aie-profile`**.

```ini
[Debug]
aie_trace=true

[AIE_trace_settings]
graph_based_aie_tile_metrics=src_graph:all:functions
graph_based_interface_tile_metrics=src_graph:output_port:s2mm_channels:0
buffer_size=8M
periodic_offload=true
```

Timeline: measure gap between **port activity** on source shim and **kernel start** on
destination tile.

### Confidence limits

- `interface_tile_latency` gives **one value** for the selected transaction, not a distribution;
  use event trace (Phase 2) for per-transaction variation.
- Single `interface_tile_latency` pair per run is easiest; multiple pairs use **`;`** but
  watch counter budget.

---

## "Where and when are my data transfers stalling?"

### Phase 1 — Locate **where** (profile)

Layered counters by stall **domain**:

```ini
[Debug]
aie_profile=true

[AIE_profile_settings]
interval_us=500
graph_based_aie_metrics=all:all:stalls
tile_based_aie_memory_metrics=all:conflicts
graph_based_interface_tile_metrics=all:all:input_ports_stalls:0
```

| Layer | Metric | Reveals |
|-------|--------|---------|
| Core | `stalls` | Memory / stream / lock / cascade stall cycles per tile |
| Memory module | `conflicts`, `dma_locks` | DMA lock contention, bank conflicts |
| Interface | `input_ports_stalls`, `output_ports_stalls`, `s2mm_throughputs` | PLIO/GMIO back-pressure |

Narrow to hot graph after first run:

```ini
graph_based_aie_metrics=hot_graph:all:stalls
tile_based_aie_memory_metrics={12,1}:{12,1}:dma_locks
```

**Where** = tiles / ports with highest stall counts or conflict rates in Analyzer tables.

### Phase 2 — Pinpoint **when** (trace)

```ini
[Debug]
aie_trace=true

[AIE_trace_settings]
tile_based_aie_tile_metrics={10,0}:{14,5}:all_stalls
graph_based_memory_tile_metrics=all:all:all_stalls_dma
buffer_size=16M
periodic_offload=true
buffer_offload_interval_us=100
```

Use **tile ranges** to focus on the stall region found in Phase 1.

Trace metrics:

- `all_stalls` — full stall taxonomy on core tiles
- `partial_stalls` — stream/lock/cascade without memory
- `s2mm_channels` / `mm2s_channels` — **when** DMA channels stall or starve

**When** = timestamps of stall events in Trace view (zoom per tile row).

### Multi-run note

Interface throughput/stall counters often allow **one channel per run** — profile channel
0, rerun with channel 4, consolidate reports in Analyzer (UG1076).

### Confirming a real bottleneck vs. noise

**A real, named AMD procedure for this exists — but check its scope before citing it as
this skill's own.** UG1076's **Memory Stall Analysis** section (part of "AI Engine Stall
Analysis in the Vitis IDE") gives a concrete heuristic:

- **Repeated stalls, especially inside a loop** (recurring pattern across the run) →
  a real bottleneck worth investigating and resolving.
- **A one-time stall at the very start of kernel execution, or a very small stall count**
  → can usually be ignored/neglected as noise, not a real bottleneck.

**Scope caveat:** this heuristic is documented for the **`aiesimulator` + Vitis IDE/Analyzer
Trace view** workflow (a pre-hardware AI Engine simulation flow with VCD-derived stall
events) — **not** this skill's `xrt.ini`/hardware-runtime domain. Cite it as real AMD
documentation (UG1076), but name that scope explicitly rather than implying it's a
hardware-runtime `aie_profile`/`aie_trace` procedure.

**Hardware-runtime equivalent (general practice, not an identically-named documented
procedure):** compare the tile's stall count/percentage in `heat_map`/`stalls` metrics
against the rest of the array (an isolated outlier vs. many tiles showing similar levels
is a stronger signal than one number alone), and check whether the elevated count recurs
across multiple runs rather than appearing once. This is sound practice, not a citable
named procedure equivalent to UG1076's simulator-based one — say so if asked specifically
whether it's documented that way for hardware runtime data.

### Documented resolutions by stall type

Once profiling has named the mechanism, cite the AMD guidance for it instead of inventing a
fix. These are the documented resolutions (summarized; link the source):

| Stall / symptom | Documented resolution | Source |
|-----------------|----------------------|--------|
| **Memory stall** (bank conflicts) | Examine access patterns in trace; place buffers (system memory, RTP, window buffers, DMA FIFO) on different banks; use the aiecompiler `BufferOptLevel` mapper option; guide scheduling with virtual resource annotations (`__aie_dm_resource_a`). | [UG1315 — AIE_MEMORY_STALLS](https://docs.amd.com/r/en-US/Vitis-Guidance-Messaging-UG1315/AIE_MEMORY_STALLS); [UG1076 — Memory Stall Analysis](https://docs.amd.com/r/en-US/AI-Engine-Tools-and-Flows-User-Guide-UG1076/Memory-Stall-Analysis) (scenario/solution table) |
| **Stream stall** | Examine stream access patterns in trace; increase/balance FIFO depth on the stream; adjust stream read/write instructions in the loop; for multiple streams insert a DMA FIFO or set per-net FIFO depth; for PLIO maximize AIE-PL interface bandwidth (wider interface, higher PL frequency). | [UG1315 — AIE_STALL](https://docs.amd.com/r/en-US/Vitis-Guidance-Messaging-UG1315/AIE_STALL) |
| **Cascade stall** | Adjust loop instructions so cascade input/output rates match; maximize PL bandwidth to the AI Engine. | UG1315 — AIE_STALL |
| **Lock stall** | Examine buffer access patterns in trace; acquire and release buffers on time; keep ping-pong buffers (default); balance throughput between kernels; use local buffers as needed; when PL is the source/destination, match the PL interface throughput to the AI Engine. | UG1315 — AIE_STALL |
| **Kernel compute is slow** (cycles not explained by stalls) | Kernel-code optimization: vectorization, software pipelining of loops, `chess_prepare_for_pipelining` / `chess_loop_range`, `__restrict`. | **UG1079** (AIE) / **UG1603** (AIE-ML) kernel and graph programming guides |
| **Transfers serialized against compute** | Overlap data movement with compute (split data into chunks so compute starts earlier); then apply the stall rows above to the side that is starving. | [UG1700 — Best Practices for Host Programming](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Best-Practices-for-Host-Programming) |

Anything more specific than the cited guidance (exact FIFO depths, particular pragmas or API
calls the guide does not give) is general engineering advice — say so.

---

## "Inefficiencies in how tiles transfer data between themselves?"

Look for **imbalance**: sender high `write_throughputs` / `stream_put_get` but receiver
low `read_throughputs`; memory tiles with `conflicts`; cascade/stream mismatch.

### Phase 1 — Throughput balance (profile)

```ini
[Debug]
aie_profile=true

[AIE_profile_settings]
interval_us=1000
graph_based_aie_metrics=all:all:heat_map
graph_based_aie_metrics=producer_graph:all:write_throughputs
graph_based_aie_metrics=consumer_graph:all:read_throughputs
graph_based_aie_memory_metrics=all:all:s2mm_throughputs
tile_based_interface_tile_metrics=all:s2mm_throughputs:0
```

For **stream/cascade** between AIE tiles:

```ini
graph_based_aie_metrics=all:all:stream_put_get
```

**Inefficiency signals (counters)**

- Producer **active** (heat_map) but consumer **idle** → synchronization / back-pressure.
- High `conflicts` on memory tile between producer and consumer → memory arbitration issue.
- Interface `packets` / throughputs **asymmetric** across ports → wiring or rate mismatch.

### Phase 2 — Temporal pattern (trace)

```ini
[Debug]
aie_trace=true

[AIE_trace_settings]
graph_based_aie_tile_metrics=producer_graph:all:all_dma
graph_based_aie_tile_metrics=consumer_graph:all:all_dma
tile_based_aie_tile_metrics={8,2}:{10,4}:all_stalls_dma
enable_system_timeline=true
```

Requires trace-enabled build; enable host trace and PL `device_trace` in the same run if
those domains should appear on the unified timeline. **`enable_system_timeline`** is
**runtime-only** (no extra compile flags beyond normal trace).

**Inefficiency signals (trace)**

- Bursty DMA with long idle gaps between tiles
- Persistent `all_stalls_dma` on bridge memory tile while neighbors run
- Stream starvation events on consumer without matching producer puts

---

## Combined profile + trace (single run)

Enable both only when necessary (resource / overhead):

```ini
[Debug]
aie_profile=true
aie_trace=true

[AIE_profile_settings]
interval_us=1000
graph_based_aie_metrics=mygraph:all:heat_map

[AIE_trace_settings]
graph_based_aie_tile_metrics=mygraph:all:partial_stalls
buffer_size=8M
periodic_offload=true
```

Prefer **separate runs** when counter/trace contention causes empty data.

---

## Iteration checklist

```
- [ ] Tiles/graphs discovered from compile summary or AIE_TRACE_METADATA
- [ ] Trace build verified (if using aie_trace)
- [ ] Narrowest graph/tile scope that answers the question
- [ ] Profile run analyzed before enabling trace
- [ ] Analyzer compile directory set for kernel names
- [ ] Multi-run plan if multiple interface channels needed
```
