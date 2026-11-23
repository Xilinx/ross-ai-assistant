<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Profiling artifacts (CSV-focused)

Outputs are typically written under the process **current working directory**, or
under **`Debug.profiling_directory`** when set. Exact filenames come from XDP
writers; common host-side CSVs:

| Approximate file | Typical source | VP / purpose (when registered) |
|------------------|----------------|----------------------------------|
| `native_trace.csv` | Native XRT host trace plugin | `VP_TRACE` |
| `opencl_trace.csv` | OpenCL host trace plugin | `VP_TRACE` |
| `hal_host_trace.csv` | HAL host trace writer | `VP_TRACE` |
| `lop_trace.csv` | **`lop_trace=true`** — low-overhead **OpenCL** host trace only (UG1700) | `VP_TRACE` — **minimal OpenCL events**; **not** for native XRT (`xrt::`) apps. Omits device/CU events, memory addr/size detail, rich enqueue metadata, buffer↔kernel deps. Use **`native_trace.csv`** for native XRT. Do **not** combine with **`opencl_trace`**. |
| `summary.csv` | Database summary writer | summary |
| `device_trace_<id>.csv` | Device offload / PL device trace | device timeline |
| `user_events.csv` | User events plugin | Custom **User** markers and ranges — **same host timebase** as `native_trace.csv` / `opencl_trace.csv`; overlay in Vitis Analyzer for application-phase context |

Other plugins (power, NOC, AIE, VART, etc.) emit additional CSVs; include them only when
those `Debug.*` flags are enabled.

## Trace offload vs user data (read this before H2D analysis)

If **PL device trace** (`device_trace` ≠ `off`), **AIE event trace** (`aie_trace`), or
**continuous trace offload** (`continuous_trace` and/or `[AIE_trace_settings]`
`periodic_offload`, etc.) was enabled, **host** CSVs will often show **buffer and API
activity caused by moving trace data**, not only by the user’s kernels and buffers.
**Continuous offload** in particular can create **many** such transfers on a fixed
interval. When answering “How long are my host↔device transfers?” **qualify** results or
use a **baseline** run without those features.

## Timestamp alignment (this skill’s sweet spot)

- **PL device trace** and **host** trace files share **aligned** timestamps — safe to
  correlate `device_trace_*.csv` with `native_trace.csv` / `opencl_trace.csv` / HAL host
  trace for **host + limited PL** questions.
- **AIE event trace** is **not** aligned to host traces in raw CSVs. For unified timeline,
  set **`enable_system_timeline=true`** at runtime (**`vitis-aie-profile`**) — no extra
  compile flags beyond normal trace; small polling overhead; alignment in post-processing.

**Skill default:** prioritize **host-only** or **host + limited PL**; only expand into
**AIE** when the user explicitly needs it and will run alignment / tooling after capture.

**Build prerequisite:** if **`device_trace`** / **`aie_trace`** are on but expected PL
or AIE columns/streams never show up, the gap is often **compile-time** (xclbin not built
with that trace)—recommend a rebuild conceptually; **v++ / build flags belong in another
skill**, not here.

## How to use this map when answering questions

1. **Confirm which flags were on** for the run (from xrt.ini or user notes).
2. **Open the corresponding CSV(s)** — host questions usually start with
   `native_trace.csv` (native XRT), `opencl_trace.csv` (OpenCL), or **`lop_trace.csv`**
   (OpenCL low-overhead only — **not** native XRT); overlap / kernel questions add
   `device_trace_*.csv` or summary/counter outputs.
3. **Timebases** — **Host** and **PL device trace** (`device_trace_*.csv`) timestamps are
  **aligned**; compare them directly. **AIE event trace** needs **`enable_system_timeline=true`**
  at runtime for post-processed alignment with host traces (see **`vitis-aie-profile`**).
4. **H2D timing** — measure duration of host-side buffer/map/sync API records that
   bracket transfers; cross-check device-side DMA or kernel start if present.
5. **Parallel kernels** — look for **overlapping** device execution intervals for
   different kernels / queue indices.
6. **Pipelining** — quantify **overlap** vs **gaps**: host busy without device work,
   device busy without new host submits, long waits on `finish` / sync.

## Tooling note

Vitis Analyzer and other AMD tools can ingest VP trace / summary data. Prefer official
tooling when the user already has a workflow; raw CSV analysis is a fallback.
