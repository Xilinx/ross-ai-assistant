<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Vivado MCP Server

The Vivado MCP Server connects your AI agent to a live Vivado session. The agent can start Vivado, run Tcl commands, read logs, search AMD documentation, and drive synthesis/implementation — all through the MCP protocol.

```
Your AI Client  ──MCP protocol──>  Vivado MCP Server  ──Tcl──>  Vivado
```

## Prerequisites

- **Vivado 2026.1** installed and on your `PATH`
- **One of the following** (not both):
    - **Vivado AI Assistant Extension** VSIX — for IDE setups (VS Code / Cursor). The extension bundles the MCP server.
    - **Vivado MCP Server** binary — for CLI setups (Claude Code, Copilot CLI, Codex CLI). Download from the EA release page.

> **Version Compatibility:** Vivado **2026.1** is the tested baseline. The MCP server communicates through Vivado's standard Tcl interface and is architecturally compatible with all released versions. Individual skills and examples note their minimum supported versions.

---

## VS Code & Cursor

The VSIX extension includes both the Vivado AI Assistant UI **and** the MCP server — no separate binary install or MCP configuration needed.

### Install the Vivado AI Assistant Extension

**From VSIX file:**

1. Download the Vivado AI Extension `.vsix` file from the EA release page
2. Open VS Code (or Cursor)
3. Go to **Extensions** sidebar > click `...` > **Install from VSIX...**
4. Select the downloaded `.vsix` file

**From command line:**

```bash
# VS Code
code --install-extension vivado-ai-extension-<version>.vsix

# Cursor
cursor --install-extension vivado-ai-extension-<version>.vsix
```

### Configure Vivado Path

After installing the extension, set the path to your Vivado executable:

1. Open **Settings** (Ctrl+,)
2. Search for **Vivado Path**
3. Enter the full path to your Vivado binary (e.g., `/tools/Xilinx/Vivado/2026.1/bin/vivado`)

> If Vivado is already on your system `PATH`, this step is optional — the extension will auto-detect it. However, explicitly setting the path avoids issues when multiple versions are installed.

### Start Vivado

You don't need to manually launch Vivado — the AI agent can start it for you. Simply ask in the chat panel:

> *"Start a Vivado session"*

The agent will use the `vivado_start` MCP tool to launch Vivado automatically.

**Alternatively, start Vivado manually and connect:**

```bash
vivado -mode tcl
```

Then, in the Vivado Tcl console, enable the web server:

```tcl
webserver -start -port 8088 -key none
```

Ask the agent to connect:

> *"Connect to my Vivado session at http://localhost:8088"*

> **Note:** HLS skills (matlab-to-cpp, hls-architect, hls-optimize) do not require a running Vivado session. They provide code generation and optimization guidance directly.

### Verify

=== "VS Code"

    1. Open the **Copilot Chat** panel (Ctrl+Alt+B)
    2. Switch to **Agent** mode (click the mode selector)
    3. Type: `List the available MCP tools`
    4. You should see Vivado-related tools like `vivado_execute`, `vivado_connect`, etc.

=== "Cursor"

    1. Open **Cursor Chat** (Ctrl+L) or **Composer** (Ctrl+I)
    2. Switch to **Agent** mode
    3. Ask: `What Vivado MCP tools are available?`
    4. The agent should list tools like `vivado_execute`, `vivado_connect`, etc.

> **Tip:** Cursor auto-discovers MCP servers from the extension — no restart needed.

### Troubleshooting

| Issue | Solution |
|-------|----------|
| Chat panel not visible | VS Code: Ctrl+Alt+B. Cursor: Ctrl+L |
| Vivado MCP tools missing from tools list | Click **Configure tools** (next to model selection) and ensure Vivado MCP tools are listed. If not, reload the window (Ctrl+Shift+P > `Developer: Reload Window`) |
| "Connection refused" or Vivado MCP disconnect | Reload the window and verify MCP tools are available. Check **Output > Vivado MCP** for errors |
| Extension not loading | Verify VS Code >= 1.114 or latest Cursor. Check **Output > Vivado MCP** for errors |
| Skills not loading | **Settings** > search **skills**. Ensure **Chat: Use Agent Skills** is checked and **Chat: Agent Skills Locations** includes `.claude/skills` (workspace) or `~/.claude/skills` (home). Verify the paths contain folders with `SKILL.md` files |

---

## Claude Code

### Install the MCP Server Binary

Download the standalone binary from the EA release page, make it executable, and move it to a convenient location:

```bash
chmod +x vivado-mcp-server-linux-amd64-<version>
mv vivado-mcp-server-linux-amd64-<version> ~/tools/vivado-mcp-server
```

### Register the Server

Register the Vivado MCP server at **user scope** so it's available in every workspace:

```bash
claude mcp add vivado-mcp --scope user --transport stdio \
  --env VIVADO_PATH=/path/to/Vivado/<version>/bin/vivado \
  -- /path/to/vivado-mcp-server \
  --stdio-bridge
```

This stores the config in `~/.claude.json`:

```json
{
  "mcpServers": {
    "vivado-mcp": {
      "command": "/path/to/vivado-mcp-server",
      "args": ["--stdio-bridge"],
      "type": "stdio",
      "env": {
        "VIVADO_PATH": "/path/to/Vivado/<version>/bin/vivado"
      }
    }
  }
}
```

> **VIVADO_PATH:** Set to the full path of your Vivado executable. If Vivado is already on your system `PATH`, you can omit this — the MCP server will auto-detect it.

**Scoping options:**
- `--scope user` — available in all projects (recommended for the MCP server)
- `--scope local` (default) — only in the current project
- `--scope project` — shared with your team via `.mcp.json` in source control

### Verify

```bash
cd your-design-workspace/
claude
# Then ask: "List the available MCP tools"
```

---

## GitHub Copilot CLI

### Install

```bash
curl -fsSL https://gh.io/copilot-install | bash

# Or via npm
npm install -g @github/copilot
```

### Configure MCP

From inside an interactive `copilot` session, use the `/mcp add` slash command and fill in the server details.

The config is stored in `~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "vivado-mcp": {
      "command": "/path/to/vivado-mcp-server",
      "args": ["--stdio-bridge"],
      "type": "stdio",
      "env": {
        "VIVADO_PATH": "/path/to/Vivado/<version>/bin/vivado"
      }
    }
  }
}
```

### Verify

```bash
cd your-design-workspace/
copilot
# Then ask: "List the available MCP tools"
```

---

## OpenAI Codex CLI

### Install

```bash
npm install -g @openai/codex
```

### Configure MCP

Register the Vivado MCP server globally:

```bash
codex mcp add vivado-mcp \
  --env VIVADO_PATH=/path/to/Vivado/<version>/bin/vivado \
  -- /path/to/vivado-mcp-server \
  --stdio-bridge
```

This stores the config in `~/.codex/config.toml`:

```toml
[mcp_servers.vivado-mcp]
command = "/path/to/vivado-mcp-server"
args = ["--stdio-bridge"]

[mcp_servers.vivado-mcp.env]
VIVADO_PATH = "/path/to/Vivado/<version>/bin/vivado"
```

> **Note:** Codex CLI uses TOML, not JSON. The CLI and IDE extension share this configuration.

### Verify

```bash
cd your-design-workspace/
codex
# Then ask: "List the available MCP tools"
```

---
