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
  version: "1.2.0-ea"
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

## Efficiency Guidelines

- **Pass `session_id`** to every `vivado_execute` call — get it from `vivado_list_sessions` if unknown.
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
    puts "  IN:$p VALUE:[get_property INPUT_VALUE $p] ACTIVITY:[get_property ACTIVITY_VALUE $p]"
  }
  puts "  === OUTPUT PROBES ==="
  foreach p [get_hw_probes -of_objects $vio -filter {TYPE == vio_output}] {
    puts "  OUT:$p VALUE:[get_property OUTPUT_VALUE $p]"
  }
}
```

**No VIO found** → STOP. Guide: "No VIO cores detected. Ensure the device was programmed with a bitstream containing VIO cores and the correct .ltx probes file is associated."

**⚠️ After programming or reprogramming,** VIO output probe software values may retain stale values from the previous session. Always sync before driving outputs:
```tcl
refresh_hw_vio -update_output_values [get_hw_vios hw_vio_1]
```
or reset all outputs to design-time initial values:
```tcl
reset_hw_vio_outputs [get_hw_vios hw_vio_1]
```
This ensures the software state matches hardware, so that subsequent `set_property OUTPUT_VALUE` + `commit_hw_vio` creates proper signal transitions (e.g., 0→1 edges for pulse-triggered designs).

### Step 2: Read Input Values

```tcl
refresh_hw_vio [get_hw_vios hw_vio_1]
foreach p [get_hw_probes -of_objects [get_hw_vios hw_vio_1] -filter {TYPE == vio_input}] {
  puts "$p INPUT:[get_property INPUT_VALUE $p] ACTIVITY:[get_property ACTIVITY_VALUE $p]"
}
```

### Step 3: Drive Output Values

Map user's NL request to probe + value:

```tcl
set_property OUTPUT_VALUE 1 [get_hw_probes <probe_name> -of_objects [get_hw_vios hw_vio_1]]
commit_hw_vio [get_hw_vios hw_vio_1]
```

**Reset all outputs** to initial values:
```tcl
reset_hw_vio_outputs [get_hw_vios hw_vio_1]
```

**Clear activity detectors:**
```tcl
reset_hw_vio_activity [get_hw_vios hw_vio_1]
```

### Step 4: Write Output Files

**⚠️ Write ALL files before giving any summary.** Use agent file tools.

```bash
mkdir -p vivado_agentic_ai_reports/hw-vio-debug
```

| File | Format | Content |
|------|--------|---------|
| `vio_snapshot.json` | JSON | All probe names, directions, current values, activity |
| `REPORT.md` | Markdown | VIO core status, probe values table, actions taken |

---

## Design-Specific Rules

**All outputs MUST use ACTUAL values. NO generic placeholders.**

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
| VIO out-of-sync | Status shows "Outputs out-of-sync" | Run: `refresh_hw_vio -update_output_values [get_hw_vios]` |

---

## Examples

**"What is the VIO reset signal?"** → Discover VIO → `refresh_hw_vio` → read `INPUT_VALUE` of reset probe → report value + activity.

**"Assert reset for 1 cycle"** → `set_property OUTPUT_VALUE 1` on reset probe → `commit_hw_vio` → `set_property OUTPUT_VALUE 0` → `commit_hw_vio`.

---

## Chipscope-mcp Fallback

Use this path only when Vivado MCP is not installed. Session lifecycle per VIVADO-PATTERNS.md Pattern 19 (vivado-skill-creator):

```
chipscope_session(action="connect", hw_server_url="TCP:<host>:3121", cs_server_url="TCP:<host>:3042")
chipscope_device(action="select", device_selector="0")
chipscope_scan(action="scan", ltx_path="<path-to-design>.ltx")
```

**VIO — read inputs, drive outputs:**
```
chipscope_vio(action="read_inputs", core="<vio-name>")
chipscope_vio(action="write_outputs", core="<vio-name>", probe="<probe>", value=1)
```

Exact action/parameter names must be confirmed against the live chipscope-mcp tool schema before use — do not assume the names above are final without checking `chipscope-mcp/src/chipscope_mcp/tools/chipscope_vio.py`.

**NEVER auto-program PDI.** Stop and ask before `chipscope_device(action="program")`.

---

## Integration

**Complementary:** Drives stimulus for `hw-ila-debug` capture (combined debug session)

---

## Metadata

**Keywords:** VIO, probe, input, output, activity, monitor, drive, reset
**Confidence Threshold:** 0.80

---

## Changelog

### Version 1.2.0-ea (2026-07-16)
- Designate skill as Early Access (`-ea`); add `maturity: early-access`
- Split the former combined ILA/VIO skill into hw-vio-debug (VIO only); ILA moved to hw-ila-debug

### Version 1.1.0 (2026-07-01)
- Add chipscope-mcp fallback path (Vivado-first/chipscope-mcp-fallback routing policy)

### Version 1.0.0 (2026-04-28)
- Initial release — ILA capture + VIO monitor/control via unified hw_ila/hw_vio Tcl
- Split: SKILL.md (workflow) + REFERENCE.md (property tables, schemas)
