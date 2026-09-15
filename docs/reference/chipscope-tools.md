<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Tool Reference

ChipScope MCP exposes **19 tools** across hardware-debug domains (15 core
tools, plus 4 optional `sysdbg_*` tools when the separate `sys-dbg-util`
plugin is installed). All tools run locally over stdio and return structured
results your AI client can act on. You rarely call these directly — the AI
client selects the right tool for your request — but the catalog below shows
what ChipScope can drive.

## Session & device

| Tool | Description |
|------|-------------|
| `chipscope_session` | Server connection management and diagnostics |
| `chipscope_device` | Device management operations |
| `chipscope_scan` | Discover debug cores on the device |
| `chipscope_version` | Report server and tool version |

## Memory, NoC & system monitor

| Tool | Description |
|------|-------------|
| `chipscope_memory` | Memory access operations (read/write) |
| `chipscope_noc` | NoC element discovery and status operations |
| `chipscope_sysmon` | System monitor readings — temperature, voltages, alarms |

## ILA & VIO

| Tool | Description |
|------|-------------|
| `chipscope_ila_core` | ILA debug core control — configure and arm the trigger |
| `chipscope_ila_capture` | ILA data retrieval — wait, upload, and export waveforms |
| `chipscope_vio` | VIO (Virtual I/O) probe read/drive |

## IBERT & serial links

| Tool | Description |
|------|-------------|
| `chipscope_ibert` | IBERT (Integrated Bit Error Ratio Tester) serial link management |
| `chipscope_ibert_eye_scan` | Eye scan on a GTY/GTYP link, inline PNG output by default |
| `chipscope_ibert_yk_scan` | YK scan on a GTM link (56+ Gbps), inline PNG output by default |

## DDR memory controller

| Tool | Description |
|------|-------------|
| `chipscope_ddr` | DDR memory controller debug |
| `chipscope_ddr_eye_scan` | DDR margin (eye) scan |

## System debug utility (sys-dbg-util)

| Tool | Description |
|------|-------------|
| `sysdbg_noc` | NoC subsystem analysis via direct device memory reads plus Xrdb register metadata |
| `sysdbg_noc_timeout` | NoC timeout inspection and live timeout control |
| `sysdbg_ddr` | DDRMC subsystem analysis _(preview)_ |
| `sysdbg_gt` | GT subsystem analysis _(preview)_ |

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
ordering, environment setup, measurement, and cleanup. ChipScope MCP itself
never starts or stops `cs_server`.

| Skill | Description |
|-------|-------------|
| `hw-ila-debug` | Interact with ILA debug cores on live hardware — trigger, capture, and export waveform data |
| `hw-vio-debug` | Read input probes and drive output probes on live hardware via VIO |
| `hw-noc-debug` | Debug Versal NoC issues using `chipscope-mcp` and `sysdbg_noc` |

Additional hardware-debug skills (IBERT, DDR margin, NoC performance
monitoring, PCIe link debug, system monitor) are in progress and not yet
available in this EA release.

<p class="sphinxhide" align="center"><sub>Copyright © 2026 Advanced Micro Devices, Inc</sub></p>
<p class="sphinxhide" align="center"><sup><a href="https://www.amd.com/en/corporate/copyright">Terms and Conditions</a></sup></p>
