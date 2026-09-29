<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Frequently Asked Questions

## General

**What is the Vivado MCP Server?**

The MCP (Model Context Protocol) Server is a bridge that connects AI agents to a running Vivado session. It translates agent requests into Vivado Tcl commands and returns structured results, enabling AI-assisted FPGA design workflows.

**Which Vivado versions are supported?**

Vivado 2020.2 or later. 2026.1 is the version the flow is tested against, and the MCP server communicates through Vivado's standard Tcl interface, so it is architecturally compatible with released versions either side of that. Individual skills and examples note their own minimum supported versions where they differ.

**Does the AI modify my design files directly?**

The AI agent executes Vivado Tcl commands through the MCP server. It can modify your project within the Vivado session (adding IP, changing constraints, etc.), but it does not directly edit HDL source files unless you explicitly ask it to through your IDE's file editing capabilities.

**Do HLS skills need the MCP server?**

No. HLS skills (matlab-to-cpp, hls-architect, hls-optimize) work directly with your source code and HLS project files. They provide code generation, architecture guidance, and optimization recommendations without requiring a live Vivado/HLS session for basic usage. The `hls-run-flow` build skill invokes Vitis HLS for compilation via standard command-line tools (`v++`, `vitis-run`), not MCP.

**Does this workflow with AMD Ross™ AI assistant require an additional license?**

No additional license is required. You only need the applicable licenses for the AMD tools you use (for example Vivado or Vitis HLS).

**Which coding agents and models can I use with AMD skills?**

Use a current coding agent (Cursor, Claude Code, Copilot, or similar) that can load Agent Skills and call tools (including AMD MCP when the skill needs a live Vivado session). Use a current, large-context coding model your company already allows — not a small or chat-only model.

Current GPT-5 Sol–class and Claude Opus 5–class coding models provide the capabilities these skills require.

## Setup & Configuration

**Can I use the MCP server without the VS Code extension?**

Yes. The MCP server works with any MCP-compatible client: Cursor, Claude Code, GitHub Copilot CLI, Codex CLI, and others. The VS Code extension adds convenience features but isn't required.

**Do I need to start Vivado myself first?**

No. The `vivado_start` tool launches Vivado for you, in Tcl mode by default — just ask the agent to start a session. `vivado_connect` is for the other direction: attaching to a Vivado instance that is already running with its Tcl webserver.

**Can I run Vivado on a different machine?**

Yes. Ask the agent to start the session remotely: `vivado_ssh` runs it on a named host, and `vivado_lsf` submits it to an LSF cluster node. Both are Linux only, and both need the working directory to be on a filesystem the remote machine reaches at the same path. `vivado_lsf` with `action='check'` reports what is missing from an LSF setup.

**I see "Connection refused" — what's wrong?**

Usually the session is gone, or was never started. Ask the agent to list sessions (`vivado_list_sessions`) and start one if the list is empty. If a session is listed but unreachable, `vivado_cleanup` with `operation='health'` reports its state and `operation='cleanup_stale'` clears dead entries.

**How do I update to a new version?**

If you installed the extension from the Marketplace, it updates itself. For a manual install, download the latest VSIX and reinstall it; a standalone MCP server binary is replaced in place.

**How do I share the Vivado MCP server among multiple users in my organization?**

Place the MCP server binary in a shared location accessible by all users (e.g., a network drive or shared filesystem). Each user then configures their own `mcp.json` file — as documented in the [Getting Started](getting-started/) guides for their IDE or CLI — pointing the `"command"` field to the shared binary path.

No per-user installation is needed; only the `mcp.json` configuration differs per user.

> **Tip:** For questions, tips, and product updates, use [AMD Adaptive Support](https://adaptivesupport.amd.com/s/?language=en_US).

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

**What does a skill stage of Beta mean?**

Beta indicates that the skill has undergone preliminary validation and testing but is still maturing. Functionality, APIs, and implementation details may evolve in future releases.

## Troubleshooting

**The agent isn't using the skill I expected**

Make sure the SKILL.md is in the correct location (`.claude/skills/skill-name/SKILL.md`) and that you reference the skill by name in your prompt. You can also ask the agent: "What skills are available?"

**Vivado commands are failing through the agent**

Check the Vivado Tcl console for error messages. Common causes: design not open, wrong design state (e.g., trying to route before placing), or missing source files.

**The agent seems slow**

Complex Vivado operations (synthesis, implementation) take time regardless of the AI layer. The MCP server streams results, but you'll still wait for Vivado to finish. For faster iteration, use targeted operations like `synth_design -lint` instead of full synthesis.

## Security, Privacy, and the Knowledge Base

Ross AI assistant combines three things that are often confused: **your AI client and model** (Copilot, Claude, Cursor, etc.), **AMD MCP servers and skills** (local tool access), and **documentation search** (hosted or local knowledge base). Privacy depends on which layer you are asking about.

### Privacy: what stays on your machine

**Does Vivado MCP upload my RTL, project, or Tcl to AMD?**

No. The Vivado MCP Server runs on a machine you control. It talks to Vivado locally and returns results to your AI client. Your design files, netlists, constraints, bitstreams, and the text of Tcl commands issued through `vivado_execute` are **not** uploaded to AMD as part of normal MCP operation.

**Does the Vivado MCP Server collect telemetry?**

No. The Vivado MCP Server does not send telemetry or usage data back to AMD. The Vivado MCP server communicates only with the local Vivado installation and does not transmit design information, prompts, or user interactions to AMD.

**Does the Documentation Search MCP collect telemetry?**

No. It does not send telemetry from your machine. The hosted documentation service keeps server-side usage statistics only: which documentation resources were accessed, and aggregate usage counts. Those statistics do not include user prompts, search queries, design content, or personally identifiable design information.

**Are user prompts or design data collected?**

No. User prompts, search queries, RTL code, design content, and other customer intellectual property are not included in those server-side usage statistics.

**Does AMD train AI models on my prompts or designs?**

AMD does not provide the foundation LLM and does **not** use your prompts, inputs, or outputs to train AMD models. Ross AI assistant supplies skills, MCP tools, and optional documentation search — it plugs into **your** LLM configuration. What your model provider retains or trains on is governed by **that provider's** enterprise agreement and policies, not by the Ross AI assistant license.

### Privacy: cloud knowledge base (`vivado_doc_search`)

**What is the hosted knowledge base?**

`vivado_doc_search` comes from a **separate MCP server**, `amd-doc-search`, rather than from the Vivado MCP server. When your agent calls it, that server queries AMD's **hosted documentation index** over the network. The index contains **published AMD documentation** (user guides, programming guides, Answer Records, and related web content) — not your project files.

The extension registers both servers, so the tool is there automatically. An MCP-only setup adds `amd-doc-search` itself, and the tool is simply absent until it does — which is also the simplest way to make sure no documentation query leaves your machine.

**Does AMD store my documentation search questions?**

No. AMD does **not** store the text of your `vivado_doc_search` queries on the cloud knowledge base service.

**Which MCP tools reach AMD's network?**

Only `vivado_doc_search`, and it belongs to the `amd-doc-search` server rather than the Vivado one. Every tool the Vivado MCP server provides communicates between your local Vivado session and your MCP client only.

### Privacy: air-gapped deployments

**Can we avoid sending documentation searches to AMD's cloud?**

Yes. Deploy the [AMD Embedded Local Knowledge Base](local-kb/README.md) (offline RAG Docker) on your network and configure the MCP server to use that local endpoint for `vivado_doc_search`. Documentation retrieval then stays on your infrastructure.

**Is local knowledge base + a cloud LLM fully air-gapped?**

No. If you use GitHub Copilot or another cloud model, your question and the documentation passages the model reads can still go to that provider. A **fully** air-gapped workflow requires both local documentation search **and** a local tool-calling LLM. See [Using the RAG database with a frontier model](local-kb/frontier-model.md) and [Local LLM deployment](local-kb/local-llm.md).

**Our IT policy blocks cloud AI or proprietary data leaving the site — what should we use?**

| Concern | Typical approach |
|--------|------------------|
| Doc search must not call AMD cloud | [Local knowledge base](local-kb/README.md) |
| LLM must not leave the site | Local LLM with tool-calling support |
| Vivado automation | Vivado MCP (local); restrict which MCP tools are enabled if needed |

### Privacy: AMD NDA documentation

**Does the hosted knowledge base include AMD NDA or EA-restricted documents?**

No. The hosted index is **generally published** AMD documentation only. AMD content that is under NDA, Early Access, or another restricted program is **not** in that public index.

If your program entitles you to restricted AMD documents, keep those files under your own access controls (for example your EA download workspace). Do not assume `vivado_doc_search` against AMD's cloud can retrieve them.

### Privacy: your design data and export control

**Does AMD's knowledge base hold my company's RTL or other customer-owned files?**

No. Your project files, RTL, constraints, and similar artifacts are **your** content. AMD does not ingest them into the hosted or local AMD documentation knowledge bases. You remain the data owner and decide whether any of that material is placed in a prompt, a workspace the agent can read, or a cloud LLM.

**Who is responsible for export-controlled (ITAR/EAR) design data?**

You are. Ross AI assistant does not change your obligation to handle controlled goods correctly. Do not submit export-controlled design content to cloud LLM services unless your compliance team has approved that path.

### Still have questions?

For product support, see [Support](support.md) and [AMD Adaptive Support](https://adaptivesupport.amd.com/s/?language=en_US).

---
