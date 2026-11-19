<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Running `vitis-aie` headless (no Vitis IDE)

This skill normally runs **inside the Vitis Unified IDE's AI chat**, where the `runVitisPython`
tool executes API calls against a live, pre-connected `client`. On a **headless host** (VS Code,
Cursor, Claude Code CLI, CI) there is no live console — but the **same recipe** runs through
`vitis -s`. Only the session wrapper changes; the `create_aie_component(...)` / `comp.build(...)`
calls are identical to this skill's `SKILL.md`.

## Use `vitis -s`, never `vitis -i`, for automation

| Mode | What it is | Use it when |
|------|-----------|-------------|
| `vitis -s <script>` | runs a Python script and **exits cleanly** | **agents / batch / CI** |
| `vitis -i` | interactive IPython REPL | **a human at a real terminal only** |

`vitis -i` needs a TTY: an agent's one-shot shell hits EOF (runs nothing) or, with piped stdin,
breaks on indented blocks (`for`/`if`/`with`/`def`). Use `vitis -s`.

## Locate the CLI and discover examples **inside** the session — not with standalone shell probes

Resolve `vitis`, `$XILINX_VITIS`, and the shipped example list **inside the one `vitis -s`
Python session** (`os.environ`, `glob`), **not** as separate chained shell commands beforehand
(`command -v vitis && printf ... && find .../installed_examples/aie_examples -iname '*mat*' ...`).
Each standalone shell probe is one more fragile call that can fail transiently between tool calls
on some agent harnesses, forcing a retry. Folding discovery into the Python session removes that
surface: one call, inside the process that is already configured.

```python
import os, glob
xv = os.environ.get("XILINX_VITIS", "")          # the vitis binary self-configures this
examples = sorted(os.path.basename(p) for p in
                  glob.glob(os.path.join(xv, "installed_examples", "aie_examples", "*"))
                  if os.path.isdir(p))
print("XILINX_VITIS", xv, "EXAMPLES", examples)
```

If you must confirm the binary from the shell first, run **one** short `command -v vitis` — do
not chain it with `find`, and if a probe fails transiently, re-issue that single probe once; it
is a harness artifact, not a Vitis or skill failure.

## The same recipe, wrapped in `vitis -s` (no script file)

In-console you write `runVitisPython(code=<snippet>)`. Headless, run the **identical** snippet
through `vitis -s` reading stdin — nothing is written to disk. The full-path `vitis` binary
self-configures its environment (no `source settings64.sh`):

```bash
# Build may need an LD_PRELOAD on Ubuntu 24 — see "Environment" below. Create alone does not.
<install>/bin/vitis -s /dev/stdin <<'PY'
import vitis, os, glob
client = vitis.create_client()               # spawn a headless server (or attach: create_client(port=N))
client.set_workspace("/path/to/workspace")   # new/empty dir or existing Vitis workspace — see "Workspace setup" below
# ---- the SAME recipe this skill teaches ----
comp = client.create_aie_component(name="my_aie", part="xcvc1902-vsva2197-2MP-e-S", template="empty")
# comp.import_files(from_loc="/src", files=["my_aie_app.cpp", ...]); comp.update_top_level_file("src/my_aie_app.cpp")
rc = comp.build(target="x86sim")             # "x86sim" (functional) or "hw" (aiesim)
libadf = glob.glob(os.path.join(client.get_workspace(), "my_aie", "build", "**", "libadf.a"), recursive=True)
print("BUILD_RC", rc, "LIBADF", libadf[0] if libadf else None, "OK", bool(libadf))
vitis.dispose()                              # only for a spawned session; never for an attached IDE server
PY
```

**Trust the evidence:** the build succeeded only if `rc` is 0 **and** `libadf.a` exists.
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
**component name / on-disk path**, not a live object, or run create → build → verify in one call:

```python
comp = client.get_component(name="my_aie")   # re-fetch by name in the next call
```

## Environment: preload the system `libstdc++` only on Ubuntu 24

`comp.build()` shells out to the **system `cmake`** (`/usr/bin/cmake`). Where the system
`libstdc++` is **newer** than the bundled one (Ubuntu 24, `GLIBCXX_3.4.32`+), the Vitis library
path puts its **older bundled** `libstdc++` first, so `cmake` dies at configure:

```
cmake: .../Vitis/lib/lnx64.o/Ubuntu/24/libstdc++.so.6: version `GLIBCXX_3.4.32' not found (required by cmake)
```

**Rule: build with no preload first; add it only if cmake dies with that exact error.** It helps
**only** where the system lib is newer — true on Ubuntu 24, **false on RHEL 8** (whose
`GLIBCXX_3.4.25` is older than bundled, so there is nothing to preload — don't hunt the
filesystem for one). Resolve the path rather than hardcoding it (differs Debian vs RHEL):

```bash
SYSCXX="$(ldconfig -p | awk '/libstdc\+\+\.so\.6/ {print $NF; exit}')"
LD_PRELOAD="$SYSCXX" <install>/bin/vitis -s /dev/stdin <<'PY' … PY
```

Field evidence (2026.2): on **Ubuntu 24** the `x86sim` build failed at cmake with the `GLIBCXX`
error and passed (`libadf.a`, rc 0) once preloaded; on **RHEL 8.10** the same build passed with
**no preload at all**.

## Quick reference

| | In-console (IDE) | Headless |
|--|------------------|----------|
| Run an API snippet | `runVitisPython(code=X)` | `vitis -s /dev/stdin <<'PY' … X … PY` |
| Session | pre-bound `client` | `create_client()` + `set_workspace()` (see "Workspace setup") + `dispose()` |
| Cross-step state | REPL persists | by component name / `libadf.a` path |
| `libstdc++` preload | n/a | **Ubuntu 24 only**, after a cmake `GLIBCXX` failure (not on RHEL) |

## What this does NOT cover

This is the **execution model** only (how to run the recipe headless). For **which** skill does
what and how they connect, see [`chaining.md`](chaining.md). This skill's own `SKILL.md` holds the
recipe; this file only changes how it is invoked.
