<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Install Agent Skills

Agent skills are `SKILL.md` files that encode methodology — pragma strategies, flow commands, analysis patterns — so your AI agent can follow them. Skills work with any skill-capable client: VS Code + GitHub Copilot, Cursor, Claude Code, Codex CLI, or GitHub Copilot CLI.

## Install with `npx skills add`

Point `npx skills add` at the extracted release package:

```bash
npx skills add /path/to/agentic-ai-suite
```

This installs skills into the current workspace (project-level). To install globally into your home directory (`~/.claude/skills/`) so skills are available across all workspaces, add `--global`:

```bash
npx skills add /path/to/agentic-ai-suite --global
```

You can also use these flags:

| Flag | Effect |
|------|--------|
| `--all` | Install everything |
| `--skill <name>` | Install one specific skill (e.g., `--skill hls-optimize`) |
| `--global` | Install to home directory instead of workspace |
| `--list` | List available skills without installing |

Flags can be combined — for example, install all skills globally:

```bash
npx skills add /path/to/agentic-ai-suite --all --global
```

**Without Node.js:** copy skill folders manually — `cp -r /path/to/agentic-ai-suite/skills/hls-optimize ~/.claude/skills/`.

## Where Skills Are Stored

By default, `npx skills add` installs into the **workspace** (project-level). With `--global`, it installs into the **home directory** (user-level). During early adoption, we recommend the workspace so you can experiment and iterate easily.

**Workspace** (default):

```
your-workspace/
├── .claude/
│   └── skills/
│       └── hls-optimize/
│           └── SKILL.md
└── your-design-files/
```

**Home directory** (`--global`, available across all workspaces):

```
~/.claude/
└── skills/
    └── hls-optimize/
        └── SKILL.md
```

> **Skills vs. MCP servers:** Skills are local text files your AI agent reads for methodology guidance. MCP servers (Vivado, ChipScope) are separate processes that give the agent live tool access. The following chapters cover MCP server setup.

---
