<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# AMD user guides and product documentation

Use these references when a user needs **more detail than this skill provides** — full
ini key tables, Analyzer UI walkthroughs, compile-time `--profile` pairing, platform
notes, or official hang-debug workflows. Prefer **en-US** doc links on
[docs.amd.com](https://docs.amd.com); XRT also mirrors the ini reference in its own docs.

**When to cite:** After giving a concrete answer (ini recipe, artifact to open, or next
step), add one or two guide links if the user is new to the flow, asks “where is this
documented?”, needs UI navigation, or is debugging a hang / trace loss at platform level.

**When not to cite:** Do not dump the full guide list in every reply — pick the **most
relevant** page for the question.

---

## Primary documents

| ID | Title | Use for |
|----|-------|---------|
| **UG1702** | *Vitis Accelerated Flow Reference* | Authoritative **`xrt.ini`** key list, **`xrt-smi`**, **`--profile`** options, Vitis Analyzer / Analysis view |
| **UG1700** | *Data Center Acceleration using Vitis* | End-to-end **Alveo / data center** profiling flow: link instrumentation + runtime ini + profile summary |
| **UG1701** | *Embedded Design Development Using Vitis* | **Edge / embedded** profiling and trace (note platform limits vs UG1700) |
| **UG1315** | *Vitis Guidance Messaging* | Meaning and documented **resolution** of CU stall categories ([Kernel Stall](https://docs.amd.com/r/en-US/Vitis-Guidance-Messaging-UG1315/Kernel-Stall): Intra-Kernel Dataflow, External Memory, Inter-Kernel Pipe) — the rule Run Guidance links to |
| **XRT docs** | [XRT documentation](https://xilinx.github.io/XRT/master/html/index.html) | **`debug-ip-status`**, **`dynamic-regions`**, debug FAQ patterns (CU hang, AXI deadlock) |
| **XD100 tutorial** | *Profiling Using PL Profile Monitors* | Hands-on PL profile build + **`xrt.ini`** + Analyzer example |

---

## Question → guide mapping

| User question theme | Point to | Why |
|---------------------|----------|-----|
| What **`xrt.ini`** keys exist? Valid values for **`device_trace`**, **`stall_trace`**, **`trace_buffer_size`**, **`power_profile`**? | [UG1702 — xrt.ini File](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/xrt.ini-File) | Complete Debug-group table and defaults |
| How do I enable PL profiling end-to-end (build + run)? | [UG1700 — Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profiling-in-Your-Application) | Links **`--profile`** at link time to runtime ini |
| What **`--profile.data` / `.exec` / `.stall` / `.trace_memory`** do (compile-time)? | [UG1702 — --profile Options](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/profile-Options) | Monitor IP added at link; pairs with this skill’s runtime flags → **`vitis-pl-profile`** |
| Why is my xclbin missing monitors / trace fabric? | UG1700 *Enabling Profile and Debug when Linking* + **`vitis-pl-profile`** | [UG1700 — Enabling Profile and Debug when Linking](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profile-and-Debug-when-Linking) |
| How do I open **`xrt.run_summary`** / **`summary.csv`** / timeline? | [UG1700 — Generating and Opening the Profile Summary Report](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Generating-and-Opening-the-Profile-Summary-Report) + [UG1702 — Working with the Analysis View (Vitis Analyzer)](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/Working-with-the-Analysis-View-Vitis-Analyzer) | Profile Summary tables, **`vitis_analyzer`** / **`vitis -a`** |
| Kernel overlap, CU utilization, kernel data transfers in UI | UG1700 Profile Summary + [XD100 — Profiling Using PL Profile Monitors](https://docs.amd.com/r/en-US/Vitis-Tutorials-AI-Engine-Development/Profiling-Using-PL-Profile-Monitors) | Compute Unit Utilization, Kernel Data Transfers tabs |
| Live hang — which CU is stuck? Counter snapshot without re-run? | [UG1700 — Using the xrt-smi Utility](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Using-the-xrt-smi-Utility) + [UG1702 — xrt-smi Utility](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/xrt-smi-Utility) + [XRT Debug FAQ](https://xilinx.github.io/XRT/master/html/debug-faq.html) | **`debug-ip-status`**, **`dynamic-regions`**; CU START/BUSY patterns |
| **`power_profile`** on Alveo | UG1700 *Enabling Profiling* + UG1702 *xrt.ini* | **`power_profile_<device>.csv`**; UG1702 notes **not supported on embedded or AWS** |
| Edge / Versal embedded PL trace | [UG1701 — Enabling Trace in Your Application](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Enabling-Trace-in-Your-Application) | Embedded profiling flow; do **not** recommend **`power_profile`** on edge |
| Host + PL correlation, unified timeline | UG1702 *xrt.ini* (`enable_system_timeline` where applicable) + **`vitis-host-profile`** | System timeline is runtime ini; AIE trace prerequisite documented in UG1702 |
| AIE + PL in same design | UG1702 *--profile* (discourage **`--profile.aie`**) → **`vitis-aie-profile`** / UG1076 | PL runtime skill stays PL-focused |

---

## Key pages (direct links)

### UG1702 — Vitis Accelerated Flow Reference

- [xrt.ini File](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/xrt.ini-File) — **`device_trace`**, **`device_counters`**, **`stall_trace`**, **`trace_buffer_size`**, **`power_profile`**, etc.
- [--profile Options](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/profile-Options) — compile-time counterpart (hand off to **`vitis-pl-profile`**)
- [xrt-smi Utility](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/xrt-smi-Utility) — full command reference; links to [XRT xrt-smi HTML](https://xilinx.github.io/XRT/master/html/xrt-smi.html)
- [Working with the Analysis View (Vitis Analyzer)](https://docs.amd.com/r/en-US/ug1702-vitis-accelerated-reference/Working-with-the-Analysis-View-Vitis-Analyzer) — **`vitis_analyzer`**, **`vitis -a`**, Profile Summary navigation

### UG1700 — Data Center Acceleration using Vitis

- [Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profiling-in-Your-Application) — runtime ini examples (`device_trace=fine`, `stall_trace=all`, `power_profile=true`)
- [Enabling Profile and Debug when Linking](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Enabling-Profile-and-Debug-when-Linking) — why monitors must exist in xclbin before ini helps
- [Generating and Opening the Profile Summary Report](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Generating-and-Opening-the-Profile-Summary-Report) — artifact → Analyzer workflow
- [Using the xrt-smi Utility](https://docs.amd.com/r/en-US/ug1700-vitis-accelerated-data-center/Using-the-xrt-smi-Utility) — **`examine -r debug-ip-status`**, **`dynamic-regions`**

### UG1701 — Embedded (edge)

- [Enabling Profiling in Your Application](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Enabling-Profiling-in-Your-Application)
- [Enabling Trace in Your Application](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Enabling-Trace-in-Your-Application)
- [Using the xrt-smi Utility](https://docs.amd.com/r/en-US/ug1701-vitis-accelerated-embedded/Using-the-xrt-smi-Utility)

### Tutorials and XRT

- [Profiling Using PL Profile Monitors (XD100)](https://docs.amd.com/r/en-US/Vitis-Tutorials-AI-Engine-Development/Profiling-Using-PL-Profile-Monitors) — `--profile.data`, **`xrt.ini`**, Analyzer CU / data-transfer views
- [XRT Debug FAQ](https://xilinx.github.io/XRT/master/html/debug-faq.html) — CU hang, AXI deadlock, **`dynamic-regions`** / **`debug-ip-status`** usage patterns
- [XRT xrt.ini (in-tree pointer)](https://xilinx.github.io/XRT/master/html/xrt_ini.html) — defers to UG1702 for the full key list

---

## Companion skills (not user guides)

| Topic | Skill |
|-------|-------|
| Add / fix PL monitors in xclbin | This skill — Workflow §3 (`--profile.*`) |
| Host trace, pipelining on host API | **`vitis-host-profile`** |
| AIE counters / event trace (compile + runtime) | **`vitis-aie-profile`** |
