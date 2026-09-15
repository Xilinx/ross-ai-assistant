<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Pre-Flight Checklist

Verify your setup is ready before using the AMD Embedded Agentic AI Suite. This checklist works on **Linux** and **Windows**.

---

## 1. Vivado Installation

| Check | How to Verify |
|-------|--------------|
| Vivado is installed | Open a terminal and run: `vivado -version` |
| Version is 2026.1 (recommended) | Output should show `Vivado v2026.1` — other versions are architecturally compatible but 2026.1 is tested |
| Vivado is on PATH | If `vivado -version` fails, source the settings script first: `source /tools/Xilinx/Vivado/2026.1/settings64.sh` (Linux) or run from the Vivado command prompt (Windows) |

---

## 2. MCP Server or Extension

!!! warning "Choose One — Not Both"
    Install the **Extension** (VS Code / Cursor) **or** the **MCP Server Binary** (CLI tools). Not both.

### Option A: Extension (VS Code / Cursor)

| Check | How to Verify |
|-------|--------------|
| VSIX installed | In VS Code: **Extensions** sidebar → search "Vivado" — it should appear in the list |
| Extension version matches | Extension version should be **v{{ mcp_version }}** |
| Vivado path configured | **Settings** → search "Vivado Path" → should point to your Vivado binary |

### Option B: MCP Server Binary (CLI tools)

| Check | How to Verify |
|-------|--------------|
| Binary downloaded | Check the file exists: `ls ~/bin/vivado-mcp-server-*` (Linux) or check your chosen install directory (Windows) |
| Binary is executable | Linux: `chmod +x ~/bin/vivado-mcp-server-*` — Windows: no action needed |
| Binary responds | Run: `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | ./vivado-mcp-server-linux-amd64-{{ mcp_version }}` — should return a JSON response |

---

## 3. ChipScope MCP Server (Optional)

If you plan to use ChipScope for Versal hardware debug, verify the binary is working.

| Check | How to Verify |
|-------|--------------|
| Binary extracted | Check the file exists: `ls ~/chipscope-mcp/chipscope-mcp-onedir-lin64/chipscope-mcp` (Linux) or check your chosen install directory (Windows) |
| Binary responds | Run: `./chipscope-mcp version` — should print the version number |
| Preflight passes | Run: `./chipscope-mcp doctor --dry-run --dir /path/to/workspace` — exit code 0 means ready |
| Client configured | For VS Code/Cursor: check `.vscode/mcp.json` or `.cursor/mcp.json` contains a `chipscope` entry. For Claude Code: run `claude mcp list` and look for `chipscope` |

---

## 4. MCP Client Configuration

Your AI client needs a config file that tells it where to find the MCP server. Check the location for your tool:

=== "VS Code"

    **File:** `.vscode/mcp.json` (workspace) or `~/.config/Code/User/mcp.json` (global)

    Verify it contains a `vivado-mcp-server` entry:
    ```json
    {
      "servers": {
        "vivado": {
          "command": "/path/to/vivado-mcp-server-linux-amd64-{{ mcp_version }}"
        }
      }
    }
    ```

    !!! tip
        If you installed the **VSIX extension**, this config is created automatically — no manual setup needed.

=== "Cursor"

    **File:** `.cursor/mcp.json` (workspace) or `~/.cursor/mcp.json` (global)

    Same JSON format as VS Code above.

=== "Claude Desktop"

    **File:**

    - Linux: `~/.config/Claude/claude_desktop_config.json`
    - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

=== "Claude Code"

    **File:** `.mcp.json` (project) or `~/.claude.json` (user)

    Verify with: `cat .mcp.json | grep vivado`

=== "Windsurf"

    **File:** `~/.codeium/windsurf/mcp_config.json`

=== "GitHub Copilot CLI"

    MCP servers are passed at invocation:
    ```bash
    gh copilot --mcp-server vivado=/path/to/vivado-mcp-server-linux-amd64-{{ mcp_version }}
    ```

=== "Codex CLI"

    **File:** `~/.codex/config.toml` or `.codex/config.toml` (project)

    ```toml
    [mcp.vivado]
    command = "/path/to/vivado-mcp-server-linux-amd64-{{ mcp_version }}"
    ```

---

## 5. AI Client Installed

| Client | How to Check |
|--------|-------------|
| **VS Code** | `code --version` |
| **Cursor** | `cursor --version` |
| **Claude Code** | `claude --version` |
| **GitHub Copilot CLI** | `gh copilot --version` |
| **Codex CLI** | `codex --version` |
| **Windsurf** | `windsurf --version` |
| **Claude Desktop** | Open the app — check the MCP icon in the chat input area |

---

## 6. Knowledge Base

The Knowledge Base provides RAG-powered documentation search via `vivado_doc_search`. It is hosted by **AMD** and publicly accessible — no VPN or local setup required. The MCP server connects to it automatically.

| Check | How to Verify |
|-------|--------------|
| `vivado_doc_search` responds | In your AI chat, ask: *"Search AMD docs for async clock domain crossings"* — you should get cited results from UG903, UG949, etc. |

---

## Quick Smoke Test

After verifying all the above, run this in your AI chat to confirm end-to-end:

> *"What version of Vivado do I have?"*

If the agent calls `vivado_execute` and returns your Vivado version, everything is working.

<p class="sphinxhide" align="center"><sub>Copyright © 2026 Advanced Micro Devices, Inc</sub></p>
<p class="sphinxhide" align="center"><sup><a href="https://www.amd.com/en/corporate/copyright">Terms and Conditions</a></sup></p>
