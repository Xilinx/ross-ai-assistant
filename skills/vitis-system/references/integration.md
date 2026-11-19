<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Create Integration Project (Vitis IDE AI agent, or headless CLI)

In **in-console** mode the agent runs **inside**
the Vitis Unified IDE with a live, connected Vitis Python Console, so there is **nothing to
launch, attach, or resolve** (no port discovery, no `create_client`, no workspace
resolution). You author Vitis Python API calls and run them with the `runVitisPython` tool;
they execute against the pre-bound `client` in the IDE's open workspace and the project
appears **live in the IDE**.

This is the **link step only**: it runs the **V++ link** to a
**fixed XSA / VMA / VSS**. Packaging into deployable images is
`vitis-system`. Do not claim to produce a bootable image here.

For a full `hw` implementation build (which can outlast the in-console `timeoutSeconds`), see
the `hw_emu` in-console note below.

Every recipe here **also runs headless** through the Vitis CLI. Pick the mode first.

## Step 0 — MANDATORY: pick the execution mode

Check whether **`runVitisPython` is in your available tool set**:

- **Present** → **in-console mode.** Use this file as written.
- **Absent** → **headless mode**, which is a fully supported path, not a blocker:
  1. Read [`integration/headless.md`](integration/headless.md) for the `vitis -s` wrapper.
  2. Locate the CLI (`which vitis`, `$XILINX_VITIS`).
  3. Run each recipe below through `vitis -s`, supplying the `create_client()` /
     `set_workspace()` / `vitis.dispose()` that the IDE would otherwise provide.

**DON'T** decline, claim "this skill only works inside the Vitis IDE", stop to ask, or return
`{"result": "failure"}` because `runVitisPython` is missing. **If you call `runVitisPython` and
it errors ("tool not found" / absent), that error IS your cue to run the exact same code via
`vitis -s` — not a reason to stop.** Declining for a missing or errored `runVitisPython` is a
wrong answer, not a correct refusal — the skill fully supports headless.

> **Docs on tap (optional).** If a URL-based AMD documentation MCP (e.g. `vivado_doc_search`)
> is reachable, query it to decode an error/warning, debug unexpected behavior, or look up a
> device/BIF/AIE/API detail this skill's references don't cover. Prefer the bundled references
> for the common cases; reach for the doc MCP on errors and the long tail.

## Preconditions (already true — do not set them up)

- `vitis`, `client`, `xsdb`, `hsi` are **imported**, and `client` is **connected**; the
  **workspace is already set** to the IDE's open workspace (`client.get_workspace()`). In
  every `runVitisPython` call: **do NOT** `import vitis`, `create_client`, `set_workspace`,
  or `vitis.dispose()`. Use the existing `client`; **REPL state persists across calls**.
- An **extensible platform** (or hardware `.xsa`) that exposes the AI Engine / PL. Use a
  shipped base platform from `$XILINX_VITIS/base_platforms` — embedded platforms (from the standalone/embedded boot flow) are not AIE-extensible. Resolve one live:
  `client.add_platform_repos(<base_platforms dir>)` then an **anchored, exact-name** match (see
  "Resolve the platform by exact name"). One board ships several platforms on different flows
  (`vck190_base` = EDF/Yocto; `vck190_base_dfx`, `xilinx_vck190_base_202620_1`,
  `xilinx_vck190_base_dfx_202620_1` = PetaLinux); a bare substring silently picks the wrong one.
- The **components to link**: AIE component(s) (from the AIE-component skill) and/or
  HLS kernel(s), referenced **by name** in the open workspace, or prebuilt `.xo` / `libadf.a`.
  A component that is only created-but-unbuilt has no `.xo`/`libadf.a` to link — **offer to build
  it (ask + approve) before linking** (see *Chaining*).

## How you run code — the `runVitisPython` tool

> **If `runVitisPython` errors or isn't available here, do not stop or report failure** — run
> the identical `code` through `vitis -s` instead (see Step 0 and [`integration/headless.md`](integration/headless.md)).

Every operation is the `code` payload of a `runVitisPython` call; the tool runs it in the
dedicated agent console and returns captured stdout/stderr.

- **Mutating calls prompt for confirmation** (create/build) — expected.
- **Output is truncated** (~20k, head+tail) — keep prints concise; don't dump full link logs.
- **A bare expression prints nothing** (code runs via `exec`). **Always `print(...)`** what
  you need to read back.
- **Set `timeoutSeconds` per operation.** The tool defaults to **180 s**, a fixed wall-clock
  limit (streaming output does not extend it); on expiry the call is interrupted and, if it
  does not recover within 15 s, the console restarts (REPL state lost). Quick queries: ~60;
  create/compose: a few hundred; a **`proj.build(target="hw_emu")`** link: **~1800–3600s**.
  Any positive value is used as given (no maximum).

## `hw_emu` in-console, defer full `hw`

A `hw_emu` link fits a ~1800–3600 s `timeoutSeconds`. A full **`hw`** link is Vivado synth +
place-and-route + PDI (**tens of minutes → hours**); if it may run longer than the
`timeoutSeconds` you pass, it is interrupted mid-build. So:

- **`hw_emu`** (default for non-VSS output) → build **in-console** here. **Not valid for
  `build_output_type='VSS'`** — VSS projects only list `hw` in `supported_targets`.
- **`hw`** (deployable impl; **the only target for VSS**) → **run headless via `vitis -s`**
  outside the console, which has no tool timeout. Create/compose in-console is fine; hand
  the long `hw` build to a `vitis -s` run, or warn the user before attempting it in-console.

## If a build fails with `GLIBCXX` / `libstdc++` / `binutils` (host env, not the design)

`proj.build()` spawns the host's `cmake`/`v++`, which inherit the environment the **IDE was
launched with**. If the IDE was started so that the Vitis-bundled `libstdc++` shadows a newer
system one, the build dies with e.g. *"version `GLIBCXX_3.4.32' not found (required by
/usr/bin/cmake)"*. This is a **host launch-environment** problem, **not** the project — and it
**cannot be fixed from inside `runVitisPython`** (the build subprocess already inherited the
IDE's launch env; setting `os.environ`/`LD_PRELOAD` in the REPL does not reach it). **Report it
and stop; do NOT hand-run `cmake`/`make` to route around it.** The fix is to relaunch the IDE
with the system `libstdc++` preloaded, resolving its path rather than hardcoding it
(`/usr/lib/x86_64-linux-gnu` on Debian/Ubuntu, `/usr/lib64` on RHEL/SLES):
`LD_PRELOAD="$(ldconfig -p | awk '/libstdc\+\+\.so\.6/ {print $NF; exit}')" <install>/Vitis/bin/vitis`.

## Pick the design path FIRST — Mode A (template one-shot) vs Mode B (hand-compose)

Before you create *anything*, decide which path fits the request — getting this wrong makes you
hand-build a stack a single call already produces:

- **Mode A — template one-shot (PREFER when a shipped template matches).** If the user asks for a
  **complete AIE(-ML) + PL + PS system design** (or names a design that maps to a shipped
  `ACCL_APP` template — `aie_sys_design`, `aie-ml_sys_design`, `vadd`, …), use **one call**:
  `client.create_system_and_integration_project(name, platform=xpfm, template=tmpl,
  embedded_component_paths={...}, packaging_mode='EDF'|'PetaLinux')`. Set **`packaging_mode`** to match
  the resolved `.xpfm` flow (same rules as [`system/packaging.md`](system/packaging.md) — the vck190 table plus
  "ask" for a custom `*_e2e`-style name); the API default `'EDF'` is wrong for a PetaLinux platform if the
  user never stated the flow. It creates the AIE + HLS kernels + host app + integration
  project + per-target system projects together, correctly wired — no hand-authoring, no
  scavenging. **Discover templates first:** `client.get_templates(type="ACCL_APP")`, and match the
  device family (Versal **Gen2 / AIE-ML** → `aie-ml_sys_design`; Gen1 → `aie_sys_design`).
  The secondary Linux system projects need `embedded_component_paths` (sysroot / rootfs / kernel
  image) — **ask the user for those paths; the skill hardcodes none and must not guess or scan for
  them** (the integration link → XSA itself needs no sysroot).
- **Mode B — hand-compose (only when no template matches).** Manually create each component and wire
  them with `create_container_options` + `create_integration_project`. Use this only for a **custom**
  component set the templates don't cover, or when the user supplies their own sources.

> **Red flag — STOP:** about to `create_aie_component` + `create_hls_component` + host one-by-one for
> a standard "AIE-ML + PL + PS system design" **without** first running
> `client.get_templates(type="ACCL_APP")` to check for a matching one-shot template. Check Mode A first.

## Workflow (create/compose; link when prompted)

```
- [ ] 1. Parse request -> name, platform, components, output type, connectivity, target; note whether the user asked to BUILD/LINK
- [ ] 1a. If the request is a full AIE(-ML)+PL+PS system design: get_templates(type="ACCL_APP") and PREFER Mode A one-shot if a template matches (see "Pick the design path FIRST")
- [ ] 2. Resolve the extensible platform (.xpfm from base_platforms if none given)
- [ ] 3. Ensure components exist + BUILT (see "Match the design to the request"): missing AIE -> LOAD & follow the AIE-component skill (shipped `simple` example, approval-gated); if a component is created-but-unbuilt, offer to build it (ask + approve)
- [ ] 4. CREATE + compose: create_integration_project(...) with container_options (or the template one-shot). Report it (composed, unlinked).
- [ ] 5. LINK only when asked: proj.build(target hw_emu; defer hw to headless; VSS -> target hw ONLY) -> VERIFY the fixed XSA/VMA/VSS -> report.
```

**Create/compose only unless asked to link.** On a "create"/"compose" request, compose the project
and **stop — do not link**. Run `proj.build(target="hw_emu")` (the V++ link) only when the user
asks to build/link (a separate follow-up) or asks to create *and* link in one go. **Exception:
`build_output_type='VSS'` → `proj.build(target="hw")` only** (`hw_emu` raises `ValueError`).

> **Downstream needs the LINKED output.** A composed-but-unlinked project has no fixed `.xsa`;
> the system (packaging) step (the consumer) will **offer to link it (ask + approve) before
> packaging** — never silently.

**If the name already exists:** live workspace, **no `--clean`** — do not delete a project
from the running session. Pick a new `name`, rebuild the existing one
(`client.get_sys_project(name=...)` → `.build(...)`), or ask the user to remove it in the IDE.

### Inputs

| Parameter | Required | Notes |
|-----------|----------|-------|
| `name` | Y | project name |
| `platform` / `hw_design` | Y for XSA/VMA (one) | extensible `.xpfm` path or name (base_platforms). **VSS: omit / leave `hw_design` empty** — a non-empty value is rejected by the server (the link is part-based, not platform-based) |
| `template` (Mode A) | N | `aie_sys_design`, `aie-ml_sys_design`, `vadd`, … (one-shot) |
| `packaging_mode` (Mode A) | Y (implicit) | `'EDF'` / `'PetaLinux'` on `create_system_and_integration_project` — match the platform flow; see [`system/packaging.md`](system/packaging.md) |
| `aie_component` / `hls_kernel` (Mode B) | N | existing component names / `name:top:src.cpp` |
| `output_type` | N (`XSA`) | `XSA` / `VMA` / `VSS` — **VMA:** the Vitis Export to Vivado archive (`v++ --link --export_archive`): the link stops before synthesis and you finish implementation in Vivado; needs a custom Versal platform that supports it, not `hw_emu` (UG1701). **VSS:** Mode B only; add **`vss=`** in the link cfg before build ([`connectivity-cfg.md`](integration/connectivity-cfg.md)); `proj.build(target='hw')` only |
| `connectivity` | N | stream specs (`mm2s_1.s:ai_engine_0.DataIn1`) → a cfg |
| `target` | N (`hw_emu`) | `hw_emu` in-console; `hw` → headless. **VSS: `hw` only** — `hw_emu` raises `ValueError: ... Valid targets - hw` |

See [`integration/integration_api.md`](integration/integration_api.md)
(full `create_integration_project` / `create_container_options` signatures, component
helpers, targets, output artifact) and
[`integration/connectivity-cfg.md`](integration/connectivity-cfg.md)
(connectivity cfg format).

## Resolve the platform by exact name (never a bare substring)

`find_platform_in_repos(name)` matches `name` as a **regex** and returns the **first** hit
(`firstMatch=True`). One board ships several platforms on **different flows** — on vck190:

| Platform | Flow |
|---|---|
| `vck190_base` | Embedded Accel (**EDF / Yocto**) |
| `vck190_base_dfx` | Embedded Accel (PetaLinux) |
| `xilinx_vck190_base_202620_1` | Embedded Accel (PetaLinux) |
| `xilinx_vck190_base_dfx_202620_1` | Embedded Accel (PetaLinux) |

A bare `find_platform_in_repos("vck190_base")` returns `xilinx_vck190_base_dfx_202620_1` (wrong
flow); `"xilinx_vck190_base_202"` returns the non-dfx PetaLinux one — **neither lands on the EDF
`vck190_base`.** So:

1. Match the **exact name the user gave**, anchored: `find_platforms_in_repos(f"/{plat}/{plat}.xpfm")` (or `f"^{plat}$"`).
2. **If it resolves to ≠1 platform, STOP** — print the candidates (`.xpfm` paths / flow) and ask which; never silently pick one.
3. **Echo the resolved `.xpfm`**, and ensure `packaging_mode` matches the platform's flow (**EDF** vs **PetaLinux**) — a mismatch fails packaging.

## The recipe (compose; link when prompted, in-console)

Run as `runVitisPython` calls (state persists across them).

```python
# RESOLVE the extensible platform by its EXACT name (see "Resolve the platform by exact name").
# find_platform_in_repos(name) is a REGEX with firstMatch=True -> a bare substring grabs a sibling on a different flow.
client.add_platform_repos("/path/to/base_platforms")
plat = "vck190_base"                                   # the platform the user named, verbatim
hits = sorted(set(client.find_platforms_in_repos(f"/{plat}/{plat}.xpfm")))
if len(hits) != 1:                                     # 0 or >1 -> STOP, list candidates, ask the user
    raise SystemExit(f"Platform '{plat}' not uniquely resolved: {hits}")
xpfm = hits[0]
print("PLATFORM", xpfm)

# COMPOSE (default): container options wire AIE + HLS (+ optional user .xo / cfg) into a container.
# cfg_files is a RELATIVE bare filename that lives in the project dir (see Gotchas).
container_options = client.create_container_options(
    "binary_container_1", hw_design=xpfm,
    aie_component=["aie_test"],            # existing AIE component name(s)
    kernels=["mm2s", "s2mm"],              # existing HLS component name(s)
    cfg_files=["system.cfg"])              # RELATIVE cfg name (lives in the project dir)
proj = client.create_integration_project(
    name="aie_hls_int", build_output_type="XSA", container_options=container_options)
print("INT_COMPOSED (unlinked)", proj)

# WRITE connectivity AFTER create. create scaffolds a system.cfg with only the `nk` lines, so a
# cfg written BEFORE create (or wiped by a manual cmake reconfigure) loses your stream_connect.
import os
cfg = os.path.join(client.get_workspace(), "aie_hls_int", "system.cfg")
with open(cfg, "w") as fh:
    fh.write("[connectivity]\n"
             "nk=mm2s:1:mm2s_1\n"
             "nk=s2mm:1:s2mm_1\n"
             "stream_connect=mm2s_1.s:ai_engine_0.DataIn1\n"
             "stream_connect=ai_engine_0.DataOut1:s2mm_1.s\n")
print(open(cfg).read())                    # confirm nk + stream_connect are BOTH on disk
```

```python
# LINK — only when the user asks to build/link (a separate follow-up). Do NOT hand-run cmake/make
# (a manual reconfigure regenerates system.cfg and drops stream_connect). hw_emu in-console; hw via vitis -s.
# XSA example. For build_output_type='VSS' use proj.build(target="hw") ONLY (hw_emu is rejected).
rc = proj.build(target="hw_emu")

# VERIFY: build rc plus the fixed XSA (or VMA/VSS) under the project tree.
import os, glob
ws = client.get_workspace()
xsa = glob.glob(os.path.join(ws, "aie_hls_int", "**", "*.xsa"), recursive=True)
print("BUILD_RC", rc, "XSA", xsa[0] if xsa else None, "OK", bool(xsa))
```

For the **template one-shot** (Mode A), use
`client.create_system_and_integration_project(name, platform=xpfm, template=tmpl, ...)` then
`proj = client.get_sys_project(name)` (see the API reference). Report resource utilization
only from the real v++ link report — never invent numbers.

## Gotchas (field-validated — check these before you link)

These three bit a real run (aie_simple + mm2s/s2mm on
`xilinx_vck190_base_202620_1`); the in-console flow must apply the same discipline.

1. **"No stream resources found …" means the link ran with NO connectivity — two causes, check
   both.** v++ fails with *"No stream resources found that can accommodate `ai_engine_0.DataIn1`"*
   whenever no `stream_connect` reached the linker. Either:
   (a) **the edited file is not the one the link reads** — a relative `system.cfg` is
   `<proj>/system.cfg`, but an absolute `/foo/system.cfg` is **copied** to
   `<proj>/hw_link/system.cfg` at create and the link reads the copy, so later edits to
   `/foo/system.cfg` never reach v++. Prefer a relative bare name (`cfg_files=["system.cfg"]`);
   with an absolute path, edit `hw_link/<name>`; **or**
   (b) **`system.cfg` had no `stream_connect` at build time** — create scaffolds a `system.cfg`
   with only the `nk` lines, so a cfg written **before** create, or regenerated by a **manual
   `cmake` reconfigure**, loses your `stream_connect`. Write the cfg **after** create, build via
   `proj.build()` (never hand-run cmake/make), and **read the on-disk `system.cfg` right before
   building to confirm it has BOTH `nk` and `stream_connect`.**
2. **Never re-add `flow_target=vitis` to an HLS component's `hls_config.cfg`.**
   `create_hls_component(...)` **already writes** `flow_target=vitis`. Adding it again (as some
   API examples show) creates a **duplicate ini entry** that HLS rejects and the kernel build
   fails. Only add the lines that aren't already there (`syn.file`, `syn.top`).
3. **Set the HLS `part` to match the extensible platform's silicon.** A wrong or stand-in part
   makes the HLS build fail or produces a kernel that won't link against the platform. Use the
   platform's device part (e.g. `xilinx_vck190_base_202620_1` → `xcvc1902-vsva2197-2MP-e-S`).

**Before calling `proj.build(...)`, verify:** (a) you know the on-disk cfg the link reads (the
`cfgFiles` entry in `vitis-sys.json`: `system.cfg` or `hw_link/<name>`); (b) that file actually
contains the expected `stream_connect=` lines and the AIE PLIO names (`DataIn1`, `DataOut1`, …)
match the graph source (case-sensitive). See
[`integration/connectivity-cfg.md`](integration/connectivity-cfg.md) for the cfg path detail.

## Match the design to the request (determinism + fidelity)

The **same prompt must produce the same design regardless of what already sits in the
workspace.** Two field runs of the *identical* `aie_simple + mm2s + s2mm` prompt diverged — an
empty workspace built a genuinely simple 2-PLIO graph, while a workspace holding a leftover
`aie_sys_design` got a scavenged **4-PLIO** graph plus an **unrequested `polar_clip`** kernel —
because "where do `aie_simple`'s sources come from" was left to chance.

- **Missing AIE component → load and follow the AIE-component skill; don't improvise.**
  **REQUIRED SUB-SKILL:** before you create *any* AIE component, **load the AIE-component
  skill and follow it** — do **not** author a graph from your own knowledge, and do **not** copy
  sources you found in the workspace or the shipped examples to hand-roll one. Create it
  (approval-gated) from the shipped `installed_aie_examples/simple` example (named PLIOs
  `DataIn1`/`DataOut1`). Reading example `.cpp`/`.h` files to write your own graph **is** the
  improvisation this forbids — it silently changes the design and is why the same prompt has
  produced different graphs across runs.
- **Match PLIO topology to the requested kernels.** Link exactly the kernels asked for. If the
  AIE graph exposes **more** PLIOs than those kernels can connect (e.g. a 4-PLIO graph for a
  `mm2s`+`s2mm` request), **STOP and ask** — do not invent extra kernels (like `polar_clip`) to
  fill them and do not leave PLIO ports unconnected (the link fails: *"port should have one and
  only one valid connection"*). `mm2s`+`s2mm` wants a 1-input/1-output graph.
- **Report the design you actually built — never relabel a substitute.** State the graph's real
  PLIO count, the actual kernels linked, and **whether it matches the request**. If you created a
  component, added a kernel, or used a different graph than asked, say so in the report. A build
  that succeeds but does not match the request is a **deviation, not a success** — report it as
  such, with the reason.

**Red flags — STOP:**
- About to create an AIE component (or read example graph sources to author one) **without having
  loaded the AIE-component skill** — load it and follow it first.
- About to import AIE sources from an unrelated workspace component to stand in for a requested one.
- About to add a kernel the user didn't ask for, just to satisfy extra PLIOs.
- About to report ✅ for a graph/kernel set that differs from the request without disclosing it.

## Chaining

One hop in `AIE → integration → system → target`. Full contract:
[`chaining.md`](chaining.md).
This skill's edges:

- **Upstream:** the AIE-component skill (and HLS) → consumes components **by name**. If an AIE
  component is missing, offer to create it first from the shipped `simple` example (ask +
  approve; see "Match the design to the request"); if it exists but is only created-but-unbuilt,
  **offer to build it (ask + approve) before linking** — don't scavenge.
- **Produces:** a fixed **`.xsa`** (or VMA/VSS) under `<workspace>/<name>/`, plus the compiled
  kernel `.xo` — **only after the link (`proj.build()`) runs**; a composed-but-unlinked project
  has neither yet.
- **Downstream:** the system flow (`references/system.md`) packages the linked output; if this project is only
  composed-but-unlinked, it will offer to link it first. Hand off the kernel **by name** or by
  the `.xo` **path**. Don't package here — this link step stops at the fixed XSA.

## Reporting to the user (keep it brief)

One-line status + a compact table; no narration of console/tool steps, no log echoes.

```
✅ Integration project <name> composed (not linked)   # after CREATE/compose only
✅ Integration project <name> linked successfully     # after the V++ link

| Property | Value |
|----------|-------|
| Name | <name> |
| Mode | <template / compose> |
| Platform | <platform> |
| Output type | <XSA / VMA / VSS> |
| Target | <hw_emu / hw — VSS: hw only> |
| Artifact | <path — only after the link> |
| Build status | Not linked  /  SUCCESS ✅ |
```

Follow-up: after compose, "Run the V++ link now?"; after link, "Package it into a system
project?". On failure: one line + `build_status` + the log path (`<workspace>/<name>/logs`, v++ link log).

## Notes

- **No transport/session code** — the tool + console provide the session (no port discovery,
  `create_client`, `getActiveComponentLocation`, `set_workspace`, `--flags`,
  `INTEGRATION_RESULT` JSON, or `dispose()`).
- **`hw` runs headless** — a full Vivado-impl `hw` link runs via `vitis -s` outside the console
  (no tool timeout).
