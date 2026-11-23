<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Running `vitis-system` headless (no Vitis IDE)

This skill normally runs **inside the Vitis Unified IDE's AI chat**, where the `runVitisPython`
tool executes API calls against a live, pre-connected `client`. On a **headless host** (VS Code,
Cursor, Claude Code CLI, CI) there is no live console — but the **same recipe** runs through
`vitis -s`. Only the session wrapper changes; the `create_sys_project(...)` / compose /
`proj.build(...)` calls are identical to this skill's `SKILL.md`.

## Use `vitis -s`, never `vitis -i`, for automation

| Mode | What it is | Use it when |
|------|-----------|-------------|
| `vitis -s <script>` | runs a Python script and **exits cleanly** | **agents / batch / CI** |
| `vitis -i` | interactive IPython REPL | **a human at a real terminal only** |

`vitis -i` needs a TTY: an agent's one-shot shell hits EOF (runs nothing) or, with piped stdin,
breaks on indented blocks (`for`/`if`/`with`/`def`). Use `vitis -s`.

## Headless has no tool timeout — this is where a full `hw` build+package belongs

In-console each call stops at its `timeoutSeconds` (default 180 s). A `hw_emu` build+package fits
a ~1800–3600 s budget, but a full **`hw`** build (Vivado impl + package, tens of min → hours) that
runs longer is interrupted mid-build. `vitis -s` has **no tool timeout** — compose / `hw_emu`
in-console is fine; hand the long `hw` build to `vitis -s`.

## The same recipe, wrapped in `vitis -s` (no script file)

In-console you write `runVitisPython(code=<snippet>)`. Headless, run the **identical** snippet
through `vitis -s` reading stdin — nothing is written to disk. The full-path `vitis` binary
self-configures its environment (no `source settings64.sh`):

```bash
# A hw / hw_emu build may need an LD_PRELOAD on Ubuntu 24 — see "Environment" below. Compose alone does not.
<install>/bin/vitis -s /dev/stdin <<'PY'
import vitis, os
client = vitis.create_client()               # spawn a headless server (or attach: create_client(port=N))
client.set_workspace("/path/to/workspace")   # new/empty dir or existing Vitis workspace — see "Workspace setup" below
# ---- the SAME recipe this skill teaches ----
client.add_platform_repos("/path/to/base_platforms")
plat = "vck190_base"                          # resolve by EXACT name (bare substrings pick the wrong flow)
hits = sorted(set(client.find_platforms_in_repos(f"/{plat}/{plat}.xpfm")))
if len(hits) != 1:
    raise SystemExit(f"Platform '{plat}' not uniquely resolved: {hits}")
xpfm = hits[0]; print("PLATFORM", xpfm)
proj = client.create_sys_project(name="vadd_system", platform=xpfm,
    template=client.get_template(type="ACCL_APP", name="empty_accelerated_application", platforms=[xpfm]))
proj.add_container(name="binary_container_1")
proj.add_component(name="aie_test", container_name=["binary_container_1"])
proj.add_component(name="host_app")
proj.add_cfg_files(name="binary_container_1", cfg_files=["system.cfg"])
# proj.build(target="hw_emu", build_comps=True, build_package=False)   # hw_emu: full link+package (see build gotcha)
# proj.build(target="hw", build_comps=True, build_package=False)       # hw: link + package; long — this is the headless win
vitis.dispose()                              # only for a spawned session; never for an attached IDE server
PY
```

Differences from in-console: **you** add `create_client` / `set_workspace` / `dispose`.

### Workspace setup (headless)

`set_workspace(path)` is strict. It accepts only: a folder that doesn't exist, a truly empty folder,
or a folder that is already a Vitis workspace at the current version (`_ide/version.ini`). Check the
folder before the first call:

| Folder state | Call |
|---|---|
| Missing, empty, or already a current-version workspace (has `_ide/version.ini`) | `client.set_workspace(path)` |
| Has files but no `_ide/` (including a folder with only an empty subfolder) | Prefer a new or empty subfolder. Use `client.update_workspace(path)` **only after the user confirms** that folder. |
| `_ide/version.ini` from an older release | `set_workspace` fails with "…detected a workspace from version X. Use update_workspace API…". Ask the user, then `client.update_workspace(path)`. |

`update_workspace(path)` replaces `set_workspace(path)`; it also sets the workspace, so don't call
both. It leaves your files alone and initializes (or upgrades) `_ide/version.ini`.

If `set_workspace` fails with `Vitis IDE cannot recognize the workspace version … Click 'Update'`
(a GUI-worded message), treat it as the second row. Stop and ask; don't retry in a loop.

## Continuity across steps

Each `vitis -s` call is a fresh process — Python locals don't persist. Carry state by
**project/component name / on-disk path**, not a live object, or run compose → build → verify in
one call:

```python
proj = client.get_sys_project(name="vadd_system")   # re-fetch by name in the next call
```

Two things the recipe already prescribes still apply headless: resolve the platform by **anchored
exact name** each call (a bare substring grabs the wrong flow), and `packaging_mode` **must match
the platform's flow** (EDF/Yocto vs PetaLinux).

## Environment: preload the system `libstdc++` only on Ubuntu 24

`proj.build()` (`v++ --link` under the package flow) shells out to the **system `cmake`**
(`/usr/bin/cmake`). Where the system `libstdc++` is **newer** than the bundled one (Ubuntu 24,
`GLIBCXX_3.4.32`+), cmake dies at configure:

```
cmake: .../Vitis/lib/lnx64.o/Ubuntu/24/libstdc++.so.6: version `GLIBCXX_3.4.32' not found (required by cmake)
```

**Rule: build with no preload first; add it only if cmake dies with that exact error.** It helps
**only** where the system lib is newer — true on Ubuntu 24, **false on RHEL 8** (older bundled
wins; nothing to preload — don't hunt the filesystem). Resolve the path (differs Debian vs RHEL):

```bash
SYSCXX="$(ldconfig -p | awk '/libstdc\+\+\.so\.6/ {print $NF; exit}')"
LD_PRELOAD="$SYSCXX" <install>/bin/vitis -s /dev/stdin <<'PY' … PY
```

Field evidence (2026.2, Ubuntu 24): a `vadd` `hw_emu` build+package completed with the preload,
emitting `vadd.xsa` / `vadd.xclbin` / `launch_hw_emu.sh`.

## Quick reference

| | In-console (IDE) | Headless |
|--|------------------|----------|
| Run an API snippet | `runVitisPython(code=X)` | `vitis -s /dev/stdin <<'PY' … X … PY` |
| Session | pre-bound `client` | `create_client()` + `set_workspace()` (see "Workspace setup") + `dispose()` |
| Cross-step state | REPL persists | by project name / package `sd_card.img`·`BOOT.BIN` path |
| Platform ref | name or path | **anchored exact `.xpfm`** + `packaging_mode` matches flow |
| Long `hw` build | defer if it may outlast `timeoutSeconds` | `vitis -s` has **no tool timeout** |
| `libstdc++` preload | n/a | **Ubuntu 24 only**, after a cmake `GLIBCXX` failure (not on RHEL) |

## What this does NOT cover

This is the **execution model** only (how to run the recipe headless). For **which** skill does
what and how they connect, see [`chaining.md`](../chaining.md). This skill's own `SKILL.md` holds the
recipe; this file only changes how it is invoked.
