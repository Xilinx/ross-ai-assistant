<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# v++ `--profile` options for PL

## Build targets

**`sw_emu` (`-t sw_emu`) is no longer supported** — new designs **cannot** be built
targeting software emulation. PL profile instrumentation guidance targets **`hw`**
(or **`hw_emu`** where legacy emulation flows still apply).

Authoritative product reference: AMD **UG1702** (*Vitis Accelerated Flow Reference*),
section **--profile Options**, and **UG1700** (*Data Center Acceleration using Vitis*),
*Enabling Profile and Debug when Linking*.

`v++` profile switches instrument the linked design with monitor IP that XRT later
drives via `debug_ip_layout`. Runtime ini is documented in **`vitis-pl-profile`**.

## Option summary

| Option | Phase | Adds (typical) | Answers |
|--------|-------|----------------|---------|
| `--profile.data` | **Link** | `AXI_MM_MONITOR`, `AXI_STREAM_MONITOR` on kernel ports; **data trace (`:all`) also includes execution trace (AM)** | Bytes / beats on memory and stream interfaces; CU exec when trace enabled |
| `--profile.exec` | **Link** | `ACCEL_MONITOR` on CUs | CU execution time, occupancy, kernel overlap — use **only** when execution trace is needed **without** `--profile.data … :all` |
| `--profile.stall` | **Compile + link** | Stall ports on AM | Pipe / dataflow / memory stall events in trace |
| `--profile.trace_memory` | **Link** (`-t hw` only) | Trace buffer backing resource | **Platform-specific** memory name — link fails if not on platform; see § `--profile.trace_memory` |
| `--profile.aie` | **Link** | Monitors on **AIE PLIO** connections in PL | **Discourage** — see § `--profile.aie`; prefer AIE profiling / event trace |
| `--profile.memory` | **Link** | Counters on **DDR memory controller** | **Rare** — aggregate DDR traffic only; see § `--profile.memory` |

**Important:** Using `--profile` in `v++` requires corresponding runtime options in
`xrt.ini` (`device_trace`, `device_counters`, etc.) — see **`vitis-pl-profile`**.

## Config file format (`[profile]` section)

Profile options may be specified on the **`v++` command line** or in a **config file**
(`.cfg`) passed with `--config` (e.g. `link.cfg`, `binary_container-link.cfg`).

**Each command-line option follows the same pattern:**

| Command line | Config file |
|--------------|-------------|
| `--profile.<option> <value>` | `[profile]` → `<option>=<value>` |

Drop the **`profile.`** prefix; put the remainder under a **`[profile]`** section header.

### Mapping table

| Command line | `[profile]` entry |
|--------------|-------------------|
| `--profile.data all:all:all` | `data=all:all:all` |
| `--profile.data my_kernel:my_cu:m_axi_gmem0:all` | `data=my_kernel:my_cu:m_axi_gmem0:all` |
| `--profile.exec all:all` | `exec=all:all` |
| `--profile.stall all:all` | `stall=all:all` |
| `--profile.trace_memory DDR[0]` | `trace_memory=DDR[0]` |
| `--profile.aie all` | `aie=all` |
| `--profile.aie DataIn1` | `aie=DataIn1` |
| `--profile.memory all` | `memory=all` |
| `--profile.memory <sptag>` | `memory=<sptag>` |

### Example link config

```ini
[profile]
data=all:all:all
stall=all:all
trace_memory=DDR[0]
```

```bash
v++ -l -t hw --platform <platform.xpfm> --config link.cfg -o design.xclbin *.xo
```

### Example kernel compile config (stall)

```ini
[profile]
stall=all:all

[hls]
syn.rtl.kernel_profile=1
```

Multiple `[profile]` keys can appear in one file; values merge per UG1702. Use the same
`<option>=<value>` syntax whether the option applies at **compile** or **link** — the
build phase is determined by which `v++ -c` / `v++ -l` invocation consumes the config.

## `profile.data` vs `profile.exec`

**Rule:** Adding **data trace** to PL kernels (`--profile.data … :all`, or omit
`:counters`) **automatically includes execution trace**. The **`v++` command line does
not need both `--profile.exec` and `--profile.data … :all`**.

| User need | Recommend |
|-----------|-----------|
| Data trace + CU execution timeline | `--profile.data … :all` **only** — omit `--profile.exec` |
| Data counters only (`:counters`) | `--profile.data … :counters`; add `--profile.exec` separately if AM counters are still required |
| **Execution trace only** (no data port monitors) | `--profile.exec … :all` **without** `--profile.data` trace |

## Syntax

```
--profile.data   <kernel>:<cu>:<interface>[:counters|all]
--profile.exec   <kernel>:<cu>[:counters|all]
--profile.stall  <kernel>:<cu>[:counters|all]
```

- **`<kernel>|all`**, **`<cu>|all`**, **`<interface>|all`** — scope of instrumentation.
- **`<cu>` is the compute unit's instance name** in the linked design: the names given in
  `connectivity.nk` (e.g. `nk=k1:2:cu1,cu2`), or by default `<kernel>_1`, `<kernel>_2`, …
  (UG1700). When the user names a CU ("cu2 of kernel k1"), use that name as written —
  `--profile.stall k1:cu2`, exactly UG1702's own example — and at most add a note that CUs left
  at default names would be `k1_1`, `k1_2`. Do not replace a given name with a guessed default.
- **`:counters`** — monitor IP with **counter** access only (smaller area).
- **`:all`** (default when omitted) — counters **and** **trace** (`m_properties & 0x1`
  in `debug_ip_layout`).

### Examples

```bash
# All kernels, all CUs, all interfaces, trace + counters (includes execution trace)
--profile.data all:all:all

# Counters only (area-friendly)
--profile.data all:all:all:counters

# Single kernel memory port (CU left at its default name my_vadd_1)
--profile.data my_vadd:my_vadd_1:m_axi_gmem0:all

# Stalls for one named CU only (UG1702 example; also needs --profile.stall at compile)
--profile.stall k1:cu2

# CU execution monitors only — no data trace
--profile.exec all:all

# Stall monitors (compile AND link)
--profile.stall all:all
```

## `--profile.data`

Enables **AIM** on memory interfaces and **ASM** on stream interfaces.

When **` :all`** (trace) is selected, **execution trace is included automatically** —
do not also add `--profile.exec` for the same scope.

- Use **`all:all:all`** when the user asks about **data volume** or **pipelining**
  timelines across the design.
- Use **`:counters`** when the user only needs **`summary.csv`** totals and area is
  constrained.
- Use **`:all`** when **`device_trace`** timelines or fine-grained transfer patterns
  are needed.

## `--profile.exec`

Enables **ACCEL_MONITOR (AM)** on compute units — kernel start/stop, execution count,
occupancy. Essential for **pipelining** and **which CU is running** when **data trace
is not** being added.

**Use `--profile.exec` only when the user wants execution trace alone** — without
`--profile.data … :all`. If data trace is already enabled on the kernels, **`profile.exec`
is redundant**.

Minimum port data collection is included even with `:counters`.

## `--profile.stall`

Adds **stall monitoring** to the xclbin. **Must be specified during compilation and
linking.**

### HLS / Vitis kernel compile

Per UG1702 *HW Link System Configuration*, stall profiling relates to
`syn.rtl.kernel_profile=1` in the **HLS component config** (`hls_config.cfg`):

```ini
[hls]
syn.rtl.kernel_profile=1
```

Without kernel-side stall ports, `--profile.stall` at link alone is insufficient.

### Runtime pairing

When AM stall property is set (`m_properties & 0x4`), enable in `xrt.ini`:

```ini
stall_trace=all
```

(Requires `device_trace` not `off` — see **`vitis-pl-profile`**.)

## `--profile.trace_memory`

**Hardware target (`-t hw`) only.** Do not use for **`hw_emu`**. **`sw_emu`** is no
longer supported — new designs cannot be built with `-t sw_emu`.

Specifies the **platform-specific** memory resource for the **trace buffer** (pairs with
`trace_buffer_size` in `xrt.ini`).

### Platform-specific name (required)

The argument must name a memory resource **that exists on the target platform**. Link
**will not compile** if the string does not match a platform memory bank. Names vary by
platform — examples include `DDR`, `LPDDR1`, `HBM[0]`, `PLRAM[0]`, not a single universal
string.

**This skill does not enumerate platform memory names.** Selecting the correct value
requires **platform details outside this skill** — e.g. the platform `.xpfm`,
`platforminfo`, or Vitis platform documentation. Ask the user for their platform memory
list or point them to those tools; **do not assume `DDR` or `DDR[0]` works**.

**Example syntax only** (valid only if `DDR[0]` exists on the platform):

```bash
--profile.trace_memory DDR[0]
```

UG1702 also documents other forms when supported on the platform, such as
`FIFO:<size>`, per-bank indices, and optional `:SLR` assignment — still subject to the
same **name must exist on platform** rule.

### Other notes

- **FIFO** — `FIFO:<size>` (size in KB; max ~128K per UG1702; 64K often recommended) —
  on-chip trace buffer, not a platform DDR/HBM bank name.
- **Multi-SLR** — per-SLR memory assignment can ease timing when the platform supports
  it; coordinate with `--connectivity.slr`.

XRT warns when FIFO fills or trace buffer is too small (`tracedefs.h`); recommend
larger `trace_memory` and/or runtime `trace_buffer_size` / `device_trace=coarse`.

## `--profile.aie` (discouraged)

Adds profiling monitors to the **PLIO connections of the AI Engine in PL** for
profiling and trace (link phase).

```bash
--profile.aie all
--profile.aie <ADF_graph_argument|pin_name>
```

Config file:

```ini
[profile]
aie=all
aie=DataIn1
```

**Default guidance: do not recommend `--profile.aie`.** It adds **complexity to the PL**
routing and monitor fabric but does **not** provide data beyond what **AIE profiling**
(`aie_profile`, **`vitis-aie-profile`**) and **AIE event trace** (compile-time
`event-trace=runtime`, runtime **`vitis-aie-profile`**) already capture on **shim /
interface** connections. Reserve for **extremely niche** cases only — e.g. a legacy flow
that cannot use AIE-side instrumentation and explicitly requires PLIO-side monitors.

When the user asks about AIE bandwidth, stalls, or timelines, hand off to
**`vitis-aie-profile`** and **`vitis-aie-profile`** instead of `--profile.aie`.

## `--profile.memory` (isolated tests only)

Adds profiling **counters on the DDR memory controller** (platform memory SP tags).

```bash
--profile.memory all
--profile.memory <sptag>
```

Config file:

```ini
[profile]
memory=all
memory=DDR[0]
```

Counters report **aggregate, coarse-grained** traffic on a DDR connection — **total**
read/write volume — but **do not distinguish which kernel, AIE shim, or host agent**
 produced the transfers.

**Use only in isolated test cases** where the user **knows all sources** of memory
traffic on that DDR (e.g. a single kernel and a single bank with no competing masters).
Not suitable for typical multi-kernel / Versal AIE+PL designs where DDR is shared.

For per-kernel or per-port byte counts, prefer **`--profile.data`** on kernel interfaces.
For platform NOC-level views at runtime, see **`noc_profile`** in **`vitis-pl-profile`**
when applicable.

## Deadlock-related build options

| Mechanism | Build hook | Runtime hook | Notes |
|-----------|------------|--------------|-------|
| **Stall + exec trace** | `--profile.stall` + `--profile.data … :all` (includes exec) or `--profile.stall` + `--profile.exec` if exec-only | `device_trace`, `stall_trace` | Primary hardware hang diagnosis path |
| **hw_emu deadlock detection** | `--advanced.param compiler.deadlockDetection=true` on **`v++ --compile`** (or `[advanced] param=…` in the compile cfg); UG1702 lists it for compile / Vitis HLS `config_export`, not link | Emulation stops with deadlock message | Boolean, default false (UG1702 *--advanced Options*) |
| **ACCEL_DEADLOCK_DETECTOR IP** | Platform / link overlay (when supported) | `pl_deadlock_detection=true` | Appears as `ACCEL_DEADLOCK_DETECTOR` in `debug_ip_layout`; verify after link |

There is no single `v++` flag literally named `ACCEL_DEADLOCK_DETECTOR`. After link,
**always verify** with `xclbinutil` whether ADD IP is present before recommending
`pl_deadlock_detection`.

## Question → minimal profile set

| User question | Minimal build |
|---------------|---------------|
| Pipelining between PL kernels | `data` (`:all`) + `stall` (compile+link) + `trace_memory` (hw) — **omit `exec`** |
| Data amounts correct? | `data=all:all:all:counters` (upgrade to `:all` if timing matters) |
| Hang / which kernel? | `data` (`:all` or `:counters` + `exec` if counters-only) + `stall` (compile+link); hw_emu add `compiler.deadlockDetection=true` at compile |
| Execution timeline only (no data ports) | `exec` (`:all`) — **without** `data` trace |

## Area / timing guidance

- Start with **narrow scope** (`kernel:cu:port`) on congested designs.
- Prefer **`:counters`** first; add **`:all`** only when timelines are required.
- If link fails timing after full `all:all:all` profiling, try per-SLR `trace_memory`,
  `FIFO` trace memory, or counters-only on non-critical ports.

## Product references

- **UG1702** — *--profile Options* (compile-time monitor IP)
- **UG1700** — *Enabling Profile and Debug when Linking*
- **UG1701** — embedded link / trace flows where applicable

Full guide links and question→guide mapping: **`references/user_guides.md`**.
