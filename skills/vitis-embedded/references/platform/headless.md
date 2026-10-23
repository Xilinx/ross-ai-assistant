<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Running `vitis-embedded` headless (no Vitis IDE)

This skill normally runs **inside the Vitis Unified IDE's AI chat**, where the `runVitisPython`
tool executes API calls against a live, pre-connected `client`. On a **headless host** (VS Code,
Cursor, Claude Code CLI, CI) there is no live console — but the **same recipe** runs through
`vitis -s`. Only the session wrapper changes; the `create_platform_component(...)` calls are
identical to this skill's `SKILL.md`.

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
import vitis, os
client = vitis.create_client()               # spawn a headless server (or attach: create_client(port=N))
client.set_workspace("/path/to/workspace")
# ---- the SAME recipe this skill teaches ----
# (zcu102 / psu_cortexa53_0 are EXAMPLE values — use the board + cpu from the user's
#  request; never default a board, see platform.md "Ask — never guess a missing board")
platform = client.create_platform_component(
    name="zcu102_plat", hw_design="zcu102", os="standalone",
    cpu="psu_cortexa53_0", domain_name="standalone_psu_cortexa53_0")
print("BUILD_RC", platform.build())          # emits <ws>/zcu102_plat/export/zcu102_plat/zcu102_plat.xpfm
vitis.dispose()                              # only for a spawned session; never for an attached IDE server
PY
```

Differences from in-console: **you** add `create_client` / `set_workspace` / `dispose` (the IDE
provides these; headless you don't).

## Continuity across steps

Each `vitis -s` call is a fresh process — Python locals don't persist. Carry state by
**component name**, not a live object (or run create → build in one call):

```python
platform = client.get_component(name="zcu102_plat")   # re-fetch by name in the next call
xpfm = os.path.join(client.get_workspace(), "zcu102_plat", "export", "zcu102_plat", "zcu102_plat.xpfm")
```

The exported `.xpfm` path is what the downstream app / integration / system skills consume.

## Environment: no `libstdc++` preload needed for this skill

`platform.build()` runs the platform packager via the **bundled** `tps/lnx64/cmake-4.0.1`, not
the system `cmake`, so the `GLIBCXX_...' not found` failure that hits AIE / `v++ --link` builds
does **not** apply here. Build with no `LD_PRELOAD`. (If you later chain into an AIE or
integration build in the same shell, see that skill's `headless.md` for the Ubuntu-24 preload.)

## Quick reference

| | In-console (IDE) | Headless |
|--|------------------|----------|
| Run an API snippet | `runVitisPython(code=X)` | `vitis -s /dev/stdin <<'PY' … X … PY` |
| Session | pre-bound `client` | `create_client()` + `set_workspace()` + `dispose()` |
| Cross-step state | REPL persists | by component name / exported `.xpfm` path |
| `libstdc++` preload | n/a | **not needed** (bundled cmake) |

## What this does NOT cover

This is the **execution model** only (how to run the recipe headless). For **which** skill does
what and how they connect, see [`chaining.md`](../chaining.md). This skill's own `SKILL.md` holds the
recipe; this file only changes how it is invoked.
