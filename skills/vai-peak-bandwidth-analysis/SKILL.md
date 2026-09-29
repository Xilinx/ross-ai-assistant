---
name: vai-peak-bandwidth-analysis
description: From L3 DDR DMA-channel profiling, judge compute- vs memory-bound execution
  and recommend tiling / QoS changes, focused on the channels and operators with the
  most backpressure/starvation stall
license: Apache-2.0 WITH LLVM-exception
compatibility: Requires VitisAI 6.3+
metadata:
  version: 6.3.0
  stage: beta
---
<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
-->

# Peak L3 DDR bandwidth analysis

Analyze the **L3 DDR DMA-channel** profiling that VitisExecutionProvider emits when
`ai_analyzer_enhanced_profiling: ["control_instrumentation"]` is enabled in runner-options config-file for VART flow. Produce: (1) per-direction
channel throughput and utilization, (2) a compute-bound vs memory-bound verdict per operator,
and (3) prioritized improvement recommendations across five families — cut L3 bytes (tiling /
fusion / precision), hide latency (double-buffering / prefetch), balance channels, relieve L2,
and DDR QoS — always leading with the operators/channels carrying the most backpressure/
starvation stall cycles, because that is where the gain is.

Run it when the user asks about DDR/L3 bandwidth, channel throughput/utilization, stall
(backpressure/starvation) analysis, whether layers are compute- or memory-bound, or how to
raise channel utilization.

## Constraint: MCP tools only, no raw disk artifacts

Use ONLY these MCP tools, all of which return computed/summarized results:
`get_reports`, `get_timelines`, `get_performance_metrics`, `get_single_timing_dataframe`,
`get_npu_summary`, `get_memory_map` (for per-buffer ping/pong double-buffer state), and
(optionally, for exact per-channel/per-operator cycle counts) `get_summary_data`. Do NOT reach
into raw on-disk files — no `get_vaiml_artifact`, `search_mlir`, `get_make_log`, or
`get_spilling_reasons`. The spill **magnitude** you need is already in `get_npu_summary`
(`l3_memory_allocation` → the `Spills` entry); you do not need the spilling CSV for this
analysis.

## Background needed to interpret the counters

- **Peak per channel:** each running cycle transfers a fixed 64-bit word. On the default
  **VEK385** embedded platform (1250 MHz) that is **10 GB/s per channel**. Other Vitis Versal
  embedded platforms run at **1000 MHz → 8 GB/s per channel**. Read `clock_freq_mhz` from the
  performance summary and scale peak = `10 GB/s × clock/1250`.
- **Utilization** of a channel = `running / (running + stall)`. **Achieved BW ≈ peak ×
  utilization**. Prefer the timeline's own `(MB/s)` column as the authoritative achieved figure;
  use peak only as the ceiling.
- **Stall splits into two causes, and their meaning DEPENDS ON DIRECTION.** The general rule is:
  **backpressure = the channel's _destination_ is busy / cannot accept; starvation = the
  channel's _source_ cannot supply.** A read channel moves L3 DDR → L2 (source = DDR,
  destination = L2); a write channel moves L2 → L3 DDR (source = L2, destination = DDR). So the
  two directions flip which side each stall points at:

  | Direction         | Backpressure stall (destination busy)                                                             | Stream starvation stall (source can't supply)                                                    |
  | ----------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
  | **Read** (L3→L2)  | **L2** busy — L2 memory bank conflicts, or waiting to acquire the **write** lock on the L2 buffer | **L3 DDR** memory controller busy and cannot supply data                                         |
  | **Write** (L2→L3) | **L3 DDR** memory controller busy to accept the data                                              | **L2** busy — L2 memory bank conflicts, or waiting to acquire the **read** lock on the L2 buffer |

  The remediation follows the side, not the label. **DDR-bound** = read **starvation** _or_ write
  **backpressure** → DDR-channel QoS + reduce L3 volume. **L2-bound** = read **backpressure** _or_
  write **starvation** → relieve L2 (spill/tiling/L2-chaining, bank-conflict/lock mitigation).
  Do NOT assume "backpressure = L2" — that only holds for reads; for writes it is the DDR side.

- **Channels:** each NPU column has **2 read + 2 write channels** to DDR, each with QoS
  parameters set through the Vitis embedded platform. QoS is the lever for the **DDR-bound** case
  (read starvation / write backpressure).
- **Platform cap:** the DDR controller has a fixed **bytes/sec ceiling for the whole platform**.
  QoS only redistributes within that cap — it cannot exceed it. So the primary lever for a
  bandwidth-bound model is **reducing L3 transfers** (weights + spill), which is what the tiling
  section targets.

## Data sources and their shapes

- **`get_performance_metrics` → `summary`** (one row). Aggregate (sum over all channels of a
  direction) channel counters: `peak_read_total_ch_running_cycles`, `…_stall_cycles`,
  `…_backpressure_stall_cycles`, `…_starvation_stall_cycles`, and the `peak_write_total_ch_*`
  equivalents; plus `clock_freq_mhz`, `batch_count`, `total_gmacs`, `average_npu_time_usec`,
  `average_inference_time_usec`. These `peak_*_total_ch_*` fields are the **whole-direction
  aggregate**, not a single channel.
  - **`operators`** table: per op-type `value_usec` (execution time), `count`, `percent`,
    `cycles_per_count`, `category` (npu / cpu / host_profiling).
- **`get_single_timing_dataframe(folder, timeline=<NPU>, inference=0)`** — one row per operator
  instance. Carries `Name`, `Type`, `Execution Time.μs` / `.Cycles`, and the per-op
  min-throughput channel: `L3 Throughput Min Read.(MB/s)`,
  `L3 Throughput Min Read.Backpressure Stall (%)`, `L3 Throughput Min Read.Starvation Stall (%)`,
  `L3 Throughput Min Read.Column:Channel` (and the `L3 Throughput Min Write.*` set). This ties
  each operator to its worst channel with per-op stall % — the core of the compute/memory verdict
  and the per-channel attribution, without needing `get_summary_data`. Large table: dump with
  `format="parquet"`/`csv` + `output_dir` and aggregate by `Type`; report totals and top-N.
- **`get_npu_summary`** — `total_l3_allocated`, `total_l3_transfers`, `l3_memory_allocation`
  (per class: `Spills`, `Weights`, `IFM`, `OFM` with `size_in_bytes`), `gmacs_per_operator`
  (which op-types carry compute), `total_gmacs`. Large payload — read only those fields.
- **`get_memory_map(folder, level="L2")`** — one row per buffer. Two uses here:
  - **Double-buffering:** `double_buffer` = `ping` / `pong` (DMA overlaps compute) vs `single`
    (no overlap → compute waits on the transfer). `force_single_buffer` true = pinned single,
    cannot ping/pong as-is.
  - **L2 provisioning:** aggregate `size` per `be_layer_name` (and by `buf_type`) to get each
    operator's **total L2 footprint** and its **buffer granularity** (few coarse buffers vs many
    fine ones). This is the MCP-visible proxy for how much L2 / how many stamps an operator got.
  - Other fields: `be_layer_name` (join key to the operator/layer), `buf_type`
    (IFM / OFM / WTS / HEAP_STACK), `offset`, `is_l2_no_spill`. `partition_memory_size` is often
    `0` — the tool does NOT give L2 capacity, so treat capacity as external context (see the stamp
    note in family D).
  - Ping/pong and these buffers live at **L2** (memtile), so query `level="L2"`; L3 rows do not
    carry meaningful double-buffer state. Large table — dump with `format="parquet"`/`csv` +
    `output_dir` and aggregate/filter to the layers of interest (never dump raw rows).
- **`get_summary_data` (optional, large)** — `data[].perModelCharts[].peakReadBwSummary` /
  `peakWriteBwSummary`. Exact per-channel and per-operator cycle counts:
  `chRunningCycles[]`, `chBackpressureStallCycles[]`, `chStreamStarvationStallCycles[]` (each a
  `{name:"Column N Channel M", cycles}` list); the `totalCh{Running,Stall,Backpressure,Starvation}Cycles`
  scalars (== the perf-metrics aggregates); and `operator{Running,Backpressure,StreamStarvation}StallCycles[]`
  (per op-type `{name, cycles, elements}`). Use only when the user wants exact per-channel or
  per-operator cycle counts; otherwise the timeline's per-op stall % is enough.

## Single-direction captures

An `ai_analyzer_enhanced_profiling` capture usually instruments **one direction**. Detect it: if
`peak_write_total_ch_backpressure_stall_cycles == -1` (or the running/stall totals are 0), the
write channels were not captured in this folder — and vice versa for reads. Say so plainly and,
if the user wants both directions, ask for the sibling capture (e.g. a `read_*` folder alongside
a `write_*` folder). Never present the empty direction as "no stalls".

## Method

1. **Locate the NPU timeline.** `get_reports` → `get_timelines(folder, report_id)` on the
   HARDWARE/SIMULATED timing report; the NPU timeline is the non-`CPU` one (e.g. `vaiml_par_0/0`).
2. **Channel-level throughput (from `get_performance_metrics.summary`).** For each captured
   direction compute utilization = `running/(running+stall)` and achieved BW = `peak ×
utilization`. Report which cause dominates the stall: compare `…_backpressure_stall_cycles`
   vs `…_starvation_stall_cycles`, then translate to a **side** using the direction table above:
   read starvation → DDR, read backpressure → L2; write backpressure → DDR, write starvation →
   L2. State the dominant side (DDR-bound vs L2-bound), not just the raw label.
3. **Per-operator compute vs memory bound (from `get_single_timing_dataframe`).** Aggregate by
   `Type`: sum `Execution Time.μs`, and take the mean read/write `Backpressure %` and
   `Starvation %` and `(MB/s)`. Join `gmacs_per_operator` from `get_npu_summary` to know which
   types carry MACs. Classify each op-type:
   - **Compute-bound:** low total stall %, and the op carries real GMACs. Its time is arithmetic.
   - **Memory-bound, DDR side:** high read **starvation %** _or_ high write **backpressure %** →
     the L3 DDR controller is the limiter. QoS candidate (+ reduce L3 volume).
   - **Memory-bound, L2 side:** high read **backpressure %** _or_ high write **starvation %** →
     L2 bank conflicts / lock contention is the limiter. Spill/tiling/L2-chaining and
     bank-conflict candidate — **not** QoS.
   - **Movement op:** zero GMAC (Transpose/Reshape/Slice/Concat/BufferUnpad…) → memory-bound by
     nature; only worth attention if its execution-time share is non-trivial.
     Note the important nuance: an op can hold most of the model's GMACs (high arithmetic
     intensity, _should_ be compute-bound) yet run **memory-bound in practice** because it is
     read-starved — low achieved MB/s + high starvation % = **DDR latency/QoS-limited, not
     bandwidth-saturated**. Call that out; it is the highest-value finding.
4. **L3 read-reduction potential (from `get_npu_summary`).** Report the re-fetch ratio
   `total_l3_transfers / total_l3_allocated` (>1 means bytes cross DDR repeatedly — tiling can
   reclaim it) and the `l3_memory_allocation` split (Spills / Weights / IFM / OFM). This tells
   you which read category dominates and therefore which tiling lever matters most.
5. **Ping/pong (double-buffer) check for the high-stall operators.** Only for the operators that
   step 3 flags with large stall — do NOT dump the whole buffer table for every layer. Call
   `get_memory_map(folder, level="L2")`, then for each high-stall operator match its layer via
   `be_layer_name` and inspect the buffers on the stalling **direction**: the **IFM/WTS** buffers
   for any read-side stall (read starvation _or_ read backpressure), the **OFM** buffer for any
   write-side stall (write backpressure _or_ write starvation). A buffer with
   `double_buffer == "single"` on the stalling side is a **prime cause of that stall** — the
   compute cannot overlap with its transfer. Converting it to ping/pong hides the transfer behind
   compute and removes stall cycles _without_ reducing bytes. Flag each such buffer with its op,
   `buf_type`, and `size`. If `force_single_buffer == true`, note it is pinned single (something —
   often an L2-capacity or spill constraint — is preventing double-buffering, so freeing L2 is a
   prerequisite). Rank these by the stalling op's absolute stall cycles.
6. **L2 provisioning check for the L2-bound operators.** For operators whose stall is L2-side
   (read backpressure / write starvation) and whose buffers are _already_ ping/pong (so B is not
   the fix), aggregate `size` per `be_layer_name` from the same `get_memory_map(level="L2")` call
   to get each operator's **total L2 footprint** and **buffer granularity** (count × size). Compare
   the high-stall op against a low-stall neighbor: a markedly **smaller total L2 / coarser (fewer,
   larger) buffers** signals it is **under-stamped** (too few L2 write ports to absorb the stream)
   — the family-D "more stamps / finer buffers" lever. Do NOT read stamp count from the
   mapped-graph `Tiling` entry count; it is unreliable (see family D).
7. **Rank by gain and recommend.** Order operators and channels by **absolute stall cycles**
   (not %), so a large op with moderate % outranks a tiny op at 90%. Lead every recommendation
   with the top stall contributors.

## Recommendations to produce

Give concrete, prioritized suggestions, each tied to a number from the tools above. First pick
the right _family_ from the stall signal, then the specific lever:

First translate each stall to a **side** with the direction table (read starvation / write
backpressure = **DDR**; read backpressure / write starvation = **L2**), then:

| Signal (from the tools)                                                    | Side                      | Lever family                                             |
| -------------------------------------------------------------------------- | ------------------------- | -------------------------------------------------------- |
| Read **starvation** or write **backpressure** high, **and** MB/s near peak | DDR, volume-saturated     | **A** cut L3 bytes + **E** QoS                           |
| Read **starvation** or write **backpressure** high, **but** MB/s low       | DDR, latency-limited      | **B** hide latency + **C** balance + **E** QoS           |
| Read **backpressure** or write **starvation** high                         | L2 (bank conflict / lock) | **A** cut spill/L2-chaining + **D** relieve L2 (+ **B**) |
| Some channels idle while others saturate                                   | channel imbalance         | **C** spread load                                        |
| Low total stall + carries GMACs                                            | genuinely compute-bound   | none — leave it                                          |

### A. Reduce L3 byte volume

When near the platform bytes/sec cap (re-fetch ratio ≫ 1, MB/s near peak). Map the
`l3_memory_allocation` split to the lever, and always state the tradeoff:

- **Tiling — weights dominate** → larger output/spatial tiles (output-stationary) so each weight
  fetch is amortized over more work → fewer weight re-streams.
- **Tiling — spills dominate** (common; spills often exceed weights) → biggest lever. Retile so
  producer→consumer stays resident in **L2 (L2 chaining)**, removing both the spill write and its
  read-back. This relieves the **L2-bound** stalls (read backpressure / write starvation) and
  cuts total L3 volume.
- **Tiling — IFM re-reads** → fewer/larger tiles reduce conv halo overlap (usually a small pool).
- **Operator fusion / L2 chaining** (distinct from tiling) → fuse producer→consumer so the
  intermediate never spills to L3. Attacks the spill footprint at its source; the `vai-custom-op-candidates`
  skill finds these fusion chains.
- **Concat / pseudo-op elimination** → spills are often _forced_ by a successor that is a
  pseudo-op / L3-buffer node / templated graph. Removing or rewriting that boundary removes the
  forced spill (write + read).
- **Batching for weight reuse** → a larger batch amortizes each weight fetch over more samples
  (fewer weight reads/sample), at the cost of larger activation residency — trades against spill.
- **Precision / weight compression / sparsity** → lower bit-width, weight packing, or pruning
  cut bytes across IFM/OFM/WTS/spill. **Accuracy-affecting — flag as a model-level decision, not
  a pure perf knob.**
- **Tiling tradeoff (state it):** bigger tiles raise L2/L1 pressure and can _cause more spilling_.
  When the **L2-bound** side is already hot (read backpressure / write starvation), prefer tiling
  that improves L2 chaining over tiling that merely maximizes weight reuse. Larger contiguous L3
  bursts also improve DDR row-buffer locality, easing read starvation even where volume is fixed.

### B. Hide latency / raise utilization (same bytes, fewer stall cycles)

When starvation is high but MB/s is well under peak (latency-limited, not saturated).

- **Double-buffering (ping/pong)** — from the step-5 check, convert `single`-buffered buffers on
  the stalling side to ping/pong so the DMA overlaps compute. Often the fastest win for a
  latency-limited op (low MB/s), where cutting bytes would not help. List each candidate:
  operator, `buf_type` (IFM/WTS for a read-side stall, OFM for a write-side stall), `size`,
  current `double_buffer` state. **Tradeoff:** ping/pong doubles that buffer's L2 footprint, so it
  competes with spill — when L2 is saturated, pair it with L2-chaining/tiling that frees L2, or
  apply it on the read side. If `force_single_buffer == true`, the buffer is pinned single and
  the underlying L2/spill constraint must be lifted first.
- **Prefetch / schedule overlap** → issue weight/IFM loads ahead of the consuming compute so the
  read latency is hidden behind prior work; a compiler-scheduling lever.

### C. Spread load across channels (fix imbalance, not arbitration)

When the per-channel arrays (`get_summary_data`) or per-op `Column:Channel` show some channels
saturated while others sit idle.

- **Channel / DMA load balancing** → distribute buffers/transfers across all 2R+2W channels per
  column so aggregate utilization rises without changing QoS priorities. Complementary to QoS,
  which only re-prioritizes _among contending_ channels.
- **Partitioning / placement** → how the graph is partitioned across NPU columns decides which
  producer→consumer pairs cross L3 vs stay on-column in L2. Keeping hot pairs on the same column
  cuts L3 round-trips.

### D. Relieve L2 contention (the L2-bound case)

When the L2 side is the limiter — **read backpressure** (L2 can't accept, write-lock wait) or
**write starvation** (L2 can't supply, bank conflict / read-lock wait).

- **Bank-conflict / lock mitigation** → change buffer placement/stride/alignment so concurrent
  L2 accesses hit different banks and reduce lock contention. Targeted L2-bound fix, separate from
  tiling.
- **More stamps for under-provisioned L2-bound operators** → a **stamp** = 4×4 = 16 cores across
  **4 columns**, and each column has its own L2 memtile, so a stamp gives an operator its own set
  of L2 IFM/OFM/WTS buffers and parallel DMA write ports. An L2-bound operator running on **few
  stamps** has few L2 write ports to absorb the DDR stream → backpressure. Adding stamps (up to 9)
  adds L2 buffers and parallel ports, and finer per-buffer tiling reduces per-buffer bank/lock
  contention. This is usually the **biggest** L2-bound lever.
  - **How to read it (MCP-only):** stamp count is NOT reliably exposed by the graph tools — do
    **not** infer it from the mapped-graph `Tiling` entry count (that is per-tile shapes, not
    stamps, and misreads badly). Instead use `get_memory_map(level="L2")`: aggregate `size` per
    `be_layer_name` for the high-stall operator and compare its **total L2 footprint and buffer
    granularity** against a well-behaved (low-stall) neighbor. An L2-bound op with markedly
    **smaller total L2 / coarser (fewer, larger) buffers** than its peers is under-stamped.
  - **Utilization check:** if columns-per-stamp is known (a stamp spans 4 columns; a Versal
    AIE-ML memtile is ~512 KB/column → ~2 MB L2 per stamp), compare the op's total L2 against
    stamps × ~2 MB. Low utilization with high backpressure means the stall is the single stamp's
    write-port / bank contention, not L2 capacity — so add stamps and/or finer buffers rather than
    just enlarging buffers. State the memtile size as external context: the tool returns
    `partition_memory_size = 0`, so capacity is not measured.
- **L2 capacity reallocation** → give more memtile L2 to the spilling / highest-stall L2 buffers
  so they stay resident (and to make room for ping/pong from B).

### E. QoS changes for low-performing channels

For the **DDR-bound** case only — **read starvation** _or_ **write backpressure** (both = the L3
DDR controller is the limiter).

- Identify the low-utilization DDR-bound channels (timeline `Column:Channel` per op, or
  `get_summary_data` per-channel arrays): read channels high in starvation, write channels high in
  backpressure.
- Raise the QoS priority/arbitration weight for those NPU-column channels in the Vitis embedded
  platform so the DDR controller serves them sooner (supplies reads / accepts writes faster).
- The **L2-bound** cases — read backpressure and write starvation — are **not** QoS candidates;
  their fix is L2/tiling/bank-conflict (A/D). Do not aim QoS at them.
- Respect the platform bytes/sec cap: QoS reallocates bandwidth between channels, so call out
  which channels give it up. If most DDR-bound channels are limited together, QoS alone can't add
  total bandwidth — pair it with L3 volume reduction (A).

## Output

Lead with a one-line verdict (compute- vs memory-bound overall, and the dominant stall cause per
direction). Then: a channel utilization/stall table per captured direction; a per-op-type table
(exec µs, GMACs?, read starv %, write bp %, MB/s, verdict) sorted by execution time; for the
top-stall operators, a ping/pong column or note (single vs ping/pong on the stalling-side
buffer); then a prioritized recommendation list drawn from families A–E (pick each via the
signal→lever table) led by the highest absolute-stall operators/channels. Quote concrete numbers
from the tools; use tables for per-op and per-channel rows.

## Caveats to always state

- **Backpressure and starvation point at opposite sides depending on direction** — backpressure =
  destination busy, starvation = source can't supply. Read: backpressure = L2, starvation = DDR.
  Write: backpressure = DDR, starvation = L2. Never route a write-backpressure stall to an L2 fix
  or a write-starvation stall to QoS (see the direction table in Background).
- `peak_*_total_ch_*` are **aggregates over all channels** of a direction, not one peak channel.
- Achieved BW from utilization assumes the stated per-channel peak; cross-check against the
  timeline `(MB/s)` column and flag any large disagreement.
- A single capture is usually one direction — never report the uncaptured direction as
  stall-free (see "Single-direction captures").
- Stall **percentages** flag intensity; rank real gain by **absolute stall cycles**.
- These are DMA-channel counters. A DDR-bound stall (read starvation / write backpressure) with
  low MB/s is a DDR **latency/QoS** limit, not necessarily a bandwidth-volume limit — do not
  recommend cutting bytes when the fix is QoS, and vice versa.
