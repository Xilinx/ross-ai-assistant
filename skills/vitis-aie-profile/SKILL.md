---
name: vitis-aie-profile
description: >-
  Guides AIE profiling and event trace from compile time through runtime on Versal / AI Engine
  designs. Covers counters-vs-trace decisions, v++ event-trace build flags, recovery from
  trace-related compile failures, and xrt.ini [AIE_profile_settings] and [AIE_trace_settings] for
  tile-scoped capture. Answers questions such as "What is the latency of my AIE kernel?", "Where
  and when are my data transfers stalling?", "What is the average throughput/bandwidth on my PLIO
  or GMIO port?", and "Are my AIE kernels overlapping in time?" Interprets aie_profile.csv,
  aie_trace outputs, and Vitis Analyzer timelines. For PL or host-only profiling, use
  vitis-pl-profile or vitis-host-profile instead. Load it before answering even if the answer
  seems familiar: it holds version-checked metric names and documented stall resolutions that
  general knowledge gets wrong. Not for general Vitis development without a profiling or trace
  question (e.g. writing HLS or ADF code, packaging, creating platforms).
license: MIT
compatibility: >-
  Versal / AI Engine design with XRT; hardware or hw_emu (sw_emu not supported).
  Event trace requires graph compile with event-trace=runtime. Unified host+PL+AIE
  timeline may require vitis-host-profile and vitis-pl-profile.
metadata:
  stage: beta
  author: AMD AIG
  version: "1.0.0"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vitis AIE profiling and trace

Help users **profile AI Engine kernels** end to end: choose **counters vs event trace**,
add **compile-time trace infrastructure** only when needed, configure **runtime** `xrt.ini`,
recover from **trace-related build failures**, and **interpret** profiling/trace artifacts.

**When asked directly whether a "standard" or "documented" procedure exists, check before
answering — don't default to "yes," and don't default to "no" either.** Some AMD
documentation genuinely does contain a named, citable procedure for a question that sounds
like it might be open-ended (e.g. UG1076's **Memory Stall Analysis** section gives a real
repeated/loop-pattern-vs-one-time-or-small-count heuristic for telling a real stall
bottleneck from noise — see `references/methodologies.md`). Denying that such a procedure
exists when it does is the same overclaiming problem as inventing one that doesn't — both
misrepresent what's actually documented. If the procedure that exists is scoped to a
different tool/flow than this skill covers (e.g. UG1076's stall-analysis heuristic above is
written for the `aiesimulator` + Vitis IDE/Analyzer trace-view workflow, not this skill's
`xrt.ini`/hardware-runtime domain), say so explicitly — cite the real procedure, name the
scope it applies to, and give this skill's own closest hardware-runtime equivalent (compare
the tile's stall count/percentage against the rest of the array, check whether it recurs
across runs) as general practice, not as an equivalent named procedure if one doesn't exist
in this skill's own domain.

**Diagnosis first, then the documented fix — not invented code.** This skill's own content
covers *reading* metrics/counters/trace and naming the mechanism (memory, stream, lock, or
cascade stall; throughput imbalance; serialization). AMD documents the **resolutions** in other
guides — this skill does not reproduce them, but it must point to them instead of improvising:

| Diagnosed mechanism | Documented resolution to cite |
|---------------------|-------------------------------|
| Memory stall | **UG1315** `AIE_MEMORY_STALLS` / `AIE_STALL` and **UG1076** *Memory Stall Analysis* (scenario/solution table: separate banks, `BufferOptLevel`, virtual resource annotations) |
| Stream / cascade stall | **UG1315** `AIE_STALL` (FIFO depth, balance stream reads/writes in the loop, maximize PL-to-AIE interface bandwidth) |
| Lock stall | **UG1315** `AIE_STALL` → LOCK_STALL (acquire/release on time, ping-pong buffers, balance kernel throughput, local buffers, match PL interface throughput) |
| Kernel compute itself is slow (cycles not explained by stalls) | **UG1079** (AIE) / **UG1603** (AIE-ML) kernel optimization: vectorization, software pipelining, `chess_loop_range` |
| Transfers serialized against compute | **UG1700** *Best Practices for Host Programming* (overlap data transfer with compute) + the stall rows above |

Details and links: `references/methodologies.md` § *Documented resolutions by stall type*.

Required answer shape for any "how do I fix / optimize this?" question:
1. **Diagnosis** — the mechanism, confirmed with documented metrics/trace.
2. **Documented resolution** — name the guide and rule/section from the table above and
   summarize what it says.
3. Anything beyond that guidance (specific pragmas, buffer depths, API calls the cited guide
   does not give) is **labelled as general engineering guidance**, not presented as AMD's
   documented procedure or as this skill's content.

**Companion skills**

| Skill | Scope |
|-------|-------|
| **`vitis-pl-profile`** | PL `v++ --profile.*` monitors and `debug_ip_layout` (system-level PL trace for unified timeline). |
| **`vitis-host-profile`** | Host trace; **`enable_system_timeline=true`** at runtime aligns AIE with host (no extra compile flags). |

## When to apply this skill

- User asks about **AIE kernel performance**, **latency**, **stalls**, **pipelining**,
  **utilization**, or **timeline / ordering** on Versal AI Engine graphs.
- User needs **`aie_profile`** or **`aie_trace`** enabled via `xrt.ini`.
- User needs to **add event trace at compile time** (`event-trace=runtime`, trace routing).
- **AIE graph compile or system link fails** after enabling event trace.
- User is unsure whether **`aie_profile` counters** are enough vs full **event trace**.
- User shares **`xrt.run_summary`**, `aie_profile.csv`, or `aie_trace_*.txt` outputs.
- User configured wrong graph/tile names and gets XRT warnings about invalid tiles.

**Run targets:** **`sw_emu` (`-t sw_emu`) is no longer supported** — new designs
**cannot** be built targeting software emulation. Use **`hw`** for trace-enabled graph
and link builds.

## Core decision: counters vs event trace

| | **Profiling counters (`aie_profile`)** | **Event trace (`--event-trace`)** |
|---|----------------------------------------|-----------------------------------|
| **Compile-time change** | Usually **none** for counters on hardware | **Required** — `event-trace=runtime` only; trace ports, streams, offload path |
| **Runtime switch** | `[Debug] aie_profile=true`, `[AIE_profile_settings]` | `[Debug] aie_trace=true`, `[AIE_trace_settings]` |
| **Data shape** | Aggregated samples (rates, heat maps, conflict counts) | Timestamped event streams (function entry/exit, stall episodes) |
| **Overhead** | Lower | Higher (routing resources, DDR/PL bandwidth) |
| **Best for** | Throughput, utilization %, DMA lock conflicts, port bandwidth averages | *When* kernels run relative to each other, stall *sequences*, function-level timeline, debug hangs |

**Default stance:** recommend **`aie_profile` counters first** unless the question needs
**time order** or **per-invocation** detail. Only then add compile-time trace.

Full decision table: `references/counters_vs_trace.md`.

## Core concepts (runtime)

1. **Tile dependency** — Metric strings reference **graph names**, **kernel/buffer names**,
   or **`{col,row}`** / **`{colMin,rowMin}:{colMax,rowMax}`** from the compiled map.
   Invalid names log XRT warnings and are **skipped**. Discover tiles before writing ini
   (workflow §2, `references/tile_discovery.md`).

2. **Counter budget** — Each AI Engine array element supports **limited simultaneous
   profile/trace metrics** (UG1076). Prefer **narrow tile scope**; use **multiple runs**
   with different ini for full coverage.

3. **Last match wins (metric scoping)** — If multiple metric specifications on the
   same ini key match the **same tile**, **only the last one takes effect**. Use combined
   metric sets (e.g. `input_output_ports_stalls`) when multiple behaviors are needed on
   the same tiles. See `references/parameter_outline.md`.

4. **Phased methodology (default)**

   1. **Profile counters** on suspect graphs/tiles (no recompile).
   2. Add **event trace** only if counters show a problem but not **when** it happens.
   3. If trace metadata is missing, add compile-time **`event-trace=runtime`** (workflow §4).

5. **Timestamp alignment / system timeline** — Raw AIE event trace is **not** on the host
   trace timebase until **post-processing**. Set **`enable_system_timeline=true`** in
   **`[AIE_trace_settings]`** at **runtime only** — no extra compile flags beyond normal
   trace builds. XRT polls AIE timestamps during the run; Vitis Analyzer aligns AIE with
   host and PL in post-processing. A **unified host + PL + AIE** timeline typically needs
   all three companion skills in the same run: **`vitis-host-profile`** (host trace ini),
   **`vitis-pl-profile`** (PL `device_trace` + profile-enabled xclbin), and this skill
   (`aie_trace` + `enable_system_timeline`).

## Workflow

### 1. Classify the user's question

| Question pattern | Prefer | Compile-time trace? |
|------------------|--------|---------------------|
| Average throughput / bandwidth on PLIO or GMIO | `aie_profile` + `s2mm_throughputs`/`mm2s_throughputs` (⚠️ only these 2 names exist — **no combined "both directions" metric**, never `input_output_throughputs` or `input_output_ports_throughputs`). **Direction:** an **output** port (AIE → PL/DDR, e.g. `DataOut1`) is `s2mm_throughputs` (older name `output_throughputs`); an **input** port (PL/DDR → AIE) is `mm2s_throughputs` (older name `input_throughputs`) — UG1076 *Profiling for Interface Tiles* | **No** |
| Which tiles are busy (utilization heat map) | `aie_profile` + `heat_map` | **No** |
| Memory lock / DMA conflicts | `aie_profile` + `conflicts`, `dma_locks` | **No** |
| **What is the latency** of my AIE kernel? | `interface_tile_latency` (profile) | **No** first |
| **Where** are data transfers stalling? | `stalls`, `dma_stalls_*`, interface `*_stalls` | **No** first |
| Tile-to-tile transfer inefficiency | `heat_map`, throughput counters | **No** first |
| Are AIE kernels **overlapping** in time? | Event trace + timeline | **Yes** |
| **Which function** is stalling and **when**? | Event trace (`functions`, `all_stalls`, …) | **Yes** |
| Stall time **broken down by cause** (memory / stream / lock / cascade), aggregated | `aie_profile` + **`stalls`** metric set — reports Memory, Stream, Lock, and Cascade Stall Time separately (UG1076 *Profiling for AI Engine*) | **No** |
| Stall **type over time** (which stall, when, in which function) | Event trace (`all_stalls` / `partial_stalls`) | **Yes** |
| Unified **host + PL + AIE** timeline | **`vitis-host-profile`** + **`vitis-pl-profile`** + `enable_system_timeline` | **Yes** for AIE trace build |
| **"How do I fix / optimize / speed up …?"** | Confirm the mechanism with the rows above, then cite the **documented resolution** for that mechanism (UG1315 / UG1076 / UG1079 / UG1603 — see Diagnosis-first table); label anything beyond it as general guidance | Per the diagnosis row |

If counters suffice, skip to **§6** (runtime `xrt.ini`) and **do not** add `--event-trace`.

Question-specific ini recipes: `references/methodologies.md`.

### 2. Discover tiles and trace capability

**Tiles / graphs**

- Open `Work/<graph>.aiecompile_summary` in Vitis Analyzer → **Graph** / **Array** views.
- Or dump from xclbin:

```bash
xclbinutil --input <design.xclbin> \
  --dump-section AIE_TRACE_METADATA:JSON:aie_trace_metadata.json
```

Use `TileMapping.AIEKernelToTileMapping` (graph, column, row, function).

**Event trace available?**

- In the same JSON, `aiecompiler_options.event_trace` should be **`runtime`**. If section
  missing or `event_trace` absent → **`aie_trace=true` will not work**; use profiling only
  or continue to **§4** (compile-time trace).

Details: `references/tile_discovery.md`.

### 3. Map the question → ini strategy (runtime)

| Question | Start with (profile) | Escalate to (trace) if needed |
|----------|----------------------|-------------------------------|
| **Kernel latency** | `interface_tile_latency` between source/dest graph ports | Trace + function/stall timeline on path tiles |
| **Where/when transfers stall** | `stalls`, `dma_stalls_*`, interface `*_stalls` | `all_stalls`, `s2mm_channels`, `mm2s_channels` on tile ranges |
| **Tile transfer inefficiency** | `heat_map`, throughput counters | `all_dma`, channel trace metrics on bottleneck ranges |

### 4. Add compile-time trace (when required)

**Step A — AI Engine graph compile** (`v++ -c --mode aie`)

Use **`event-trace=runtime` only** — fixed compile-time values (`functions`,
`functions_partial_stalls`, etc.) are **removed** and **invalid**. Choose trace metric
depth in **`xrt.ini`** at run time (§6).

```bash
v++ -c --mode aie --platform <platform.xpfm> \
  --config aie_trace.cfg \
  --include ./src --include ./src/kernels \
  --aie.workdir ./Work \
  -o libadf.a \
  ./src/graph.cpp
```

`aie_trace.cfg`:

```ini
[aie]
event-trace=runtime
event-trace-port=gmio
broadcast-enable-core=true
xlopt=0
```

| Flag | Role |
|------|------|
| `event-trace=runtime` | **Required.** Only valid compile-time value |
| `event-trace-port=gmio` | **Default** when PL is scarce or trace fits DDR buffer |
| `event-trace-port=plio` | PL offload — circular DDR buffer + continuous offload for large history |
| `broadcast-enable-core=true` | Aligns core start times (~100 cycle skew without it) |
| `xlopt=0` | Disables kernel inlining so **function names** appear in trace |
| `num-trace-streams=N` | **PLIO only** — minimum streams to avoid overruns without wasting NMU/PL |

**GMIO vs PLIO:** Assess **remaining PL resources after the application is placed** first —
this is the dominant, non-negotiable factor, not one of two equally-weighted options. **If PL
is already scarce/heavily consumed by the application's own compute logic, GMIO is normally the
only path that fits** — PLIO would consume additional PL routing/stream resources on top of
what's already scarce, risking build/link failure for no benefit. Only consider PLIO (circular
buffer + continuous offload for long trace history) when there is confirmed PL headroom left
after the application. Right-size `num-trace-streams` — extra streams beyond what the design
needs only waste resources.

Full compile options: `references/event_trace_options.md`.

**Step B — System link** (heterogeneous designs with PL kernels)

If the user also needs **PL** activity on a unified timeline, build PL trace with
**`vitis-pl-profile`** (e.g. `--profile.data` at link). **`enable_system_timeline`**
is **runtime-only** — not an extra compile requirement.

**`--profile.aie:<arg>` is a real `v++ --link` flag** for PL-routed AIE stream
profiling (`<arg>` = `all` / `<pin_name>` / `<ADF_graph_argument>` — e.g.
`--profile.aie:DataIn1`) — never deny it exists. **Pin-naming rule (always state this
when scoping to one stream):** `<pin_name>` must be the port's `create()` string
(e.g. `adf::input_plio::create("DataIn1", ...)`), **not** the graph's C++ member
variable name — the two are often the same by convention but are not required to
be, and using the member-variable name silently profiles the wrong port (or none)
with no build error. `--profile.aie` is usually **not** the first choice: prefer
runtime `aie_profile`/`aie_trace` (no PL routing cost) unless PL and AIE activity
specifically need to appear together on one PL-instrumented view. Full detail:
`references/event_trace_options.md`.

```bash
v++ -l -t hw --platform <platform.xpfm> \
  --profile.data all:all:all \
  libadf.a <kernel.xo> ... \
  -o binary_container.xclbin
```

**Step C — Verify trace was compiled in**

```bash
xclbinutil --input binary_container.xclbin \
  --dump-section AIE_TRACE_METADATA:JSON:aie_trace_metadata.json
```

Confirm `aiecompiler_options.event_trace` is **`runtime`**. If missing, return to Step A.

### 5. Build failed after adding trace

Follow `references/compile_failure_recovery.md`. Summary:

1. **Trace net routing errors** → `--aie.Xrouter=skipunroutabletracenets` (partial trace).
2. **NMU / trace stream exhaustion** → reduce `--aie.num-trace-streams`; try **gmio**.
3. **Congested array** → **`location_constraint bounding_box(...)`** in graph code.
4. **Still failing** → narrow **runtime** trace scope in **`xrt.ini`** — do **not** use
   removed fixed compile-time `event-trace` values.

Always state **impact**: partial routing, fewer tiles traced, or placement constraints.

### 6. Propose minimal runtime `xrt.ini`

**Profiling-only (latency snapshot)**

```ini
[Debug]
aie_profile=true
profiling_directory=/path/to/output

[AIE_profile_settings]
interval_us=1000
# last field = which transaction to measure (the nth, UG1076); XRT reports one cycle count
interface_tile_latency=mygraph:in_port:mygraph:out_port:100
graph_based_aie_metrics=mygraph:my_kernel:execution
```

**Stall investigation (counters)**

```ini
[Debug]
aie_profile=true

[AIE_profile_settings]
interval_us=500
graph_based_aie_metrics=all:all:stalls
tile_based_aie_memory_metrics=all:conflicts
graph_based_interface_tile_metrics=all:all:input_ports_stalls:0
```

**Event trace (requires trace-enabled build)**

```ini
[Debug]
aie_trace=true

[AIE_trace_settings]
graph_based_aie_tile_metrics=mygraph:all:all_stalls
tile_based_aie_tile_metrics={10,0}:{12,3}:all_stalls
buffer_size=16M
periodic_offload=true
buffer_offload_interval_us=100
enable_system_timeline=true
```

Use **graph-based** names when known; use **tile-based** ranges when optimizing scope.
Separate specs with **`;`** — remember **last match wins** per tile.

Full key reference: `references/parameter_outline.md`.

### 7. After the run — artifacts and answers

See `references/artifact_map.md`. Open `xrt.run_summary` in Vitis Analyzer; set **AI
Engine Compile Directory** to `Work/*.aiecompile_summary` for symbol resolution.

| Question | Where to look |
|----------|---------------|
| Latency | Profile **Interface Latency** table from `interface_tile_latency` |
| Stall **where** | Profile heat/stall columns; memory `conflicts` / `dma_locks` |
| Stall **when** | Trace timeline — stall-colored intervals per tile |
| Tile transfer inefficiency | Throughput counters tile-to-tile; trace `all_dma` for ordering gaps |

**Iterate:** change profile metrics / tiles without recompile; change trace metrics freely
if build used `event-trace=runtime`; narrow ranges to cut volume.

**Don't over-interpret a metric pattern the data alone can't resolve.** An outlier tile
(e.g. one or two tiles with counter values orders of magnitude above the rest) is a real
signal worth surfacing, but whether it's a **problem** or an **expected structural role**
(a designated aggregation/fan-in/gather point in the graph topology) is not something
profiling-counter data alone can determine — that depends on the graph's intended design.
State the pattern plainly and name what would resolve the ambiguity (the graph's topology
or the designer's intent), rather than confidently asserting it's a bug or confidently
dismissing it as benign.

**This caution covers design intent only — not what a metric measures.** When the user asks
which mechanism a counter reflects, answer directly from UG1076's metric definitions: e.g.
`dma_locks` = time DMA channels spend stalled on lock acquires (DMA lock contention), which
is a different mechanism from `conflicts` = memory-bank access conflicts; lock stalls are
buffer synchronization, not memory bandwidth. When the numbers the user gives already support
a diagnosis, **lead with that diagnosis** — do not replace the answer with a request for more
data. Suggest a follow-up measurement only as a confirmation step after the direct answer.

**When remediation is requested, don't let it displace the diagnosis.** Confirm which
tile/mechanism is actually implicated using the real metrics/counters this skill documents
(§1-§2 above) before naming a fix — then cite the documented resolution for that mechanism.

When the user wants **official documentation**, **Analyzer UI steps**, or **platform-level**
detail beyond a minimal ini recipe, cite **`references/user_guides.md`** (typically
**UG1076** for AIE metrics and event trace, **UG1702** for `xrt.ini` keys, **UG1700** for
profiling flow). Add one or two relevant links — do not paste the full guide list in every
answer.

## Guardrails

- **`[AIE_trace_settings]` takes trace metrics only.** For core tiles: `functions`,
  `partial_stalls`, `all_stalls` (UG1076: the runtime names of `functions_partial_stalls` /
  `functions_all_stalls`), and the DMA sets `all_dma`, `all_stalls_dma`, … (UG1076 *Event Trace
  Build Flow*, AI Engine Metrics table). For "when / are they overlapping" questions use
  `functions` (the basic kernel-activity timeline); `partial_stalls` / `all_stalls` add stall
  events on top when the question is about stalls. Counter metric sets — `execution`,
  `heat_map`, `stalls`, `*_throughputs` — belong under `[AIE_profile_settings]` and are not
  valid trace metrics.
- **Fixes come from the documented resolution for the diagnosed mechanism** (UG1315
  `AIE_STALL` / `AIE_MEMORY_STALLS`, UG1076 *Memory Stall Analysis*, UG1079 / UG1603 kernel
  optimization). Do not invent fixes and present them as documented; label anything beyond
  the cited guide as general guidance.
- **Do not confidently assert whether an outlier metric pattern is a problem or an
  expected structural role** (e.g. a designated fan-in/aggregation tile) when profiling
  data alone can't determine that — name what the data shows and what additional
  information (graph topology, design intent) would resolve the ambiguity.
- **Do not** add `--event-trace` when **`aie_profile` counters** answer the question.
- **`event-trace=runtime` only** — never recommend removed fixed compile-time values.
- **Choose `event-trace-port` from design resources and trace volume** — not one default for all designs.
- **Right-size `num-trace-streams`** (PLIO) — minimum to avoid overruns.
- **Never** promise `aie_trace` without verifying `AIE_TRACE_METADATA`.
- **Last match wins** — use combined metric sets when multiple behaviors are needed on same tiles.
- **`enable_system_timeline`** is **runtime-only** — no extra compile flags beyond normal trace builds.
- **`--profile.trace_memory`** and PL options → **`vitis-pl-profile`**.
- Warn on **trace buffer full** / black bars — increase `buffer_size`, narrow scope, or lighter metrics.
- **`aie_profile` and `aie_trace` together** only when counters **and** timeline are needed in one run.
- **`sw_emu` is not supported** for new designs.

## References

### In-skill references

- [Counters vs trace decision guide](references/counters_vs_trace.md)
- [Event trace compile & runtime options](references/event_trace_options.md)
- [Compile failure recovery & bounding boxes](references/compile_failure_recovery.md)
- [Ini keys and metric syntax](references/parameter_outline.md)
- [Finding tiles and graphs](references/tile_discovery.md)
- [Question methodologies](references/methodologies.md)
- [Artifacts and Analyzer](references/artifact_map.md)
- [AMD user guides — when to cite](references/user_guides.md)

### User guides (cite when appropriate)

When the user needs official documentation beyond a minimal ini recipe — full metric tables,
event-trace build flow, or Analyzer AIE views — point to **`references/user_guides.md`**
and include **one or two** relevant links.

| Topic | Primary guide |
|-------|----------------|
| AIE profile/trace metrics and event trace | **UG1076** — *AI Engine Tools and Flows* |
| **`xrt.ini`** Debug / AIE settings keys | **UG1702** — *xrt.ini File* |
| Profiling flow + Profile Summary | **UG1700** — *Enabling Profiling in Your Application* |
| Event trace build / routing / troubleshooting | **UG1076** — Event Trace sections |
| Unified host + PL + AIE timeline | **UG1076** + **`vitis-host-profile`** + **`vitis-pl-profile`** |
| Missing PL device trace in xclbin | **`vitis-pl-profile`** + UG1700 link instrumentation |

Direct URLs and a question→guide table: [references/user_guides.md](references/user_guides.md).
