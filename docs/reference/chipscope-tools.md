<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Tool Reference

ChipScope MCP exposes **23 tools** across hardware-debug domains: 18
`chipscope_*` tools plus 5 `sysdbg_*` system-debug tools from the bundled
`sys-dbg-util` plugin. All tools run locally over stdio and return structured
results your AI client can act on. You rarely call these directly — the AI
client selects the right tool for your request — but the catalog below shows
what ChipScope can drive.

## Session & device

| Tool | What it does |
|------|--------------|
| `chipscope_session` | Connect to or disconnect from `hw_server`/`cs_server`, and show session status and the hardware tree |
| `chipscope_device` | List, select, program (PDI), or reset a JTAG device, and show its resources and logs |
| `chipscope_scan` | Scan the selected device for debug cores, optionally using an LTX file for probe names |
| `chipscope_property` | Read or change a cable, debug-core, device, or `cs_server` property without reconnecting |
| `chipscope_version` | Report the server, bundled package, Python, and output-directory versions; no hardware needed |

## Memory, NoC & system monitor

| Tool | What it does |
|------|--------------|
| `chipscope_memory` | Read device memory, write values (with explicit confirmation), or list memory targets |
| `chipscope_noc` | Discover NoC elements and check the state of a named element |
| `chipscope_noc_perfmon` | Configure NoC performance monitors and capture read/write bandwidth and latency samples |
| `chipscope_sysmon` | Read system-monitor temperature and voltage sensors |

## ILA & VIO

| Tool | What it does |
|------|--------------|
| `chipscope_ila_core` | Inspect, configure, and arm an ILA core's trigger and capture settings |
| `chipscope_ila_capture` | Wait for, upload, and export captured ILA samples as waveform data |
| `chipscope_vio` | Read VIO input probes and drive VIO output probes and ports |

## IBERT & serial links

| Tool | What it does |
|------|--------------|
| `chipscope_ibert` | Discover, configure, inspect, or reset IBERT serial links |
| `chipscope_ibert_eye_scan` | Run and export a 2D eye scan on an active GTY/GTYP link |
| `chipscope_ibert_yk_scan` | Run and export a YK scan on an active GTM link |

## DDR memory controller

| Tool | What it does |
|------|--------------|
| `chipscope_ddr` | Inspect DDR controller state, calibration results, and diagnostics |
| `chipscope_ddr_eye_scan` | Run a DDR 2D eye (margin) scan after calibration passes |

## PCIe

| Tool | What it does |
|------|--------------|
| `chipscope_pcie` | Read the PCIe link-debug core's LTSSM state trace and draw the state-transition graph |

## System debug utility (sys-dbg-util)

| Tool | What it does |
|------|--------------|
| `sysdbg_analyze` | Analyze a GT, NoC, CPM, DDRMC, or HBMMC subsystem for errors and rule violations |
| `sysdbg_noc` | Scan NoC registers for subsystem-level errors and rule violations, without `cs_server` |
| `sysdbg_noc_timeout` | Show, set, enable, or disable NoC timeout registers |
| `sysdbg_ddr` | Analyze the DDRMC subsystem for errors and configuration issues |
| `sysdbg_gt` | Check GTY/GTYP/GTM transceiver status — reset completion, errors, and PRBS |

## Visual scan output

`chipscope_ibert_eye_scan`, `chipscope_ibert_yk_scan`, and
`chipscope_ddr_eye_scan` render results inline by default. Control the output
with a `display` parameter:

| Value | What you get |
|-------|--------------|
| `both` | Inline PNG image in chat, plus structured app content |
| `chat` | Inline PNG image in chat only |
| `app` | Structured app content only (no PNG) |
| `text` | ASCII heatmap/waveform and metrics only — no PNG or app content |

When you don't set `display`, it auto-selects based on your client — `text`
for terminal-style CLIs (like Claude Code), `both` for GUI hosts (like VS
Code or Cursor).

To save a scan plot to disk, pass `save_plot` (eye scans) or `export_path`
(YK scans, which are transient and must be exported at scan time). Plot
files must use the `.png` extension. Saved paths must resolve inside your
configured output sandbox — see
[Output files](../getting-started/chipscope-mcp.md#output-files) in Getting Started.

## Workflow skills

Core ChipScope MCP tools are atomic primitives — a single call configures or
reads one thing. Separately installed **hardware-debug skills** build on top
of these tools for end-to-end tasks; your AI client should prefer the
matching skill over raw tool calls — skills own workflow selection,
ordering, environment setup, measurement, and cleanup.

When you connect without a `cs_server` URL and none is answering on the
`hw_server` host, ChipScope MCP starts a `cs_server` for the session and
stops it on disconnect. An already-running `cs_server` is reused and never
stopped.

| Skill | Description |
|-------|-------------|
| `hw-ila-debug` | Interact with ILA debug cores on live hardware — trigger, capture, and export waveform data |
| `hw-vio-debug` | Read input probes and drive output probes on live hardware via VIO |
| `hw-noc-debug` | Debug Versal NoC issues using `chipscope-mcp` and `sysdbg_noc` |
| `hw-noc-perfmon` | Measure Versal NoC bandwidth, latency, and QoS with the NoC performance monitors |
| `hw-ibert-gt-debug` | Run GT eye and YK scans, sweep TX/RX equalization, and assess link margin |
| `hw-ddrmc-debug` | Report DDRMC calibration status and per-byte-lane margins, and diagnose calibration failures |
| `hw-pcie-link-debug` | Debug CPM5 PCIe link bring-up and LTSSM link training on Versal Premium |

The system monitor skill (`hw-sysmon`) is not yet included in this release.

<p class="sphinxhide" align="center"><sub>Copyright © 2026 Advanced Micro Devices, Inc</sub></p>
<p class="sphinxhide" align="center"><sup><a href="https://www.amd.com/en/corporate/copyright">Terms and Conditions</a></sup></p>
