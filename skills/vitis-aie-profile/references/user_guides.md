<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# AMD user guides and product documentation

Use these references when a user needs **more detail than this skill provides** — full
AIE profile/trace metric tables, event-trace compile options, tile discovery in Vitis
Analyzer, or official troubleshooting for trace routing failures. Prefer **en-US** doc
links on [docs.amd.com](https://docs.amd.com).

**When to cite:** After giving a concrete answer (ini recipe, compile flag, artifact to
open, or counters-vs-trace decision), add one or two guide links if the user is new to
AIE profiling, asks “where is this documented?”, needs Analyzer UI steps, or is building
a unified host + PL + AIE timeline.

**When not to cite:** Do not dump the full guide list in every reply — pick the **most
relevant** page for the question.

---

## Primary documents

| ID | Title | Use for |
|----|-------|---------|
| **UG1076** | *Versal Adaptive SoC AIE-ML and AIE Architecture and Programming* | **Authoritative** AIE profiling counters, event trace, metric syntax, routing, troubleshooting |
| **UG1702** | *Vitis Accelerated Flow Reference* | **`xrt.ini`** keys (`aie_profile`, `aie_trace`, `[AIE_profile_settings]`, `[AIE_trace_settings]`) |
| **UG1700** | *Data Center Acceleration using Vitis* | End-to-end profiling flow, profile summary, Analyzer overview |
| **UG1701** | *Embedded Design Development Using Vitis* | **Edge / embedded** AIE profiling and trace |
| **XRT docs** | [XRT documentation](https://xilinx.github.io/XRT/master/html/index.html) | Runtime profiling plugins, `xrt.ini` mirror |
| **UG1315** | *Vitis Guidance Messaging* | Documented **resolution** for each AIE stall type ([AIE_STALL](https://docs.amd.com/r/en-US/Vitis-Guidance-Messaging-UG1315/AIE_STALL), [AIE_MEMORY_STALLS](https://docs.amd.com/r/en-US/Vitis-Guidance-Messaging-UG1315/AIE_MEMORY_STALLS)) — cite when the user asks how to fix a stall |
| **UG1079** / **UG1603** | *AI Engine (AIE-ML) Kernel and Graph Programming Guide* | Kernel-code optimization (vectorization, software pipelining, loop pragmas) when cycles are compute-bound |

---

## Question → guide mapping

| User question theme | Point to | Why |
|---------------------|----------|-----|
| What **`aie_profile`** / **`aie_trace`** ini keys and metric strings exist? | [UG1702 — xrt.ini File](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/xrt.ini-File) + **UG1076** metric tables | Complete Debug-group keys; UG1076 defines metric names and tile syntax |
| Counters vs event trace — which do I need? | This skill — `references/counters_vs_trace.md` + **UG1076** | Decision table; UG1076 explains trace vs profile counter models |
| How do I add **event trace at compile time**? | **UG1076** — Event Trace Build Flow + this skill Workflow §4 | `event-trace=runtime`, GMIO vs PLIO, `num-trace-streams` |
| Trace compile / link **fails** (routing, NMU) | **UG1076** — Troubleshooting Event Trace + `references/compile_failure_recovery.md` | Bounding boxes, stream reduction, partial routing |
| **What is the latency** of my AIE kernel? | **UG1076** + `references/methodologies.md` | `interface_tile_latency` profile metrics |
| **Where / when** are transfers stalling? | **UG1076** stall/DMA metrics + trace timeline sections | Profile `stalls` / `conflicts`; trace `all_stalls` for timing |
| Tile **throughput** / utilization heat map | **UG1076** profile metrics | `heat_map`, `*_throughputs`, `stream_put_get` |
| Open **`xrt.run_summary`** / AIE Analyzer views | [UG1700 — Generating and Opening the Profile Summary Report](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Generating-and-Opening-the-Profile-Summary-Report) + [UG1702 — Working with the Analysis View (Vitis Analyzer)](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/Working-with-the-Analysis-View-Vitis-Analyzer) | Set AI Engine Compile Directory to `Work/*.aiecompile_summary` |
| **Unified host + PL + AIE** timeline | **UG1076** System Level Trace + **UG1702** `enable_system_timeline` + **`vitis-host-profile`** + **`vitis-pl-profile`** | Host trace ini, PL `device_trace` xclbin, AIE `aie_trace` + runtime alignment |
| Missing **PL** activity on unified timeline | **`vitis-pl-profile`** + [UG1700 — Enabling Profile and Debug when Linking](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profile-and-Debug-when-Linking) | PL monitors must exist in xclbin |
| Missing **host** trace on unified timeline | **`vitis-host-profile`** + UG1702 *xrt.ini* | `native_xrt_trace` / `opencl_trace` as appropriate |
| Edge / embedded AIE profiling | [UG1701 — Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Enabling-Profiling-in-Your-Application) | Platform limits; **`sw_emu`** not supported for new builds |
| Hands-on AIE profile example | [XD100 — System Profiling](https://docs.amd.com/r/en-US/Vitis-Tutorials-AI-Engine-Development/System-Profiling) (tutorial series) | `aie_profile` XRT flow examples cited in `counters_vs_trace.md` |

---

## Key pages (direct links)

### UG1702 — Vitis Accelerated Flow Reference

- [xrt.ini File](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/xrt.ini-File) — **`aie_profile`**, **`aie_trace`**, `[AIE_profile_settings]`, `[AIE_trace_settings]`, **`enable_system_timeline`**
- [Working with the Analysis View (Vitis Analyzer)](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/Working-with-the-Analysis-View-Vitis-Analyzer) — AIE trace and profile views
- [--profile Options](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/profile-Options) — PL link instrumentation (hand off to **`vitis-pl-profile`**; discourage **`--profile.aie`**)

### UG1700 — Data Center Acceleration using Vitis

- [Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profiling-in-Your-Application) — runtime profiling overview
- [Enabling Profile and Debug when Linking](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profile-and-Debug-when-Linking) — why xclbin must include trace support
- [Generating and Opening the Profile Summary Report](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Generating-and-Opening-the-Profile-Summary-Report) — **`xrt.run_summary`** workflow

### UG1701 — Embedded (edge)

- [Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Enabling-Profiling-in-Your-Application)

### UG1076 — AIE architecture and programming

- Primary reference for **profile metric sets**, **event trace metrics**, **XRT flow**,
  **event trace routing**, and **troubleshooting** — obtain the current UG1076 PDF/HTML from
  [docs.amd.com](https://docs.amd.com) for your Versal / AIE-ML generation.

### XRT

- [XRT xrt.ini (in-tree pointer)](https://xilinx.github.io/XRT/master/html/xrt_ini.html) — defers to UG1702 for the full key list
- [XRT profiling overview](https://xilinx.github.io/XRT/master/html/profiling.html) — XDP capture model

---

## Companion skills (not user guides)

| Topic | Skill |
|-------|-------|
| AIE counters / event trace (compile + runtime) | This skill — Workflow + `references/parameter_outline.md` |
| PL `device_trace`, `debug_ip_layout`, `--profile.*` at link | **`vitis-pl-profile`** |
| Host trace, H2D/D2H, host↔device pipelining | **`vitis-host-profile`** |
