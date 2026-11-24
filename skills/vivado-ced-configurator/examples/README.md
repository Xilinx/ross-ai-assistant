<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Examples

Nothing here is part of the skill. The skill runs on the stock Vivado MCP (`references/setup.md`); these are host setups for cases the stock server does not cover.

## `ced_guard.py` — a relay for local models

A local stdio MCP server that wraps the upstream Vivado MCP and re-exposes it with CED semantics:

- `vivado_execute` takes the workflow's JSON object as the whole command, instead of `ced_op {<json>}` Tcl. A model that cannot be trusted to emit well-formed Tcl never writes any.
- `ced_apply_phase2` is a separate tool, so the host can gate the Phase 2 write with a permission rule (`"ask"`) rather than relying on the model to stop and ask.

Use it when you serve a local model. With a frontier model on the stock MCP it only adds a moving part: the skill already sends `ced_op {...}` and already stops for approval.

It needs Python 3 with the `mcp` package, `git`, `tclsh`, Vivado 2026.1 or later, and the engine bundled in `engine/` (`CED_LITE_SKILL` points at a different engine root).

```text
/usr/bin/python3 <skill>/examples/ced_guard.py
  --upstream <Vivado MCP executable>
  --evidence <absolute scratch dir>/evidence
  --ced-cache <absolute scratch dir>/ced-mirror
  [--database-config <lite IP metadata runtime.json>]
  [--state-dir <absolute scratch dir>/state]
```

All paths must be outside `/home`. The relay gives the upstream server and Vivado private home folders under `--state-dir`, so other Vivado MCP servers on the host cannot adopt the session or reach its webserver (that interference crashes long commands such as example-design instantiation).

### OpenCode

```json
"mcp": {
  "vivado": {
    "type": "local",
    "command": ["/usr/bin/python3", "<skill>/examples/ced_guard.py", "--upstream", "<mcp>",
                "--evidence", "/scratch/<user>/ced/evidence", "--ced-cache", "/scratch/<user>/ced/mirror"],
    "environment": {"VIVADO_PATH": "<Vivado>/bin/vivado", "PYTHONPATH": "<site-packages with mcp>",
                    "TMPDIR": "/scratch/<user>/ced/tmp"},
    "enabled": true, "timeout": 1800000
  }
},
"permission": {
  "vivado_vivado_start": "allow", "vivado_vivado_execute": "allow", "vivado_vivado_stop": "allow",
  "vivado_ced_apply_phase2": "ask",
  "skill": {"vivado-ced-configurator": "allow"}
}
```

`"ask"` makes OpenCode itself ask the user before any Phase 2 write, independently of the model. Non-interactive runs (`opencode run`) reject "ask" permissions, so unattended evaluation sets it to `"allow"` and states the approval in the prompt.

The MCP timeout must exceed the longest instantiation; the relay waits for commands that the upstream server detaches after about two minutes.

## Ports and custom boards

A request that asks to port a design to a board it does not list adds `"port":"allowed"` at
`begin` and sends `{"op":"port"}` when `next_op` says so (see `references/port.md`); the
relay passes both through unchanged. For a board outside Vivado's repository, add
`"CED_BOARD_REPO": "<board_files dir>"` to the relay's `environment`.
