<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# PL profiling artifacts

**Product reference:** **UG1700** —
[Generating and Opening the Profile Summary Report](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Generating-and-Opening-the-Profile-Summary-Report);
**UG1702** —
[Working with the Analysis View (Vitis Analyzer)](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/Working-with-the-Analysis-View-Vitis-Analyzer).
Tutorial walkthrough: **XD100** *Profiling Using PL Profile Monitors*. More links:
[user_guides.md](user_guides.md).

Outputs land in the process working directory or in **`Debug.profiling_directory`**
when set.

## Primary files

| File | Enabled by | PL content |
|------|------------|------------|
| `summary.csv` | `device_trace` is `fine`, `coarse`, or `accel` and/or `device_counters=true` (with `device_trace=off`) | Per-monitor counter totals: CU executions, read/write bytes (AIM), stream counts (ASM), stall summaries, bandwidth estimates. When `device_trace` is enabled, counters are included **without** setting `device_counters`. |
| `device_trace_<id>.csv` | `device_trace` is `fine` or `coarse` | Time-ordered PL events: CU start/stop, stalls (when `stall_trace` on), AIM/ASM activity windows. **`device_trace=all` is invalid** — use `fine` or `coarse`. |
| `guidance.csv` | Profiling guidance plugin | Hints such as `NUM_MONITORS` (how many monitors have trace enabled vs total) |
| `xrt.run_summary` | Most profiling runs | Open in **Vitis Analyzer** for integrated timeline |
| `power_profile_<device>.csv` | `power_profile=true` (**Alveo only**) | Time-series power rail samples / running power summary while the app runs |

Host trace files (`native_trace.csv`, `opencl_trace.csv`, …) are documented in
**`vitis-host-profile`**. PL `device_trace_*.csv` timestamps **align** with host
trace timestamps for correlation.

## Live / interactive (no new ini run required)

When the xclbin is **loaded** and the user needs **current** hang state:

```bash
xrt-smi examine --device <bdf> --report dynamic-regions
xrt-smi examine --device <bdf> --report debug-ip-status
```

| Report | Hang / debug signal |
|--------|---------------------|
| `dynamic-regions` | CU state stuck in **START** or inactive while host waits |
| `debug-ip-status` | Live AM/AIM/ASM counter values — identify CUs with no progress, ports with zero traffic, deadlock detector status |

These require the same debug IP that xclbinutil shows in `DEBUG_IP_LAYOUT`.

## Answering the three canonical questions

### "Can the pipelining between my PL kernels work better?"

1. Confirm **AM** entries exist per kernel/CU in `debug_ip_layout.json`.
2. Enable `device_trace=fine` (omit `device_counters` — summary totals are automatic);
   add `stall_trace=all` if AM stall bit set at compile.
3. In **`device_trace_*.csv`**: find overlapping CU active intervals — good pipelining
   shows CU B active while CU A still draining or while AIM on B receives data.
4. In **`summary.csv`**: compare CU execution counts and stall totals — high
   `Intra-CU` / `Inter-CU` stalls (or pipe/dataflow/memory stall columns) suggest
   bottlenecks.
5. In **AIM** summary rows: memory ports busy while CUs idle → memory-bound; CUs idle
   with no AIM traffic → synchronization or host-side gap (check host trace).

### "Are my PL kernels receiving and sending the right amount of data?"

1. Map each kernel port to **AIM** (`m_axi_*`) or **ASM** (`m_name` in layout).
2. Enable **`device_trace=fine`** for **`summary.csv`** byte/packet totals (no
   `device_counters` needed); use timeline in `device_trace_*.csv` when timing matters.
   For totals only with lowest overhead: `device_counters=true`, `device_trace=off`.
3. In **`summary.csv`**: compare **read/write byte** totals per AIM to expected
   `sizeof(data) * iterations`; compare ASM **transaction counts** to expected packets.
4. Scale mismatches (2×, 0×) often indicate wrong buffer size, incomplete execution,
   or wrong CU/port association — use `m_name` to tie counters to specific ports.

### "My system is hanging — which PL kernel could be the problem?"

1. Check layout for **AM** and **`ACCEL_DEADLOCK_DETECTOR`**.
2. Runtime ini: `device_trace=fine`, `stall_trace=all`; `pl_deadlock_detection=true` if ADD present.
3. **While hung**: `dynamic-regions` — CUs in START; `debug-ip-status` — which AM
   counters stopped advancing.
4. **Post-run** (if trace captured): last events in `device_trace_*.csv` before gap;
   AIM with outstanding transactions but no progress → memory deadlock candidate.
5. Cross-check XRT debug FAQ patterns: CU AXI deadlock often blocks other CUs on same
   memory resource.

## Gaps between kernel executions on the timeline

A gap **between** two executions of a CU on the Timeline Trace means the CU was idle —
nothing had been launched yet. That points to the **host side**: kernel enqueue/launch
overhead, waiting on a host↔device data transfer, or a dependency the host program
serializes (UG1700 *Best Practices for Host Programming*: overlap data transfer with kernel
compute, reduce enqueue count). Stalls **inside** the kernel (dataflow, memory, pipe) show up
**within** a CU's execution interval and in the Profile Summary stall percentages, not as gaps between
executions. Confirm with host trace (`vitis-host-profile`: `native_xrt_trace` /
`opencl_trace`) to see what the host was doing during the gap.

## Kernel stall categories and documented resolutions

The Profile Summary reports three CU stall percentages. Run Guidance (Run Summary →
Guidance report in Vitis Analyzer) flags kernel stall above 50% and links to the
**UG1315 Kernel Stall** rule, which defines each category and its documented resolution.
Cite it instead of inventing fixes:

| Stall category | Meaning (UG1315) | Documented resolution (UG1315) |
|----------------|------------------|--------------------------------|
| **Intra-Kernel Dataflow Stalls (%)** | Running time stalled while streaming data **within** the kernel — internal dataflow blocks' stall signals high (e.g. producer II=1, consumer II=2 → 50% stall). | Use **hw_emu** for more detail (it taps hierarchical stall signals); **increase FIFO sizing** within the kernel's internal blocks/loops. |
| **External Memory Stalls (%)** | Running time stalled on memory transfers **outside** the CU — e.g. multiple ports accessing memory at the same time (contention). | **Increase the number of memory ports** used for access, if the application allows (e.g. spread ports across banks with `--connectivity.sp`). |
| **Inter-Kernel Pipe Stalls (%)** | Running time stalled streaming data **to/from outside** the CU — writing to a full FIFO or reading an empty FIFO between kernels. | Use hw_emu for detail; **increase FIFO depth between kernels**; improve the throughput of whichever side (producer or consumer) is slower. |

**Kernel Internals** data (per-block dataflow/stall detail) is an **hw_emu** feature: set
`debug_mode=batch` in the `[Emulation]` section of `xrt.ini` in addition to the profiling
keys (UG1701 / UG1700 *Enabling Profiling in Your Application*).

Anything more specific than this guidance (exact FIFO depths, particular pragmas) is
general HLS advice — say so rather than presenting it as documented procedure.

## Empty or missing artifacts

| Symptom | Likely cause |
|---------|--------------|
| No `device_trace_*.csv` with `device_trace` on | No trace fabric in xclbin, or trace not compiled on monitors (`m_properties & 1 == 0`) |
| `summary.csv` missing PL rows | No AM/AIM/ASM in layout, or `device_counters` and `device_trace` both off |
| `pl_deadlock_detection` silent | No `ACCEL_DEADLOCK_DETECTOR` in layout |
| Guidance shows `NUM_MONITORS` trace enabled = 0 | Counters-only build — rebuild via **`vitis-pl-profile`** for trace |

## Tooling

**Vitis Analyzer** (Analysis view) is the preferred viewer for `summary.csv`,
`device_trace_*.csv`, and `xrt.run_summary`:

```bash
vitis_analyzer xrt.run_summary
# or: vitis -a xrt.run_summary
```

See **UG1702** *Working with the Analysis View* and **UG1700** *Generating and Opening
the Profile Summary Report* for Profile Summary navigation (CU utilization, kernel data
transfers). Use raw CSV analysis when the user has no Analyzer workflow.
