<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# MCP Tool Reference

Complete reference for all tools provided by the Vivado MCP Server. These are the capabilities available to any MCP-compatible AI agent (VS Code Copilot, Cursor, Claude Code, etc.) when connected to the server.

---

## Quick Reference

| Tool | Category | Description |
|------|----------|-------------|
| [`vivado_start`](#vivado_start) | Session | Launch a new Vivado session |
| [`vivado_stop`](#vivado_stop) | Session | Stop a Vivado session |
| [`vivado_connect`](#vivado_connect) | Session | Reconnect to an existing Vivado instance |
| [`vivado_list_sessions`](#vivado_list_sessions) | Session | List all active sessions |
| [`vivado_cleanup`](#vivado_cleanup) | Session | Clean up stale sessions and orphaned processes |
| [`vivado_execute`](#vivado_execute) | Execution | Execute Tcl commands in Vivado |
| [`vivado_status`](#vivado_status) | Monitoring | Check session health and run progress |
| [`vivado_log_messages`](#vivado_log_messages) | Monitoring | Parse log for errors, warnings, and messages |
| [`vivado_history`](#vivado_history) | Monitoring | Get or search command history |
| [`vivado_display`](#vivado_display) | GUI | Manage displays, screenshots, and virtual displays (Linux only) |
| [`vivado_doc_search`](#vivado_doc_search) | Research | Search AMD/Xilinx documentation |
| [`vivado_ssh`](#vivado_ssh) | Remote | Run Vivado on remote machines via SSH (Linux only) |
| [`vivado_todos`](#vivado_todos) | Workflow | Track multi-step task progress in IDE sidebar |
| [`vivado_feedback`](#vivado_feedback) | Workflow | Collect user feedback after tasks |
| [`vivado_client_info`](#vivado_client_info) | Workflow | Get MCP client capabilities |

---

## Session Management

### `vivado_start`

Launch a new Vivado session. Always starts in Tcl mode by default — GUI mode is only used when the user explicitly requests graphical interaction. The server configures a Tcl webserver and smart proxy for command execution.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `working_dir` | string | **Yes** | Working directory for the Vivado project. Must exist and be writable |
| `session_type` | enum | **Yes** | `ipi` for block design workflows, `general` for everything else |
| `gui_mode` | boolean | No | `true` for GUI mode with bundled VNC display. Default: `false` (Tcl mode) |
| `display_mode` | enum | No | Display handling: `auto` (default), `vnc`, `x11`, `none` |
| `display` | string | No | X11 display string (e.g. `:99`). Required when `display_mode=x11` |
| `port` | number | No | Webserver port. Default: auto-assigned |
| `vivado_path` | string | No | Path to Vivado executable. Auto-resolved from PATH if omitted |
| `env` | object | No | Extra environment variables for the Vivado process |

**Tips:**
- Sessions start in Tcl mode (headless) by default. Request GUI mode only when you need visual interaction (block designs, schematics).
- You can start in Tcl mode and switch to GUI later with `start_gui` — no restart needed.
- In GUI mode, the server spins up a VNC display automatically with a VNC connection string.
- Use `ipi` session type for Block Design (IP Integrator) workflows, `general` for everything else.

---

### `vivado_stop`

Gracefully stop a Vivado session. Saves state, closes the project, and terminates the Vivado process.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | **Yes** | Session to stop |
| `close_vivado` | boolean | No | Close Vivado application. Default: `true` |

Set `close_vivado=false` to detach the MCP server while keeping Vivado running.

---

### `vivado_connect`

Reconnect to an existing Vivado instance that already has a running Tcl webserver.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `webserver_url` | string | **Yes** | URL of the Vivado webserver (e.g. `http://localhost:8088`) |
| `proxy_addr` | string | No | Smart proxy address (`host:port`). Preferred for better performance |
| `session_id` | string | No | Reconnect to a known session by ID |
| `pid` | integer | No | Verify process is alive before connecting |
| `working_dir` | string | No | Working directory reference |

---

### `vivado_list_sessions`

List all active Vivado sessions with detailed metadata. No parameters required.

Returns for each session: `session_id`, `session_type`, `deployment_type` (`local`, `ssh`), `vivado_version`, `vivado_edition`, `is_gui_mode`, `hostname`, `uptime_seconds`, `proxy_addr`, and SSH details (`ssh_host`, `ssh_user`).

---

### `vivado_cleanup`

Clean up stale sessions and orphaned processes.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `operation` | enum | **Yes** | See operations below |
| `session_id` | string | No | Target specific session |

**Operations:**

| Operation | Description |
|-----------|-------------|
| `stats` | Session count and statistics |
| `health` | Check all session health |
| `cleanup_stale` | Remove dead sessions from tracking |
| `kill_orphans` | Kill Vivado processes started by MCP but not tracked |
| `kill_all` | Kill all MCP-started Vivado processes |
| `recover` | Reconnect to persisted sessions |

> **Safe by design** — only affects Vivado processes started by the MCP server. It will never touch Vivado instances you launched manually.

---

## Command Execution

### `vivado_execute`

Execute Tcl commands in Vivado. This is the primary tool for all Vivado operations.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | **Yes** | Tcl command(s) to execute. Batch with semicolons |
| `session_id` | string | **Yes** | Target session |
| `mode` | enum | No | `auto` (default), `proxy`, or `webserver` |
| `capture_log` | boolean | No | Capture log output. Default: `true` |
| `echo` | boolean | No | Show command in Vivado console. Default: `true` |

**Batching commands:** Multiple setup commands can be combined with semicolons:
```tcl
add_files src/top.v; set_property top top [current_fileset]; update_compile_order -fileset sources_1
```

**Standalone commands** (must appear first or alone):
- Design flow: `synth_design`, `place_design`, `route_design`, `opt_design`, `phys_opt_design`, `write_bitstream`
- Run management: `launch_runs`, `wait_on_run`, `reset_runs`
- Project: `open_project`, `close_project`, `open_checkpoint`, `read_checkpoint`, `source`
- GUI: `start_gui`, `stop_gui`

**Synthesis & Implementation flow:**
```tcl
-- Step 1: launch_runs synth_1 -jobs 8
-- Step 2: wait_on_run synth_1
-- Step 3: launch_runs impl_1 -to_step write_device_image -jobs 8
-- Step 4: wait_on_run impl_1
```

**Long-running commands:** Commands like synthesis or implementation can take minutes to hours. The server hands off execution and monitors progress in the background. The agent automatically polls `vivado_status` to track progress. You can check `vivado_log_messages` at any time to see errors or warnings as they appear.

---

## Monitoring

### `vivado_status`

Check session health, monitor synthesis/implementation runs, and recover stuck processes.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | **Yes** | Target session |
| `action` | enum | No | `session` (default), `runs`, `health`, `reset_run`, `cancel_and_relaunch` |
| `run_name` | string | No | Run to reset (for `reset_run`) |
| `jobs` | integer | No | Parallel jobs (for `cancel_and_relaunch`) |

Can be called while a command is still running — it doesn't interfere with execution.

---

### `vivado_log_messages`

Parse the Vivado log file for structured error, warning, critical warning, and info messages.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | **Yes** | Target session |
| `filter` | string | No | `errors`, `warnings`, `critical`, `info`, `all`. Default: errors + critical + warnings |
| `max_messages` | number | No | Limit per category. Default: all |

Can be called at any time, even during long-running commands.

---

### `vivado_history`

Get or search the complete command history for a session.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | **Yes** | Target session, or `all` to search across all sessions |
| `search_query` | string | No | Filter by command text or output (case-insensitive) |
| `limit` | integer | No | Max entries. Default: 50 |
| `since_id` | integer | No | Only entries after this history ID |

---

## GUI & Display

### `vivado_display`

> **Linux only.** `vivado_display` is not supported on Windows.

Manage X11 displays, capture screenshots, and run virtual displays (TigerVNC Xvnc) for headless GUI operation.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | enum | **Yes** | `check`, `screenshot`, `virtual_start`, `virtual_stop`, `virtual_status` |
| `session_id` | string | Conditional | Required for `screenshot` |
| `display_num` | number | No | Display number for `virtual_start` |
| `width` | number | No | Screen width. Default: 1920 |
| `height` | number | No | Screen height. Default: 1080 |

**Native display vs. virtual display:** By default, the agent may open the GUI on a virtual VNC display rather than your native X11 display. To ensure Vivado opens on your display:

1. Check your display: `echo $DISPLAY` in your terminal (e.g. `:0`, `localhost:10.0`)
2. Tell the agent explicitly: *"Open the GUI on my native display"* or *"Use display :0"*
3. Or pass `display_mode='x11'` when starting Vivado via `vivado_start` or `vivado_ssh`

---

## Research

### `vivado_doc_search`

Search the AMD/Xilinx documentation database using hybrid search (BM25 keyword matching + vector semantic search).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | **Yes** | Search query |
| `top_k` | number | No | Number of results. Default: 25 |
| `alpha` | number | No | Semantic vs keyword weight (0.0-1.0). Default: 0.80 |

Use lower alpha values (0.3-0.5) for exact Tcl command lookups. Use higher values (0.7-0.9) for conceptual questions.

---

## Remote Deployment

### `vivado_ssh`

> **Linux only.** `vivado_ssh` is not supported on Windows.

Start and manage Vivado sessions on remote machines via SSH. The remote machine must share the same filesystem (NFS) as the local machine.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | enum | **Yes** | `start`, `status`, or `kill` |
| `ssh_host` | string | Conditional | Remote hostname. Required for `start` |
| `working_dir` | string | Conditional | Project directory on shared filesystem. Required for `start` |
| `session_type` | enum | Conditional | `general` or `ipi`. Required for `start` |
| `gui_mode` | boolean | No | Launch with GUI. Default: `false` |
| `display_mode` | enum | No | Display management: `auto`, `vnc`, `x11`, `none`. Default: `auto` |
| `ssh_user` | string | No | SSH username. Default: current user |
| `ssh_key` | string | No | Path to SSH private key |
| `session_id` | string | No | Session ID (for `status` / `kill`) |

> SSH deployment requires the working directory to be on a shared filesystem (NFS) accessible at the same path on both machines.

---

## Workflow

### `vivado_todos`

Track multi-step task progress with a structured todo list. Tasks appear in the IDE sidebar.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `operation` | enum | **Yes** | `plan`, `update`, `read`, `clear`, `show` |
| `tasks` | array | Conditional | Task list for `plan` |
| `title` | string | Conditional | Plan title. Required for `plan` |
| `id` | string | Conditional | Task ID. Required for `update` |
| `status` | enum | Conditional | `active`, `done`, `skipped`, `failed`. Required for `update` |

---

### `vivado_feedback`

Collect user feedback after completing a significant task.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | enum | **Yes** | `collect` or `submit` |
| `task_summary` | string | Conditional | What was accomplished. Required for `collect` |
| `rating` | enum | Conditional | `good`, `neutral`, `poor`. Required for `submit` |
| `feedback_text` | string | No | Free-text comments |

---

### `vivado_client_info`

Get information about the connected MCP client and its capabilities. No parameters. Returns client name, version, and supported features (GUI, terminal, sidebar).

---

## Parallel Execution

**Read-only tools** (`vivado_status`, `vivado_log_messages`, `vivado_history`, `vivado_doc_search`, `vivado_list_sessions`) can all run in parallel — even while a command is executing.

**Command execution** (`vivado_execute`) is sequential within a single session because Vivado's Tcl interpreter is single-threaded. However, commands on **different sessions** can run in parallel.

---

## Disabling Tools

If a tool causes unwanted behavior, you can disable individual MCP tools in your IDE.

**VS Code / GitHub Copilot:** In the Chat view, select the **Configure Tools** button (wrench icon) in the chat input field. Toggle specific tools on or off. To disable an entire MCP server, right-click it in the **MCP SERVERS - INSTALLED** section and select **Disable**.

**Cursor:** Use the tools picker in the chat input. Toggle individual tools on or off before sending your prompt.

**GitHub Copilot CLI:**
```bash
copilot --deny-tool='vivado(vivado_display)'
```

**Claude Code:** Add a deny rule to your `~/.claude/settings.json` or `.claude/settings.json`:
```json
{
  "permissions": {
    "deny": ["mcp__vivado__vivado_display"]
  }
}
```

**OpenAI Codex CLI:** Add `disabled_tools` to the server entry in `~/.codex/config.toml`:
```toml
[mcp_servers.vivado-mcp]
command = "/path/to/vivado-mcp-server"
args = ["--stdio-bridge"]
disabled_tools = ["vivado_display"]
```

---
