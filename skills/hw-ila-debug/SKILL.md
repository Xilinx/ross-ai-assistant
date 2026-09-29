---
name: hw-ila-debug
description: >-
  Interact with ILA debug cores via vivado-mcp or chipscope-mcp.
  Discover cores, configure triggers,
  arm, capture, upload, export waveform data (CSV/VCD), and analyze results.
  ILA in vivado-mcp supports every device family supported by Vivado.
  chipscope-mcp supports Versal only.
  Use when user asks to "capture ILA data", "set ILA trigger", "trigger on signal",
  "export waveform", "arm ILA", or "trigger immediately".
license: MIT
compatibility: Requires Vivado 2026.1+, hardware with an ILA core, and access to either vivado-mcp or chipscope-mcp.
metadata:
  version: "1.2.2"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# ILA Debug (EA)

> **Early Access** — this skill may change before general availability.

Interacts with ILA debug cores on live hardware: discover → configure trigger → arm → capture → upload → export → analyze.

See [REFERENCE.md](REFERENCE.md) for ILA property tables, Tcl command reference, TSM syntax, compare value encoding, and report schema.

**Routing:** Try Vivado MCP (`vivado_execute` + Hardware Manager Tcl, below) first if a Vivado MCP session is available — it works across all device families. Fall back to chipscope-mcp (`chipscope_ila_core`/`chipscope_ila_capture`) if Vivado MCP is not installed. See [Chipscope-mcp Fallback](#chipscope-mcp-fallback) below.

## Tools Used

**Primary path — Vivado MCP** (plus the agent's own file tools):

| Tool | Purpose |
|------|---------|
| `vivado_execute` | Run all Tcl commands (`get_hw_ilas`, `run_hw_ila`, etc.). Every Tcl block MUST be run via `vivado_execute(session_id=..., command=<tcl>)`. |
| `vivado_doc_search` | Look up unfamiliar ILA properties, probe compare value syntax, TSM syntax. No `session_id` needed. |
| `vivado_status` | Check Vivado session health. |
| `vivado_list_sessions` | Find an active session to use (get its `session_id`). |
| Agent file tools | Write output files (report_data.json, REPORT.md, CSV/VCD exports). |

**Fallback path — chipscope-mcp** (when Vivado MCP is unavailable): `chipscope_session`, `chipscope_device`, `chipscope_scan`, `chipscope_ila_core`, `chipscope_ila_capture`. See [Chipscope-mcp Fallback](#chipscope-mcp-fallback).

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
- **Batch Tcl** — discover all ILA cores in one `vivado_execute`, not separate calls.
- **Write reports to file** — do not dump full waveform data in chat. Give a short summary only.
- **Use `vivado_doc_search`** for unfamiliar probe properties, TSM syntax, or compare value encoding.
- **Do NOT** use `display_hw_ila_data` — it opens a GUI window. Use `write_hw_ila_data` + `list_hw_samples` for data extraction.
- **Do NOT** use `shell` commands or Vivado Tcl file I/O. Use agent file tools.
- **Do NOT** retry failed Tcl with different syntax. Search docs first, then report the error.

---

## Capture & Analysis Workflow

### Step 1: Discover ILA Cores & Probes

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
puts "=== ILA CORES ==="
foreach ila [get_hw_ilas] {
  puts "ILA:$ila"
  puts "  DATA_DEPTH:[get_property CONTROL.DATA_DEPTH $ila]"
  puts "  MAX_DEPTH:[get_property STATIC.MAX_DATA_DEPTH $ila]"
  puts "  TRIGGER_MODE:[get_property CONTROL.TRIGGER_MODE $ila]"
  puts "  ADV_TRIGGER:[get_property STATIC.IS_ADVANCED_TRIGGER_MODE_SUPPORTED $ila]"
  puts "  TRIG_IN:[get_property STATIC.IS_TRIG_IN_SUPPORTED $ila]"
  puts "  CAPTURE_QUAL:[get_property STATIC.IS_BASIC_CAPTURE_MODE_SUPPORTED $ila]"
  puts "  STATUS:[get_property STATUS.CORE_STATUS $ila]"
  puts "  WINDOW_COUNT:[get_property CONTROL.WINDOW_COUNT $ila]"
  puts "  === PROBES ==="
  foreach p [get_hw_probes -of_objects $ila] {
    puts "  PROBE:$p WIDTH:[get_property PROBE_PORT_BIT_COUNT $p] TYPE:[get_property TYPE $p]"
  }
}
```

**No ILA found** → STOP. Guide: "No ILA cores detected. Ensure the device was programmed with a bitstream containing ILA cores and the correct .ltx probes file is associated."

On the chipscope-mcp fallback path:
- A zero-ILA reading before `chipscope_scan(action="scan", ltx_path=...)` has run is not authoritative — don't treat it as "no ILA found" yet.
- After scanning, if the server reports a `debug_ready` field, treat `debug_ready: true` as confirmation.
- If `debug_ready` is absent, fall back to the scan's own reported core count instead.

### Step 2: Configure Trigger

**Basic trigger** — map user's NL request to probe compare values:

```tcl
set ila [lindex [get_hw_ilas] 0]
# TRIGGER_MODE is read-only on Versal (axis_ila) — use catch to skip gracefully
catch {set_property CONTROL.TRIGGER_MODE BASIC_ONLY $ila}
set_property CONTROL.TRIGGER_CONDITION AND $ila
set_property CONTROL.TRIGGER_POSITION 512 $ila
set_property CONTROL.DATA_DEPTH 1024 $ila
set_property TRIGGER_COMPARE_VALUE eq1'b1 [get_hw_probes <probe_name> -of_objects $ila]
```

**Compare value syntax:** `eq<width>'<radix><value>` — see [REFERENCE.md](REFERENCE.md) for full encoding (=, !=, <, >, R, F, B, N, X).

**Advanced trigger (TSM)** — for multi-phase triggers (e.g., AXI transactions). See [REFERENCE.md](REFERENCE.md#trigger-state-machine-tsm-syntax) for the full grammar, rejection rules, and worked examples:
1. Generate a `.tsm` file with state machine code
2. Write it using agent file tools
3. Set: `set_property CONTROL.TRIGGER_MODE ADVANCED_ONLY $ila`
4. Set: `set_property CONTROL.TSM_FILE <path> $ila`
5. Compile-check: `run_hw_ila -compile_only $ila`

**Capture condition** (storage qualifier):
```tcl
set_property CONTROL.CAPTURE_MODE BASIC $ila
set_property CONTROL.CAPTURE_CONDITION AND $ila
set_property CAPTURE_COMPARE_VALUE eq1'b1 [get_hw_probes <probe> -of_objects $ila]
```

### Step 3: Arm, Wait & Upload

```tcl
run_hw_ila $ila
wait_on_hw_ila $ila
upload_hw_ila_data $ila
```

For immediate capture (no trigger, "aliveness" check):
```tcl
run_hw_ila -trigger_now $ila
wait_on_hw_ila $ila
upload_hw_ila_data $ila
```

**⚠️ `wait_on_hw_ila` blocks until trigger fires.** If the trigger condition is never met, the agent will hang. For conditions that may not fire, use `run_hw_ila -trigger_now` first to verify probe connectivity.

**⚠️ `wait_on_hw_ila -timeout N` is in MINUTES** (not seconds). Use `-timeout 1` for a 1-minute cap. Omitting `-timeout` waits indefinitely.

**⚠️ Versal DPC arm latency.** On Versal (axis_ila via DPC JTAG), there is a brief latency between `run_hw_ila` returning and the ILA hardware being fully armed. If the triggering event is driven by VIO in the same Vivado session, issue the `commit_hw_vio` that fires the trigger in a **separate `vivado_execute` call** — not in the same command as `run_hw_ila`. The natural inter-call latency (~100 ms) is sufficient for the ILA to settle into WAITING_FOR_TRIGGER state.

### Step 4: Export & Analyze Data

```tcl
write_hw_ila_data -csv_file vivado_agentic_ai_reports/hw-ila-debug/capture_data [upload_hw_ila_data $ila]
```

Export formats: `-csv_file` (spreadsheet), `-vcd_file` (waveform viewer), or native `.ila` (Vivado reload).

To read specific probe sample values programmatically:
```tcl
list_hw_samples [get_hw_probes <probe_name> -of_objects $ila]
```

### Step 5: Write Output Files — MANDATORY after every successful capture

**This step is not optional and does not depend on the user asking for a report.** Any prompt that results in a completed ILA capture — including "display captured data", "show the waveform", "upload and display", or "arm and capture" — MUST produce all three output files before any chat summary is given.

Write every file below under `vivado_agentic_ai_reports/hw-ila-debug/`. The
file tool creates missing parent directories, so do not shell out to `mkdir`
(this skill forbids terminal commands).

| File | Format | Content |
|------|--------|---------|
| `capture_data.csv` | CSV | Raw waveform data from ILA (via `write_hw_ila_data`) |
| `report_data.json` | JSON | Structured capture metadata + analysis (schema in [REFERENCE.md](REFERENCE.md)) |
| `REPORT.md` | Markdown | Executive summary: trigger config, capture stats, signal observations |

**Prompt mapping** — these user phrases all require files to be written before summarizing in chat:

| User says | Agent does |
|---|---|
| "display the captured ILA data" | upload → export CSV → write JSON + REPORT.md → chat summary |
| "show me the waveform" | upload → export CSV → write JSON + REPORT.md → chat summary |
| "arm and capture" | arm → wait → upload → export → write JSON + REPORT.md → chat summary |
| "upload and display" | upload → export → write JSON + REPORT.md → chat summary |

**After writing files, confirm in chat:** "Files written to `vivado_agentic_ai_reports/hw-ila-debug/`: `capture_data.csv`, `report_data.json`, `REPORT.md`."

**Where to find the report:** `vivado_agentic_ai_reports/hw-ila-debug/REPORT.md` in the working directory where the agent session was started. If the report was not generated, the capture data is still uploaded (from Step 3) — write the output files from that existing upload without re-arming. Only retry from Step 3 if the capture itself failed (e.g. trigger never fired, upload returned no samples).


---

## Design-Specific Rules

**All outputs MUST use ACTUAL values.**

| Rule | Wrong | Correct |
|------|-------|---------|
| Probe value | "signal is high" | "axi_arvalid (hw_ila_1/probe3): 1'b1 at sample 512" |
| Trigger config | "set up trigger" | "TRIGGER_MODE: BASIC_ONLY, CONDITION: AND, probe3 == eq1'b1, POSITION: 512/1024" |
| Core info | "found ILA" | "hw_ila_1: 6 probes, depth 4096, adv trigger supported" |

---

## Error Handling

| Error | Symptom | Action |
|-------|---------|--------|
| No ILA cores | `get_hw_ilas` empty | Check: bitstream has debug cores? .ltx file associated? |
| No hw_server | `get_hw_servers` empty | Guide: `connect_hw_server -url localhost:3121` |
| Trigger never fires | `wait_on_hw_ila` hangs | Use `run_hw_ila -trigger_now` to verify connectivity first |
| Probe not found | `get_hw_probes` returns empty | Check: .ltx file matches bitstream? `vivado_doc_search` for probe naming |
| TSM compile error | `run_hw_ila -compile_only` fails | Check the exact rule in [REFERENCE.md](REFERENCE.md#trigger-state-machine-tsm-syntax) first (it lists the specific rejection error strings); `vivado_doc_search` for anything not covered there |
| TSM `arm()` rejected (chipscope-mcp path) | `chipscope_ila_core(action="arm", trigger_state_machine=...)` returns `success: false` with a parser error | Match the exact error string against [REFERENCE.md](REFERENCE.md#trigger-state-machine-tsm-syntax)'s "Rejected patterns" table first |

---

## Examples

**"Capture whatever is on the ILA right now"** → Discover ILA → `run_hw_ila -trigger_now` → `wait_on_hw_ila` → `upload_hw_ila_data` → export CSV → summarize.

**"Trigger when AXI RVALID goes high"** → Find probe for `axi_rvalid` → `set_property TRIGGER_COMPARE_VALUE eq1'b1` → arm → wait → upload → export.

**"Trigger on AXI read transaction (address then data then last)"** → Generate 3-state TSM → write `.tsm` file → set `ADVANCED_ONLY` → arm → capture.

---

## Chipscope-mcp Fallback

Use this path only when Vivado MCP is not installed. Session lifecycle per VIVADO-PATTERNS.md Pattern 19 (vivado-skill-creator):

```
chipscope_session(action="connect", hw_server_url="TCP:<host>:3121", cs_server_url="TCP:<host>:3042")
chipscope_device(action="select", device_selector="0")
chipscope_scan(action="scan", ltx_path="<path-to-design>.ltx")
```

**ILA — discover, arm, capture:**
```
chipscope_ila_core(action="list")
chipscope_ila_core(action="probes", ila_name="<ila-name>")
chipscope_ila_core(action="set_trigger", ila_name="<ila-name>", probe_trigger_values={"<probe>": ["==", "0x...1"]})
chipscope_ila_core(action="arm", ila_name="<ila-name>", trigger_immediately=False, window_count=<n>, window_size=<n>, trigger_position=<n>)
# For a multi-phase (TSM) trigger instead of a single probe compare, pass
# trigger_state_machine=<program text> to arm() -- see REFERENCE.md's TSM
# section for grammar, rejection rules, and worked examples.
chipscope_ila_capture(action="wait", ila_name="<ila-name>", timeout_minutes=<n>)
chipscope_ila_capture(action="upload", ila_name="<ila-name>")
chipscope_ila_capture(action="export", ila_name="<ila-name>", file_path="vivado_agentic_ai_reports/hw-ila-debug/capture_data.csv", export_format="CSV")
```

Confirmed tool schema notes:
- `chipscope_ila_core` actions: `list/status/probes/reset_probes/set_trigger/set_capture/arm/trigger_immediate`
- `chipscope_ila_capture` actions: `wait/upload/get_data/export`
- Use `ila_name=` (not `core=`)
- `export`'s `file_path` is required — always pass it explicitly under `vivado_agentic_ai_reports/hw-ila-debug/` so it lands where Step 5 requires

**NEVER auto-program PDI.** Stop and ask before `chipscope_device(action="program")`.

---

## Integration

**Complementary:** `hw-vio-debug` drives stimulus → ILA captures response (combined debug session)
**Downstream:** Export CSV/VCD for offline analysis in external waveform viewers

---

## Metadata

**Keywords:** ILA, trigger, capture, waveform, probe, debug, CSV, VCD, TSM, state machine, compare value
