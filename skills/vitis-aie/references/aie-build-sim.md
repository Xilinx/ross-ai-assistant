<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# AIE Component: Create, Compile (Vitis Python API)

Ground truth for the driver flow, for both x86sim and aiesim. Works for any
AIE-capable Versal part (AI Core / AI Edge / Gen2); the examples below use the
vck190 part (`xcvc1902-vsva2197-2MP-e-S`) — substitute the part for your silicon.

## Create — `client.create_aie_component`

```python
comp = client.create_aie_component(name="my_aie", part="xcvc1902-vsva2197-2MP-e-S",
                                   template="empty")
# or platform="/path/to/platform.xpfm" instead of part=
```
- Requires `name` **and exactly one of `part` or `platform`** (errors if both
  omitted). Standalone create/compile/sim is **part-based**; use `platform` only
  when targeting/integrating a specific platform (Requirements 5/6).
- vck190 part = `xcvc1902-vsva2197-2MP-e-S`. Pick the part for the AIE silicon
  (Versal AI Core / AI Edge / Gen2).

## Add sources

```python
comp.import_files(from_loc="<dir>", files=["my_aie.h","my_aie.cpp",
                                           "my_aie_graph.h","my_aie_app.cpp","input.txt"])
comp.update_top_level_file(top_level_file="my_aie_app.cpp")   # the graph/app top
# comp.add_cfg_file(cfg_file="aie.cfg")  # see caveat below
```

## Compile

| build target | `comp.build(...)` | output |
|--------------|-------------------|--------|
| `x86sim` (default) | `comp.build(target="x86sim")` | `libadf.a` (functional) |
| `hw` | `comp.build(target="hw")` | `libadf.a` (cycle-accurate) |

`comp.build(target=...)` accepts only `x86sim` or `hw`, and returns `0`/SUCCESS on success.
**Running the simulators (`x86simulator` / `aiesimulator`) is out of scope for this skill** — it
builds the `libadf.a`; the simulation run is a separate step handled elsewhere.

## Gotchas (learned during validation)

1. **The AIE component ships its own cfg.** `add_cfg_file` fails with
   *"Multiple cfg files are not allowed"* — don't add a second; use the default
   (the driver tolerates this).
2. **Unconnected graph ports fail compile** — the top-level app must wire the
   graph's `input_port`/`output_port` to PLIO/GMIO (see kernel-authoring.md §4).
3. **`__X86SIM__` / `__AIESIM__`** macros guard the app's `main()`; the standard
   `init()/run(n)/end()` testbench works for both.

## CLI (standalone, no Vitis component) — for reference

The same design can be built without a component via v++ (part-based):
```
v++ --compile --config aie.cfg --mode aie --target=x86sim <app>.cpp --aie.output-archive=libadf.a
v++ --compile --config aie.cfg --mode aie --target=hw     <app>.cpp --aie.output-archive=libadf.a
```
The skill uses the Python API path (managed component) by default.
