<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Frequently Asked Questions

## General

**What is the Vivado MCP Server?**

The MCP (Model Context Protocol) Server is a bridge that connects AI agents to a running Vivado session. It translates agent requests into Vivado Tcl commands and returns structured results, enabling AI-assisted FPGA design workflows.

**Which Vivado versions are supported?**

Vivado 2026.1 is the tested version. The MCP server is architecturally compatible with all released versions of Vivado, as it communicates through Vivado's standard Tcl interface. Individual skills and examples note their own minimum supported versions where they differ.

**Does the AI modify my design files directly?**

The AI agent executes Vivado Tcl commands through the MCP server. It can modify your project within the Vivado session (adding IP, changing constraints, etc.), but it does not directly edit HDL source files unless you explicitly ask it to through your IDE's file editing capabilities.

**Do HLS skills need the MCP server?**

No. HLS skills (matlab-to-cpp, hls-architect, hls-optimize) work directly with your source code and HLS project files. They provide code generation, architecture guidance, and optimization recommendations without requiring a live Vivado/HLS session for basic usage. The `hls-run-flow` build skill invokes Vitis HLS for compilation via standard command-line tools (`v++`, `vitis-run`), not MCP.

## Setup & Configuration

**Can I use the MCP server without the VS Code extension?**

Yes. The MCP server works with any MCP-compatible client: Cursor, Claude Code, GitHub Copilot CLI, Codex CLI, and others. The VS Code extension adds convenience features but isn't required.

**Can I connect to a remote Vivado session?**

The MCP server connects to a Vivado instance running on the same machine by default. For remote access, you can use SSH tunneling or the SSE transport mode.

**I see "Connection refused" — what's wrong?**

Ensure Vivado is running in Tcl mode (`vivado -mode tcl`) before starting the MCP server. The server needs an active Vivado session to connect to.

**How do I update to a new version?**

Download the latest binaries, replace the old MCP server binary, and reinstall the VSIX extension if applicable.

**How do I share the Vivado MCP server among multiple users in my organization?**

Place the MCP server binary in a shared location accessible by all users (e.g., a network drive or shared filesystem). Each user then configures their own `mcp.json` file — as documented in the [Getting Started](getting-started/) guides for their IDE or CLI — pointing the `"command"` field to the shared binary path.

No per-user installation is needed; only the `mcp.json` configuration differs per user.

> **Tip:** We strongly recommend that every user in your organization requests access to the Early Access site individually. This ensures they are automatically added to the [private EA forum](https://adaptivesupport.amd.com/s/group/0F9Pd00000091UjKAI/vivado-ai-assistant), where they can ask questions, share tips, and stay informed about new releases.

## Skills

**What is a SKILL.md file?**

A SKILL.md is a structured instruction file that teaches an AI agent how to perform a specific FPGA design task. It contains step-by-step workflows, interpretation guides, and fix recommendations. Agents read these files to gain domain expertise.

**Where do I put skill files?**

Place them under `.claude/skills/` in your workspace. The AI agent automatically discovers and reads them from that location.

```
your-workspace/
└── .claude/
    └── skills/
        └── your-skill/
            └── SKILL.md
```

**Can I write my own skills?**

Yes. Skills follow the open [Agent Skills](https://agentskills.io) standard: each is a `SKILL.md` Markdown file with YAML frontmatter (at minimum a `name` and `description`) followed by the instructions. See the standard for the full format, and browse the `skills/` folder in this package for working examples to model yours on.

**What's the difference between user-facing and helper skills?**

User-facing skills (matlab-to-cpp, hls-architect, hls-optimize) are what you invoke directly by asking the agent. Helper skills (dataflow checks, burst inference, array partitioning, report extraction, etc.) are automatically invoked by core skills as needed — you don't need to know about them or install them separately.

## Troubleshooting

**The agent isn't using the skill I expected**

Make sure the SKILL.md is in the correct location (`.claude/skills/skill-name/SKILL.md`) and that you reference the skill by name in your prompt. You can also ask the agent: "What skills are available?"

**Vivado commands are failing through the agent**

Check the Vivado Tcl console for error messages. Common causes: design not open, wrong design state (e.g., trying to route before placing), or missing source files.

**The agent seems slow**

Complex Vivado operations (synthesis, implementation) take time regardless of the AI layer. The MCP server streams results, but you'll still wait for Vivado to finish. For faster iteration, use targeted operations like `synth_design -lint` instead of full synthesis.

---
