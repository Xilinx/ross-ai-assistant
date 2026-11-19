<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# ChipScope MCP Server

Debug AMD Versal devices straight from your AI environment. ChipScope MCP brings agentic AI to hardware debug — drive ILA captures, IBERT eye scans, DDR margin checks, NoC performance analysis, and more just by asking your AI client.

| Capability | What you can ask for |
|------------|----------------------|
| **ILA capture** | Configure triggers, arm, capture, and upload waveforms |
| **IBERT / GT links** | Eye scans and YK scans with inline plots, link tuning |
| **DDR memory** | Calibration status and margin (eye) scans |
| **NoC** | Element discovery, status, and performance/latency analysis |
| **System monitor** | Temperature, voltage, and alarm readings |
| **Device & memory** | Scan for debug cores, read/write memory, device management |

## Prerequisites

- One supported AI client: **VS Code + GitHub Copilot Chat**, **Cursor**, or **Claude Code**
- `hw_server` running — only when you plan to debug live hardware. Local servers commonly use `TCP:localhost:3121` for `hw_server` and `TCP:localhost:3042` for `cs_server`. Use `TCP:host:port` or `host:port` — not `http://host:port`. If no `cs_server` is running, ChipScope MCP starts one for the session.
- A `cs_server` executable the server can launch: either Vivado's `bin` directory on `PATH`, or `CHIPSCOPE_MCP_CS_SERVER` set to the full path of `cs_server` (see [Settings](#settings)).

## Download and Extract

Download the release archive for your host from the Downloads page and extract it to a permanent folder. Your AI client launches the binary directly from this folder — don't leave it in a temporary download location.

| Package name | Platform |
|---|---|
| `chipscope-mcp-onefile-win-<version>.zip` | Windows |
| `chipscope-mcp-onefile-lin64-<version>.tgz` | Linux |

Replace `<version>` in the commands below with the release you downloaded.

Compare the archive's SHA-256 with the checksum published next to the download before extracting:

=== "Windows"

    ```powershell
    Get-FileHash .\chipscope-mcp-onefile-win-<version>.zip -Algorithm SHA256
    ```

=== "Linux"

    ```bash
    sha256sum chipscope-mcp-onefile-lin64-<version>.tgz
    ```

Extract:

=== "Windows"

    ```powershell
    mkdir C:\chipscope-mcp
    Expand-Archive .\chipscope-mcp-onefile-win-<version>.zip -DestinationPath C:\chipscope-mcp
    cd C:\chipscope-mcp\chipscope-mcp-onefile-win
    ```

=== "Linux"

    ```bash
    mkdir -p ~/chipscope-mcp
    tar xzf chipscope-mcp-onefile-lin64-<version>.tgz -C ~/chipscope-mcp
    cd ~/chipscope-mcp/chipscope-mcp-onefile-lin64
    ```

Each archive holds a single self-contained program plus its release notes:

| File | Purpose |
|---|---|
| `chipscope-mcp.exe` (Windows) / `chipscope-mcp` (Linux) | The MCP server and its `doctor` and `version` utilities |
| `build_manifest.json` | Build version, commit, and bundled component versions |
| `third_party_notices-*.txt` | Third-party license notices |
| `xilinx-mcp-server-extension-license.pdf` | License agreement |

On first run the program unpacks its bundled plugins into a per-user cache (`~/.local/share/chipscope-mcp/plugins` on Linux), so the first start takes a little longer than later ones.

## Install Preflight

Run the built-in `doctor` from the extracted folder against the workspace you will open in your AI client. `doctor --dry-run` is read-only and writes nothing — **exit code 0** means the install would succeed, **exit code 1** means it found a blocking problem (read the `[FAIL]` line and its `Fix:` hint).

=== "Windows"

    ```powershell
    chipscope-mcp.exe version
    chipscope-mcp.exe doctor --dry-run --dir C:\path\to\workspace
    ```

=== "Linux"

    ```bash
    ./chipscope-mcp version
    ./chipscope-mcp doctor --dry-run --dir /path/to/workspace
    ```

`version` prints the release, build number, and the versions of the bundled components (`chipscopy`, `amd_xrdb`, `sys_dbg_util`). Quote this output when reporting an issue.

The MCP server entry name is `chipscope`.

---

## VS Code & Cursor

`doctor --fix` generates the client config for you.

### VS Code

Run `doctor --fix` against the workspace you will open in VS Code:

=== "Windows"

    ```powershell
    chipscope-mcp.exe doctor --fix --dir C:\path\to\workspace
    ```

=== "Linux"

    ```bash
    ./chipscope-mcp doctor --fix --dir /path/to/workspace
    ```

This writes `.vscode/mcp.json` in that workspace. `CHIPSCOPE_MCP_CS_SERVER` is filled in only when `doctor` finds `cs_server`:

=== "Windows"

    ```json
    {
      "servers": {
        "chipscope": {
          "command": "C:\\chipscope-mcp\\chipscope-mcp-onefile-win\\chipscope-mcp.exe",
          "args": ["start"],
          "env": {
            "CHIPSCOPE_MCP_OUTPUT_DIR": "C:\\path\\to\\workspace\\chipscope-mcp-out",
            "CHIPSCOPE_MCP_CS_SERVER": "C:\\path\\to\\Vivado\\bin\\cs_server"
          }
        }
      }
    }
    ```

=== "Linux"

    ```json
    {
      "servers": {
        "chipscope": {
          "command": "/home/you/chipscope-mcp/chipscope-mcp-onefile-lin64/chipscope-mcp",
          "args": ["start"],
          "env": {
            "CHIPSCOPE_MCP_OUTPUT_DIR": "/path/to/workspace/chipscope-mcp-out",
            "CHIPSCOPE_MCP_CS_SERVER": "/path/to/Vivado/bin/cs_server"
          }
        }
      }
    }
    ```

`doctor --fix` also writes `.vscode/settings.json` with `{ "chat.mcp.apps.enabled": true }`, safely merges any existing MCP config, and removes the legacy entry that used the old server name.

Open the workspace in VS Code, open `.vscode/mcp.json`, and click **Start** next to `chipscope`. Then open Copilot Chat, switch to **Agent** mode, and ask a hardware-debug question.

### Cursor

Run `doctor --fix` against the workspace you will open in Cursor:

=== "Windows"

    ```powershell
    chipscope-mcp.exe doctor --fix --dir C:\path\to\workspace
    ```

=== "Linux"

    ```bash
    ./chipscope-mcp doctor --fix --dir /path/to/workspace
    ```

This writes `.cursor/mcp.json`:

=== "Windows"

    ```json
    {
      "mcpServers": {
        "chipscope": {
          "command": "C:\\chipscope-mcp\\chipscope-mcp-onefile-win\\chipscope-mcp.exe",
          "args": ["start"],
          "env": {
            "CHIPSCOPE_MCP_OUTPUT_DIR": "C:\\path\\to\\workspace\\chipscope-mcp-out",
            "CHIPSCOPE_MCP_CS_SERVER": "C:\\path\\to\\Vivado\\bin\\cs_server"
          }
        }
      }
    }
    ```

=== "Linux"

    ```json
    {
      "mcpServers": {
        "chipscope": {
          "command": "/home/you/chipscope-mcp/chipscope-mcp-onefile-lin64/chipscope-mcp",
          "args": ["start"],
          "env": {
            "CHIPSCOPE_MCP_OUTPUT_DIR": "/path/to/workspace/chipscope-mcp-out",
            "CHIPSCOPE_MCP_CS_SERVER": "/path/to/Vivado/bin/cs_server"
          }
        }
      }
    }
    ```

Note `mcpServers` (not `servers`) — Cursor's config key differs from VS Code's. `doctor --fix` also writes VS Code config files; that's expected and does not affect Cursor.

Open the workspace in Cursor, then enable or start the `chipscope` server from Cursor's MCP settings. Verify with the targeted check:

```bash
chipscope-mcp doctor --client cursor --dir /path/to/workspace
```

---

## Claude Code

`doctor --fix` does **not** configure Claude Code — register the server with `claude mcp add` instead, using an **absolute path** to the extracted binary. Don't use `~` in the path; Claude Code launches the command directly, not through a shell.

Claude Code remembers the folder you were in when you registered the server and only treats it as configured from that folder, so `cd` into the workspace first:

=== "Windows"

    ```cmd
    cd C:\path\to\workspace
    claude mcp add chipscope -- C:\chipscope-mcp\chipscope-mcp-onefile-win\chipscope-mcp.exe start
    ```

=== "Linux"

    ```bash
    cd /path/to/workspace
    claude mcp add chipscope \
      -- /absolute/path/to/chipscope-mcp-onefile-lin64/chipscope-mcp start
    ```

To pass [settings](#settings), put each `-e NAME=value` after the server name and before `--`:

```bash
claude mcp add chipscope \
  -e CHIPSCOPE_MCP_CS_SERVER=/path/to/Vivado/bin/cs_server \
  -e HW_SERVER_URL=TCP:localhost:3121 \
  -- /absolute/path/to/chipscope-mcp-onefile-lin64/chipscope-mcp start
```

Verify the registration, then run the targeted doctor check:

=== "Windows"

    ```cmd
    claude mcp list
    C:\chipscope-mcp\chipscope-mcp-onefile-win\chipscope-mcp.exe doctor --client claude --dir C:\path\to\workspace
    ```

=== "Linux"

    ```bash
    claude mcp list
    /absolute/path/to/chipscope-mcp doctor --client claude --dir /path/to/workspace
    ```

---

## Settings

The server reads these environment variables from the `env` block of the client config (or `-e` for Claude Code):

| Variable | Purpose |
|---|---|
| `CHIPSCOPE_MCP_CS_SERVER` | Full path to `cs_server`. Needed when Vivado's `bin` directory is not on `PATH` |
| `HW_SERVER_URL` | Default `hw_server` URL, e.g. `TCP:localhost:3121` |
| `CS_SERVER_URL` | Default `cs_server` URL, e.g. `TCP:localhost:3042` |
| `CHIPSCOPE_MCP_OUTPUT_DIR` | Output sandbox for exported files — see [Output Files](#output-files) |

## Output Files

ChipScope MCP writes exported artifacts (eye-scan images, waveform files) under an output sandbox. Paths outside the sandbox are rejected by design.

- Default: `~/chipscope-mcp-out`.
- For VS Code and Cursor, `doctor --fix` sets `CHIPSCOPE_MCP_OUTPUT_DIR` in the generated config to a `chipscope-mcp-out` folder inside your workspace.
- For Claude Code, either save under `~/chipscope-mcp-out` or pass the variable explicitly when registering:

  ```bash
  claude mcp add chipscope \
    -e CHIPSCOPE_MCP_OUTPUT_DIR=/absolute/path/to/chipscope-mcp-out \
    -- /absolute/path/to/chipscope-mcp start
  ```

## Verify

Ask your AI client:

> List the available ChipScope MCP tools.

To connect to live hardware, include your server URLs:

> Connect to the hardware server at TCP:localhost:3121 and cs server at TCP:localhost:3042.

## Live Hardware Notes

- If you program a design and scan for debug cores, provide the matching LTX file when asked for probe metadata — ILA and VIO cores need it to show probe names.
- Reprogramming the PDI is normally enough when the wrong design is loaded or a run needs to start over.
- If a board becomes unresponsive after a bad memory access or interrupted session, power-cycle it, then reprogram the PDI and try again.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `doctor --dry-run` fails | Read the `[FAIL]` line and its `Fix:` hint |
| `cs_server` not found | Add Vivado's `bin` directory to `PATH`, or set `CHIPSCOPE_MCP_CS_SERVER` — see [Settings](#settings) |
| Which build is installed? | Run `chipscope-mcp version`, or open `build_manifest.json` next to the binary |
| Client can't start `chipscope` | Confirm the config points at the extracted binary in its permanent location (`chipscope-mcp.exe` on Windows, `chipscope-mcp` on Linux). If you moved or deleted the folder after running `doctor --fix`, rerun it from the new location |
| Saved files rejected as outside sandbox | Save under the configured output directory — see [Output Files](#output-files) above |
| No ILA or VIO cores appear | Scan with the matching `.ltx` file for the programmed design |
| Windows security blocks the binary | Run `doctor --dry-run` first to confirm the failure mode, then ask IT/security for an allow rule |

---
