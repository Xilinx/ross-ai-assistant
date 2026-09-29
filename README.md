<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/amd-ross-lockup-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/amd-ross-lockup-light.png">
    <img alt="AMD Ross" src="docs/images/amd-ross-lockup-light.png" width="280">
  </picture>
</p>

# AMD Ross™ Agentic AI Assistant

![Agent Skills](https://img.shields.io/badge/Agent_Skills-Standard-7B2D8E)
[![GitHub Copilot](https://img.shields.io/badge/GitHub_Copilot-Compatible-007ACC)](https://github.com/features/copilot)
[![Cursor](https://img.shields.io/badge/Cursor-Compatible-000000)](https://cursor.com)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Compatible-F07535)](https://www.anthropic.com/claude-code)
[![OpenAI Codex](https://img.shields.io/badge/OpenAI_Codex-Compatible-412991)](https://openai.com/codex/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

Agent skills for AMD FPGA/SoC development.

**New here?** Start with the [Getting Started](docs/getting-started/) guide. See [docs](docs/) for FAQ, MCP reference, and support.

## Quick Start

We recommend cloning this repository locally. It includes not only skills, but also docs and examples. In the install commands below, replace `/path/to/ross-ai-assistant` with the path to your local clone.

### Prerequisites

Install **one** of the following from the [AMD Ross Agentic AI Assistant download page](https://www.amd.com/en/support/downloads/ross-agentic-ai.html):

| Component | Description | Follow-up installation |
|-----------|-------------|------------------------|
| Vivado AI Extension (VS Code / Cursor) | Includes the Vivado MCP server, the skills in this repository, and the `amd-doc-search` MCP configuration | None |
| Vivado MCP Server (Linux or Windows) | The Vivado MCP server: Tcl command execution, project management, and synthesis/implementation | Configure the [`amd-doc-search` MCP server](#install-the-amd-doc-search-mcp-server), then install the skills in this repo as a [plugin](#install-as-a-plugin) or [individually](#install-skills-interactive) |

Use the MCP server for CLI clients, or the VS Code / Cursor extension for IDE setups — not both.

### Install the amd-doc-search MCP server

The `amd-doc-search` MCP server searches AMD FPGA and Adaptive SoC documentation: user guides and device collaterals, and tool usage for Vivado, Vitis, embedded system software, and related products.

The Vivado AI Extension already includes this configuration. With the standalone Vivado MCP Server, add the server yourself.

For detailed installation instructions, see [https://ross.amd.com/chat#setup](https://ross.amd.com/chat#setup).

> **Local knowledge base.** If you want documentation search to stay on your own machine, use the [local knowledge base](docs/local-kb/README.md) instead of the hosted `amd-doc-search` service.

### Install as a plugin

This repository is also an agent plugin: your local clone can be installed as a plugin, which adds every skill in one step. Updating is then a `git pull` of the clone.

Once installed, invoke the skills by name, for example `/hls-optimize`, and they call each other by those same names. Some clients list them under the plugin's name, `amd-ross-agentic-ai-assistant`.

> **Choose one install method.** If you install the plugin, do not also install the skills with `npx skills add` or by copying them. Every skill would then be loaded twice.

The plugin contains skills only. The Vivado MCP Server or the Vivado AI Extension from [Prerequisites](#prerequisites) is still installed separately.

See [Install as a plugin](docs/getting-started/install-plugin.md) for the steps in your AI client and how to update.

### Install skills (interactive)

Install [Node.js](https://nodejs.org/) to use the `npx skills` commands below.

Point `npx skills add` at your local clone (or an extracted release package):

```bash
npx skills add /path/to/ross-ai-assistant
```

This installs skills into the current workspace. Add `--global` to install into your home directory (`~/.claude/skills/`) so they are available across all workspaces.

### Install all skills

```bash
npx skills add /path/to/ross-ai-assistant --all
```

### Install all skills globally

```bash
npx skills add /path/to/ross-ai-assistant --all --global
```

### Install a specific skill

```bash
npx skills add /path/to/ross-ai-assistant --skill hls-optimize
```

### List available skills

```bash
npx skills add /path/to/ross-ai-assistant --list
```

### Manual installation (no Node.js)

Copy desired skill folders to your agent's skills directory:

```bash
cp -r /path/to/ross-ai-assistant/skills/hls-optimize ~/.claude/skills/
```

## Skills

### Vivado

These skills require the [Vivado MCP Server](docs/reference/vivado-mcp-tools.md) for live Vivado interaction.

| Skill                                                         | Description                                                                    | Examples                                                             |
|---------------------------------------------------------------|--------------------------------------------------------------------------------|----------------------------------------------------------------------|
| [vivado-simulate-rtl](skills/vivado-simulate-rtl/)            | Run and diagnose RTL simulation across XSim and supported third-party simulators | -                                                                    |
| [vivado-rtl-lint](skills/vivado-rtl-lint/)                    | Run Vivado's RTL linter and report design issues with prioritized, code-level fixes | [multi-violation](examples/rtl-lint/multi-violation/)                |
| [vivado-timing-methodology-checks](skills/vivado-timing-methodology-checks/) | Run timing methodology checks and analyze violations            | [multi-violation](examples/timing-methodology-checks/multi-violation/) |
| [vivado-revision-control](skills/vivado-revision-control/)    | Manage Vivado project files under revision control                             | [export-ipi-project](examples/vivado-revision-control/export-ipi-project/) |
| [vivado-ip-configurator](skills/vivado-ip-configurator/) | Configure one Vivado IP cell from a natural-language description | — |

### Vitis HLS

| Skill                                                  | Description                                                                    | Examples                                                                                                                                                           |
|--------------------------------------------------------|--------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [hls-matlab-to-cpp](skills/hls-matlab-to-cpp/)        | Convert MATLAB sample-based code to synthesizable C++ for HLS                  | [edge-detection](examples/hls-matlab-to-cpp/edge-detection/), [matlab-kernel-gemm](examples/hls-matlab-to-cpp/matlab-kernel-gemm/), [matlab-kernel-svd](examples/hls-matlab-to-cpp/matlab-kernel-svd/) |
| [hls-architect](skills/hls-architect/)                 | Convert input code to multi-stage HLS dataflow architecture                    | —                                                                                                                                                                  |
| [hls-optimize](skills/hls-optimize/)                   | Iteratively optimize HLS kernel against target performance/resource criteria   | [globaltonemapping](examples/hls-optimize/globaltonemapping/)                                                                                                      |

These three skills are the primary entry points for HLS development. Each skill automatically invokes specialized helper skills (dataflow checks, burst inference, array partitioning, report extraction, etc.) as needed — you don't need to install or invoke helpers separately.

**Build skill:** [hls-run-flow](skills/hls-run-flow/) runs the HLS compilation pipeline (csim, csynth, cosim, implementation) and is called by the core skills above or can be invoked directly. Example: [intro-matmul](examples/hls-run-flow/intro-matmul/).

### Hardware Debug

These skills debug live FPGA/SoC hardware via Vivado Hardware Manager Tcl.

| Skill                                        | Description                                                                    | Examples                                                                                                                                                                                                  |
|----------------------------------------------|--------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [hw-ila-debug](skills/hw-ila-debug/)         | Interact with ILA debug cores on live hardware: trigger, capture, and export waveform data | [axi-protocol-capture](examples/hw-ila-debug/axi-protocol-capture/)                                                                                                                                     |
| [hw-vio-debug](skills/hw-vio-debug/)         | Read input probes and drive output probes on live hardware via VIO             | [axi-register-rw](examples/hw-vio-debug/axi-register-rw/)                                                                                                                                               |

### Vitis AI

These skills compile and optimize ONNX models for AMD NPU hardware with the VAIML compiler. Start with an orchestrator skill and let it load implementation skills and [worker subagents](agents/) as needed.

| Skill | Description | Examples |
|-------|-------------|----------|
| [vai-quantization-guide](skills/vai-quantization-guide/) | Mixed-precision quantization for AMD NPU (VINT8, BF16, hybrid) | [resnet50-quantize-vint8-bf16](examples/vai-quantization-guide/resnet50-quantize-vint8-bf16/), [vit-encoder-full-offload](examples/vai-quantization-guide/vit-encoder-full-offload/) |
| [vai-flag-configuration](skills/vai-flag-configuration/) | Iteratively tune `vitisai_config.json` compiler flags | — |
| [vai-custom-op](skills/vai-custom-op/) | Orchestrate parallel custom AIE operator development | [multi-input-multi-node-custom-op](examples/vai-custom-op/multi-input-multi-node-custom-op/) |

Helper skills (`vai-custom-op-implementation`, `vai-fe-args`, `vai-perf-analysis`, and the other `vai-*` folders) are invoked by those orchestrators and by the workers in `agents/`.
