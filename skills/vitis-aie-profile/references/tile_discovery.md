<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# Discovering AIE tiles and trace capability

Runtime ini metrics are **ignored** for tiles that do not exist in the compiled design.
Discover the map **before** writing `graph_based_*` or `tile_based_*` settings.

## Method 1 — Vitis Analyzer compile summary (recommended)

1. Locate `Work/<graph_name>.aiecompile_summary` from the AIE compile.
2. Open in Vitis Analyzer (or set compile directory when viewing `xrt.run_summary`).
3. Use **Graph** view: graph name → kernel instances → **tile column/row**.
4. Use **Array** / **Partition** views for memory tiles and interface tiles.

Record for each kernel of interest:

- Graph name (e.g. `mygraph`)
- Kernel / function name (e.g. `my_kernel`)
- Tile `{column, row}`
- PLIO / GMIO port names for interface metrics

## Method 2 — `AIE_TRACE_METADATA` in xclbin

```bash
xclbinutil --input <design.xclbin> \
  --dump-section AIE_TRACE_METADATA:JSON:aie_trace_metadata.json
```

### Tile mapping

`TileMapping.AIEKernelToTileMapping[]` entries:

```json
{
  "graph": "mygraph",
  "tile": "aie",
  "column": "19",
  "row": "0",
  "function": "my_kernel.first"
}
```

Use these coordinates in `tile_based_*` ini keys.

### Event trace compile check

`aiecompiler_options`:

```json
"event_trace": "runtime",
"broadcast_enable_core": "true"
```

| Finding | Implication |
|---------|-------------|
| Section missing | No AIE trace metadata packaged — `aie_trace` unlikely to work |
| No `event_trace` field | Graph compiled **without** trace — profile only, or rebuild |
| `event_trace`: `runtime` | Trace metrics selectable in ini **without** recompile — **only valid compile-time mode** |
| `event_trace`: `functions` / `functions_all_stalls` / other fixed values | **Legacy / invalid** — fixed compile-time modes removed; rebuild with **`event-trace=runtime`** |

Hand off to **`vitis-aie-profile`** when trace is required but metadata is absent.

### Trace plumbing

`PLIOs`, `GMIOs`, `EventGraphs` — confirm trace offload path exists (pairs with compile
`event-trace-port`).

## Method 3 — Graph source / constraints

If the design uses `location_constraint bounding_box(c0,r0,c1,r1)` (UG1079), ini tile
ranges should **align** with those regions when scoping trace/profile to a subgraph.

## Validating ini after a run

XRT logs **warnings** for unknown graphs, kernels, or buffers in metric strings. If
settings are skipped:

1. Re-check spellings against compile summary.
2. Confirm partition / `config_one_partition` if using multi-partition designs.
3. Try `all:all:<metric>` on one graph first, then narrow.

## Translating user language → ini scope

| User says | Ini scope |
|-----------|-----------|
| "my AIE kernel `foo` in graph `bar`" | `graph_based_aie_metrics=bar:foo:...` |
| "tile at column 10 row 2" | `tile_based_aie_metrics={10,2}:...` |
| "kernels in the northeast corner" | Range from compile map: `{c1,r1}:{c2,r2}:...` |
| "between graph A and graph B" | `interface_tile_latency=A:portX:B:portY:N` or interface trace on those ports |
