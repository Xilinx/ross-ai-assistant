---
name: vitis-host-profile
description: >-
  Guides host-side XDP profiling via xrt.ini [Debug] flags (opencl_trace, native_xrt_trace,
  lop_trace, host_trace) and interpretation of host trace CSVs (native_trace.csv,
  opencl_trace.csv, user_events.csv). Inspects host code to select the correct API-family trace
  flags and answers performance questions from profiling output. Use when users ask about
  host-to-device transfer time (H2D/D2H), kernel parallel execution, or host/device pipelining and
  idle time. For PL kernel instrumentation or AIE tile profiling, use vitis-pl-profile or
  vitis-aie-profile instead. Load it before answering even if the answer seems familiar: it holds
  version-checked flag names and guardrails that general knowledge gets wrong. Not for general
  Vitis development without a profiling or trace question (e.g. writing HLS or ADF code,
  packaging, creating platforms).
license: MIT
compatibility: >-
  XRT installed; hardware or hw_emu runs (sw_emu not supported). Optional PL
  device_trace requires a profile-enabled xclbin (see vitis-pl-profile).
metadata:
  stage: beta
  author: AMD AIG
  version: "1.0.0"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vitis host profiling

Guide **xrt.ini** configuration for **host-side** XDP profiling and help **interpret**
the resulting **CSV** (and related) outputs so users can reason about data movement,
kernel overlap, and host/device pipelining.

**Companion skills**

| Skill | Scope |
|-------|-------|
| **`vitis-pl-profile`** | PL compile + runtime profiling; `device_trace`, `debug_ip_layout`, `stall_trace`. |
| **`vitis-aie-profile`** | AIE compile + runtime profiling; `enable_system_timeline` for unified host + PL + AIE timeline. |

## When to apply this skill

- User asks about **H2D / D2H** time, **buffer** transfers, or **memcpy** vs device
  timeline.
- User asks about **parallel kernels**, **queue** overlap, or **device** utilization.
- User asks whether the **host** and **device** are **both busy** or mostly **idle /
  waiting**.
- User shares **host** code (OpenCL, native XRT, or HAL-level) and wants profiling
  enabled.
- User shares **CSV** files from a run with `Debug.*` profiling enabled (e.g.
  `native_trace.csv`, `opencl_trace.csv`, `lop_trace.csv`, `summary.csv`,
  `device_trace_*.csv`).
- User asks whether **`lop_trace`**, **`opencl_trace`**, or **`native_xrt_trace`** applies
  to their host API (OpenCL vs native XRT).
- **Default stance:** prioritize **host-only** or **host + limited PL** analysis; full
  **AIE event trace** correlation is out of band unless the user accepts post-processing
  for time alignment (see §6–7 under Core concepts).
- **Runtime vs build:** host profiling from `xrt.ini` is always bounded by what the
  **xclbin** was built to expose; missing PL/AIE trace after enabling flags usually means
  **compile-time** gaps (see **§8**).
- **Run targets:** **`sw_emu` (`-t sw_emu`) is no longer supported** — new designs
  **cannot** be built targeting software emulation. Host profiling guidance assumes
  **hardware** (or **`hw_emu`** where a legacy flow still applies).

## Core concepts (XRT)

1. **Host trace layer** — `Debug.host_trace` selects **one** host trace layer: the
   **first** API stack that initializes profiling wins (OpenCL vs native XRT vs HAL).
   Prefer explicit flags when you know the API:
   - **OpenCL** apps: `opencl_trace=true` (and usually `host_trace` or rely on explicit
     `opencl_trace`; see `references/parameter_outline.md`).
   - **Native XRT** (`xrt::` APIs): `native_xrt_trace=true` (or `host_trace=true` if
     native is the top layer).
   - **HAL / driver** level: `xrt_trace=true` (used from HAL loaders; see XRT build /
     path).
   - **`lop_trace`** — **OpenCL-only**, low-overhead host trace → `lop_trace.csv` (UG1700).
     Captures **minimal OpenCL events** with **reduced overhead** vs full
     **`opencl_trace`**. **Do not** use on **native XRT** apps (`xrt::` APIs): it will
     **not** record `xrt::bo::sync`, kernel/graph launches, or waits — use
     **`native_xrt_trace=true`** instead (same mismatch class as **`opencl_trace`** on
     native hosts). **`lop_trace`** also **omits by design**: device/CU events, memory
     transfer address/size detail, rich kernel enqueue metadata, and buffer↔enqueue
     dependencies. Do **not** combine with **`opencl_trace`** or full OpenCL profiling.
     Details: `references/parameter_outline.md` § **`lop_trace`**.

2. **Device-side context** — Questions about **kernels in parallel** or **device**
   busy time almost always need **`device_trace`** (not `off`) and/or
   **`device_counters=true`** in addition to host trace. Encode that in every
   recommendation unless the user scope is strictly host-only.

3. **Output location** — Set **`profiling_directory`** when you need a stable path for
   CSVs; otherwise defaults are relative to the process working directory (typical).

4. **CSV analysis** — Treat VP trace CSVs as **time-ordered events** with function /
   object names and timestamps (exact columns depend on writer). Correlate **host** API
   intervals with **device** activity windows to answer pipelining questions.

5. **Trace vs user transfers (critical)** — When **PL device trace** (`device_trace` not
   `off`) and/or **AIE event trace** (`aie_trace=true`) is enabled alongside host
   profiling, **moving trace data to the host** shows up as ordinary buffer / API
   activity in host traces. That traffic **does not come from the user’s application
   logic**; it is infrastructure for profiling. If **continuous offload** of trace data
   is on (`continuous_trace` for applicable host/OpenCL paths, and/or AIE trace
   **`periodic_offload`** plus offload intervals in `[AIE_trace_settings]`), expect
   **many** such transfers and recurring host activity that can **drown out** or distort
   answers about “my” H2D time and pipelining. Prefer isolating runs (trace off vs on),
   or interpret host CSVs with this contamination in mind. See
   `references/parameter_outline.md` § “Host trace contamination”.

6. **Timestamp alignment** — **PL device trace** (`device_trace_*.csv`) and **host**
   trace files (e.g. `native_trace.csv`, `opencl_trace.csv`, `hal_host_trace.csv`) share
   **aligned** timestamps, so host and PL timelines can be compared **directly** in
   this skill. **AIE event trace** is **not** on that timebase in raw capture; for
   **unified host + PL + AIE** timeline in Vitis Analyzer, set
   **`enable_system_timeline=true`** in **`[AIE_trace_settings]`** at **runtime** (handled
   by **`vitis-aie-profile`**) — it requires **no extra compile flags** beyond normal
   trace builds, adds **small overhead** from polling AIE timestamps, and alignment is
   done in **post-processing**.

7. **Skill focus** — Prefer **host-only** performance analysis, or **host plus limited PL**
   (host trace + `device_trace` / `device_counters` where correlation is
   straightforward). Treat **AIE event trace** as **secondary** here: contamination (§5),
   **misalignment** until post-process (§6), and heavier interpretation belong in
   AIE-focused guidance unless the user explicitly accepts that scope.

8. **Build vs runtime (PL / AIE)** — `xrt.ini` only controls what XRT **records at
   runtime**. If the **design / xclbin was not compiled** with the needed **PL profiling
   monitors**, **PL device trace** support, and/or **AIE event trace** (per product flow),
   that information **will not appear** in traces or CSVs **even when** the matching ini
   flags are set. When more hardware visibility is required, **recommend recompiling**
   via **`vitis-pl-profile`** (PL monitors, trace fabric) or **`vitis-aie-profile`** (AIE
   event trace). Do **not** emit **`v++` / link / packaging commands** from this skill.

9. **Augmenting host code — prefer XDP User events and ranges** — When the user will
   add timing markers or phase measurements in application code, recommend **XDP User
   events and ranges** (`xrt::profile::user_event`, `xrt::profile::user_range` in
   `xrt/experimental/xrt_profile.h`, or C APIs `xrtUEMark` / `xrtURStart` /
   `xrtUREnd`) instead of **`std::chrono::steady_clock::now()`** or equivalent ad-hoc
   timers. User events use the **same `xrt_core::time_ns()` timebase** as other host
   trace events, so they land in **`user_events.csv`** and appear on the **same
   Vitis Analyzer timeline** as `native_trace.csv` / `opencl_trace.csv` — giving
   **direct visual context** for how much host time different phases consume relative to
   XRT API calls, buffer transfers, and (when enabled) PL device trace. Custom
   `chrono` timestamps are **not** aligned with XDP host trace and cannot be overlaid
   without manual conversion. Enable a **host trace flag** in `xrt.ini` so the timeline
   is captured; use **`user_range`** for durations and **`user_event::mark()`** for
   instant markers.

## Workflow

### 1. Inspect host code

- Identify **API family**: OpenCL (`cl*`), native XRT (`xrt::`, `xrtRun*`, BO maps),
  or mixed. If **native XRT**, reject **`lop_trace`** and **`opencl_trace`** — recommend
  **`native_xrt_trace`**. Reserve **`lop_trace`** for **OpenCL-only** hosts that need
  lighter capture than **`opencl_trace`** (see `references/parameter_outline.md`).
- **Check build capability (PL / AIE):** if answers need **PL device trace**, **PL
  profiling**, or **AIE event trace**, confirm whether the user’s **xclbin / build** was
  produced with that instrumentation — **verify it with `xclbinutil --info --input
  <design>.xclbin` before trusting `device_trace`/`aie_trace` numbers:** the *Sections*
  list in that report (UG1702, *xclbinutil Utility*) must include `DEBUG_IP_LAYOUT` for PL
  profiling monitors and `AIE_TRACE_METADATA` for AIE event trace. For detail on which
  monitors exist, `xclbinutil --dump-section DEBUG_IP_LAYOUT:JSON:debug_ip_layout.json
  --input <design>.xclbin`. These are inspection commands, not build flags — always in
  scope to recommend, even though this skill doesn't cover the build side. Skip this check
  when the user only asks how to open or read reports they already have (e.g. a
  `run_summary` in Vitis Analyzer) — answer that directly. If the check shows the
  instrumentation is missing, **say so** and **recommend a rebuild** for
  richer data — then stop at **what** is missing and hand off to **`vitis-pl-profile`**
  or **`vitis-aie-profile`** for the actual **v++** flags to add; **do not** specify
  those flags from this skill. See Core concept **§8**.
- List **hot paths**: buffer read/write/map, kernel enqueue/sync, user events, multiple
  queues or in-order queues.
- Note **synchronization**: blocking reads, `finish`, `wait`, single-queue vs
  out-of-order.
- If the user will **instrument phases** in host code, recommend **XDP User ranges /
  events** (not `std::chrono::steady_clock::now()` or printf timing) so markers share
  the host trace timebase and appear in Vitis Analyzer alongside XRT API spans (see
  core concept **§9**).

### 2. Propose `[Debug]` xrt.ini (minimal but sufficient)

- Enable the correct **host** trace flag(s) per API family; add **`device_trace`** /
  **`device_counters`** when the question needs device visibility—prefer **limited PL**
  depth (enough to answer overlap / pipelining) rather than maximal trace combinations.
- **Default:** steer toward **host-only** or **host + limited PL**; treat **AIE event
  trace** as optional and only when the user will **post-process** for time alignment
  (§6) and accepts contamination risk (§5).
- Set **`profiling_directory`** for predictable artifact pickup.
- **Do not** routinely suggest **`profile_api`** (see `references/parameter_outline.md`):
  it enables **in-host probing of PL profiling monitors** so the application can read
  counter values at runtime—only when the **app must branch on live PL data**. PL
  counters for analysis are already captured via **`device_trace`** / related flags;
  this skill targets **host performance**, not hardware-side live queries.
- Optional: **`continuous_trace`**, **`trace_file_dump_interval_s`**, buffer sizes when
  runs are long or traces large (see `references/parameter_outline.md`).

**Before enabling PL / AIE trace or continuous offload:** confirm whether the user
still wants to read **application** H2D from host CSVs. If yes, call out that
**`device_trace`**, **`aie_trace`**, and **continuous trace offload** will inject
**non-user** transfers into host profiling; recommend a **baseline run** (host
profiling only, or device/AIE trace off) for comparison, or separate runs. Also confirm
the **xclbin** actually includes the requested PL/AIE trace support—otherwise ini
changes alone will not produce data (§8).

Document **conflicts** (e.g., avoid redundant overlapping trace modes that only add
overhead without new signal). Never pair **`lop_trace`** with **`opencl_trace`**; never
suggest **`lop_trace`** for **native XRT** host code.

### 3. After the run — map artifacts

Use **`references/artifact_map.md`** to map flags → filenames, then open the listed
CSVs.

### 4. Answer the user’s question from CSVs

| Question theme | What to compare in CSV / timeline |
|----------------|-----------------------------------|
| **H2D / host transfers** | Host buffer/map/write/read API spans vs device start; gap
  between enqueue and DMA/device activity if visible in device trace. **Subtract or
  flag intervals** tied to trace offload when `device_trace`, `aie_trace`, and/or
  continuous offload are on (see §5 above). |
| **Parallel kernels** | Overlapping device executions (kernels) on timeline; queue
  IDs if present in OpenCL trace. **Host vs PL:** timestamps are **aligned** (§6); do
  not mix in raw **AIE** timelines without post-alignment. |
| **Pipelining / waiting** | Idle gaps: long host waits with no overlapping device work,
  or device idle while host is not submitting; measure overlap of host “active”
  intervals with device busy intervals. **User ranges** on the same timeline help
  label application phases vs XRT API idle/wait time. |

State **confidence limits**: without device trace, kernel overlap and device-side wait
cannot be fully answered. If **`device_trace` / `aie_trace`** were enabled in ini but
**PL / AIE trace data never materialized**, suspect **missing compile-time** support
before blaming runtime (§8).

When the user wants **official documentation**, **Analyzer UI steps**, or **platform-level**
detail beyond a minimal ini recipe, cite **`references/user_guides.md`** (typically
**UG1702** for ini keys, **UG1700** for profiling flow and **`lop_trace`**, **UG1701** on
edge). Add one or two relevant links — do not paste the full guide list in every answer.

## Guardrails

- Do not put **secrets** in xrt.ini (tokens, passwords).
- Warn about **runtime overhead** and **disk** use when multiple traces and continuous
  dump are enabled.
- Prefer **smallest** set of flags that still answers the question.
- Whenever **PL device trace**, **AIE event trace**, or **continuous trace offload** is
  enabled with host profiling, **warn explicitly** that host traces will show **trace
  traffic**, not only user buffers — especially under **continuous offload**, which can
  produce **large volumes** of non-user transfers.
- **Avoid suggesting `profile_api`** unless the user explicitly needs **runtime PL
  counter reads from host code**; it is out of scope for normal host-performance work
  covered here.
- **Prefer** recommendations that stay within **host-only** or **host + limited PL**
  (aligned timestamps, §6); for **AIE + host** unified timeline, point to
  **`enable_system_timeline=true`** in **`vitis-aie-profile`** (runtime-only).
- **Do not suggest `lop_trace`** for **native XRT** applications — use
  **`native_xrt_trace`**. **`lop_trace`** is **OpenCL-only** and records **less** than
  **`opencl_trace`**, not a low-overhead native alternative.
- **Compile / link / v++ out of scope:** never emit **v++**, **package**, or detailed
  **synthesis/implementation flags** from this skill; recommend rebuild via
  **`vitis-pl-profile`** or **`vitis-aie-profile`** for exact compile commands (§8).
- When suggesting **host code instrumentation**, prefer **`xrt::profile::user_range`** /
  **`xrt::profile::user_event`** (or C `xrtUR*` / `xrtUE*`) over **`std::chrono`** or
  wall-clock timers — only XDP user markers share the host trace timebase and Vitis
  Analyzer timeline.
- **`sw_emu` is not supported** for new designs — do not recommend software emulation
  build or profile runs.

## References

### In-skill references

- [Host profiling flags](references/parameter_outline.md)
- [Artifacts and CSV roles](references/artifact_map.md)
- [AMD user guides — when to cite](references/user_guides.md)

### User guides (cite when appropriate)

When the user needs official documentation beyond a minimal ini recipe — full key tables,
Analyzer UI steps, or **`lop_trace`** product guidance — point to
**`references/user_guides.md`** and include **one or two** relevant links.

| Topic | Primary guide |
|-------|----------------|
| **`xrt.ini`** host trace keys | **UG1702** — *xrt.ini File* |
| **`lop_trace`** (low-overhead OpenCL) | **UG1700** — *Enabling Low Overhead Profiling* |
| Profiling flow + Profile Summary | **UG1700** — *Enabling Profiling in Your Application* |
| Vitis Analyzer timelines | **UG1702** — *Working with the Analysis View* |
| Missing PL device trace in xclbin | **UG1700** link instrumentation + **`vitis-pl-profile`** |
| Edge / embedded host trace | **UG1701** — *Enabling Profiling / Trace in Your Application* |

Direct URLs and a question→guide table: [references/user_guides.md](references/user_guides.md).
