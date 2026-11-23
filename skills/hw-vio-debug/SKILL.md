---
name: hw-vio-debug
description: >-
  Interact with VIO debug cores via vivado-mcp or chipscope-mcp.
  Read input probes, drive output probes, monitor activity, and reset outputs.
  vivado-mcp supports all device families. chipscope-mcp supports Versal only.
  Use when user asks to "read VIO inputs", "drive VIO output", "assert <signal> via VIO",
  "check signal activity", "what is the value of <signal>", or "monitor <probe>".
license: MIT
compatibility: Requires Vivado 2026.1+, hardware with a VIO core, and access to either vivado-mcp or chipscope-mcp.
metadata:
  version: "1.2.4"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# VIO Debug (EA)

> **Early Access** — this skill may change before general availability.

Interacts with VIO debug cores on live hardware: discover → read inputs → drive outputs → monitor activity.

See [REFERENCE.md](REFERENCE.md) for VIO property tables and Tcl command reference.

**Routing:** Try Vivado MCP (`vivado_execute` + Hardware Manager Tcl, below) first if a Vivado MCP session is available — it works across all device families. Fall back to chipscope-mcp (`chipscope_vio`) if Vivado MCP is not installed. See [Chipscope-mcp Fallback](#chipscope-mcp-fallback) below.

## Tools Used

**Primary path — Vivado MCP** (plus the agent's own file tools):

| Tool | Purpose |
|------|---------|
| `vivado_execute` | Run all Tcl commands (`get_hw_vios`, `commit_hw_vio`, etc.). Every Tcl block MUST be run via `vivado_execute(session_id=..., command=<tcl>)`. |
| `vivado_doc_search` | Look up unfamiliar VIO properties. No `session_id` needed. |
| `vivado_status` | Check Vivado session health. |
| `vivado_list_sessions` | Find an active session to use (get its `session_id`). |
| Agent file tools | Write output files (vio_snapshot.json, REPORT.md). |

**Fallback path — chipscope-mcp** (when Vivado MCP is unavailable): `chipscope_session`, `chipscope_device`, `chipscope_scan`, `chipscope_vio`. See [Chipscope-mcp Fallback](#chipscope-mcp-fallback).

**No terminal commands, no Python.** Everything goes through Vivado MCP or, as fallback, chipscope-mcp.

---

## Session Setup: Vivado Version Must Match hw_server

- `vivado_list_sessions` → check for an active session.
- Before reusing one, run `version -short` and compare to the target `hw_server`'s version.
- Mismatch → `ERROR: [Labtools 27-3366] Cannot support older hw_server version`.
- **On mismatch: do not retry or restart automatically.** `vivado_start` does not pick a
  matching Vivado version for you — an omitted `vivado_path` just resolves from `PATH`,
  which is often the same mismatched install. Report the exact error, the session's
  version, and the target `hw_server`'s version to the user, and ask them to point at
  the correct Vivado install (`vivado_path`) or the correct `hw_server`.
- This only applies to an **already-running, independently-started** `hw_server`. A
  `hw_server` that Vivado auto-starts itself when connecting to a locally attached board
  is launched from that same Vivado install, so it cannot be version-mismatched — see
  "No hw_server" below.
- Only reuse a session once its version matches.

---

## Efficiency Guidelines

- **Pass `session_id`** to every `vivado_execute` call — get it from `vivado_list_sessions` if unknown.
- **Verify session version against `hw_server` before reuse, and report a mismatch to the user rather than retrying** — see [Session Setup](#session-setup-vivado-version-must-match-hw_server) above.
- **Batch Tcl** — discover all VIO cores in one `vivado_execute`, not separate calls.
- **Write reports to file** — do not dump full probe data in chat. Give a short summary only.
- **Use `vivado_doc_search`** for unfamiliar probe properties.
- **Do NOT** use `shell` commands or Vivado Tcl file I/O. Use agent file tools.
- **Do NOT** retry failed Tcl with different syntax. Search docs first, then report the error.

---

## Monitor & Control Workflow

### Step 1: Discover VIO Cores & Probes

```tcl
puts "=== VIO CORES ==="
foreach vio [get_hw_vios] {
  puts "VIO:$vio INSTANCE:[get_property INSTANCE_NAME $vio] REFRESH:[get_property CORE_REFRESH_RATE_MS $vio]"
  puts "  === INPUT PROBES ==="
  foreach p [get_hw_probes -of_objects $vio -filter {TYPE == vio_input}] {
    refresh_hw_vio $vio
    puts "  IN:$p WIDTH:[get_property WIDTH $p] RADIX:[get_property INPUT_VALUE_RADIX $p] VALUE:[get_property INPUT_VALUE $p] ACTIVITY:[get_property ACTIVITY_VALUE $p]"
  }
  puts "  === OUTPUT PROBES ==="
  # A plain refresh_hw_vio reads inputs only; OUTPUT_VALUE can read blank until this runs
  refresh_hw_vio -update_output_values $vio
  foreach p [get_hw_probes -of_objects $vio -filter {TYPE == vio_output}] {
    puts "  OUT:$p WIDTH:[get_property WIDTH $p] RADIX:[get_property OUTPUT_VALUE_RADIX $p] VALUE:[get_property OUTPUT_VALUE $p]"
  }
}
```

**No VIO found** → STOP. Guide: "No VIO cores detected. Ensure the device was programmed with a bitstream containing VIO cores and the correct .ltx probes file is associated."

**A blank VIO Dashboard is not "no VIO found".** The VIO Dashboard starts out empty until probes are added to it (UG908). Check `get_hw_vios` instead. Hardware window right-click menu names on a VIO core: **Refresh Input and Output Values from VIO Core** (`refresh_hw_vio -update_output_values`), **Commit VIO Core Outputs** (`commit_hw_vio`), **Reset VIO Core Outputs** (`reset_hw_vio_outputs`), **Open Dashboard**.

**⚠️ After programming or reprogramming,** VIO output probe software values can read blank or keep stale values from the previous session, and a plain `refresh_hw_vio` reads inputs only. Always sync before reading `OUTPUT_VALUE` or driving outputs, by reading the hardware values into `OUTPUT_VALUE`:
```tcl
refresh_hw_vio -update_output_values [get_hw_vios hw_vio_1]
```
This makes the software state match hardware, so that subsequent `set_property OUTPUT_VALUE` + `commit_hw_vio` creates proper signal transitions (e.g., 0→1 edges for pulse-triggered designs).
The sync does not protect a commit: the first `commit_hw_vio` after it can still send the previous values, so check every commit (Step 3).

If the core shows "Outputs out-of-sync", UG908 gives two fixes: the refresh above, which keeps what the hardware drives, or `commit_hw_vio [get_hw_vios hw_vio_1]`, which drives your `OUTPUT_VALUE` settings to the hardware. Commit only when those settings are what you want: right after programming they can be stale.

**`reset_hw_vio_outputs` is not a sync.** It drives the hardware outputs to their design-time initial values. UG835 says it does not update `OUTPUT_VALUE`, and UG908 says it leaves the core "Outputs out-of-sync". Do not rely on it to update `OUTPUT_VALUE`. After a reset, run `refresh_hw_vio -update_output_values` before reading `OUTPUT_VALUE` or driving outputs.

### Step 2: Read Input Values

```tcl
refresh_hw_vio [get_hw_vios hw_vio_1]
foreach p [get_hw_probes -of_objects [get_hw_vios hw_vio_1] -filter {TYPE == vio_input}] {
  puts "$p INPUT:[get_property INPUT_VALUE $p] ACTIVITY:[get_property ACTIVITY_VALUE $p]"
}
```

### Step 3: Drive Output Values

Map user's NL request to probe + value. Set, commit, then read back and check:

```tcl
set vio [get_hw_vios hw_vio_1]
set p [get_hw_probes <probe_name> -of_objects $vio]
set_property OUTPUT_VALUE 1 $p
commit_hw_vio $vio
refresh_hw_vio -update_output_values $vio
if {![string equal -nocase [get_property OUTPUT_VALUE $p] 1]} {
  set_property OUTPUT_VALUE 1 $p
  commit_hw_vio $vio
  refresh_hw_vio -update_output_values $vio
}
puts "$p OUTPUT_VALUE:[get_property OUTPUT_VALUE $p]"
```

**Check every commit.** `commit_hw_vio` can send the previous values and still return no error (Vivado CR-1288058). Vivado's first background refresh can overwrite `OUTPUT_VALUE` between your `set_property` and the commit, even right after the sync. On a VCK190, a first write held open 1 s after the sync sent the previous values in 38 of 40 fresh sessions, and one more set and commit fixed 19 of 19. So after every `commit_hw_vio`, run `refresh_hw_vio -update_output_values` and compare each probe you set. Compare ignoring letter case; in `UNSIGNED` or `SIGNED`, compare as numbers. Vivado lowercases `HEX` letters; `UNSIGNED` drops leading zeros. If one differs, set it again and commit once more. If it still differs, stop and report the probe, the value you set, and the value read back.

**Bus (multi-bit) probes:** `OUTPUT_VALUE` and `INPUT_VALUE` are strings in the probe's radix (`OUTPUT_VALUE_RADIX` or `INPUT_VALUE_RADIX`), not decimal numbers. On the Vivado Tcl path, the default is `BINARY` on a 1-bit probe and `HEX` on a wider one. In `HEX` or `BINARY`, write the full width, one `BINARY` digit per bit or one `HEX` digit per 4 bits: decimal 16 is `00000010` on a 32-bit `HEX` probe (`10` on an 8-bit one). A shorter string is rejected (`[Designutils 20-1474]`) and the probe keeps its previous value. To write a decimal number, set the radix to `UNSIGNED` (or `SIGNED`) first:

```tcl
set vio [get_hw_vios hw_vio_1]
set p [get_hw_probes <bus_probe_name> -of_objects $vio]
set_property OUTPUT_VALUE_RADIX UNSIGNED $p
set_property OUTPUT_VALUE 16 $p
commit_hw_vio $vio
refresh_hw_vio -update_output_values $vio
puts "$p OUTPUT_VALUE:[get_property OUTPUT_VALUE $p]"
```

Then check the read-back and re-commit once as above.

**Reset all outputs** to initial values, then read them back (the reset leaves the core out-of-sync):
```tcl
reset_hw_vio_outputs [get_hw_vios hw_vio_1]
refresh_hw_vio -update_output_values [get_hw_vios hw_vio_1]
```

**Clear activity detectors:**
```tcl
reset_hw_vio_activity [get_hw_vios hw_vio_1]
```

### Step 4: Write Output Files

**⚠️ Write ALL files before giving any summary.** Use agent file tools.

Write every file below under `vivado_agentic_ai_reports/hw-vio-debug/`. The
file tool creates missing parent directories, so do not shell out to `mkdir`
(this skill forbids terminal commands).

| File | Format | Content |
|------|--------|---------|
| `vio_snapshot.json` | JSON | All probe names, directions, current values, activity |
| `REPORT.md` | Markdown | VIO core status, probe values table, actions taken |

---

## Design-Specific Rules

**All outputs MUST use ACTUAL values.**

| Rule | Wrong | Correct |
|------|-------|---------|
| VIO output | "asserted reset" | "Set OUTPUT_VALUE=1 on rst_n (hw_vio_1), committed" |
| Core info | "found VIO" | "hw_vio_1: 4 input probes, 2 output probes, refresh 500ms" |

---

## Error Handling

| Error | Symptom | Action |
|-------|---------|--------|
| No VIO cores | `get_hw_vios` empty | Check: bitstream has debug cores? .ltx file associated? |
| No hw_server | `get_hw_servers` empty | Guide: `connect_hw_server -url localhost:3121` |
| VIO out-of-sync | Status shows "Outputs out-of-sync" (after programming, after `reset_hw_vio_outputs`, or another Vivado wrote the outputs) | Keep what the hardware drives: `refresh_hw_vio -update_output_values [get_hw_vios]`. Or drive your `OUTPUT_VALUE` settings, only when they are what you want: `commit_hw_vio [get_hw_vios]`. A reset does not fix it. |
| Commit did not take | `commit_hw_vio` returns no error, but the read-back shows the previous value | Known Vivado issue CR-1288058. Compare ignoring letter case; in `UNSIGNED` or `SIGNED`, compare as numbers. Vivado lowercases `HEX` letters; `UNSIGNED` drops leading zeros. Set the value again, commit once more, and read back. If it still differs, stop and report the probe, the value set and the value read. |
| Design stalls after a chipscope-mcp write | Write succeeded and readback matches, but inputs stop changing | Partial write. Write every output of the VIO in one call (see [Chipscope-mcp Fallback](#chipscope-mcp-fallback)) |

---

## Examples

**"What is the VIO reset signal?"** → Discover VIO → `refresh_hw_vio` → read `INPUT_VALUE` of reset probe → report value + activity.

**"Pulse reset"** → `set_property OUTPUT_VALUE 1` on reset probe → `commit_hw_vio` → read back and check (Step 3) → `set_property OUTPUT_VALUE 0` → `commit_hw_vio` → read back and check. Check both commits. One `commit_hw_vio` takes about 60 to 190 ms (measured on a VCK190), and a 1-then-0 pulse over two commits stays high about 160 to 200 ms, longer with the read-back between them. That is many thousands of clock cycles, never one cycle. A one-cycle strobe needs an edge detector in the design.

---

## Chipscope-mcp Fallback

Use this path only when Vivado MCP is not installed. Session lifecycle per VIVADO-PATTERNS.md Pattern 19 (vivado-skill-creator):

```
chipscope_session(action="connect", hw_server_url="TCP:<host>:3121", cs_server_url="TCP:<host>:3042")
chipscope_device(action="select", device_selector="0")
chipscope_scan(action="scan", ltx_path="<path-to-design>.ltx")
```

**VIO — read inputs:**
```
chipscope_vio(action="read", vio_name="<vio-name>")
```

chipscope-mcp reports each probe's width (from its bus indices) and plain integer values but no radix. If asked for a radix, say it is not available through chipscope-mcp.

**VIO — drive outputs: ALWAYS write every output probe of the VIO in one `write` call.**

Some chipscope-mcp versions write only the probes you name. After programming, a write that names only some outputs can leave the VIO's other outputs driving wrong values and stall the design. The tool still returns success, and the readback shows the value you asked for. Writing every output in one call is correct on every version, with or without the fix.

1. List the VIO's output probes (entries with `direction` `"out"`):
   ```
   chipscope_vio(action="probes", vio_name="<vio-name>")
   ```
2. Read their current values:
   ```
   chipscope_vio(action="read", vio_name="<vio-name>", probes=["<out1>", "<out2>", ...])
   ```
3. Write all of them in one call. Change only what the user asked for; pass the current value for every other output:
   ```
   chipscope_vio(action="write", vio_name="<vio-name>",
                 probe_values={"<out1>": <current>, "<out2>": <new>, ...})
   ```
4. Confirm the change through an **input** probe the output drives (value moving, direction, activity). Do not use the `readback` field that `write` returns as proof: it shows the value you asked for. (On the Vivado MCP path, the Step 3 read-back after each commit is the check.)

- A pulse (assert, then deassert) is two `write` calls, each with every output.
- `action="ports_write"` has the same problem. If you use it, pass every `probe_out<N>` port.
- `action="reset"` restores initial values but does not prevent this. Still write every output afterwards.
- If the design already stalled after a partial write, one write of every output recovers it.

Exact action/parameter names must be confirmed against the live chipscope-mcp tool schema before use — do not assume the names above are final without checking `chipscope-mcp/src/chipscope_mcp/tools/chipscope_vio.py`.

**NEVER auto-program PDI.** Stop and ask before `chipscope_device(action="program")`.

---

## Integration

**Complementary:** Drives stimulus for `hw-ila-debug` capture (combined debug session)

---

## Metadata

**Keywords:** VIO, probe, input, output, activity, monitor, drive, reset
