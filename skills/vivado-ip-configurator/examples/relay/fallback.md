> Relay only: this is the `scripts/guard_mcp.py` relay's route (see [../README.md](../README.md)). The skill on the stock MCP never writes this Tcl.

# Missing metadata and bounded recovery

An ambiguous VLNV requires choosing among live catalog candidates using the request and documentation. A missing parameter, sparse native description, or unknown dependency requires targeted live-cell inspection/documentation, not an IP-specific recipe or guessed key.

On INIT_FAIL, correct begin arguments and retry. begin reuses the open project, or reopens the saved scratch project, when its part and block design match, keeping cells from earlier prompts; it rejects a project for another part. Do not recreate projects or sessions to evade state.

Read only selected parameter details and named current-run evidence. Keep ungrounded requirements unresolved. Use lite::gates for published direct port/interface dependencies and lite::detail for selected metadata. Per-subkey ranges and broad normalized readback coverage remain incomplete; do not assume every catalog IP is already supported.

`INCOMPLETE_TCL` means the rejected script did not run. Return to the fixed helper invocations. `REPEATED_FAILURE` and `CFG:STOP` refuse unchanged retries. Call `lite::finish cell` after requirements are registered; otherwise save partial BD with `save_bd_design`. Report unresolved features; the session stays open for the next cell, so stop Vivado only when the user is done with the design.

The full skill's targeted recovery may be used only when the run profile permits it and the original wall budget allows it. The lite profile does not silently enable additional skills or tools. All families use the same metadata/configuration path; unsupported semantics remain explicit partial results.
