---
name: vitis-pl-profile
description: >-
  Guides PL profiling and trace from compile time through runtime on Vitis / XRT designs. Covers
  v++ --profile.data, --profile.exec, --profile.stall, and --profile.trace_memory to instrument
  xclbin debug_ip_layout, xrt.ini device_trace and stall_trace flags, and interpretation of
  device_trace CSVs and summary.csv. Answers questions such as "Can the pipelining between my PL
  kernels work better?", "Are my PL kernels receiving and sending the right amount of data?", or
  "My system is hanging, which PL kernel could be the problem?" Inspects xclbin via xclbinutil
  before recommending runtime ini. For AIE or host-only profiling, use vitis-aie-profile or
  vitis-host-profile instead. Load it before answering even if the answer seems familiar: it holds
  version-checked option names, hw vs hw_emu rules, and documented stall resolutions that general
  knowledge gets wrong. Not for general Vitis development without a profiling or trace question
  (e.g. writing HLS or ADF code, packaging, creating platforms).
license: MIT
compatibility: >-
  Vitis / XRT with hardware or hw_emu xclbin (sw_emu not supported). PL profiling requires
  debug_ip_layout from v++ --profile.* at link; trace_memory name is platform-specific.
metadata:
  stage: beta
  author: AMD AIG
  version: "1.0.0"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vitis PL profiling and trace

Help users **profile PL kernels** end to end: add **compile-time** profiling IP during
Vitis link, verify **`DEBUG_IP_LAYOUT`**, configure **runtime** `xrt.ini`, and
**interpret** PL profiling artifacts (CSVs, summary, live status).

**Companion skills**

| Skill | Scope |
|-------|-------|
| **`vitis-host-profile`** | Host-side trace and host↔device pipelining; correlates with PL `device_trace_*.csv`. |
| **`vitis-aie-profile`** | AIE compile/runtime profiling and event trace — preferred over `--profile.aie`. |

## When to apply this skill

- User asks about **PL kernel pipelining**, **overlap**, or **stalls** between kernels.
- User asks whether PL kernels are **receiving / sending the right amount of data**.
- User reports a **hang** or **deadlock** and wants to know **which PL kernel** is stuck.
- User's xclbin **lacks** `DEBUG_IP_LAYOUT` entries (or has counters-only monitors when
  trace is needed).
- User asks to **add profiling during Vitis build** before profiling a design.
- User wants to **enable PL device trace** or **PL counters** at runtime.
- User asks about **card power usage** during a run on **Alveo** (not edge).
- User shares an **xclbin**, **Makefile**, **link config**, or **`v++` command**.
- User shares **PL profiling CSVs** from a run with `Debug.device_trace` enabled.

**Run targets:** **`sw_emu` (`-t sw_emu`) is no longer supported** — new designs
**cannot** be built targeting software emulation. Target **`hw`** for production profiling;
**`hw_emu`** only where an existing emulation flow still applies.

## Core concepts

### Compile time (Vitis + xclbin)

1. **Link phase inserts IP** — `--profile.data`, `--profile.exec`, and trace fabric are
   applied during **`v++ --link`**. Monitors appear in **`DEBUG_IP_LAYOUT`**.

2. **Stall needs compile + link** — `--profile.stall` on **both** kernel compile and link;
   HLS: `syn.rtl.kernel_profile=1` in `hls_config.cfg` (UG1702).

3. **Counters vs trace** — Profile options accept `:counters` or `:all` (default **`all`**).
   **`counters`** = summary counters only; **`all`** = trace + counters (`m_properties & 0x1`).

4. **`profile.data` trace includes execution trace** — Do **not** combine
   **`--profile.exec`** with **`--profile.data … :all`** — redundant. Use **`profile.exec`**
   only for execution trace **without** data port monitors.

5. **`profile.trace_memory`** — **hw** only; platform-specific memory name (link fails if wrong).

6. **Config mapping** — `--profile.<option> <value>` → `[profile]` section `<option>=<value>`.

   **CU scoping** — the `<cu>` field is the CU's instance name (`connectivity.nk` names, or the
   defaults `<kernel>_1`, `<kernel>_2`, …). If the user names a CU (e.g. "cu2 of kernel k1"), use
   it as written: `--profile.stall k1:cu2` (UG1702's example). Don't swap in a guessed default.

7. **Discouraged** — **`--profile.aie`** → use **`vitis-aie-profile`**; **`--profile.memory`**
   only for isolated aggregate DDR tests.

Details: `references/profile_options.md`, `references/vpp_workflow.md`.

### Runtime (xrt.ini)

1. **Build vs runtime** — `xrt.ini` controls what XRT **records**. Missing debug IP cannot
   be created at runtime — inspect xclbin first (workflow §2).

2. **Key debug IP types** — AM, AIM, ASM, TRACE_S2MM, funnel/FIFO, ACCEL_DEADLOCK_DETECTOR.
   See `references/debug_ip_layout.md`.

3. **Primary `[Debug]` keys** — `device_trace` (`off`|`fine`|`coarse`|`accel` — **never `all`**),
   `device_counters` (only when `device_trace=off`), `stall_trace`, `pl_deadlock_detection`,
   `profiling_directory`, `power_profile` (Alveo only).

4. **`device_trace` vs `device_counters`** — When `device_trace` is `fine`/`coarse`/`accel`,
   **omit** `device_counters=true` — counters flow into **`summary.csv`** automatically.

5. **Host correlation** — PL `device_trace_*.csv` timestamps align with **host** trace
   CSVs (`native_trace.csv`, `opencl_trace.csv`, …). A **unified host + PL** timeline
   needs both this skill (`device_trace` + profile-enabled xclbin) and
   **`vitis-host-profile`** (host trace ini such as `native_xrt_trace` or
   `opencl_trace`) in the same run. For **host + PL + AIE**, add **`vitis-aie-profile`**
   (`aie_trace` + `enable_system_timeline`). Warn about trace-offload traffic in host
   CSVs when continuous offload is enabled.

Details: `references/parameter_outline.md`.

## Workflow

### 1. Understand the design and goal

- List **PL kernels**, **CUs**, connectivity (memory ports, streams).
- Identify **build flow**: Makefile `v++`, config-driven link, or Vitis IDE System Project.

**Ask before recommending, when the request is ambiguous.** Do **not** silently pick one
interpretation and hand back a specific recipe when any of the following is unclear:

- **Domain** — is this **AIE**, **PL**, or **host-side** profiling (or a combined view)?
  "Enable profiling" / "profile my kernel" / "profile all kernels and compute units" alone
  do not say which — **"kernel" and "compute unit" are not PL-exclusive terms** (AIE kernels
  and graphs use the same words), so mentioning them does **not** by itself resolve the
  domain question. Confirm PL explicitly rather than inferring it from generic wording.
- **Scope** — "profiling my **Vitis HLS** kernel" is ambiguous between **HLS-level**
  C-simulation/synthesis profiling (a separate HLS toolchain workflow, **not** covered by
  this skill) and **system-level** `v++`/XRT profiling of a kernel that happens to be
  written in HLS (which this skill **does** cover). Ask which one is meant before
  answering as if only one exists.
- **Existing build state** — is there already an instrumented xclbin to inspect, or is
  this starting from scratch?

**Even when the user explicitly asks to skip clarification** ("just give me the config",
"I don't know, just pick something") — **still ask first.** Acknowledge the request in one
short clause, then ask the specific question(s) that are actually blocking a correct
answer. A response that gives a full, ready-to-run recipe and merely *notes* the ambiguity
in a caveat (e.g. "you'll need to supply your platform's real memory name here") is **not**
a clarifying question and is the wrong shape of answer, even if the recipe itself is
technically correct. It is fine to sketch the likely default in the same breath (e.g. "a
reasonable default for all PL kernels/CUs is `--profile.data all:all:all` — but first: is
this AIE, PL, or host profiling?"), but the message must still end on the open question,
not on a finished, directly-runnable command block.

**Phrase every clarifying question as an actual interrogative sentence ending in a literal
`?`** — e.g. "Is this AIE, PL, or host-side profiling?" or "Do you already have a linked
xclbin, or are we starting from scratch?". Do **not** phrase it as an imperative instead
("Tell me the platform and I'll fill in the recipe", "Let me know if you need X") — that
reads as a footnote on a decision you already made, not as a real question the user needs
to answer before you can proceed. When more than one thing is unclear, lead with the
**domain** question first (AIE vs PL vs host) — resolve that before scope/build-state,
since it determines which skill even applies. Once domain/scope/build-state are clear
(from the user's original message or their answer), proceed with the steps below.

- Map the user's question:

| Question theme | Build need | Runtime focus |
|----------------|------------|---------------|
| **Pipelining between PL kernels** | AM + trace fabric | `device_trace=fine`, `stall_trace=all` |
| **Right amount of data in/out** | AIM/ASM | `device_trace=fine` or counters-only |
| **Hang / which kernel** | AM + stall; optional ADD | **`hw` only:** `device_trace=fine`, `stall_trace=all`, `pl_deadlock_detection`. **`hw_emu` instead:** `compiler.deadlockDetection=true` at kernel **compile** (`v++ --compile`; UG1702) — not an `xrt.ini` runtime key at all |
| **"High X stalls — what does it mean / how do I fix it?"** (Intra-Kernel Dataflow, External Memory, Inter-Kernel Pipe) | — (interpretation) | Explain the category and cite **UG1315 Kernel Stall**'s documented resolution — see §6 |
| **Gaps between kernel executions** on the timeline | — (interpretation) | CU idle between launches → **host side** (launch/enqueue overhead, transfers not overlapped with compute); not kernel-internal stalls — see §6 |

### 2. Inspect xclbin `debug_ip_layout`

```bash
xclbinutil --input <path/to/design.xclbin> \
  --dump-section DEBUG_IP_LAYOUT:JSON:debug_ip_layout.json
```

| Finding | Meaning | Next step |
|---------|---------|-----------|
| Section absent / `m_count` = 0 | No profiling IP | Continue to **§3** (compile-time instrumentation) |
| AM/AIM/ASM, no TRACE_S2MM | Counters-only build | `device_counters=true` works; timeline needs **§3** rebuild with trace |
| Monitors present, no `m_properties` bit 0 | Counters only | `device_trace` timelines unavailable — rebuild with `:all` |
| Layout matches goal | Ready for runtime | Continue to **§5** |

Interpretation: `references/debug_ip_layout.md`.

### 3. Add compile-time instrumentation (when required)

| Question theme | Link (`v++ -l`) | Compile (`v++ -c` / HLS) |
|----------------|-----------------|--------------------------|
| **Pipelining** | `--profile.data all:all:all` + `--profile.stall all:all` | `--profile.stall all:all` + `syn.rtl.kernel_profile=1` |
| **Data volumes** | `--profile.data all:all:all` (or `:counters`) | — |
| **Hang** | `--profile.data all:all:all` + `--profile.stall all:all` | stall + HLS profile |
| **Gaps between executions** | A gap **between** CU executions = CU idle = host side (enqueue/launch overhead, transfer not overlapped, serialized dependency) per **UG1700** *Best Practices for Host Programming*; kernel-internal stalls appear **inside** an execution interval. Confirm with host trace (`vitis-host-profile`). |
| **Execution trace only** | `--profile.exec all:all` | — |

**Recommended full PL visibility:**

```bash
v++ -l -t hw --platform <platform> \
  --profile.data all:all:all \
  --profile.stall all:all \
  --profile.trace_memory DDR[0] \
  -o binary_container.xclbin <kernel.xo ...>
```

Replace **`DDR[0]`** with a **platform-valid** memory name. Do **not** add `--profile.exec`
when using `--profile.data … :all`.

**Config-file equivalent** (`link.cfg`):

```ini
[profile]
data=all:all:all
stall=all:all
trace_memory=DDR[0]
```

**Kernel compile** with stall:

```ini
[profile]
stall=all:all

[hls]
syn.rtl.kernel_profile=1
```

### 4. Verify the xclbin

After link, confirm instrumentation before recommending a profiled run:

```bash
xclbinutil --input binary_container.xclbin \
  --dump-section DEBUG_IP_LAYOUT:JSON:debug_ip_layout.json
```

Checklist (`references/verification.md`):

- [ ] `m_count` > 0
- [ ] `ACCEL_MONITOR` when exec or data trace requested
- [ ] `AXI_MM_MONITOR` / `AXI_STREAM_MONITOR` when `--profile.data` used
- [ ] `TRACE_S2MM` (+ funnel/FIFO) when trace (`:all`) requested
- [ ] AM `m_properties` bit 0 (trace), bit 2 (stall) as expected

If verification fails, adjust profile options and **re-link** (and **re-compile** for stall).

### 5. Configure minimal runtime `xrt.ini`

| Question theme | Recommended `[Debug]` flags |
|----------------|------------------------------|
| **Pipelining** | `device_trace=fine`, `stall_trace=all` |
| **Data volumes** | `device_trace=fine` (or `device_counters=true`, `device_trace=off`) |
| **Hang** | **`hw` only** — `device_trace=fine`, `stall_trace=all`, `pl_deadlock_detection=true` (if ADD present). **`hw_emu` deadlocks use `compiler.deadlockDetection=true` on the kernel compile (`v++ --compile`) instead — see below, not this table.** |

**Full PL visibility example (hardware only):**

```ini
[Debug]
device_trace=fine
stall_trace=all
pl_deadlock_detection=true
profiling_directory=/path/to/profile_output
```

**`hw_emu` deadlock/hang diagnosis is a different, build-time mechanism — not an
`xrt.ini` key at all.** None of `device_trace`, `stall_trace`, `pl_deadlock_detection`,
or any other `[Debug]` runtime key diagnoses a hang in `hw_emu`; `--profile.trace_memory`
is also **hardware only** and does not apply. For an `hw_emu` hang/deadlock, enable
`compiler.deadlockDetection` when **compiling** the kernel — UG1702 (*--advanced Options*)
lists it as applying to `v++ --compile` (and Vitis HLS `config_export`), Boolean, default
false. Not a link option:

```bash
v++ --compile -t hw_emu ... --advanced.param compiler.deadlockDetection=true
```

or in the kernel's compile config file (`v++ -c --config compile.cfg`):

```ini
[advanced]
param=compiler.deadlockDetection=true
```

Emulation then stops with a deadlock message identifying the stuck stream/port — no
runtime ini changes needed or applicable.

**Counters-only example:**

```ini
[Debug]
device_trace=off
device_counters=true
profiling_directory=/path/to/profile_output
```

Tune `trace_buffer_size`, `continuous_trace`, etc. when runs are long — see
`references/parameter_outline.md`.

**Live hang diagnosis** (device loaded): `xrt-smi examine --device <bdf> --report
dynamic-regions` and `--report debug-ip-status`.

### 6. After the run — artifacts and answers

Use `references/artifact_map.md`. Open `xrt.run_summary` in Vitis Analyzer.

| Question | Where to look |
|----------|---------------|
| **Pipelining** | `device_trace_*.csv`, `summary.csv` — overlapping AM intervals, stall events |
| **Data volumes** | `summary.csv`, `debug-ip-status` — AIM bytes, ASM packet counts |
| **Hang** | `dynamic-regions`, `debug-ip-status`, trace timeline, ADD |
| **Stall percentages** (Profile Summary) | Meaning + documented resolution per category: **UG1315 Kernel Stall** — Intra-Kernel Dataflow → hw_emu detail + increase internal FIFO sizing; External Memory → port contention, increase number of memory ports; Inter-Kernel Pipe → increase inter-kernel FIFO depth / speed up the slower side. Run Guidance flags kernel stall > 50%. **Kernel Internals** detail is **hw_emu** only (`[Emulation] debug_mode=batch`). See `references/artifact_map.md`. |

When the user wants **official documentation**, **Analyzer UI steps**, or **platform-level**
detail beyond a minimal ini recipe, cite **`references/user_guides.md`** (typically
**UG1702** for `xrt.ini` and `--profile` options, **UG1700** for profiling flow and
summary, **UG1701** on edge, **XRT Debug FAQ** for hang patterns). Add one or two
relevant links — do not paste the full guide list in every answer.

## Guardrails

- **Do not silently pick one interpretation of an ambiguous request.** If domain (AIE vs
  PL vs host), scope (HLS-level vs system-level profiling), or existing build state is
  unclear, ask a brief clarifying question first — ending in a literal `?` — even if the
  user asks to "just give me the config." Noting the ambiguity as a caveat inside an
  otherwise complete, ready-to-run recipe does **not** count as asking. See Workflow §1.
- **Always** mention **area, timing, and compile time** impact of `:all` vs `:counters`.
- **`--profile.stall` on compile and link** — missing compile-phase stall empties stall trace.
- **`--profile.trace_memory`** — **hw** only; platform-specific name; never guess.
- **`hw_emu` hang/deadlock diagnosis** — use `compiler.deadlockDetection=true` when
  **compiling** the kernel (`v++ --compile`, per UG1702 — not the link config), **never** the runtime `[Debug]` keys (`device_trace`, `stall_trace`,
  `pl_deadlock_detection`) — those, and `--profile.trace_memory`, are **hardware only**
  and do not diagnose an `hw_emu` hang.
- **Do not** set runtime ini as substitute for missing compile-time IP.
- **Pipelining / overlap questions always include stall profiling** ("does CU B start while
  CU A is still streaming?", "are my kernels overlapping?"): `--profile.stall all:all` on the
  kernel compile **and** the link (HLS: `syn.rtl.kernel_profile=1`) plus `stall_trace=all` at
  runtime, alongside `--profile.data … :all` and `device_trace=fine`. Keep it in the recipe even
  when the main fix is something else (e.g. a counters-only xclbin or an invalid ini value).
- **Lead with the diagnosis the user's numbers support** (e.g. a small average transfer size
  against a low bandwidth efficiency, or a stall category percentage) — do not replace the
  answer with a demand for more data; suggest follow-up measurements as confirmation only.
- **Stall fixes come from UG1315** (Kernel Stall rule) for the diagnosed category — do not
  invent other fixes and present them as documented; label extra advice as general HLS
  guidance. Do not call real Analyzer terms (Kernel Internals, Intra-Kernel Dataflow
  Stalls) unknown or nonexistent.
- **`device_trace`** — only `off`, `fine`, `coarse`, or `accel`; **never `all`**.
- **Do not** set `device_counters=true` when `device_trace` is enabled.
- **Do not** combine `--profile.exec` with `--profile.data … :all`.
- **Do not recommend `--profile.aie`** — use **`vitis-aie-profile`**.
- **`power_profile`** — Alveo only; not on edge boards.
- Prefer **smallest** profile scope that answers the question.
- **`sw_emu` is not supported** for new designs.

## References

### In-skill references

- [v++ --profile options](references/profile_options.md)
- [Build flows and config files](references/vpp_workflow.md)
- [Post-build verification](references/verification.md)
- [Inspecting debug_ip_layout](references/debug_ip_layout.md)
- [PL runtime ini keys](references/parameter_outline.md)
- [PL artifacts and analysis](references/artifact_map.md)
- [AMD user guides — when to cite](references/user_guides.md)

### User guides (cite when appropriate)

When the user needs official documentation beyond a minimal ini recipe — full key tables,
`--profile` compile options, or Analyzer hang-debug steps — point to
**`references/user_guides.md`** and include **one or two** relevant links.

| Topic | Primary guide |
|-------|----------------|
| **`xrt.ini`** PL keys (`device_trace`, `stall_trace`, buffers) | **UG1702** — *xrt.ini File* |
| **`--profile.data` / `.exec` / `.stall` / `.trace_memory`** | **UG1702** — *--profile Options* |
| Profiling flow + Profile Summary | **UG1700** — *Enabling Profiling in Your Application* |
| Why xclbin lacks monitors / trace fabric | **UG1700** — *Enabling Profile and Debug when Linking* |
| Live hang / `debug-ip-status` / `dynamic-regions` | **UG1700** + **XRT Debug FAQ** |
| Host + PL correlation | **`vitis-host-profile`** + UG1702 *xrt.ini* |
| Edge / embedded PL trace | **UG1701** — *Enabling Trace in Your Application* |

Direct URLs and a question→guide table: [references/user_guides.md](references/user_guides.md).
