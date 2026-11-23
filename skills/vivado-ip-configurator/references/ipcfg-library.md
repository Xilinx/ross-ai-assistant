# ipcfg library reference (for direct Tcl callers)

This skill's `SKILL.md` describes the JSON workflow for configuring one cell per prompt. Callers
that drive the bundled `ipcfg` Tcl library directly -- the `vivado-ipi-assembler` skill when it
builds a whole block design -- use this page instead. The library is in `lib/ipcfg/`; load it with
`source -notrace <vivado-ip-configurator>/lib/ipcfg/ipcfg.tcl`. These sections were moved here
from the original skill's `SKILL.md` on 2026-11-07, with paths updated to `lib/ipcfg/`.

## Python quick reference and local API

[FUNCTIONS.md](../FUNCTIONS.md) lists every `ipcfg::` helper with its call shape, purpose and
success/failure results, generated from the code and readable without Vivado. In the Vivado
session, `ipcfg::help <word>` prints the same entry. Use these instead of reading `lib/ipcfg/*.tcl`. The local Tcl API inventory (in the vivado-ipi-assembler skill, `REFERENCE.md`) adds
gates and mutation ownership. Do not infer a helper's
contract from its name or parse every return as a status line.

**Offline scripts:** use `lib/ipcfg/vivado_python.sh` (`vivado_python.cmd` on Windows).
The skill should try the installed Vivado's bundled Python automatically before
an existing host interpreter. Customers do not need a separate Python installation,
notebook changes, or a mandatory policy flag. Use the active Vivado environment;
the Linux launcher also discovers `vivado` on PATH. If discovery fails, locate the
installed Vivado and set `IPCFG_VIVADO_ROOT` to its root, not its executable.
Do not ask customers to install Python as the first remedy.
Do not start Vivado merely to run Python. **Inside an already loaded
Vivado session**, reuse `ipcfg::python_exec`; do not build another resolver or use
`exec [ipcfg::python_exe] ...`, which bypasses the environment wrapper.

Offline Linux invocation (replace both absolute paths; keep the quotes):

```sh
/bin/sh "/absolute/workspace/.claude/skills/vivado-ip-configurator/lib/ipcfg/vivado_python.sh" \
  "/absolute/workspace with spaces/outputs/check.py" "argument with spaces"
```

This starts Python, not Vivado. A nonzero child exit remains nonzero. Each shell
invocation probes its runtime afresh; the once-per-process cache below belongs to
the loaded Tcl helper. The offline launchers reject multiple compatible bundles
as ambiguous; choose an approved `IPCFG_PYTHON` override in that case.

| Existing helper | Return / use |
|---|---|
| `ipcfg::python_status` | `PYTHON:OK source=... version=... executable=...`, or returned `PYTHON_RUNTIME_FAIL:...` text; check the prefix, not just Tcl success. |
| `ipcfg::python_runtime` | Dict with `executable`, `root`, `source`, `version`, `score`; resolves lazily, then reuses the cache. Raises on resolution failure. |
| `ipcfg::python_exe` | Selected executable path only, not an environment-aware invocation. Raises on resolution failure. |
| `ipcfg::python_exec args...` | Child output as a string; pass the script path and each argument separately. Resolution, import and nonzero-exit failures raise Tcl errors. |
| `ipcfg::python_script script args...` | Preferred script entry point: requires an existing absolute `.py` path before invoking `python_exec`. Invalid paths raise `PYTHON_SCRIPT_FAIL`; child errors remain Tcl errors. |

Once per session, before mutations, record the interpreter and required stdlib
imports in the workflow evidence (after loading the helper). The skill performs
this preflight; do not add it to the user's notebook or require them to run it:

```tcl
set python_status [ipcfg::python_status]
puts $python_status
if {![string match {PYTHON:OK *} $python_status]} { error $python_status }
puts [ipcfg::python_exec -c {import argparse, base64, hashlib, json, pathlib, re, sys, typing; print("PYTHON_IMPORTS:OK")}]
```

Run a script with list-safe arguments and absolute paths, including paths with spaces:

```tcl
set script {/absolute/workspace/.claude/skills/vivado-spec-generator/scripts/validate_spec.py}
set arguments [list {/absolute/workspace with spaces/outputs/design_spec.json}]
puts [ipcfg::python_script $script {*}$arguments]
```

Replace example paths with the actual workspace paths. A stdlib preflight does not
prove optional packages are installed; add only imports required by the chosen script.
Keep errors visible and stop that stage on failure.

The Tcl resolver prefers an explicit `IPCFG_PYTHON` override, then the newest usable Vivado
bundle, then host `python3`/`python`. An invalid override fails, rather than silently
falling back. Set the override to an approved interpreter path before preflight.
The cache key includes platform, `IPCFG_PYTHON`, `IPCFG_VIVADO_ROOT`, `XILINX_VIVADO`,
`VIVADO_PATH`, `PATH` and `IPCFG_REQUIRE_BUNDLED`; unchanged keys reuse the runtime without repeat probes.
`IPCFG_REQUIRE_BUNDLED=1` is optional for controlled tests or an explicitly requested
bundle-only policy; do not enable it by default. It disables implicit host fallback,
but an explicit `IPCFG_PYTHON` override still takes precedence.
Do not routinely call `python_reset` or re-source libraries to rediscover Python.
The wrapper temporarily clears `PYTHONHOME`, `PYTHONPATH`, `PYTHONSTARTUP` and
`PYTHONINSPECT`, adjusts the child runtime library path (`LD_LIBRARY_PATH` on Linux,
`PATH` on Windows), and restores prior values or absence on success **and failure**.
Preserve that per-invocation restoration; never permanently unset Vivado's environment.

## Design workflow and scratch experiments

For real designs, describe a **requirements checklist**, **configuration readback** and
**observed hardware interfaces**. Benchmark grades are not design acceptance criteria.
When called during system planning, use the assembler's scratch project and preserve its
delivery-project boundary. Do not change the target part or create another session.

For each uncertain capability, record the expected observable result before testing.
Use the existing creation, automation, parameter-discovery and readback helpers; batch
related operations. Check actual interface protocol/direction and pin width as well as
CONFIG values. A stored count does not prove a vector grew. For connection-derived
properties, make a minimal documented connection and inspect the result after propagation;
do not force ownership of a derived property based on a guessed workaround.

Capture defaults before configuration when a before/after comparison is needed. A cell
created and configured in one call is already configured, not a default baseline. Defaults
that satisfy documented requirements are valid and do not require an artificial change.
Scratch success must be rechecked in the delivery design. Allow at most 10 total attempts
per IP, including the initial attempt, shared across parameter configuration, automation,
stub connections and nested helpers. Do not restart the count on a nested call or resume.
Record the attempt count and diagnostic evidence in the run output. After the tenth failed
attempt, stop mutating that IP and ask for user intervention before any further attempt.

### Persistent attempt enforcement

An explicitly selected external independent-audit controller may supply
`ipcfg::repair_guard` for bounded corrective transactions after initial construction.
In that mode, allow at most ten persistent corrective transactions per logical IP,
rechecking independent requirements after each and stopping on PASS. The controller
must own the project ledger lock, reserve before mutation, enforce declared operations
and stable identities, and refuse exhausted legacy ledgers without migration approval.
Do not set this hook directly or use it to bypass a refusal. Without that controller,
the legacy mutation-count policy below remains in force.

The configuration helpers use `ipcfg::attempt_call` and persist counts plus diagnostic
history in `<project-directory>/.ipcfg/attempts.tcldict`, keyed by BD and full cell path.
Creation/configuration, board binding, parameter repair, automation, stub recovery and
mutating sub-key discovery share a hard limit of ten attempts. Each actual mutation
operation counts, including successful ones and fallback attempts; forwarding calls and
empty/idempotent operations do not add a count. A temporary range probe and its required
restoration form one attempt. Library reload, assembler initialization and a new Tcl
process on the same project do not reset the ledger.

`CONFIGURE_FAIL:ATTEMPT_LIMIT` requires user intervention, not another recovery path.
An unreadable ledger or existing lock fails closed with `ATTEMPT_STATE`; preserve the
files and investigate before asking the user to authorize recovery. There is no automatic
reset or budget extension. Preserve the project directory when resuming.

This is helper-level enforcement, not a global Vivado command interceptor. Direct Tcl
writes and assembler infrastructure writes outside these helpers are not automatically
counted. Do not use those paths to bypass exhaustion. A different project, BD or cell
identity has a separate ledger entry; do not rename or recreate context to obtain retries.
