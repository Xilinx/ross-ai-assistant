<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# AMD Embedded Agentic AI Suite

Agent skills for AMD FPGA/SoC development.

**New here?** Start with the [Getting Started](docs/getting-started/) guide. See [docs](docs/) for FAQ, MCP reference, and support.

## Quick Start

### Prerequisites

| Component | Description | Download |
|-----------|-------------|----------|
| Vivado MCP Server (Linux) | Vivado Tcl command execution, project management, synthesis/implementation | v0.6.9 Linux amd64 |
| Vivado MCP Server (Windows) | Vivado Tcl command execution, project management, synthesis/implementation | v0.6.9 Windows amd64 |
| Vivado AI Extension (VS Code / Cursor) | VS Code extension for Vivado AI assistant integration | v0.6.9 VSIX |

Install [Node.js](https://nodejs.org/) to use the `npx skills` commands below.

### Install skills (interactive)

Point `npx skills add` at the extracted release package:

```bash
npx skills add /path/to/agentic-ai-suite
```

This installs skills into the current workspace. Add `--global` to install into your home directory (`~/.claude/skills/`) so they are available across all workspaces.

### Install all skills

```bash
npx skills add /path/to/agentic-ai-suite --all
```

### Install all skills globally

```bash
npx skills add /path/to/agentic-ai-suite --all --global
```

### Install a specific skill

```bash
npx skills add /path/to/agentic-ai-suite --skill hls-optimize
```

### List available skills

```bash
npx skills add /path/to/agentic-ai-suite --list
```

### Manual installation (no Node.js)

Copy desired skill folders to your agent's skills directory:

```bash
cp -r /path/to/agentic-ai-suite/skills/hls-optimize ~/.claude/skills/
```

## Skills

### Vivado

These skills require the [Vivado MCP Server](docs/reference/vivado-mcp-tools.md) for live Vivado interaction.

| Skill                                                         | Description                                                                    | Examples                                                             |
|---------------------------------------------------------------|--------------------------------------------------------------------------------|----------------------------------------------------------------------|
| [rtl-lint](skills/rtl-lint/)                                  | Run Vivado's RTL linter and report design issues with prioritized, code-level fixes | [multi-violation](examples/rtl-lint/multi-violation/)                |
| [timing-methodology-checks](skills/timing-methodology-checks/) | Run timing methodology checks and analyze violations                          | [multi-violation](examples/timing-methodology-checks/multi-violation/) |
| [vivado-revision-control](skills/vivado-revision-control/)    | Manage Vivado project files under revision control                             | [export-ipi-project](examples/vivado-revision-control/export-ipi-project/) |

### Vitis HLS

| Skill                                                  | Description                                                                    | Examples                                                                                                                                                           |
|--------------------------------------------------------|--------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [hls-matlab-to-cpp](skills/hls-matlab-to-cpp/)        | Convert MATLAB sample-based code to synthesizable C++ for HLS                  | [edge-detection](examples/hls-matlab-to-cpp/edge-detection/), [matlab-kernel-gemm](examples/hls-matlab-to-cpp/matlab-kernel-gemm/), [matlab-kernel-svd](examples/hls-matlab-to-cpp/matlab-kernel-svd/) |
| [hls-architect](skills/hls-architect/)                 | Convert input code to multi-stage HLS dataflow architecture                    | —                                                                                                                                                                  |
| [hls-optimize](skills/hls-optimize/)                   | Iteratively optimize HLS kernel against target performance/resource criteria   | [globaltonemapping](examples/hls-optimize/globaltonemapping/)                                                                                                      |

These three skills are the primary entry points for HLS development. Each skill automatically invokes specialized helper skills (dataflow checks, burst inference, array partitioning, report extraction, etc.) as needed — you don't need to install or invoke helpers separately.

**Build skill:** [hls-run-flow](skills/hls-run-flow/) runs the HLS compilation pipeline (csim, csynth, cosim, implementation) and is called by the core skills above or can be invoked directly. Example: [intro-matmul](examples/hls-run-flow/intro-matmul/).

### Hardware Debug

These skills debug live FPGA/SoC hardware via Vivado Hardware Manager Tcl or ChipScope MCP tools.

| Skill                                        | Description                                                                    | Examples                                                                                                                                                                                                  |
|----------------------------------------------|--------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [hw-ila-debug](skills/hw-ila-debug/)         | Interact with ILA debug cores on live hardware: trigger, capture, and export waveform data | [axi-protocol-capture](examples/hw-ila-debug/axi-protocol-capture/)                                                                                                                                     |
| [hw-vio-debug](skills/hw-vio-debug/)         | Read input probes and drive output probes on live hardware via VIO             | [axi-register-rw](examples/hw-vio-debug/axi-register-rw/)                                                                                                                                               |
| [hw-noc-debug](skills/hw-noc-debug/)         | Debug Versal NoC issues using chipscope-mcp and sysdbg_noc                     | [write-decode-error](examples/hw-noc-debug/write-decode-error/), [axsize-violation](examples/hw-noc-debug/axsize-violation/), [burst-4k-crossing](examples/hw-noc-debug/burst-4k-crossing/), [write-timeout](examples/hw-noc-debug/write-timeout/) |
