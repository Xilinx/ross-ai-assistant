<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Running `vitis-embedded` headless (no Vitis IDE)

This skill normally runs **inside the Vitis Unified IDE's AI chat**, where the `runVitisPython`
tool executes API calls against a live, pre-connected `client`. On a **headless host** (VS Code,
Cursor, Claude Code CLI, CI) there is no live console — but the **same recipe** runs through
`vitis -s`. Only the session wrapper changes; the `create_app_component(...)` / `app.build()`
calls are identical to this skill's `SKILL.md`.

## Use `vitis -s`, never `vitis -i`, for automation

| Mode | What it is | Use it when |
|------|-----------|-------------|
| `vitis -s <script>` | runs a Python script and **exits cleanly** | **agents / batch / CI** |
| `vitis -i` | interactive IPython REPL | **a human at a real terminal only** |

`vitis -i` needs a TTY: an agent's one-shot shell hits EOF (runs nothing) or, with piped stdin,
breaks on indented blocks (`for`/`if`/`with`/`def`). Use `vitis -s`.

## The same recipe, wrapped in `vitis -s` (no script file)

In-console you write `runVitisPython(code=<snippet>)`. Headless, run the **identical** snippet
through `vitis -s` reading stdin — nothing is written to disk. The full-path `vitis` binary
self-configures its environment (no `source settings64.sh`):

```bash
<install>/bin/vitis -s /dev/stdin <<'PY'
import vitis, os, glob
client = vitis.create_client()               # spawn a headless server (or attach: create_client(port=N))
client.set_workspace("/path/to/workspace")
# ---- the SAME recipe this skill teaches ----
# Pass the platform's BUILT .xpfm PATH (the only form portable across 2026.1/2026.2).
xpfm = f"{client.get_workspace()}/vck190_linux_multi/export/vck190_linux_multi/vck190_linux_multi.xpfm"
app = client.create_app_component(name="hello_app", platform=xpfm,
        domain="linux_psv_cortexa72", template="linux_hello_world")
# app.set_sysroot("/…/sysroots/cortexa72-cortexa53-amd-linux")   # Linux only: a FULL sysroot
rc = app.build()                             # target defaults to hw for embedded apps
build_dir = os.path.join(client.get_workspace(), "hello_app", "build")
elfs = glob.glob(os.path.join(build_dir, "**", "*.elf"), recursive=True)
print("BUILD_RC", rc, "ELF", max(elfs, key=os.path.getsize) if elfs else None)
vitis.dispose()                              # only for a spawned session; never for an attached IDE server
PY
```

Differences from in-console: **you** add `create_client` / `set_workspace` / `dispose` (the IDE
provides these; headless you don't).

## Pass the platform as its `.xpfm` PATH — bare names misresolve

Headless, a **bare platform name resolves relative to the launch cwd** and fails
(`Platform '/home/you/vck190_platform' does not exist in the repository`) because
`create_app_component()` runs a non-env-var platform string through `os.path.abspath()`. Pass the
absolute built `.xpfm` path (works on both 2026.1 and 2026.2). For a shipped base platform,
register then resolve by **anchored exact name** (a bare substring grabs the wrong flow):

```python
client.add_platform_repos("/path/to/base_platforms")
xpfm = client.find_platform_in_repos("^xilinx_zynqmp_virt_202620_1$")
```

## Continuity across steps

Each `vitis -s` call is a fresh process — Python locals don't persist. Carry state by
**component name / on-disk path**, not a live object (`client.list_components()` is the source of
truth), or run create → build → verify in one call:

```python
app = client.get_component(name="hello_app")   # re-fetch by name in the next call
```

Note `app.build()` does **not** write the FLOW-panel `build/.buildstatus` marker; after a
verified `.elf`, write `hw=SUCCESS` yourself (see `SKILL.md`).

## Environment: no `libstdc++` preload needed for this skill

The embedded-app build shells out to `empyro build_app`, **not** the system `cmake`, so the
`GLIBCXX_...' not found` failure that hits AIE / `v++ --link` builds does **not** apply here.
Build with no `LD_PRELOAD`.

## Quick reference

| | In-console (IDE) | Headless |
|--|------------------|----------|
| Run an API snippet | `runVitisPython(code=X)` | `vitis -s /dev/stdin <<'PY' … X … PY` |
| Session | pre-bound `client` | `create_client()` + `set_workspace()` + `dispose()` |
| Cross-step state | REPL persists | by component name / `.elf` path |
| Platform ref | name or path | **full `.xpfm` path** (bare name misresolves) |
| `libstdc++` preload | n/a | **not needed** (`empyro build_app`) |

## What this does NOT cover

This is the **execution model** only (how to run the recipe headless). For **which** skill does
what and how they connect, see [`chaining.md`](../chaining.md). This skill's own `SKILL.md` holds the
recipe; this file only changes how it is invoked.
