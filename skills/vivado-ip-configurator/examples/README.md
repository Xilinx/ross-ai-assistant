<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Examples

Nothing here is needed by the skill. The skill runs on the stock Vivado MCP: the model sources `lib/ipcfg_op.tcl` and sends each step as `ipcfg_op {<json>}` through plain `vivado_execute` (see SKILL.md). This folder documents an optional host for cases the stock server does not cover.

## The relay for local models: `scripts/guard_mcp.py`

A local stdio MCP server that wraps the upstream Vivado MCP and re-exposes `vivado_start`, `vivado_execute` and `vivado_stop`. It lives in `scripts/` because the CED skill's bundled engine imports it from there.

- `--profile structured`: `vivado_execute` takes the workflow's JSON object as the whole command, with no Tcl around it, and refuses anything else. Use this for a local model that cannot be trusted to write well-formed Tcl. `seq` is optional here.
- `--profile legacy`: the original raw-Tcl `lite::` route, documented in [relay/tcl-workflow.md](relay/tcl-workflow.md), [relay/operations.md](relay/operations.md) and [relay/fallback.md](relay/fallback.md).
- `--database-config <runtime.json>`: serves a local vector metadata snapshot through the reserved `ipdb` session ([relay/database.md](relay/database.md)). It needs `sqlite-vec` and `sentence-transformers` (`scripts/requirements-database.txt`), which Vivado's bundled Python does not have. Without it, both the relay and the stock-MCP path use the database Vivado ships (2026.2 and later) or live discovery.

It needs Python 3 with the `mcp` package, `tclsh` for the legacy profile's completeness check, and Vivado 2026.1 or later.

```text
/usr/bin/python3 <skill>/scripts/guard_mcp.py
  --upstream <Vivado MCP executable>
  --evidence <absolute scratch dir>/evidence
  --profile structured
  [--database-config <snapshot runtime.json>]
```

Keep the evidence, cache and temporary directories outside `/home`.

### OpenCode

```json
"mcp": {
  "vivado": {
    "type": "local",
    "command": ["/usr/bin/python3", "<skill>/scripts/guard_mcp.py", "--upstream", "<mcp>",
                "--evidence", "/scratch/<user>/ipcfg/evidence", "--profile", "structured"],
    "environment": {"VIVADO_PATH": "<Vivado>/bin/vivado", "PYTHONPATH": "<site-packages with mcp>",
                    "TMPDIR": "/scratch/<user>/ipcfg/tmp"},
    "enabled": true, "timeout": 300000
  }
},
"permission": {
  "vivado_vivado_start": "allow", "vivado_vivado_execute": "allow", "vivado_vivado_stop": "allow",
  "skill": {"vivado-ip-configurator": "allow"}
}
```
