<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# AIE event trace — compile and runtime options

## Build targets

**`sw_emu` (`-t sw_emu`) is no longer supported** — new designs **cannot** be built
targeting software emulation. AIE event trace and profiling compile guidance assumes
**`-t hw`** (or existing **`hw_emu`** flows where still applicable).

## Compile-time (`v++ -c --mode aie`)

Options belong under `[aie]` in a config file or `--aie.<option>` on the command line.

### Recommended production starting point

**Compile with `event-trace=runtime` only** — do not use removed fixed compile-time values.

```ini
[aie]
event-trace=runtime
event-trace-port=gmio
broadcast-enable-core=true
xlopt=0
```

```bash
v++ -c --mode aie --platform <platform.xpfm> \
  --config aie_trace.cfg \
  --aie.workdir ./Work \
  --include ./src --include ./src/kernels \
  -o libadf.a ./src/graph.cpp
```

### `event-trace` (compile-time)

**Only `runtime` is supported.** Fixed compile-time values such as `functions`,
`functions_partial_stalls`, and `functions_all_stalls` have been **removed** and are
**no longer valid** in `[aie]` or `--aie.event-trace=…`.

| Compile value | Status |
|---------------|--------|
| `runtime` | **Required** — trace infrastructure only; pick metrics in `xrt.ini` at run time |
| `functions`, `functions_partial_stalls`, `functions_all_stalls`, … | **Invalid** — do not use |

Trace metric depth (`functions`, `partial_stalls`, `all_stalls`, …) is selected at
**runtime** in `[AIE_trace_settings]` — see **`vitis-aie-profile`**.

### Port and stream options

#### GMIO vs PLIO — how to choose `event-trace-port`

The choice between **GMIO** and **PLIO** for event trace offload depends on the
**design and its current configuration**. The **first determining factor** is whether
there are **enough resources for trace after the user application has taken its
resources** (PL logic, NoC/NMU streams, DDR buffer budget).

| Port | Resource cost | Offload / buffer behavior |
|------|---------------|---------------------------|
| **`gmio`** | Uses NoC path; **preserves PL** | DDR event trace buffer only — **no circular buffer**. Trace history is **limited to `buffer_size`**. |
| **`plio`** | **Consumes PL resources** (streams, width) | DDR event trace buffer can operate as a **circular buffer**, combined with **continuous offload** at runtime to sustain **large trace volumes** over long runs. |

**Decision flow**

1. **Resources first** — If the application already uses most PL, prefer **GMIO** so
   trace can still be added. If PL headroom remains and link/route succeeds, **PLIO**
   is an option.
2. **Trace volume** — If the user needs extended trace history beyond a single DDR
   buffer fill, recommend **PLIO** (circular buffer + continuous offload configured at
   runtime via **`vitis-aie-profile`**). If trace fits one buffer, **GMIO** is
   often sufficient and saves PL.
3. **Recovery** — If PLIO link fails (NMU exhaustion, timing), try fewer
   `num-trace-streams` first; switch to **GMIO** only when PLIO cannot fit — accepting
   the GMIO buffer-size limit.

#### Trace stream count (`num-trace-streams`)

The number of **output trace streams** added to the AIE should be **enough to offload
all the desired trace without hitting overruns**. Streams exist to provide sufficient
**parallel egress bandwidth** for the compile-time trace infrastructure and the
**runtime trace scope** (which tiles/graphs and metrics are enabled in `xrt.ini`).

**Sizing principle:** Use the **minimum stream count that avoids overruns** in a
representative run. **Additional output trace streams beyond that point only consume
resources (NMU, PL routing, link pressure) without any benefit** — do not recommend
max streams “just in case.”

**Example:** A design that only consumes **one AIE column** needs at most **one**
output event trace stream (`num-trace-streams=1`). Values larger than one provide no
additional offload capacity for that placement and should not be recommended.

| Situation | Action |
|-----------|--------|
| Overruns / black bars / packet drops at runtime | **Increase** `num-trace-streams` and/or `trace-plio-width`; may also need larger `buffer_size` or narrower tile scope |
| Link fails (`CFGEN 83-2251`, NMU exhaustion) | **Decrease** streams until link succeeds (see `compile_failure_recovery.md`) |
| Test run clean at lower count | **Decrease** to free resources for the application |
| Single-column design | **`num-trace-streams=1`** — do not exceed one stream |
| AIE-ML + PLIO, 16 streams fails link | Stay **&lt; 12** per AR 000036444; find lowest count that still avoids overruns |

| Option | Values | Notes |
|--------|--------|-------|
| `event-trace-port` | `gmio` (default), `plio` | See table above — not a universal default. **GMIO:** NoC offload, no circular buffer. **PLIO:** PL cost, circular DDR buffer + continuous offload for large trace. |
| `num-trace-streams` | 1–16 | **PLIO** output trace streams. Right-size to avoid overruns; extra streams waste resources. Tool default often 16 — reduce if link fails or runtime shows no overruns at lower count. **PLIO + AIE-ML:** avoid 16 if link fails (AR 000036444). |
| `trace-plio-width` | 32, 64, 128 | Increase to 128 if trace packets drop (PLIO). |
| `broadcast-enable-core` | true/false | **true** for aligned kernel start (default in recent tools). |
| `xlopt` | 0, 1, 2 | **0** for visible kernel names in trace. |
| `graph-iterator-event` | true/false | Delay trace start by graph iteration when needed. |

### System link and package (Versal)

On **Versal**, `v++ --link` writes a **fixed `.xsa`**. `-o *.xclbin` on the link
command is valid only for **Zynq-7000 and Zynq UltraScale+ MPSoC** (UG1701
*Linking with the Vitis Integrated Flow*). The xclbin that XRT loads is produced by
a following **`v++ --package`** step. `libadf.a` is an input to **both** commands.
`AIE_TRACE_METADATA` is inserted at package time; it is not in the `.xsa`.

When the design includes PL kernels and the user wants **PL** on a unified timeline,
put PL profile options on the **link** command per **`vitis-pl-profile`** (e.g.
`--profile.data`). They are implemented into the XSA and carried into the xclbin by
package. **System timeline alignment** stays **runtime-only** (`enable_system_timeline`).

```bash
v++ -l -t hw --platform <platform.xpfm> \
  --profile.data all:all:all \
  libadf.a <kernel.xo> ... \
  -o binary_container.xsa

v++ -p -t hw \
  --platform binary_container.xsa \
  --package.defer_aie_run \
  libadf.a \
  -o binary_container.xclbin
```

`--platform` on package is the **linked fixed XSA**, not the original `.xpfm`.
`--package.defer_aie_run` keeps the graph from starting at boot, so `xrt.ini`
capture is not racing an auto-started graph. SD-card / boot-image package options
are deployment, not trace enablement.

### System timeline (`enable_system_timeline`) — runtime only

**`enable_system_timeline`** does **not** require compile-time options beyond the
**normal trace flags** for each domain you capture (AIE `--event-trace`, host trace
ini, PL `device_trace` / PL build as applicable). Set it in **`[AIE_trace_settings]`**
at run time:

```ini
[Debug]
aie_trace=true
native_xrt_trace=true
device_trace=fine

[AIE_trace_settings]
enable_system_timeline=true
poll_timers_interval_us=50
max_timer_samples=2000000
```

XRT then **polls AIE timestamps** during the run (**small additional overhead**) and
writes alignment data (e.g. `aie_event_timestamps.bin`) so Vitis Analyzer can align
AIE with host and PL in **post-processing**. See **`vitis-aie-profile`** for full
runtime recipes.

### Verify compile

Dump the **packaged** xclbin (`v++ -p` output). The linked `.xsa` does not contain
this section.

```bash
xclbinutil --input design.xclbin \
  --dump-section AIE_TRACE_METADATA:JSON:aie_trace_metadata.json
```

Check `aiecompiler_options.event_trace` and trace port / stream configuration.
The expected steps that produce this section are: compile the graph with
`event-trace=runtime` (writing `libadf.a`), link to a fixed `.xsa`, then package
that `.xsa` together with `libadf.a`. Do not re-link with `-o *.xclbin`.

---

## Runtime (`xrt.ini`) — after trace-enabled build

### Main switches

```ini
[Debug]
aie_trace=true
```

### `[AIE_trace_settings]` metric scoping

**Graph-based** (all tiles running kernels in a graph):

```
graph_based_aie_tile_metrics=<graph>:<tile|all>:<metric>
graph_based_memory_tile_metrics=...
graph_based_interface_tile_metrics=...
```

**Tile-based** (array coordinates or ranges). Write the key **once per section**. These
are alternate value forms, not three lines in one section. To pass more than one, put
them on that same key separated by `;`.

```
tile_based_aie_tile_metrics=all:all:<metric>
```

```
tile_based_aie_tile_metrics={colMin,rowMin}:{colMax,rowMax}:<metric>
```

```
tile_based_aie_tile_metrics={col,row}:<metric>
```

**Example — bounding box at runtime** (UG1076 Example 4; no recompile when
`event-trace=runtime`):

```ini
[AIE_trace_settings]
tile_based_aie_tile_metrics={4,1}:{6,2}:all_stalls;{4,1}:functions
```

### Common trace metrics (runtime names)

| Metric | Use |
|--------|-----|
| `functions` | Function call/return timeline |
| `partial_stalls` | Stream, cascade, lock stalls |
| `all_stalls` | All stall types incl. memory |
| `all_dma` | Four memory DMA channels |
| `all_stalls_dma` | Combined stalls + DMA |
| `s2mm_channels` / `mm2s_channels` | Per-direction DMA detail |

Legacy **runtime** metric names in ini: `partial_stalls` / `all_stalls` (not compile
options). Older docs may refer to compile-time `functions_partial_stalls` /
`functions_all_stalls` — those compile values are **no longer valid**; use
`event-trace=runtime` and set metrics in `xrt.ini`.

### Buffer / offload tuning

```ini
[AIE_trace_settings]
buffer_size=8M
periodic_offload=true
buffer_offload_interval_us=100
reuse_buffer=false
start_type=time
start_time=0
```

**PLIO vs GMIO at runtime:** **Continuous offload** (`periodic_offload`) combined with
the DDR trace buffer is most valuable on **PLIO** builds, where the buffer can act as a
**circular buffer** and sustain large trace over long runs. On **GMIO**, there is **no
circular buffer** — history is capped by `buffer_size` regardless of offload settings.

Large designs: increase `buffer_size`; watch XRT warnings about buffer full or
circular overwrite (`tracedefs.h` AIE trace messages).

### Graph placement bounding box (compile-time, in source)

Restrict where a graph is **placed** — can improve trace routing (UG1079):

```cpp
mygraph g;
location_constraint bounding_box(4, 1, 6, 2);
```

Or constraints JSON via `--aie.constraints=<file>`. This affects **implementation
placement**, not just which tiles are traced at runtime.

---

## References

- UG1076 — Event Trace Build Flow, XRT Flow, System Level Trace, Troubleshooting
- UG1702 — Using System Trace (`v++ --aie.*`, link `--profile.data`)
- UG1701 — Linking with the Vitis Integrated Flow (Versal link output is `.xsa`; Zynq link output is `.xclbin`) and Packaging with Vitis and EDF (`v++ -p` writes the xclbin)
- UG1079 — `location_constraint bounding_box`
