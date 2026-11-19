<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Create System Project (Vitis IDE AI agent, or headless CLI)

In **in-console** mode the agent runs **inside** the
Vitis Unified IDE with a live, connected Vitis Python Console, so there is **nothing to
launch, attach, or resolve** (no port discovery, no `create_client`, no workspace
resolution). You author Vitis Python API calls and run them with the `runVitisPython` tool;
they execute against the pre-bound `client` in the IDE's open workspace and the project
appears **live in the IDE**.

This is the **package step**: it assembles binary **containers**
(AIE + HLS kernels) plus an optional **PS/host app** against an extensible platform and runs
the Vitis **package** step to emit a **deployable image**. The `v++ --link` into a fixed XSA
is the integration step (`references/integration.md`); standalone bootgen is a separate boot-image step.

For a full `hw` image build (which can outlast the in-console `timeoutSeconds`), see the
`hw_emu` in-console note below.

Every recipe here **also runs headless** through the Vitis CLI. Pick the mode first.

## Step 0 — MANDATORY: pick the execution mode

Check whether **`runVitisPython` is in your available tool set**:

- **Present** → **in-console mode.** Use this file as written.
- **Absent** → **headless mode**, which is a fully supported path, not a blocker:
  1. Read [`system/headless.md`](system/headless.md) for the `vitis -s` wrapper.
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
- An **extensible platform** (or hardware `.xsa`) — a shipped base platform from
  `$XILINX_VITIS/base_platforms`. Resolve live via `client.add_platform_repos(...)` then an
  **anchored, exact-name** match (see "Resolve the platform by exact name"). One board ships
  several platforms on different flows (`vck190_base` = EDF/Yocto; `vck190_base_dfx`,
  `xilinx_vck190_base_202620_1`, `xilinx_vck190_base_dfx_202620_1` = PetaLinux); a bare substring
  silently picks the wrong one.
- The **components to package** (referenced by name, must already exist): AIE
  (from the AIE-component skill), HLS / integration `.xo` (from the integration step),
  and/or a host app. A `--template` (e.g. `vadd`) brings its own components.
- For a **Linux `hw` SD image**: a sysroot + rootfs + kernel Image — **ask the user for these
  paths** (see the environment note); the skill hardcodes none and must not guess them.

## How you run code — the `runVitisPython` tool

> **If `runVitisPython` errors or isn't available here, do not stop or report failure** — run
> the identical `code` through `vitis -s` instead (see Step 0 and [`system/headless.md`](system/headless.md)).

Every operation is the `code` payload of a `runVitisPython` call; the tool runs it in the
dedicated agent console and returns captured stdout/stderr.

- **Mutating calls prompt for confirmation** (create/build) — expected.
- **Output is truncated** (~20k, head+tail) — keep prints concise; don't dump full build logs.
- **A bare expression prints nothing** (code runs via `exec`). **Always `print(...)`** what
  you need to read back.
- **Set `timeoutSeconds` per operation.** The tool defaults to **180 s**, a fixed wall-clock
  limit (streaming output does not extend it); on expiry the call is interrupted and, if it
  does not recover within 15 s, the console restarts (REPL state lost). Quick queries: ~60;
  create/compose: a few hundred; a **`proj.build(...)`**: **~1800–3600s**. Any positive value
  is used as given (no maximum).

## `hw_emu` in-console, defer full `hw`

A `hw_emu` build + package fits a ~1800–3600 s `timeoutSeconds`. A full **`hw`** build is
Vivado implementation + package (**tens of minutes → hours**); if it may run longer than the
`timeoutSeconds` you pass, it is interrupted mid-build. So:

- **`hw_emu`** (default) → build + package **in-console** here.
- **`hw`** (deployable SD image / BOOT.BIN) → **run headless via `vitis -s`** outside the
  console, which has no tool timeout. Create/compose in-console is fine; hand the long
  `hw` build to a `vitis -s` run, or warn the user first.

## Pick the design path FIRST — full-system template one-shot vs hand-compose

Before composing containers by hand, decide — getting this wrong makes you hand-build a stack a
single call already produces:

- **Full AIE(-ML) + PL + PS system design → PREFER the template one-shot.** If the user asks for a
  **complete system design** (AIE/AIE-ML + PL kernels + PS/host together) and a shipped `ACCL_APP`
  template matches (`aie_sys_design`, `aie-ml_sys_design`, `vadd`, …), do **not** hand-compose:
  use its
  **`client.create_system_and_integration_project(name, platform=xpfm, template=tmpl,
  embedded_component_paths={...})`** one-shot. It creates the AIE + HLS + host components **and** the
  integration + per-target system projects in a single call, correctly wired. **Discover templates
  first:** `client.get_templates(type="ACCL_APP")`; match the device family (Versal **Gen2 / AIE-ML**
  → `aie-ml_sys_design`; Gen1 → `aie_sys_design`).
- **Hand-compose (the recipe below) only when** you are packaging an **already-produced**
  component/kernel set (e.g. a linked `.xo` handed off from `vitis-system`), or a
  custom mix no template covers.

> **Red flag — STOP:** about to `create_sys_project` + `add_container` + `add_component` for each
> piece of a standard AIE-ML + PL + PS design **without** first running
> `client.get_templates(type="ACCL_APP")` to check for a one-shot template. Check it first.

## Workflow (create/compose; build+package when prompted)

```
- [ ] 1. Parse request -> name, platform, containers[{name,components}], ps_app, build_target, packaging flow (EDF vs PetaLinux); note whether the user asked to BUILD. Map *PetaLinux flow* / *package with PetaLinux* -> packaging_mode='PetaLinux' (never rely on the API default when the user said PetaLinux).
- [ ] 1a. If the request is a full AIE(-ML)+PL+PS system design: get_templates(type="ACCL_APP") and PREFER the create_system_and_integration_project one-shot (see "Pick the design path FIRST")
- [ ] 2. Ensure components exist + BUILT — if missing, LOAD & follow the producer (the AIE-component skill / the integration flow references/integration.md), approval-gated; if created-but-unbuilt, offer to build it (ask + approve); don't improvise
- [ ] 3. CREATE + compose: create_sys_project(..., packaging_mode=...) [or template one-shot with the same] -> add_container -> add_component -> add_cfg_files. Report it (composed, unbuilt).
- [ ] 3b. VERIFY packaging mode: echo packagingMode from vitis-sys.json; if the user asked PetaLinux and JSON is EDF, STOP (recreate with packaging_mode='PetaLinux'). Then, for PetaLinux: if the project platform is an .xpfm, grep <project>/package/CMakeLists.txt for --package.generate_sdcard — if missing, STOP; if the platform is a fixed .xsa, Rigel does not add generate_sdcard — add the boot files and generate_sdcard=true per "Fixed-XSA platform" (see system/packaging.md).
- [ ] 3c. If the platform is a fixed .xsa linked from an AIE design and the project has no AIE component, add a top-level input_files=<libadf.a> to package/package.cfg before building, or v++ -p fails with 82-3880 (see "Fixed-XSA platform from an AIE design" in system/packaging.md).
- [ ] 4. BUILD + PACKAGE only when asked (target hw_emu; defer hw to headless) -> VERIFY the package artifact -> report.
- [ ] 5. Hand off to the on-board debug/flash flow for hw.
```

**Create/compose only unless asked to build.** On a "create"/"compose" request, compose the
project and **stop — do not build/package**. When the user asks to build (a separate follow-up)
or to create *and* build in one go, run the full component+link build with
`proj.build(target="hw_emu", build_comps=True, build_package=False)` — the emulation package
(`launch_hw_emu.sh` + emu `.xclbin`) comes out of that build. **Do not pass `build_package=True`**
(`hw` or `hw_emu`; it skips the link — see the build gotcha below).

> **Downstream needs the PACKAGE.** A composed-but-unbuilt project has no image; the on-board
> debug/flash flow consumes the package, so build/package before deploying (or offer to build first).

**If the name already exists:** live workspace, **no `--clean`** — do not delete a project
from the running session. Pick a new `name`, rebuild the existing one
(`client.get_sys_project(name=...)` → `.build(...)`), or ask the user to remove it in the IDE.

> **Environment note (sysroot required for a Linux `hw` package) — ASK the user for it.** The
> accelerated host is an embedded **Linux + XRT** app — building/packaging it needs an **aarch64
> Versal Linux sysroot with XRT** (from a 2026.x Common Image `sdk.sh`), plus the matching rootfs
> and kernel `Image`, passed via `embedded_component_paths`. **These are user inputs: ASK the user
> for the sysroot / rootfs / kernel-image paths.** Do **NOT** hardcode a path, guess a
> device-family directory, scan the filesystem to discover one, or substitute the install's
> bundled toolchain sysroot — the skill ships **no** default sysroot location. Validate what the
> user provides (a full PetaLinux/Yocto sysroot has a top-level `/lib`); if the user doesn't have
> one, say so and stop — don't fabricate one. Without it, `create_sys_project` **succeeds** but the
> host component is flagged "sysroot not set" and build/package cannot complete. This is a missing
> input, not a skill defect.

### Inputs

| Parameter | Required | Notes |
|-----------|----------|-------|
| `name` | Y | project name |
| `platform` | Y | extensible `.xpfm` path or name |
| `containers` | Y | binary containers + assigned AIE/HLS component names |
| `ps_app` | N | PS/host application component name |
| `build_target` | N (`hw_emu`) | `hw_emu` in-console; `hw` → headless |
| `packaging_mode` | Y (implicit) | `'EDF'` or `'PetaLinux'` on `create_sys_project` / `create_system_and_integration_project`. **Must match the platform's packaging flow** — see [`system/packaging.md`](system/packaging.md). User phrases: *"EDF flow"*, *"Yocto/EDF packaging"*, *"PetaLinux flow"*, *"package with PetaLinux"*. If unstated, infer from the resolved `.xpfm` (table below + custom-platform rules in `packaging.md`); **echo `packagingMode` from `vitis-sys.json` after compose** so a wrong default is visible. |
| sysroot / rootfs / kernel Image | N (Y for Linux `hw`) | `embedded_component_paths={...}` — **ask the user for these paths**; never hardcode/guess |
| BMA metadata (`.bma`) | N | **Not** an `embedded_component_paths` key — pass via `package/package.cfg` (`bma=…` under `[package]`) when needed; see [`system/packaging.md`](system/packaging.md). |

See [`system/system_project_api.md`](system/system_project_api.md)
(full `create_sys_project` signature, compose/build/package methods, `add_precompiled_kernel`,
targets, output artifacts) and
[`system/packaging.md`](system/packaging.md)
(hw_emu vs hw split, Linux SD-image inputs).

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

### Custom / user-built platforms (not in the table)

A custom extensible `.xpfm` (e.g. an `*_e2e` platform exported from the embedded platform flow) is
**not** in the table above, so its flow can't be looked up there. For those:

1. **Ask** if the user hasn't said: *"Is this platform EDF/Yocto packaging or PetaLinux packaging?"*
2. An **EDF-style** extensible platform (a typical e2e export on Versal) → `packaging_mode='EDF'`.
3. A shipped **PetaLinux** base platform (a `xilinx_*_base_*` name, not the EDF `vck190_base`) → `packaging_mode='PetaLinux'`.
4. **After compose**, read `<project>/vitis-sys.json` → `"packagingMode"` (`PACKAGING_MODE_EDF` vs
   `PACKAGING_MODE_PETALINUX`) and report it. If it disagrees with the user's stated flow, **STOP
   before `proj.build()`** — fix `packaging_mode` at create time (recreate or clone; don't silently
   repackage with the wrong mode, since the API default is `'EDF'`).

## The recipe (compose; build+package when prompted, in-console)

Run as `runVitisPython` calls (state persists across them).

```python
# RESOLVE the extensible platform by its EXACT name (see "Resolve the platform by exact name").
# find_platform_in_repos(name) is a REGEX with firstMatch=True, so a bare substring
# ("vck190_base" / "xilinx_vck190_base_202") silently grabs a sibling on a DIFFERENT flow.
client.add_platform_repos("/path/to/base_platforms")
plat = "vck190_base"                                   # the platform the user named, verbatim
hits = sorted(set(client.find_platforms_in_repos(f"/{plat}/{plat}.xpfm")))
if len(hits) != 1:                                     # 0 or >1 -> STOP, list candidates, ask the user
    raise SystemExit(f"Platform '{plat}' not uniquely resolved: {hits}")
xpfm = hits[0]
print("PLATFORM", xpfm)                                # echo so a wrong pick is visible

# CREATE + compose (default). Components (aie_test/mm2s/host_app) must already exist in the workspace.
# packaging_mode MUST match the resolved platform flow (see "Resolve..." + custom-platform rules).
pack_mode = "EDF"                                      # or "PetaLinux" — from the prompt / platform table / ask
proj = client.create_sys_project(
    name="vadd_system", platform=xpfm, packaging_mode=pack_mode,
    template=client.get_template(type="ACCL_APP", name="empty_accelerated_application", platforms=[xpfm]))
proj.add_container(name="binary_container_1")
proj.add_component(name="aie_test", container_name=["binary_container_1"])
proj.add_component(name="mm2s", container_name=["binary_container_1"])
proj.add_component(name="host_app")                       # PS/host app (no container)
proj.add_cfg_files(name="binary_container_1", cfg_files=["/…/system.cfg"])
print("SYS_COMPOSED (unbuilt)", proj)

# VERIFY the packaging mode resolved as intended (a wrong EDF/PetaLinux default is otherwise silent).
import json, os
with open(os.path.join(client.get_workspace(), "vadd_system", "vitis-sys.json")) as f:
    print("PACKAGING_MODE", json.load(f).get("packagingMode"))

# INGEST a precompiled .xo from vitis-system, if handing off by path:
# proj.add_precompiled_kernel(xo_file_path="/…/vadd_int_v1_vadd/vadd/vadd.xo", containers=["binary_container_1"])
```

```python
# BUILD + PACKAGE — only when the user asks. Do NOT pass build_package=True (hw or hw_emu) — it builds
# only the package step and skips v++ --link (no XSA -> package fails). Run the full component+link
# build; it emits the emulation package (launch_hw_emu.sh + emu .xclbin). hw -> run via vitis -s.
rc = proj.build(target="hw_emu", build_comps=True, build_package=False)

# VERIFY: the package artifact exists and is non-trivial.
import os, glob
ws = client.get_workspace()
pkg = glob.glob(os.path.join(ws, "vadd_system", "build", "**", "package", "**"), recursive=True)
img = [p for p in pkg if p.endswith(("sd_card.img", "BOOT.BIN", "launch_hw_emu.sh")) or p.endswith(".xclbin")]
print("BUILD_RC", rc, "IMAGE", img[:3], "PACKAGED", bool(img))
```

Report package contents from the real emitted tree — never invent partition/size numbers.
`hw_emu` success = the emulation package is produced; `hw` success = deployable image produced
(on-board boot is out of scope → the on-board debug/flash flow).

## Build gotcha — `build_package=True` skips the link (EDF projects)

`build_package=True` builds **only** the package step. In an EDF project (the
`create_sys_project` default) the generated `VppPackage` target depends only on the xclbin, not
on `VppLink_<container>`, so `v++ -p` fails on the missing `build/<target>/hw_link/<container>.xsa`
(`v++ 60-587`, `60-702`). This hits **`hw` and `hw_emu`** alike (`hw` run and the CMake dependency checked on 2026.2 and 2027.1);
`build_comps=True` only builds the components, not the link. Run the full
**components → link → package** flow instead:

```python
rc = proj.build(target="hw_emu", build_comps=True, build_package=False)   # or target="hw" (via vitis -s)
```

For `hw_emu` the emulation package (`launch_hw_emu.sh` + emu `.xclbin`) is produced by that build.

## Chaining

One hop in `AIE → integration → system → target`. Full contract:
[`chaining.md`](chaining.md).
This skill's edges:

- **Upstream:** the integration (link) step → consumes its linked kernels **by name** (same
  workspace) or its `.xo` **by path** (`add_precompiled_kernel`). If missing, **REQUIRED
  SUB-SKILL:** load and follow the integration flow (`references/integration.md`, which itself loads the AIE-component skill) to produce them (ask + approve); if a component/project is only
  created-but-unbuilt, **offer to build it (ask + approve)** — don't improvise — then continue.
- **Produces:** a deployable image (`sd_card.img` / `BOOT.BIN` / `launch_hw_emu.sh` /
  `.xclbin`) under `<workspace>/<name>/build/<target>/…/package/` — **only after build+package**;
  a composed-but-unbuilt project has no image yet.
- **Downstream:** the on-board debug/flash flow — a **shell** skill (xsdb / program_flash) with no
  `client`/REPL, so hand off the **image PATH** (or the package dir), not a live object.
- **Boundary with standalone bootgen:** don't double-package — this skill's package step is the
  system/acceleration path; standalone bootgen produces explicit BIF/component boot images.

## Reporting to the user (keep it brief)

One-line status + a compact table; no narration of console/tool steps, no log echoes.

```
✅ System project <name> composed (not built)              # after CREATE/compose only
✅ System project <name> built and packaged successfully   # after build+package

| Property | Value |
|----------|-------|
| Name | <name> |
| Platform | <platform> |
| Containers | <containers> |
| Build target | <hw_emu / hw> |
| Packaged | <yes/no — yes only after build> |
| Image / package | <path — only after build> |
| Build status | Not built  /  SUCCESS ✅ |
```

Follow-up: after compose, "Build + package now?"; after a build, report the package artifact
path. On failure: one line + `build_status` + the log path.

## Notes

- **No transport/session code** — the tool + console provide the session (no port discovery,
  `create_client`, `getActiveComponentLocation`, `set_workspace`, `--flags`, `SYSTEM_RESULT`
  JSON, or `dispose()`).
- **`hw` runs headless** — a full Vivado-impl `hw` image build runs via `vitis -s` outside the
  console (no tool timeout).
