<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Recovering from AIE trace compile / link failures

When `--event-trace` causes **aiecompiler** or **v++ link** to fail, apply restrictions
in order from **lowest user impact** (if it solves the failure) to **highest**.

Always tell the user **what trace coverage they lose**.

## Symptom → fix matrix

| Error / symptom | Likely cause | Restriction | Impact on user |
|-----------------|--------------|-------------|----------------|
| `CRITICAL WARNING [aiecompiler 35-4476] Trace Net ... failed to route` then **ERROR** | Trace routing exhaustion | Add `--aie.Xrouter=skipunroutabletracenets` | Build completes; **tiles on failed nets produce no trace** — hang/debug on those tiles may be blind |
| `ERROR [CFGEN 83-2251] Exhausted NMU count` / trace insertion | Too many PLIO trace streams | Reduce `--aie.num-trace-streams` (try 8, 4); PLIO **&lt; 12** on AIE-ML | **Less trace bandwidth** — more packet drops or black bars in Analyzer unless buffer/offload tuned |
| PL timing / congestion with `event-trace-port=plio` | PL trace path | Switch to `--aie.event-trace-port=gmio` | **Frees PL** but **no circular buffer** — trace history limited to DDR `buffer_size` |
| Routing failures on large arrays | Horizontal stream allocation | Add **`location_constraint bounding_box(...)`** per graph in source; prefer **vertical** tile placement | **Constrains mapper** — may reduce kernel performance vs free placement; trace routing more likely to succeed |
| Still fails with `event-trace=runtime` | Full-array trace infrastructure | Narrow **runtime** tile/graph metrics in `xrt.ini`; add `bounding_box`; reduce streams / switch port | **Less trace coverage** on omitted tiles or lighter metrics — no fixed compile-time fallback |
| Link OK but massive trace loss at runtime | Buffer / streams | Increase `buffer_size`; PLIO: `trace-plio-width=128`; reduce scoped metrics | Shorter history or fewer tiles in runtime scope |

## 1. Allow partial trace routing

Add to `[aie]` section or command line:

```ini
[aie]
Xrouter=skipunroutabletracenets
```

```bash
v++ -c --mode aie ... --aie.Xrouter=skipunroutabletracenets ...
```

**Impact:** Compiler **warns per unrouted trace net** instead of failing. User must read
critical warnings to know **which memory tiles / columns lack trace**. Performance
analysis on omitted tiles is incomplete.

## 2. Reduce trace streams (PLIO)

Use this when link fails — but remember the **target** is the **minimum** stream count
that still offloads all desired trace **without overruns**. Streams reduced only for
link success may need a follow-up hw test; if overruns return, raise count or narrow
runtime trace scope.

```ini
[aie]
event-trace-port=plio
num-trace-streams=8
trace-plio-width=128
```

Start at 8; decrease to 4 if link still fails. On AIE-ML + PLIO, avoid 16 streams
(AMD AR 000036444). **Do not** leave extra streams above what overrun-free offload
requires — surplus streams consume NMU/PL resources with no trace benefit.

**Impact:** Fewer parallel trace egress paths → higher chance of **overruns** (black
bars in trace view). May need smaller runtime tile scope or larger `buffer_size`.

## 3. Switch to GMIO port

```ini
[aie]
event-trace-port=gmio
```

Remove PLIO-only options (`num-trace-streams`, `trace-plio-width`) unless still
required by platform.

**Impact:** Uses NoC/DDR path; **frees PL** compared to PLIO. Use when PLIO streams
cannot fit or PL is exhausted — but accept that **GMIO does not support the circular
DDR trace buffer**; trace history is limited to `buffer_size`. Not compatible with some
HSDP-only flows that explicitly require PLIO — check platform guide.

## 4. Bounding box placement constraints

In graph source (UG1079):

```cpp
location_constraint bounding_box(column_min, row_min, column_max, row_max);
```

Apply to hot or debug-focused subgraphs first — not necessarily the whole design.

**Impact:**

- **Placement** restricted to the box — may increase routing congestion for **data**
  paths or hurt fmax if the box is tight.
- **Does not** by itself limit which tiles emit trace when using `event-trace=runtime`;
  combine with **runtime** tile ranges in `xrt.ini` to limit collection scope without
  another compile:

```ini
[AIE_trace_settings]
tile_based_aie_tile_metrics={4,1}:{6,2}:functions
```

**Runtime tile bounding** reduces **data volume** and Analyzer scope; compile-time
**bounding_box** helps **routing succeed** for the tiles that remain.

### Constraints file alternative

```bash
v++ -c --mode aie ... --aie.constraints=constraints.json ...
```

Use when bounding boxes are easier to maintain in JSON than in graph constructors.

## 5. Narrow runtime trace scope (not compile-time mode)

Fixed compile-time `event-trace` values (`functions`, `functions_partial_stalls`,
`functions_all_stalls`, …) are **removed** — **`event-trace=runtime` is the only valid
compile setting**. When the build still fails or trace volume is too high, narrow
collection in **`xrt.ini`** instead:

```ini
[AIE_trace_settings]
tile_based_aie_tile_metrics={4,1}:{6,2}:functions
```

**Impact:** Fewer tiles or lighter metrics at run time — may require multiple runs to
cover the full design. Does **not** fix routing failures by itself; combine with
`Xrouter=skipunroutabletracenets`, port/stream changes, or `bounding_box` as needed.

## 6. System link failures (after graph compiles)

If `v++ -l` fails when integrating `libadf.a`:

- Reduce PLIO trace streams before link (graph recompile).
- Confirm `--profile.data` is only added once; PL trace memory per
  `vitis-pl-profile`.
- For NMU exhaustion, prefer **gmio** trace port on the graph compile.

## Suggested escalation script for the agent

Present options to the user in this order, re-running compile after each:

1. `Xrouter=skipunroutabletracenets` (if routing error)
2. `event-trace-port=gmio` (if on plio)
3. Lower `num-trace-streams` (if PLIO / NMU error)
4. Add `bounding_box` on largest graph + runtime tile range in ini example
5. Narrow runtime `[AIE_trace_settings]` metrics / tile ranges (last resort before
   declaring platform/graph incompatible with full trace)

**Do not** recommend fixed compile-time `event-trace=functions` or similar — those
options are **removed**; only **`event-trace=runtime`** is valid at compile time.

After any change, re-dump `AIE_TRACE_METADATA` or inspect `Work/*/aiecompile_summary`
for routed trace warnings.

## References

- UG1076 — Event Trace Routing, Troubleshooting Event Trace in Hardware
- UG1079 — Mapping Constraints (`bounding_box`)
- AMD AR 000036444 — PLIO `num-trace-streams=16` on AIE-ML
