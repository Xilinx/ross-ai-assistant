---
name: hw-ddrmc-debug
description: >
  Debug DDR memory controller (DDRMC) calibration and health on live AMD Versal
  devices. Lists DDRMC controllers, reports per-stage calibration PASS/FAIL,
  analyzes per-byte-lane calibration-window margins, renders margin/eye
  visualizations, and diagnoses calibration failures. Primary path is Vivado MCP
  (get_hw_ddrmcs / report_hw_ddrmc); ChipScoPy MCP (chipscope_ddr /
  chipscope_ddr_eye_scan) is the fallback and the only path for a true 2D eye
  scan. Use when the user asks to "check DDR calibration", "DDR status",
  "DDR health", "calibration failed", "DDR margins", "DDR eye scan",
  "run DDR eye scan", "byte lane margins", "DDRMC status", "memory calibration",
  "DDR window analysis", or "check DDR controller".
license: MIT
compatibility: Requires Vivado 2026.1+ or ChipScoPy 2026.1+ with access to a programmed AMD Versal device containing a DDRMC.
metadata:
  version: "1.3.0"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# DDRMC Calibration Debug (EA)

> **Early Access** — this skill may change before general availability.

Debugs DDR memory controller calibration and health on live Versal hardware.
**Versal only** — DDRMC debug cores exist only on Versal devices.

See [REFERENCE.md](REFERENCE.md) for the `report_hw_ddrmc` output structure,
the `chipscope_ddr` action list, margin interpretation, eye-scan parameters,
and the report schema.

## Path Selection (deterministic — pick exactly one base path, then layer add-ons)

Evaluate top to bottom; use the first base path (rows 1–2) whose condition
holds. The add-on row (2D eye scan) is not an alternative to the base path — it
runs in addition to it, only when its own condition holds.

| # | Condition | Path | Use for |
|---|-----------|------|---------|
| 1 | Vivado MCP tools (`vivado_execute`) are available | **Primary base path: Vivado MCP** | list controllers, calibration, margins, margin-window visualization |
| 2 | No Vivado MCP; ChipScoPy MCP tools (`chipscope_ddr`) are available | **Fallback base path: ChipScoPy MCP** | list controllers, calibration, margins; the **only** path for a true Vref-swept 2D eye scan |
| add-on | You need a true 2D eye scan (Vref sweep) | ChipScoPy MCP, layered on top of whichever base path ran | 2D eye scan (used even when Vivado MCP is the base path) |
| — | Neither MCP available, OR zero DDRMCs after a completed scan | **STOP** | emit the STOP message below |

**STOP message:** "No DDRMC debug path is available (or the programmed design
has no DDRMC). Ensure Vivado MCP or ChipScoPy MCP is connected and the device is
programmed with a design containing a DDRMC."

## Version Gate (check once, before any analysis)

- **Required:** Vivado >= 2026.1 (primary) or ChipScoPy >= 2026.1 (fallback).
  Below this, STOP with: "Unsupported version: DDRMC debug requires Vivado/ChipScoPy 2026.1 or newer."
- Read the version with `version -short` (Vivado) or `chipscope_version` (ChipScoPy),
  and record it in `report_data.json` under `device.tool_version`.

---

## Primary Path — Vivado MCP

### Step 1 — Bring up hardware and select the Versal device

Run as one `vivado_execute` call:

```tcl
open_hw_manager
connect_hw_server -url TCP:<host>:<port>
open_hw_target
set fpga ""; foreach d [get_hw_devices] { if {[get_property PART $d] ne "arm_dap"} { set fpga $d; break } }
current_hw_device $fpga
refresh_hw_device [current_hw_device]
```

`get_hw_devices` lists a JTAG chain that includes `arm_dap`; the loop selects the
FPGA (the non-`arm_dap` device) deterministically.

### Step 2 — Enumerate controllers and read each one

```tcl
set out {}
foreach m [get_hw_ddrmcs] {
  if {[catch {get_property CAL_STATUS $m}]} {
    lappend out "CONTROLLER $m ENABLED false"
  } else {
    lappend out "CONTROLLER $m ENABLED true"
    lappend out [report_hw_ddrmc -return_string $m]
  }
}
join $out "\n"
```

- Enumerate with `get_hw_ddrmcs`; do **not** assume names — real names look like
  `DDRMC_1`, `DDRMC_2` (not `ddr_0`).
- A controller with **no `CAL_STATUS` property is disabled** (present in the
  design but no memory attached). Report it as disabled; never as FAIL.
- For each enabled controller, `report_hw_ddrmc -return_string` returns
  everything in one call (low iterations): DDRMC Status, Memory Configuration,
  Calibration Stages, and Calibration Window Margin Analysis. Parse this text —
  see [REFERENCE.md](REFERENCE.md#report_hw_ddrmc-output-structure).

### Step 3 — Extract results from the report text (per enabled controller)

- **Calibration:** `Calibration Status:` line = overall PASS/FAIL. The
  `Calibration Stages Information` block lists stages **verbatim and
  dynamically** (e.g. DDR4 shows `CAL_STAGE.01_F0_PHY_BISC` .. `12_CAL_DONE`;
  DDR5/LPDDR5 differ). Do **not** assume a fixed stage list or count — read the
  lines that are present.
- **Margins:** the `Calibration Window Margin Analysis` block gives, per
  Read/Write and Simple/Complex pattern, per byte/nibble `Left Margin`,
  `Center Point`, `Right Margin`, each as `pS (delay taps)`.
- **Config:** `Memory Configuration` block (Interface, Ranks, Data Width, Bytes,
  Nibbles, Frequency, MC Location).

### Step 4 (Vivado) — Margin-window visualization

The Vivado path has **no Vref-swept eye** (no eye/window property exists). Provide
a visualization from the calibration-window margins:

- Always write `ddr_margins.csv`: one row per (controller, mode, byte/nibble) with
  left/right margin in both pS and taps.
- Render `ddr_margin_window.png`: a per-byte/nibble left/right margin bar/heatmap
  from that CSV.

For a true Vref-swept 2D eye, switch to the ChipScoPy path (below).

---

## Fallback Path — ChipScoPy MCP

Use only when Vivado MCP is unavailable (or when a true 2D eye is required).

### Step 1 — Connect

```
chipscope_session(action='connect',
    hw_server_url='TCP:<hw_host>:<port>',
    cs_server_url='TCP:<cs_host>:3042')
```

- `cs_server_url` is **required for DDR** (debug cores). If the cs_server is on a
  different host than the hw_server, pass it explicitly — omitting it derives
  `<hw_host>:3042`, which is wrong when they differ.
- The connect result may include `debug_ready`. Treat `debug_ready: true` as
  cores discovered. **A zero-controller reading before a scan is not
  authoritative.**

### Step 2 — List controllers (auto-scans)

```
chipscope_ddr(action='list')
```

Returns each controller with `enabled` and `calibration_status`. Names look like
`ddr_0`, `ddr_1` (enumerate; do not hardcode). `list` performs the DDR core scan
automatically. Only if it returns **zero** controllers, run
`chipscope_scan(action='scan')` once; a zero result after that is authoritative →
STOP (no DDRMC).

### Step 3 — Per enabled controller, read status/calibration/margins/config

```
chipscope_ddr(action='report', ddr_name='<name>')     # status + calibration + config in one call
chipscope_ddr(action='stages', ddr_name='<name>')      # dynamic per-stage PASS/FAIL
chipscope_ddr(action='margins', ddr_name='<name>')     # per-byte/nibble margins
```

- `stages` returns stage names verbatim (dynamic) — do not assume a fixed set.
- `margins` returns each edge as `[taps, pS]`. If only the left edge is populated
  (`right: [0,0]`), fall back to `chipscope_ddr(action='report')` /
  `chipscope_ddr(action='properties', property_group='...')` for the full window.

### Step 4 (ChipScoPy) — True 2D eye scan

Only after calibration reports PASS:

```
chipscope_ddr(action='eye_scan_defaults', ddr_name='<name>', mode='read')   # get VRef/step defaults
chipscope_ddr_eye_scan(ddr_name='<name>', mode='read', steps=15, unit_index=0)   # inline PNG heatmap
```

`unit_index` selects the nibble (read) or byte (write). Export raw data with
`chipscope_ddr(action='eye_scan_data', ddr_name='<name>', output_format='csv',
export_path='vivado_agentic_ai_reports/hw-ddrmc-debug/ddr_eye_scan.csv')`.

---

## Step 5 — Write Output Files — MANDATORY after every analysis

**This step is not optional and does not depend on the user asking for a report.**
Any prompt that results in a completed DDRMC read — including "check DDR
calibration", "DDR status", "show margins", "run eye scan", "compare
controllers" — MUST write all output files before any chat summary.

Write every file under `vivado_agentic_ai_reports/hw-ddrmc-debug/` using agent
file tools. The file tool creates missing parent directories — do **not** shell
out to `mkdir` (this skill forbids terminal commands). ChipScoPy exports are
confined to `CHIPSCOPE_MCP_OUTPUT_DIR`.

| File | Format | Content |
|------|--------|---------|
| `report_data.json` | JSON | Structured status, config, dynamic stages, margins, versions (schema in [REFERENCE.md](REFERENCE.md#report-json-schema)) |
| `REPORT.md` | Markdown | Summary: controller table, calibration verdicts, margin analysis, recommendations |
| `ddr_margins.csv` | CSV | Per byte/nibble margins in pS + taps (Vivado path) |
| `ddr_margin_window.png` | PNG | Margin-window visualization (Vivado path) |
| `ddr_eye_scan.png` | PNG | 2D eye heatmap (ChipScoPy path) |
| `ddr_eye_scan.csv` | CSV | Raw eye-scan data (ChipScoPy path, if exported) |

**Prompt mapping** — these user phrases all require files to be written before
summarizing in chat:

| User says | Agent does |
|---|---|
| "check DDR calibration" / "DDR status" / "DDR health" | read → write `report_data.json` + `REPORT.md` → chat summary |
| "analyze DDR margins" / "byte lane margins" | read margins → write CSV + `report_data.json` + `REPORT.md` → chat summary |
| "run DDR eye scan" | verify cal PASS → eye scan → write PNG + `report_data.json` + `REPORT.md` → chat summary |
| "compare DDR controllers" | read all → write `report_data.json` + `REPORT.md` (comparison) → chat summary |

**After writing files, confirm in chat:** "Files written to
`vivado_agentic_ai_reports/hw-ddrmc-debug/`: `report_data.json`, `REPORT.md`, ..."

---

## Calibration Failure Diagnosis

When `Calibration Status` is FAIL:

1. Identify the failing stage from the stage block (the first non-Pass stage).
2. Map the stage to likely causes — see
   [REFERENCE.md](REFERENCE.md#calibration-failure-decision-tree).
3. Cross-reference `hw-sysmon` (supply voltage affecting the DDR PHY) and
   `hw-noc-debug` (NoC timeout pointing at the DDRMC).

Report the failing stage by its **exact name** from the output; never relabel it.

## Multi-DDRMC

1. Enumerate all controllers (`get_hw_ddrmcs` / `chipscope_ddr(action='list')`).
2. Read each **enabled** controller; skip disabled ones (report as disabled).
3. Compare margins across enabled controllers; report the weakest controller and
   its weakest byte/nibble with the exact margin values.

## Error Handling

| Symptom | Meaning | Action |
|---------|---------|--------|
| `get_hw_ddrmcs` / `list` empty after a scan | Design has no DDRMC | STOP (see Path Selection) |
| Controller has no `CAL_STATUS` / `enabled=false` | Present but no memory | Report as disabled, not FAIL |
| Zero controllers **before** a scan / `debug_ready:false` | Not yet scanned | Not authoritative — scan first |
| Calibration FAIL | A stage did not pass | Run failure diagnosis |
| Eye scan empty | Calibration not complete | Fix calibration first; eye scan needs PASS |
| Eye scan times out | `steps` too high / link slow | Increase `max_wait_minutes`, or reduce `steps` |
| Version below 2026.1 | Unsupported | STOP with the unsupported-version message |

---

## Example (grounded)

**"Check DDR status"** on a vpk120 → enumerate (4 controller sites, 1 populated) →
read `DDRMC_1` → write artifacts → summarize:

> `DDRMC_1`: calibration **PASS**, LPDDR4 x64 @ 1953 MHz, all 14 stages Pass.
> Weakest write-complex margin: byte 4 (~53 taps). `DDRMC_2/3/4`: disabled (no
> memory). Files written to `vivado_agentic_ai_reports/hw-ddrmc-debug/`.

Report values verbatim from the tool; never invent a configuration or margin.

## Integration

**Upstream:** `chipscope_session` (connection), `chipscope_device` (programming — **never auto-program; stop and ask**).
**Complementary:** `hw-noc-debug` (NoC timeout → DDRMC), `hw-sysmon` (supply affects DDR PHY).
**Downstream:** margin issues → board SI investigation, memory timing review.

## Metadata

**Keywords:** DDR, DDRMC, calibration, eye scan, margins, byte lane, memory, DDR4,
DDR5, LPDDR, VRef, write leveling, read gate, calibration window.
**Confidence Threshold:** 0.80

---

## Changelog

### Version 1.3.0-ea (2026-08-26)
- Remove the direct `sys_dbg_util` CLI cross-check path; chipscope-mcp `sysdbg_*`
  tools remain valid MCP paths. Skill forbids terminal commands with no exceptions.
- Rewrite for determinism and multi-agent clarity: single **Path Selection**
  table (Vivado MCP primary, ChipScoPy MCP fallback, explicit STOP) replacing the
  prior routing contradiction.
- Add deterministic Vivado bring-up + `get_hw_ddrmcs` enumeration; read each
  enabled controller with one `report_hw_ddrmc -return_string` call.
- Remove the hardcoded 8-stage table and the `ddr_0` literal; stages and
  controller names are now read dynamically (DDR4 shows 12 `CAL_STAGE.*`; other
  memory types differ).
- Correct the "no flat calibration property" claim — `CAL_STATUS` exists; use it,
  and fall back to `CAL_STAGE.*` if absent.
- Define the visualization per path: Vivado = margin-window PNG+CSV; ChipScoPy = true 2D eye.
- Make output-file generation mandatory and unconditional; add a prompt-mapping
  table; fix all output paths under `vivado_agentic_ai_reports/hw-ddrmc-debug/`.
- Add a non-authoritative "zero-DDRMC-before-scan / `debug_ready`" guard.
- Add a version gate (Vivado/ChipScoPy >= 2026.1).
- Pin ChipScoPy tool signatures to the live chipscope-mcp v1.5.0 schema.
- Restore the eye-scan timeout row to Error Handling; add one grounded example
  (validated live on a vpk120 LPDDR4 design).

### Version 1.2.0-ea (2026-07-16)
- Designate skill as Early Access (`-ea`); add `maturity: early-access`
- Rename skill identity to hw-ddrmc-debug: update `name:` frontmatter, report
  output path, and cross-skill references to match the renamed directory

### Version 1.1.0 (2026-07-01)
- Fix stale `chipscopy_*` tool prefix -> `chipscope_*`
- Add Vivado MCP fallback path (`get_hw_ddrmcs`/`report_hw_ddrmc`), which is
  actually the primary path per the routing policy since it works natively
  on Versal; chipscope-mcp remains available as the documented alternative
- Confirmed `chipscope_ddr`'s action list (list/status/calibration/health/
  report/config/stages/margins/eye_scan/eye_scan_data/eye_scan_defaults)
  matches this skill's usage exactly against the live tool source

### Version 1.0.0 (2026-05-01)
- Initial release — DDRMC calibration debug + eye scan via ChipScoPy MCP
- Per-stage analysis, per-byte margins, 2D eye scan with inline PNG
