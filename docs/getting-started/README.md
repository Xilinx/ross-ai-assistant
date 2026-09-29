<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Getting Started

Set up the AMD Ross Agentic AI Assistant in your preferred environment. Follow the guides below in order — install agent skills first, then configure the MCP servers you need.

## Prerequisites

**For Vivado AI Assistant (MCP Server):**

- **Vivado 2026.1** installed and on your `PATH`
- **One of the following** (not both):
    - **Vivado AI Assistant Extension** VSIX — for IDE setups (VS Code / Cursor). The extension bundles the MCP server.
    - **Vivado MCP Server** binary — for CLI setups (Claude Code, Copilot CLI, Codex). Install to a directory like `~/tools/`.
- A running Vivado session or a design checkpoint (DCP) to work with

**For Vitis HLS AI Assistant (Agent Skills):**

- **Vitis Unified IDE** installed and licensed — after sourcing `settings64.sh`, `v++ --version` and `vitis-run --version` should both work
- A skill-capable AI client: VS Code, Cursor, Claude Code, Codex CLI, or GitHub Copilot CLI
- *(Optional)* The Vivado MCP Server, for AMD-doc-grounded answers via `vivado_doc_search`

> **Version Compatibility:** Vivado **2026.1** and Vitis **2026.1** are the tested versions. The MCP server communicates with Vivado through its standard Tcl interface, so it is architecturally compatible with all released versions of Vivado. Individual skills and examples note their own minimum supported versions where they differ (see each skill's `SKILL.md` and each example's `README.md`).

## Setup Guides

> Cursor, Claude Code and Codex users can install step 1's skills as a plugin instead — see [Install as a plugin](../../README.md#install-as-a-plugin). The plugin contains skills only; the MCP servers in the remaining steps are still installed separately.

| Step | Guide | What it covers |
|------|-------|----------------|
| 1 | [Install Agent Skills](../../README.md#quick-start) | `npx skills add` — install HLS and Vivado methodology skills |
| 2 | [Vivado MCP Server](vivado-mcp.md) | VSIX extension (VS Code / Cursor) or standalone binary (CLI tools) |
| 3 | [HLS Quick Start](hls-quickstart.md) | Vitis HLS skills — no MCP server needed |
| 4 | [Pre-Flight Checklist](preflight-checklist.md) | Verify everything works end-to-end |

## Architecture Overview

**Vivado AI Assistant** uses the MCP protocol to give your AI agent live tool access:

```
Your AI Client  ──MCP protocol──>  MCP Server  ──Tcl──>  Vivado
```

1. **Your client** (IDE or CLI) connects to the MCP server via stdio
2. **The MCP server** translates AI agent requests into tool commands
3. **Vivado** executes the commands and returns results through the same chain

**Vitis HLS AI Assistant** uses agent skills — no MCP server required:

```
Your AI Client  ──reads──>  Agent Skills (SKILL.md)  ──runs──>  v++ / vitis-run
```

1. **Your client** (IDE or CLI) loads skill files from your workspace or `~/.claude/skills/`
2. **The skills** encode HLS methodology — pragma strategies, flow commands, analysis patterns
3. **Vitis tools** (`v++`, `vitis-run`) are invoked directly by the agent in your terminal

## After Setup

Once configured, try the [HLS Quick Start](hls-quickstart.md) or the example designs in the `examples/` directory.

---
