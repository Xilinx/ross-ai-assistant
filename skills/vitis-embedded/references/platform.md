<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Create Embedded Platform (Vitis IDE AI agent, or headless CLI)

In **in-console** mode the agent runs
**inside** the Vitis Unified IDE with a live, connected Vitis Python Console, so there is
**nothing to launch, attach, or resolve**. You author Vitis Python API calls and run them
with the `runVitisPython` tool; they execute against the pre-bound `client` in the IDE's
open workspace and appear **live in the Components panel**.

Because the session is provided for you, this skill ships **knowledge, not a program**:
which calls to make, how to pick the CPU, and how to know the build actually succeeded.

Every recipe here **also runs headless** through the Vitis CLI. Pick the mode first.

## Step 0 — MANDATORY: pick the execution mode

Check whether **`runVitisPython` is in your available tool set**:

- **Present** → **in-console mode.** Use this file as written.
- **Absent** → **headless mode**, which is a fully supported path, not a blocker:
  1. Read [`platform/headless.md`](platform/headless.md) for the `vitis -s` wrapper.
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

The agent console pre-loads everything and holds the IDE's workspace lock:

- `vitis`, `client`, `xsdb`, `hsi` are **imported**, and `client` is **connected**.
- The **workspace is already set** to the IDE's open workspace (`client.get_workspace()`).

So in every `runVitisPython` call: **do NOT** `import vitis`, call `vitis.create_client(...)`,
`client.set_workspace(...)`, or `vitis.dispose()`. The tool forbids/ignores that boilerplate
and it would only conflict with the live session. Just use the existing `client` and any
variables from earlier calls — **REPL state persists across `runVitisPython` calls** (a
`platform = ...` in one call is usable by `platform.build()` in the next).

## How you run code — the `runVitisPython` tool

> **If `runVitisPython` errors or isn't available here, do not stop or report failure** — run
> the identical `code` through `vitis -s` instead (see Step 0 and [`platform/headless.md`](platform/headless.md)).

Every Vitis operation in this skill is the `code` payload of a `runVitisPython` call. The
tool runs it in the dedicated agent console and returns captured stdout/stderr. Notes that
shape how to write the code:

- **Mutating calls prompt the user for confirmation** (create/delete/build) — expected; don't
  try to route around it.
- **Output is truncated** (~20k chars, head+tail). Keep prints concise; don't dump full build
  logs — read a status/`.xpfm` check instead.
- **Prefer return values + `print(...)`** over file polling. The tool hands back stdout, so
  `print()` what you need to inspect (e.g. the build rc and whether the `.xpfm` exists).
- **Set `timeoutSeconds` per operation.** The tool defaults to **600s** and an idle watchdog
  (aborts only after ~5 min of *no output* AND no completion). 600s is fine for
  create/query calls, but a **`platform.build()` can far exceed it** — pass an explicit
  budget on the build call (**~1800–3600s**; 3600s is the hard ceiling). A too-small
  timeout on a build → the call is Ctrl-C-interrupted mid-build, leaving an unbuilt `.xpfm`.
  Keep the build's own output streaming (don't silence it) so the idle watchdog never
  trips on a long-but-active build.

## Workflow

```
- [ ] 1. Parse request -> name, hw_design, os, cpu, domain(s), boot/DTB/DFX options; note whether the user asked to BUILD
- [ ] 2. RESOLVE cpu: catalog first (platform/board_catalog.md); else query live
- [ ] 3. CREATE (default): create_platform_component(...) -> report it (created, unbuilt)
- [ ] 4. BUILD only when asked: platform.build() -> VERIFY (rc == 0 AND build-status reports success) -> report
```

**Create only unless asked to build.** On a "create"/"make" request, create the component and
**stop — do not build**. Run `platform.build()` only when the user asks to build (a separate
follow-up) or asks to create *and* build in one go. Never create a **domain-less** platform —
always pass a resolved `cpu` + `domain_name`.

> **Downstream needs a BUILT platform.** `create` runs an automatic quick build that writes a
> **stub `.xpfm`** (metadata only — no compiled BSP), so the `.xpfm`'s presence does **not** mean
> the platform is built. An unbuilt platform silently breaks every downstream step (apps link
> against absent artifacts; boot-image has nothing to package). Consumers (`vitis-embedded`, …)
> **offer to build an unbuilt platform (ask + approve) before using it** — they never build it
> silently.

**If the name already exists:** this is a live workspace and there is **no `--clean`** — do not
try to delete a component out from under the running session. If `create_platform_component`
raises `ALREADY_EXISTS` (or a component dir with that name is present), either **pick a new
`name`**, or build/extend the existing one instead (`client.get_component(name=...)` →
`.build()` / `.add_domain(...)`), or ask the user to remove it in the IDE. Do not overwrite.

### Inputs

| Parameter | Required | Notes |
|-----------|----------|-------|
| `name` | Y | platform component name |
| `hw_design` | Y | board shortcut (e.g. `vek385`) or path to `.xsa` |
| `os` | N (standalone) | `standalone` / `linux` / `freertos` |
| `cpu` | N (auto) | **exact** core name (e.g. `cortexa78_0`, `psu_cortexa53_0`) |
| `domain_name` | N (auto) | defaults to `f"{os}_{cpu}"` |
| `generate_dtb` | N (linux→True) | device tree; required for Linux |
| `no_boot_bsp`, `fsbl_target`, `pmufw_Elf`, `is_pmufw_req` | N | boot components (ZynqMP/Zynq) |
| `template`, `desc`, `architecture`, `compiler` | N | forwarded to the API |
| `advanced_options` (DTS), `emu_design`, `rp_info_args` (DFX) | N | see `platform/platform_api.md` |
| extra domains | N | `platform.add_domain(cpu=.., os=.., name=..)` after create |

**Ask — never guess — a missing board or name.** `name` and `hw_design` are required; if the
request gives no board shortcut or `.xsa` (or no name), ask for it and list the board shortcuts
from [`platform/board_catalog.md`](platform/board_catalog.md) (a custom `.xsa` path also works).
Do not default a board or XSA.

**Base-platform XSA vs baremetal (2026.2).** A platform built from a board's *base-platform* XSA
(e.g. `<board>_base/hw/hw.xsa`) resolves to the Embedded Accel flow, which on 2026.2 rejects
standalone/FreeRTOS app templates. For baremetal apps, use the board shortcut or the shipped
fixed XSA (`$XILINX_VITIS/data/embeddedsw/lib/fixed_hwplatforms/<board>.xsa`), both of which give
the plain embedded flow.

See [`platform/platform_api.md`](platform/platform_api.md) for the full
`create_platform_component(...)` signature, defaults, and every platform/domain method.

### Picking the CPU (catalog first, live fallback)

- **Board shortcut** → look up `cpu`/`os` in [`platform/board_catalog.md`](platform/board_catalog.md)
  and pass it verbatim — instant, no live query (e.g. vek385 standalone → `cortexa78_0`, linux
  → `cortexa78`; zcu102 → `psu_cortexa53_0`; vck190/vek280 → `psv_cortexa72_0`).
- **Custom `.xsa`** or a board not in the catalog → query live:

```python
proc = client.get_processor_os_list(xsa="/path/to/design.xsa")   # xsa-first also resolves board shortcuts
print(proc.os_processors)   # pick an app core: indexed (e.g. *_0) for standalone/freertos, cluster for linux
```

- Always pass the user's explicit core **verbatim** if given (bare `cortexa78_N` on Versal Gen2,
  `psv_cortexa72_N` Gen1, `psu_…` ZynqMP — no invented prefixes like `psx_…`).

## The recipe (create; build when prompted, in-console)

Run as `runVitisPython` calls. Steps can be one call or several (state persists).

```python
# CREATE (default) — vek385 baremetal on cortexa78_0. No imports/client/workspace/dispose.
platform = client.create_platform_component(
    name="vek385_plat", hw_design="vek385", os="standalone",
    cpu="cortexa78_0", domain_name="standalone_cortexa78_0")
platform.report()              # created (unbuilt): XSA/hw, domains, OS/CPU, boot components
```

```python
# BUILD — only when the user asks to build (a separate follow-up). Budget ~1800-3600s.
rc = platform.build()
res = platform.get_last_build_result()   # {'success': bool, ...} — the build-status marker
print("BUILD_RC", rc, "BUILD_SUCCESS", res.get("success"))
```

**Trust the evidence, not the absence of an error:** `build()` returns an int and does not raise
on failure, and a **stub `.xpfm` already exists from create's quick build** — so success is
`rc == 0` **and** the build-status marker reporting success (`get_last_build_result()['success']`,
equivalently `export/.buildstatus == SUCCESS`), **not** `.xpfm` existence. `platform.report()` is
a config/hardware summary (shows the XSA) — it prints even for an unbuilt or failed-rebuild
platform, so it confirms *what* the platform is, **not** that it built. See [`platform/platform_api.md`](platform/platform_api.md) for the workspace layout.

## Reporting to the user (keep it brief)

One-line status + a compact property table; report the **actual state**. Do **not** narrate the
console/tool steps or echo build logs. Fill the table from `platform.report()` and the request:

```
✅ Platform <name> created (not built)        # after CREATE only
✅ Platform <name> built successfully         # after BUILD

| Property | Value |
|----------|-------|
| Name | <name> |
| Board / Family | <hw_design> / <family> |
| OS | <os> |
| CPU | <cpu> |
| Domain | <domain> |
| Boot | <e.g. ZynqMP FSBL BSP, or Versal PLM> |
| Build status | Not built  /  SUCCESS ✅ |
| .xpfm | <path — present after create; a real build = build-status SUCCESS> |
```

Follow-up: after create, "Build it now?"; after build, offer the app step with the template
choice inline — e.g. "Create an app on it next? Options: `hello_world` (default) ·
`empty_application` (your own sources) · other template (`memory_tests`, `peripheral_tests`,
`lwip_echo_server`, …)" — using the per-OS defaults in [`app.md`](app.md) §*Picking the
template*. On build failure: one line with the rc and that the `.xpfm` is missing, and point at
`<workspace>/<name>/logs`.

## Operating on an existing platform — direct method calls

State persists, so these are plain method calls on a component you fetch with
`client.get_component(name=...)` (or a variable from an earlier call). Apply modify ops
**first**, then `build()` last so a single sequence composes (e.g. delete a domain and rebuild):

| Task | Call |
|------|------|
| Build it | `platform.build()` |
| Add a domain | `platform.add_domain(cpu=.., os=.., name=.., display_name=..)` |
| Delete a domain | `platform.delete_domain("<name>")` |
| Generate boot BSP | `platform.generate_boot_bsp(target_processor="<fsbl>")` (ZynqMP/Zynq only) |
| Remove boot BSP | `platform.remove_boot_bsp()` (ZynqMP/Zynq only) |
| Set custom FSBL / PMUFW | `platform.set_fsbl_elf(<path>)` / `platform.set_pmufw_elf(<path>)` |
| Retarget FSBL | `platform.retarget_fsbl(target_processor=.., domain_name=..)` |
| Add / remove a BSP library | `platform.get_domain(d).set_lib("xilffs")` / `.remove_lib("xilffs")` (then `build()`) |
| Change an OS / proc setting | `platform.get_domain(d).set_config("os", "<param>", "<value>")` (discover via `list_params`) |
| Custom Linux DTB | `platform.get_domain(d).set_dtb("<dtb>")` / `.recompile_dtb()` (Linux) |

**Boot-BSP is device-specific:** Versal (`psv_/psx_`, `cortexa78`) has **no** boot BSP (boots via
PLM) — `generate/remove_boot_bsp` error. ZynqMP/Zynq (`psu_`/`ps7_`) create FSBL/PMUFW by default.

### Configure the domain (post-create): libraries, OS/proc settings, DTB

`create_platform_component(...)` takes no BSP-library or OS/processor-setting parameters and makes a
single domain, so requests like "add xilffs", "enable lwIP", "set stdout to UART1" or "use a custom
DTB" are **post-create** operations on the domain object — apply them **before `platform.build()`**
(they take effect when the BSP is generated). Full method list + return shapes:
[`platform/platform_api.md`](platform/platform_api.md) (§ *Domain object methods (post-create configuration)*).

```python
dom = platform.get_domain("standalone_psv_cortexa72_0")   # the domain you created
# 1) DISCOVER, then apply — never assume names
print([l["name"] for l in dom.get_applicable_libs()])     # libraries you may add
dom.set_lib("xilffs"); dom.set_lib("lwip220")             # add BSP libraries
# 2) set an OS param by its FULL name (resolve via list_params; do NOT guess "stdout")
stdout_p = next(p["parameter_name"] for p in dom.list_params("os") if "stdout" in p["parameter_name"])
dom.set_config("os", stdout_p, "psv_sbsauart_1")          # e.g. route stdout to the 2nd UART
# 3) read back for the report
print("libs:", [l["name"] for l in dom.get_libs()], "| stdout:", dom.get_config("os", stdout_p))
# Linux DTB — these are ALTERNATIVES, not sequential:
#   * supply a prebuilt DTB:  dom.set_dtb("/path/system.dtb")
#   * OR rebuild from DTS:     dom.recompile_dtb()   # only after editing the device-tree sources
#   Do NOT call recompile_dtb() right after set_dtb — it regenerates the tree and overwrites the
#   DTB you just supplied.
# (No build here — rebuilding is a separate, explicitly-requested step; see below.)
```

**Validate names first.** Add only libraries returned by `get_applicable_libs()`, and set only
params/values from `list_params(...)` (respect `possible_options` and `permission`) — invalid names
raise.

**Rebuild is a separate, explicitly-requested step (create-only default applies).** The changes above
take effect only when the platform's BSP is regenerated, so the platform — and any apps and the boot
image built against it — must be rebuilt **before** the new config is consumed. But **don't auto-build**:
run `platform.build()` only when the user asks to build, the same create-only default as everywhere
else in this skill.

**App template that needs a library (e.g. `lwip_echo_server`).** If the requested app template needs
a library the domain lacks (check `get_libs()`), **offer to add it and rebuild the platform (ask +
approve)** before creating/building the app — don't silently create an app that won't link.

## Chaining

This skill is the first hop in `platform → app → boot-image → target` (full contract:
`chaining.md`). This skill's edges:

- **Upstream:** none — this produces the platform.
- **Produces:** a `.xpfm` at `<workspace>/<name>/export/<name>/<name>.xpfm` — **built only if the
  user asked to build**; otherwise created-but-unbuilt.
- **Downstream:** offer `vitis-embedded` next. It needs a **built** `.xpfm`
  (export `.buildstatus == SUCCESS`); if the platform is only created-but-unbuilt, the app skill
  **offers to build it (ask + approve) before using it**. Hand off by **name or `.xpfm` path**.

## References

- [`platform/platform_api.md`](platform/platform_api.md) — authoritative
  `create_platform_component(...)` signature, platform/domain methods, client helpers, worked
  examples, device-family cheat-sheet, and the built workspace layout (`.xpfm`).
- [`platform/board_catalog.md`](platform/board_catalog.md) — per-board CPU/OS catalog for
  the built-in board shortcuts, so you can pick `cpu`/`os` without a live query.

## Notes

- **No transport/session code.** The tool + console provide the session (no port discovery,
  `create_client`, `set_workspace`, `--flags`, `PLATFORM_RESULT` JSON, or `dispose()`).
- **Reproducibility is automatic.** The server journals every mutating API call
  (`workspace_journal.py`), independent of this console — no need to emit an audit line.
