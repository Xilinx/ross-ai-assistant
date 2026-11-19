---
name: vitis-aie
description: Create and build a Vitis AI Engine (AIE) component/graph — inside the Vitis Unified IDE's AI agent via the `runVitisPython` tool against the live `client`, or HEADLESS on any host via `vitis -s`. Use whenever the user asks to create or build an AIE / AI Engine component, kernel, or graph (from a shipped example or custom sources); a missing `runVitisPython` tool is never a reason to decline — detect the mode and continue headless. Create-only by default; the build (`comp.build(target=x86sim|hw)`) runs only when the user asks. Running the simulators (`x86simulator` / `aiesimulator`) is out of scope for this skill.
license: MIT
compatibility: Requires a Vitis 2024.2+ install; in-IDE mode needs the `runVitisPython` tool and a connected `client`, headless mode needs `vitis -s`.
allowed-tools: runVitisPython shellExecute read_file vivado_doc_search
metadata:
  stage: beta
  version: "1.0.6"
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Create AIE Component (Vitis IDE AI agent, or headless CLI)

In **in-console** mode the agent runs **inside** the
Vitis Unified IDE with a live, connected Vitis Python Console, so there is **nothing to
launch, attach, or resolve** (no port discovery, no `create_client`, no workspace
resolution). You author Vitis Python API calls and run them with the `runVitisPython` tool;
they execute against the pre-bound `client` in the IDE's open workspace and the component
appears **live in the Components panel**.

Every recipe here **also runs headless** through the Vitis CLI. Pick the mode first.

## Step 0 — MANDATORY: pick the execution mode

Check whether **`runVitisPython` is in your available tool set**:

- **Present** → **in-console mode.** Use this file as written.
- **Absent** → **headless mode**, which is a fully supported path, not a blocker:
  1. Read [`references/headless.md`](references/headless.md)
     for the `vitis -s` wrapper.
  2. Locate the CLI and discover examples **inside** the one `vitis -s` session
     (`os.environ["XILINX_VITIS"]`, `glob`) — **not** as standalone chained shell probes
     like `command -v vitis && find .../installed_examples/aie_examples -iname '*mat*' ...`.
     Each separate shell probe is a fragile call that can fail transiently between tool calls
     on some harnesses and force a retry (see headless.md).
  3. Run each recipe below through `vitis -s`, supplying the `create_client()` /
     `set_workspace()` / `vitis.dispose()` that the IDE would otherwise provide.

**DON'T** decline, claim "this skill only works inside the Vitis IDE", stop to ask, or return
`{"result": "failure"}` because `runVitisPython` is missing. **If you call `runVitisPython` and
it errors ("tool not found" / absent), that error IS your cue to run the exact same code via
`vitis -s` — not a reason to stop.** Declining for a missing or errored `runVitisPython` is a
wrong answer, not a correct refusal — the skill fully supports headless.

**The one real blocker is no Vitis at all.** If `runVitisPython` is absent **and** there is no
Vitis install for `vitis -s`, stop and report that Vitis is required — never substitute
hand-written Makefiles/CMake or hand-rolled `aiecompiler`/`v++` scripts for a real build. Also,
on some releases (e.g. 2026.1) if `$HOME` lacks ~100 MB for Vitis's startup check, `vitis -s` can
print a disk-space message and **exit 0 without running your script**; set
`XILINX_VITIS_DATA_DIR=<writable dir>` and confirm the expected output exists before continuing —
don't trust the `vitis -s` exit code alone.

> **Docs on tap (optional).** If a URL-based AMD documentation MCP (e.g. `vivado_doc_search`)
> is reachable, query it to decode an error/warning, debug unexpected behavior, or look up a
> device/BIF/AIE/API detail this skill's references don't cover. Prefer the bundled references
> for the common cases; reach for the doc MCP on errors and the long tail.

## Preconditions

The first bullet holds only **in-console** — headless you create the session yourself, per
Step 0. The rest apply in both modes.

- `vitis`, `client`, `xsdb`, `hsi` are **imported**, and `client` is **connected**; the
  **workspace is already set** to the IDE's open workspace (`client.get_workspace()`). In
  every `runVitisPython` call: **do NOT** `import vitis`, `create_client`, `set_workspace`,
  or `vitis.dispose()`. Use the existing `client`; **REPL state persists across calls**.
- Sourced Vitis environment (already true in the IDE) so `aiecompiler` is on `PATH` for the build.
- A **target with an AI Engine**: a device **part** for standalone AIE (e.g. vck190
  `xcvc1902-vsva2197-2MP-e-S`), or an **AIE-capable extensible base platform** `.xpfm` for
  integration. Board-shortcut / embedded platforms do **not** expose the AI Engine.

## How you run code — the `runVitisPython` tool

> **If `runVitisPython` errors or isn't available here, do not stop or report failure** — run
> the identical `code` through `vitis -s` instead (see Step 0 and [`references/headless.md`](references/headless.md)).

Every Vitis operation is the `code` payload of a `runVitisPython` call; the tool runs it in
the dedicated agent console and returns captured stdout/stderr.

- **Mutating calls prompt for confirmation** (create/build) — expected; don't route around it.
- **Output is truncated** (~20k, head+tail) — keep prints concise; don't dump full build logs.
- **A bare expression prints nothing** (code runs via `exec`). **Always `print(...)`** what
  you need to read back.
- **Set `timeoutSeconds` per operation.** The tool defaults to **600s** with an idle watchdog
  (aborts only after ~5 min of *no output* AND no completion). Create/import fit the default;
  a **`comp.build(target="hw")`** (the aiesim compile) can exceed it — pass **~1800–3600s**
  (3600s is the hard ceiling). `x86sim` builds are fast. Keep build output streaming so the
  idle watchdog never trips on a long-but-active build.

## Boundary — build in-console; simulation is out of scope

`comp.build(...)` is a **Vitis API call** → run it in-console via `runVitisPython` (or headless
via `vitis -s`). It produces `libadf.a`. **`x86sim`** is the functional build target (fast);
**`hw`** is the cycle-accurate build target (**long** — budget 1800–3600s; if it may exceed the
1h console ceiling, defer the build to headless `vitis -s`).

**Running the simulators (`x86simulator` / `aiesimulator`) is out of scope for this skill** —
this skill creates and builds the component (producing `libadf.a`); the simulation run is a
separate step handled outside this skill.

## Workflow (create; build when prompted)

```
- [ ] 0. Pick the execution mode (Step 0): runVitisPython present -> in-console, absent -> headless via `vitis -s`
- [ ] 1. Parse request -> name, part/platform, template or sources, sim type; note whether the user asked to BUILD
- [ ] 2. CREATE the component (create_aie_component). Report it (created, unbuilt).
- [ ] 3. Import custom sources (Mode B) and set the top-level file, if any.
- [ ] 4. BUILD only when asked: comp.build(target="x86sim" | "hw") -> VERIFY (rc == 0 AND libadf.a) -> report.
- [ ] 5. If asked, SIMULATE in a shell (after a build).
```

**Create only unless asked to build.** On a "create"/"make" request, create the component and
**stop — do not build**. Run `comp.build(...)` only when the user asks to build/compile (a
separate follow-up) or asks to create *and* build in one go. Import user sources **before** any
build.

> **Downstream needs a BUILT component.** A created-but-unbuilt AIE component has no `libadf.a`;
> the consumer (`vitis-system`) will **offer to build an unbuilt component (ask +
> approve) before linking it** — never silently.

**Prefer a shipped example over hand-authored sources (Mode A first).** When the user asks
for a "simple" / "example" / unspecified AIE component, pass a **shipped example name as
`template=`** (Mode A) — the component is created complete (sources + `data/` + top), needs
**no `import_files`**, and validates against the example's golden data. Only fall back to
**empty + custom sources (Mode B)** when the user supplies their own kernel/graph or asks for
a design no example covers. Don't hand-roll a "simple" design when `template="simple"` exists.
Discover the shipped examples in this install at
`$XILINX_VITIS/installed_examples/aie_examples` (e.g. `simple`, `simple_chained`,
`GMIO_bandwidth`, …) — see [`references/kernel-authoring.md`](references/kernel-authoring.md).

> **No matching example → go straight to Mode B; don't hunt.** When the request names a
> design the install ships **no** example for (e.g. **matrix multiply**, a custom DSP kernel),
> do **not** spend extra shell probes searching `installed_examples/aie_examples` for one that
> isn't there (`find ... -iname '*matrix*'` returns nothing and is pure fragile overhead).
> Author the custom sources per [`references/kernel-authoring.md`](references/kernel-authoring.md)
> and create with `template="empty"`.

**Never scavenge unrelated workspace sources for a "simple" component.** Create it from the
shipped example (`template="installed_aie_examples/simple"`) — do **not** import graph sources
from another component that merely happens to be in the open workspace (e.g. a leftover
`aie_sys_design`). The same request must yield the same design regardless of workspace contents;
scavenging silently changes the graph (extra PLIOs → forces extra kernels downstream).

**For a component destined for integration, name its PLIOs.** The shipped `simple` example
creates PLIOs **unnamed** (`input_plio::create(plio_32_bits, "data/input.txt")`), so an
integration `stream_connect=` has no stable name to target. Either add explicit names in one
edit — `input_plio::create("DataIn1", plio_32_bits, "data/input.txt")` /
`output_plio::create("DataOut1", …)` — or read the compiler-generated name from the built graph
(`build/**/Work/**`, e.g. `aie_control_xrt.cpp`) and use it verbatim. Match the PLIO count to the
kernels the link will connect (a `mm2s`+`s2mm` design wants exactly one input and one output).

**Mode B: don't litter `/tmp`; clean up staging dirs.** `import_files` **copies** the sources
into the component's own directory, so any staging dir you wrote them to is disposable the
moment the import succeeds. If you stage sources (e.g. under `/tmp/<name>_src`): use **one**
staging dir (not one per candidate template), and **`shutil.rmtree` / `rm -rf` it right after
`import_files` returns** — leftover `/tmp/*_src` dirs serve no purpose once the sources live in
the workspace. Better yet, import straight from the user's real source dir (or the shipped
example path) and skip `/tmp` entirely.

**If the name already exists:** live workspace, **no `--clean`** — do not delete a component
from the running session. Pick a new `name`, build the existing one
(`client.get_component(name=...)` → `.build(...)`), or ask the user to remove it in the IDE.

### Choosing an AIE target

An AIE component needs a target that **has an AI Engine** (Versal Core / AI Edge / Gen2).
Use a device **`part`** for standalone AIE (simplest, always works for create/compile/sim);
use a **`platform`** only for an AIE-capable **extensible base** platform (integration).
Do **not** create an AIE component on a non-AIE target. A non-AIE **platform** (e.g.
`kv260_base`) is refused at create; a non-AIE **part** (Kria/ZynqMP) is **accepted** at
create but yields a component that only fails later at build. So **check eligibility before
creating**: if the requested part/platform has no AI Engine (see the table below), don't
create or build — report the eligible targets and ask which to use.

**Eligible AIE targets (verified in the 2026.2 install** —
`$XILINX_VITIS/base_platforms`; a base platform is AIE-capable iff it ships
`hw/aie_primitive.json`**):**

| Board / family | Device part (for `part=`) | AIE-capable base platform(s) (for `platform=`) | AI Engine |
|---|---|---|---|
| vck190 — Versal AI Core Gen1 | `xcvc1902-vsva2197-2MP-e-S` | `vck190_base`, `xilinx_vck190_base_202620_1`, `xilinx_vck190_base_dfx_202620_1` | AIE1 |
| vek280 — Versal AI Edge Gen1 | `xcve2802-vsvh1760-2MP-e-S` | `vek280_base`, `xilinx_vek280_base_202620_1` | AIE-ML |
| vek385 — Versal AI Edge Gen2 | `xc2ve3858-ssva2112-2MP-e-S` | `vek385_base` | AIE-ML (Gen2) |
| vrk160 — Versal RF Gen2 | `xcvr1602-vsva2488-2MP-e-S-es1` | `vrk160_base` | AIE-ML (Gen2) |
| kv260 — Kria/ZynqMP | ❌ no AI Engine | ❌ `kv260_base` is rejected | none |

The table is a snapshot of *this* install; base-platform names/parts are
install/version-specific and can go stale. Confirm live before relying on it:
`print([p for p in client.list_platforms()])` (and inspect for the AIE-capable base
platforms). Authoring details: [`references/kernel-authoring.md`](references/kernel-authoring.md).

### Inputs

| Parameter | Required | Notes |
|-----------|----------|-------|
| `name` | Y | AIE component name |
| `part` **or** `platform` | Y (exactly one) | device part for standalone; AIE-capable `.xpfm` for integration |
| `template` | N (`empty`) | `empty` or a shipped example name |
| source import | N (Mode B) | `comp.import_files(...)` + `comp.update_top_level_file(...)` |

See [`references/aie-build-sim.md`](references/aie-build-sim.md)
(create/compile details and gotchas) and
[`references/kernel-authoring.md`](references/kernel-authoring.md)
(authoring the kernel/graph/app + wiring PLIO/GMIO ports).

## The recipe (create → import; build when prompted, in-console)

Run as `runVitisPython` calls (state persists across them).

```python
# CREATE (default, standalone part-based). Exactly one of part= / platform=.
comp = client.create_aie_component(
    name="my_aie", part="xcvc1902-vsva2197-2MP-e-S", template="empty")
print("AIE_CREATED (unbuilt)", comp)

# MODE B (optional): import custom sources, then set the graph/app top — BEFORE building.
# comp.import_files(from_loc="/src", files=["my_aie.cpp","my_aie_graph.h","my_aie_app.cpp","input.txt"])
# comp.update_top_level_file(top_level_file="my_aie_app.cpp")
# top_level_file is relative to the component root and MUST include the dir you imported into:
# with dest_dir_in_cmp="src", set it to "src/<top>.cpp" — a bare "<top>.cpp" makes the build
# look in the root and fail with "cannot find <top>.cpp".
```

```python
# BUILD — only when the user asks to build/compile. target is "x86sim" (functional) or "hw" (aiesim).
rc = comp.build(target="x86sim")

# VERIFY: build rc plus the libadf.a archive under the build tree.
import os, glob
ws = client.get_workspace()
libadf = glob.glob(os.path.join(ws, "my_aie", "build", "**", "libadf.a"), recursive=True)
print("BUILD_RC", rc, "LIBADF", libadf[0] if libadf else None, "OK", bool(libadf))
```

**Trust the evidence:** the build succeeded only if `rc` is 0 **and** `libadf.a` was produced.

## Chaining

**Produces** an AIE component in the open workspace, referenced **by name**; its `libadf.a`
exists **only after a build**. Hand off **by component name** (prefer
`client.get_component(name=...)` so it survives a console restart), not the live `comp` object.
See [`references/chaining.md`](references/chaining.md) for the artifact contract (inputs/outputs).

## Reporting to the user (keep it brief)

One-line status + a compact table; no narration of console/tool steps, no log echoes.

```
✅ AIE component <name> created (not built)        # after CREATE only
✅ AIE component <name> built successfully         # after BUILD

| Property | Value |
|----------|-------|
| Component | <name> |
| Target | <part or platform> |
| Template | <template> |
| Build target | <x86sim / hw> |
| libadf.a | <path — only after a build> |
| Build status | Not built  /  SUCCESS ✅ |
```

Follow-up: after create, "Build it (x86sim or hw)?"; after a build, report the component name
and `libadf.a` path. On failure: one line + the rc and the log path (`<workspace>/<name>/logs`).

## Notes

- **A transient shell-probe failure is a harness artifact**, not a Vitis or skill failure.
  Re-issue that one probe once (better: do the discovery inside the `vitis -s` Python session
  per [`references/headless.md`](references/headless.md)); never treat it as a real setup
  failure or restart the whole flow over it.
- **No transport/session code** — the tool + console provide the session (no port discovery,
  `create_client`, `getActiveComponentLocation`, `set_workspace`, `--flags`, `AIE_IDE_RESULT`
  JSON, or `dispose()`).
- **Long compiles run headless** — a `hw` (cycle-accurate) compile that may exceed the ~1h console
  ceiling runs via `vitis -s` outside the console instead.
- **IDE view may need a manual refresh.** A component created via the Python API shares the
  IDE's backend, so it *usually* appears in the Components/Explorer panel on its own — but
  there is **no** programmatic "refresh the GUI" API (`client.list_components()` returns data
  to the console, it does not repaint the panel). If the user doesn't see the new component,
  tell them to click the **Refresh** button in the Component Explorer; don't claim an API can
  force it. The component is real regardless of the panel (`client.get_component(name=...)`
  and the on-disk `libadf.a` are the source of truth).
