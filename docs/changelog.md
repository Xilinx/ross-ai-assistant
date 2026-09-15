<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Changelog

Release history for the AMD Embedded Agentic AI Suite, organized by monthly update.

---

## July 2026

_New Vivado agent skills and a major MCP server / extension release (v0.6.9)._

### Vivado — New Agent Skills

| Skill | Category | What it does |
|-------|----------|--------------|
| `timing-methodology-checks` | Timing | Runs 55+ UG906 timing methodology checks on synthesized designs and generates actionable RTL/XDC fixes. |
| `vivado-revision-control` | Revision Control | Detects project type, exports sources/settings, and generates a build script for reproducible project recreation. |
| `hw-ila-debug` | Hardware Debug | Drives ILA cores on live hardware — trigger, arm, capture, and export waveforms (CSV/VCD). |
| `hw-noc-debug` | Hardware Debug | Root-causes Versal NoC issues (AXI failures, decode errors, timeouts) via a connect → scan → correlate pipeline. |
| `hw-vio-debug` | Hardware Debug | Reads and drives VIO probes on live hardware, monitors activity, and resets outputs. |

### MCP Server & Extension — v0.6.9

**Added**

- **AI output limiting** — `vivado_execute` returns a trimmed digest (errors, critical warnings, log tail) so large logs don't flood the model; full output stays in the terminal and `vivado.log`. _On by default (`vivadoTerminal.limitAiOutput`)._
- **On-prem / air-gapped doc search** — point `vivado_doc_search` at your own RAG backend; its schema is discovered at startup, so new fields work with no extension update. _(`vivadoTerminal.docSearchUrl`.)_
- **Kiro (AWS IDE) support** — auto-detected from `~/.kiro/settings/mcp.json`; no config needed.

**Changed**

- **Unlimited concurrent sessions** — removed the previous cap of 7.
- **glibc-free Linux binary** — statically linked (`CGO_ENABLED=0`); runs on older RHEL / CentOS with no host glibc.
- **NFS-safe port discovery** — state files moved off NFS to a local directory. _(`VIVADO_STATE_DIR`.)_

**Fixed**

- **MCP auto-start** — corrected an invalid `chat.mcp.autostart` value so tools start reliably.
- **Security** — `ws` → `^8.21.0` (fixes GHSA-96hv-2xvq-fx4p DoS); Go → 1.25.11.

**Settings & environment variables added in v0.6.9**

VS Code settings live under **Settings → Extensions → Vivado AI Extension**.
Environment variables apply when running the server directly; when both are set, the VS Code setting wins.

| VS Code setting | Environment variable | Default | Purpose |
|-----------------|----------------------|---------|---------|
| `vivadoTerminal.limitAiOutput` | `VIVADO_LIMIT_AI_OUTPUT` | on | Master toggle for AI output caps |
| — | `VIVADO_MCP_MAX_EXECUTE_OUTPUT_BYTES` | `16000` | `vivado_execute` output byte cap (`0` = off) |
| `vivadoTerminal.docSearchUrl` | `VIVADO_DOC_SEARCH_URL` | `""` | Doc-search backend URL |
| — | `VIVADO_STATE_DIR` | auto (local dir) | Port/state file directory |

### ChipScope — v1.2.0

- ChipScope MCP Server v1.2.0 release.

---

## June 2026

_Vitis HLS joins the Suite — bringing agent skills for the HLS design flow._

### Vitis HLS — New Agent Skills

A three-stage pipeline of expert-authored skills for the HLS workflow:

| Skill | What it does |
|-------|--------------|
| `matlab-to-cpp` | Converts sample-based MATLAB algorithms to frame-based C++ with bit-exact verification. |
| `hls-architect` | Builds a producer–consumer dataflow design (`load_input → compute → store_output`). |
| `hls-optimize` | Iteratively tunes kernel pragmas and structure to hit latency, DSP, and throughput targets. |
| `hls-run-flow` | Runs HLS flow stages: C Simulation, C Synthesis, Co-Simulation, and Implementation. |

### Vitis HLS — Example Designs

- New Vitis HLS example workspaces (matmul, edge-detection, global tone mapping).

---

## May 2026

_Launch release — the first public Early Access build of the AMD Embedded Agentic AI Suite._

### Vivado — MCP Server & Extension v0.6.8

- First release of the **Vivado MCP server** and the VS Code / Cursor extension, giving AI clients natural-language access to Vivado and its documentation.

### Vivado — Example Designs

Self-contained Vivado example workspaces (with agent skills) across common workflows:

- **Design Analysis** — `rtl-lint`, `opt-design-analysis`, `multi-run-analysis`, `axi4-debug`
- **Design Capture** — `design-creation-prototype`, `revision-control-prototype`
- **Design Closure** — `timing-closure-prototype`, `post-route-dcp-analysis`
- **Hardware Debug** — `bd-ila-insertion`, `bd-vio-insertion`

### Platform & Suite

- Early Access Lounge documentation portal launched with getting-started guides, downloads, and MCP tool reference.

<p class="sphinxhide" align="center"><sub>Copyright © 2026 Advanced Micro Devices, Inc</sub></p>
<p class="sphinxhide" align="center"><sup><a href="https://www.amd.com/en/corporate/copyright">Terms and Conditions</a></sup></p>
