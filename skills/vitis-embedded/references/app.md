<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Create Embedded Application (Vitis IDE AI agent, or headless CLI)

In **in-console** mode the agent runs **inside** the
Vitis Unified IDE with a live, connected Vitis Python Console, so there is **nothing to
launch, attach, or resolve**. You author Vitis Python API calls and run them with the
`runVitisPython` tool; they execute against the pre-bound `client` in the IDE's open
workspace and the app appears **live in the Components panel**.

Like the platform console skill, this ships **knowledge, not a program**: which calls to
make, how the platform's domains decide the app OS, how Linux sysroots work, and how to
verify a real ELF was produced.

Every recipe here **also runs headless** through the Vitis CLI. Pick the mode first.

## Step 0 — MANDATORY: pick the execution mode

Check whether **`runVitisPython` is in your available tool set**:

- **Present** → **in-console mode.** Use this file as written.
- **Absent** → **headless mode**, which is a fully supported path, not a blocker:
  1. Read [`app/headless.md`](app/headless.md) for the `vitis -s` wrapper.
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
- A **BUILT** platform `.xpfm` (export `.buildstatus == SUCCESS`, compiled BSP present) —
  "created" is not enough; an app on a created-but-unbuilt platform links against absent
  artifacts. If the platform is **missing or only created-but-unbuilt**, **offer to build it
  (ask + approve) then continue** — do not dead-end (see *Chaining*).
- **Platform in another workspace?** A platform created in the *current* workspace resolves
  automatically; one from another workspace (or a shipped/custom location) must first be
  registered — `client.add_platform_repos('<platform workspace>/<plat>/export')` — then passed
  by its `.xpfm` path (or resolved via `client.find_platform_in_repos('^<plat>$')`), or the
  create can't find it. Register the original `export/` dir; don't copy a built platform.

## How you run code — the `runVitisPython` tool

> **If `runVitisPython` errors or isn't available here, do not stop or report failure** — run
> the identical `code` through `vitis -s` instead (see Step 0 and [`app/headless.md`](app/headless.md)).

Every operation is the `code` payload of a `runVitisPython` call; the tool runs it in the
dedicated agent console and returns captured stdout/stderr. As with the platform skill:

- **Mutating calls prompt for confirmation** (create/build) — expected.
- **Output is truncated** (~20k, head+tail) — keep prints concise; don't dump full build logs.
- **A bare expression prints nothing** (code runs via `exec`, which discards expression
  values). **Always `print(...)`** anything you need to read back.
- **Set `timeoutSeconds` per operation.** The tool defaults to **600s** (with an idle
  watchdog that aborts only after ~5 min of *no output* AND no completion). Create/import/
  query calls fit in the default, but **`app.build()` can exceed it** — pass an explicit
  budget on the build call (**~1800–3600s**; 3600s is the hard ceiling). Too small a
  timeout → the build is Ctrl-C-interrupted mid-compile and no `.elf` is produced. Let the
  build stream its output so the idle watchdog never trips on a long-but-active build.

## Workflow (create; build when prompted)

```
- [ ] 1. Parse request -> name, platform, os, template, domain, sysroot, sources; note whether the user asked to BUILD
- [ ] 2. Resolve the platform (a BUILT .xpfm). If missing OR created-but-unbuilt, offer to build it
         (ask + approve; load the platform flow references/platform.md) — don't hand-author a platform — then continue.
- [ ] 2.5 If the template needs a BSP library (e.g. lwip_echo_server -> lwip220), check the domain's
         libs and offer to add it + rebuild the platform (ask + approve) BEFORE step 3 — see below.
- [ ] 3. CREATE the app (create_app_component). Report it (created, unbuilt).
- [ ] 4. BUILD only when asked: app.build() -> VERIFY the ELF + write build/.buildstatus -> report.
```

**Create only unless asked to build.** On a "create"/"make" request, create the app and
**stop — do not build**. Run `app.build()` only when the user asks to build (a separate
follow-up) or asks to create *and* build in one go. When importing user sources, import them
**before** any build.

> **Downstream needs a BUILT `.elf`.** A created-but-unbuilt app has no `.elf` and silently
> breaks downstream (boot-image has nothing to package). The consumer (`vitis-embedded`)
> will **offer to build an unbuilt app (ask + approve) before using it** — never silently.

**If the name already exists:** live workspace, **no `--clean`** — do not delete a component
from the running session. Pick a new `name`, build/extend the existing one
(`client.get_component(name=...)`), or ask the user to remove it in the IDE. Do not overwrite.

### The platform's domains decide the app OS (not `os` alone)

An app lands on one of the **platform's domains**, so `os` only "takes" if the platform
actually **has** a domain for that OS. Asking for `os="standalone"` on a **Linux-only**
platform yields a **Linux** app (and needs a sysroot), because there is no standalone
domain. Detect this, warn (`OS MISMATCH`), and key sysroot handling off the *resolved* OS.
To get the OS you want, target a platform that has that domain — or add one with
`vitis-embedded` (`platform.add_domain(...)`). Pass `domain=` explicitly
when the platform has **multiple** domains.

### Embedded Accel platforms reject baremetal templates (2026.2)

A platform built from a board's *base-platform* XSA (e.g. `<board>_base/hw/hw.xsa`) has the
**Embedded Accel** flow; on **2026.2** its domains reject standalone/FreeRTOS app templates.
**Read the resolved flow first** (the platform's `vitis-comp.json` `configuration.flow`), and if
it is Embedded Accel and the user wants a baremetal app, **don't offer a platform build** — a
baremetal app needs a plain embedded platform (board shortcut or the shipped fixed XSA; see
[`platform.md`](platform.md)). (2026.1 accepts it.)

### Templates that need a BSP library (e.g. `lwip_echo_server`)

Some templates require a BSP library in the **platform domain** — e.g. `lwip_echo_server` /
`freertos_lwip_echo_server` need `lwip220`; FAT/SD tests need `xilffs`. The app will not link if the
domain lacks it. **Before creating such an app, check the domain's libraries**
(`platform.get_domain(d).get_libs()`); if the needed library is missing, **offer to add it and
rebuild the platform (ask + approve)** — see [`platform.md`](platform.md)
§ *Configure the domain (post-create)* — then create/build the app. Don't silently create an app
that won't link.

### Inputs

| Parameter | Required | Notes |
|-----------|----------|-------|
| `name` | Y | application component name |
| `platform` | Y | the platform's built **`.xpfm` path** (portable — resolves on 2026.1 & 2026.2); a bare workspace-component **name string** fails, and a `Platform` object works on **2026.2 only** — see the gotcha |
| `domain` | N (Y if multi-domain) | which platform domain the app targets |
| `os` | N (from domain) | standalone / linux / freertos — only "takes" if the platform has that domain |
| `template` | N (per-OS default — see *Picking the template*; always pass explicitly) | e.g. `hello_world`, `memory_tests`, `lwip_echo_server`, `linux_hello_world`, `empty_application` |
| `cpu` | N | domain/core selection |
| `sysroot` | N (Linux) | **full** PetaLinux/Yocto sysroot (top-level `/lib`) — see below |
| source import | N | `app.import_files(from_loc=.., files=[..], dest_dir_in_cmp="src")` |
| `app_config KEY=VALUE` | N | `app.set_app_config(key, values)` build settings (repeatable) |
| `target` | N (hw) | `hw` or `x86sim` |

See [`app/app_api.md`](app/app_api.md) for the full `create_app_component(...)`
signature, app methods, the template list, and build-setting keys.

### Picking the template

**Always pass `template=` explicitly** — the API's own default for an embedded app is
`empty_application` (no `main()`, won't link until sources are added), *not* `hello_world`.
Choose per the resolved domain OS:

| Domain OS | Default | Empty (user sources) | Typical others |
|-----------|---------|----------------------|----------------|
| standalone | `hello_world` | `empty_application` | `memory_tests`, `peripheral_tests`, `lwip_echo_server`, `dhrystone` |
| freertos | `freertos_hello_world` | `empty_application` | `freertos_lwip_echo_server`, … |
| linux | `linux_hello_world` | `linux_empty_application` | — |

1. If the user named a template (or picked one from the offered list), use it.
2. If the request mentions the user's own sources, use the **Empty** template and offer to
   import the files (`app.import_files(...)`).
3. Otherwise use the **Default** for the resolved domain OS and mark it `(default)` in the
   report alongside the main alternatives, so the user can switch cheaply.

## The recipe (create → optional sysroot/import; build when prompted)

Run as `runVitisPython` calls (state persists across them).

```python
# CREATE (default). Pass the platform's BUILT .xpfm PATH — the only form that resolves on both
# 2026.1 and 2026.2 (see the gotcha after this block). A bare workspace-name string is
# os.path.abspath()'d and fails; a Platform object works on 2026.2 only.
ws = client.get_workspace()
xpfm = f"{ws}/vck190_linux_multi/export/vck190_linux_multi/vck190_linux_multi.xpfm"
app = client.create_app_component(
    name="hello_app",
    platform=xpfm,
    domain="linux_psv_cortexa72",               # required only if the platform has >1 domain
    template="linux_hello_world")
print("APP_CREATED (unbuilt)", app)

# LINUX ONLY: a FULL sysroot (top-level /lib). The bundled libc-only toolchain sysroot is rejected.
# app.set_sysroot("/…/sysroots/cortexa72-cortexa53-amd-linux")

# OPTIONAL: user sources instead of the template's — import BEFORE building.
# app.import_files(from_loc="/src", files=["main.c"], dest_dir_in_cmp="src")
```

> **Gotcha — pass the platform as its built `.xpfm` PATH (portable), not a bare name string or a
> `Platform` object.** `create_app_component()` runs a non-env-var platform string through
> `os.path.abspath()` (in `HostComponent._createHostComponent`), so a bare workspace-component
> name like `vck190_platform` becomes `<cwd>/vck190_platform` → *"Platform '...' does not exist in
> the repository."* Validated on **both 2026.1 and 2026.2**:
> - **`.xpfm` path** (absolute) → works on both (abspath is a no-op on an absolute path).
> - **bare name string** → fails on both.
> - **`Platform` object** (`client.get_component("<name>")`) → works on **2026.2 only**; on 2026.1
>   it fails with `'Platform' object has no attribute 'startswith'` (2026.1 has no `_resolve_platform_object`).
>
> Use the `.xpfm` path for portability. `client.list_components()` is the source of truth for what
> exists (`get_component()` can return a stale object after a delete).

```python
# BUILD — only when the user asks to build. target defaults to hw for embedded apps.
rc = app.build()

# VERIFY: locate the largest *.elf under the component build tree.
import os, glob
build_dir = os.path.join(client.get_workspace(), "hello_app", "build")
elfs = glob.glob(os.path.join(build_dir, "**", "*.elf"), recursive=True)
elf = max(elfs, key=os.path.getsize) if elfs else None
print("BUILD_RC", rc, "ELF", elf, "EXISTS", bool(elf) and os.path.isfile(elf))

# FLOW-panel marker: app.build() (unlike the GUI / platform.build()) does NOT write this,
# so an attached build would leave the app looking "not built". Write it after a verified ELF.
if rc == 0 and elf:
    with open(os.path.join(build_dir, ".buildstatus"), "w") as fh:
        fh.write("hw=SUCCESS\n")
```

**Trust the evidence, not the absence of an error:** the build succeeded only if `rc` is 0
**and** an `.elf` was produced. Write `build/.buildstatus` (`hw=SUCCESS`) only after the ELF
is confirmed, so the Vitis Explorer FLOW panel shows the green Build check.

### Sysroot (Linux)

A Linux app cross-compiles against a sysroot. `set_sysroot` requires a **full
PetaLinux/Yocto sysroot** with a top-level `/lib` (e.g.
`$PLATFORM_REPO_PATHS/sw/.../sysroots/cortexa72-cortexa53-amd-linux/`). The GCC toolchain
sysroot bundled with the install has only `usr/lib` and is **rejected**
(`'... does not contain a /lib'`). If the user has no full sysroot, say so — don't substitute
the bundled one.

## Chaining

This skill is one hop in `platform → app → boot-image → target`. Full contract:
[`chaining.md`](chaining.md). This skill's edges:

- **Upstream:** `vitis-embedded` → consumes a **BUILT `.xpfm`**. If the platform is
  missing OR only created-but-unbuilt, **REQUIRED SUB-SKILL:** load and follow
  `vitis-embedded` and **offer to build it (ask + approve)** — do **not** improvise a
  platform from memory — then continue. Do not dead-end.
- **Produces:** `<workspace>/<app>/build/**/<app>.elf` (+ `build/.buildstatus = hw=SUCCESS`) —
  **only after a build**; a created-only app has no `.elf` yet.
- **Downstream:** offer `vitis-embedded` next (a **shell** Bootgen skill; hand off the
  **`.elf` PATH**, not the live `app`). If the app is only created-but-unbuilt, boot-image will
  offer to build it first. Programming and on-target debug are optional follow-on work.
- **Gate:** the `.elf` exists only after a build — build (or offer to build) before packaging.

## Reporting to the user (keep it brief)

One-line status + a compact table; report the **actual state**. No narration, no log echoes.

```
✅ App <name> created (not built)        # after CREATE only
✅ App <name> built successfully         # after BUILD

| Property | Value |
|----------|-------|
| Name | <name> |
| Platform | <platform> |
| OS | <os> |
| Domain | <domain> |
| Template | <template> |
| ELF | <path — only after a successful build> |
| Build status | Not built  /  SUCCESS ✅ |
```

Follow-up: after create, "Build it now?"; after build, "Package a boot image next?". On build
failure: one line + the rc and where the log is (`<workspace>/<name>/logs`).

## Operating on an existing app — direct method calls

Fetch with `client.get_component(name=...)` (or reuse a variable from an earlier call), then:

| Task | Call |
|------|------|
| Build it | `app.build()` (then write `build/.buildstatus`) |
| Set the Linux sysroot | `app.set_sysroot("<full sysroot>")` |
| Import sources | `app.import_files(from_loc=.., files=[..], dest_dir_in_cmp="src")` |
| Build setting | `app.set_app_config("USER_COMPILE_...", "<value>")` |

## Notes

- **No transport/session code** — the tool + console provide the session (no port discovery,
  `create_client`, `set_workspace`, `--flags`, `APP_RESULT` JSON, or `dispose()`).
- **Reproducibility is automatic** — the server journals every mutating call
  (`workspace_journal.py`), independent of this console.
