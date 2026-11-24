<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# AMD user guides and product documentation

Use these references when a user needs **more detail than this skill provides** — full
**`xrt.ini`** host-trace key tables, Vitis Analyzer timeline navigation, OpenCL vs native
XRT trace selection, or official guidance on low-overhead host profiling. Prefer
**en-US** doc links on [docs.amd.com](https://docs.amd.com); XRT also mirrors the ini
reference in its own docs.

**When to cite:** After giving a concrete answer (ini recipe, CSV to open, or API-family
choice), add one or two guide links if the user is new to host profiling, asks “where is
this documented?”, needs Analyzer UI steps, or is correlating host trace with PL device
trace.

**When not to cite:** Do not dump the full guide list in every reply — pick the **most
relevant** page for the question.

---

## Primary documents

| ID | Title | Use for |
|----|-------|---------|
| **UG1702** | *Vitis Accelerated Flow Reference* | Authoritative **`xrt.ini`** key list (`host_trace`, `opencl_trace`, `native_xrt_trace`, `lop_trace`, buffers, continuous trace) |
| **UG1700** | *Data Center Acceleration using Vitis* | End-to-end profiling flow, **low-overhead OpenCL profiling** (`lop_trace`), profile summary / Analyzer |
| **UG1701** | *Embedded Design Development Using Vitis* | **Edge / embedded** host trace and profiling (platform limits vs data-center UG1700) |
| **XRT docs** | [XRT documentation](https://xilinx.github.io/XRT/master/html/index.html) | **`xrt.ini`** mirror, profiling plugins, debug patterns |

---

## Question → guide mapping

| User question theme | Point to | Why |
|---------------------|----------|-----|
| What **`xrt.ini`** host keys exist? **`host_trace`** vs **`opencl_trace`** vs **`native_xrt_trace`**? | [UG1702 — xrt.ini File](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/xrt.ini-File) | Complete Debug-group table and defaults |
| How do I enable **low-overhead OpenCL** host trace (`lop_trace`)? | [UG1700 — Enabling Low Overhead Profiling](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Low-Overhead-Profiling) | **`lop_trace.csv`**, overhead tradeoffs, OpenCL-only scope |
| How do I enable host / device profiling end-to-end? | [UG1700 — Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profiling-in-Your-Application) | Runtime ini examples alongside device trace |
| **H2D / D2H** timing, buffer transfers, host API spans | UG1702 *xrt.ini* + this skill’s `references/artifact_map.md` | Host trace CSV columns; pair with PL trace when needed |
| **Parallel kernels** / queue overlap | UG1700 Profile Summary + [UG1702 — Working with the Analysis View (Vitis Analyzer)](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/Working-with-the-Analysis-View-Vitis-Analyzer) | Timeline / trace views for host and device activity |
| **Host + device pipelining** — are both busy? | UG1700 *Enabling Profiling* + Analyzer trace view | Correlate host submits with device execution windows |
| Open **`xrt.run_summary`** / host trace timeline | [UG1700 — Generating and Opening the Profile Summary Report](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Generating-and-Opening-the-Profile-Summary-Report) | Artifact → Analyzer workflow |
| **User events / user ranges** in host code | XRT API docs + UG1702 *xrt.ini* (host trace must be on) | `xrt::profile::user_range` shares XDP host timebase |
| Host + **PL** correlation (aligned timestamps) | UG1702 *xrt.ini* + **`vitis-pl-profile`** | `device_trace` requires profile-enabled xclbin; host/PL CSV timestamps align |
| Host + **PL + AIE** unified timeline | UG1702 *xrt.ini* (`enable_system_timeline`) + **`vitis-aie-profile`** | AIE alignment is runtime ini + post-processing |
| Missing **PL device trace** after enabling ini | **`vitis-pl-profile`** + [UG1700 — Enabling Profile and Debug when Linking](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profile-and-Debug-when-Linking) | Monitors must exist in xclbin before runtime ini helps |
| Edge / embedded host trace | [UG1701 — Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Enabling-Profiling-in-Your-Application) + [UG1701 — Enabling Trace in Your Application](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Enabling-Trace-in-Your-Application) | Embedded flow; note **`sw_emu`** not supported for new builds |

---

## Key pages (direct links)

### UG1702 — Vitis Accelerated Flow Reference

- [xrt.ini File](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/xrt.ini-File) — **`host_trace`**, **`opencl_trace`**, **`native_xrt_trace`**, **`lop_trace`**, **`xrt_trace`**, **`profiling_directory`**, **`continuous_trace`**, trace buffer keys
- [Working with the Analysis View (Vitis Analyzer)](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/Working-with-the-Analysis-View-Vitis-Analyzer) — **`vitis_analyzer`**, **`vitis -a`**, host/device timeline navigation
- [--profile Options](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/profile-Options) — compile-time PL instrumentation (hand off to **`vitis-pl-profile`** when device trace is missing)

### UG1700 — Data Center Acceleration using Vitis

- [Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profiling-in-Your-Application) — runtime profiling overview (host + device)
- [Enabling Low Overhead Profiling](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Low-Overhead-Profiling) — **`lop_trace`** for OpenCL hosts
- [Generating and Opening the Profile Summary Report](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Generating-and-Opening-the-Profile-Summary-Report) — **`xrt.run_summary`**, summary tables

### UG1701 — Embedded (edge)

- [Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Enabling-Profiling-in-Your-Application)
- [Enabling Trace in Your Application](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Enabling-Trace-in-Your-Application)

### XRT

- [XRT xrt.ini (in-tree pointer)](https://xilinx.github.io/XRT/master/html/xrt_ini.html) — defers to UG1702 for the full key list
- [XRT profiling overview](https://xilinx.github.io/XRT/master/html/profiling.html) — XDP plugins and trace capture model

---

## Companion skills (not user guides)

| Topic | Skill |
|-------|-------|
| Host trace, pipelining, H2D/D2H analysis | This skill — Workflow + `references/parameter_outline.md` |
| PL `device_trace`, `debug_ip_layout`, compile-time monitors | **`vitis-pl-profile`** |
| AIE profiling / event trace, `enable_system_timeline` | **`vitis-aie-profile`** |
