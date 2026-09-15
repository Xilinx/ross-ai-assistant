<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Troubleshooting Guide

## ChipScoPy MCP Connection Issues

### `hw_server` Connection Refused
1. Check that `hw_server` is running on the board host.
2. Verify that the board is powered.
3. Test network connectivity to the board host.
4. Retry:
   ```
   chipscopy_session(action="connect", hw_server_url="TCP:<host>:3121", cs_server_url="TCP:<host>:3042")
   ```

### `cs_server` Missing Or Unreachable
This workflow requires `cs_server` for scan, NoC visibility, and VIO correlation.

1. Start `cs_server` on the board host.
2. Reconnect with an explicit `cs_server_url`.
3. If the connection succeeds with `hw_server`-only → STOP. This workflow cannot proceed without `cs_server`.

### Device Not Found
- Ensure the Versal board is powered and connected through JTAG.
- Check that the JTAG chain is visible through `hw_server`.
- Try `chipscopy_device(action="select", device_selector="0")` after reconnecting.

### IDCODE Returns 0xFFFFFFFF or 0x00000000
- Board not powered or JTAG disconnected.
- Wrong device selected.
- cs_server connection issue — reconnect with both endpoints.

### `chipscopy_memory` Read Fails Transiently
Hardware may be temporarily busy after power-up or PDI programming.
1. Wait 5 seconds.
2. Retry the read once.
3. If it fails again, STOP — hardware access is not functional.

---

## Vivado MCP Issues

### Vivado MCP Not Available
Required for this skill. Cannot proceed without it.
1. Ensure Vivado MCP server is configured in IDE's `mcp.json`.
2. Verify `--vivado-path` points to Vivado 2026.1+.
3. Reload the IDE after config changes.

### Vivado Project Won't Open
- Verify the `.xpr` path is correct and accessible.
- Check the Vivado version compatibility.
- Confirm the project is not corrupted.

### Multiple Implementation Runs
- Enumerate all runs: `vivado_execute("get_runs impl_*")`
- Ask the user which run to use.
- Never assume `impl_1`.

### `write_noc_solution` Not Found
- Ensure NoC IP is present in the design.
- Confirm a routed design is open (not synthesis).
- Open the routed run: `vivado_execute("open_run <impl_run>")`

### `report_noc` Fails
- `report_noc` is a built-in Vivado 2026.1+ command — no script sourcing needed.
- Verify routed design is open.
- Create output directory first: `vivado_execute("file mkdir <output_dir>")`

### XSA Generation Failed
1. Check implementation status — must be complete.
2. Verify routed design is open.
3. Retry: `vivado_execute("write_hw_platform -fixed -force -file design.xsa")`

### HSI API Fails to Find Master
- Verify XSA path is absolute.
- Check that the NMU port name from `noc_report.txt` matches (strip `_nmu` suffix).
- Verify the NoC instance name (e.g., `axi_noc_0`) matches the design.
- Try listing all cells: `vivado_execute("hsi::get_cells -hierarchical")`

---

## `sysdbg_noc` Runtime Issues

### `sysdbg_noc` Tool Is Missing
The MCP runtime does not have `sys-dbg-util` installed.
1. Install `sys-dbg-util` in the ChipScoPy MCP runtime.
2. Restart the MCP server.

### `sysdbg_noc` Reports `sys-dbg-util not installed`
The tool wrapper is present but the dependency is unavailable.
1. Install `sys-dbg-util`.
2. Verify the runtime picked it up, then re-run analysis.

### `sysdbg_noc` Fails With `regdb` Error
Missing `chipscope-xrdb` register database data.
1. Install `chipscope-xrdb`.
2. Populate register database files.
3. Re-run analysis.

### `sysdbg_noc` Fails With Schema Error
Older `sys-dbg-util` build.
1. Update to version emitting `schema_version: "noc.1.0.0"`.
2. Re-run analysis.

### `findings_count == 0` Despite Expected Errors
1. Confirm the correct PDI was programmed.
2. Check `program_log` for EAM errors — if present, error may have been captured and cleared.
3. Wait 5 seconds and re-run `sysdbg_noc(action="analyze", output_format="json")`.
4. If still zero, the design may not have triggered the error condition — ask user to confirm the error scenario.

### `program_log` Does Not Show Runtime NoC Error
Expected. `chipscopy_device(action="program_log")` captures boot/program-time context only.
Use `sysdbg_noc` for runtime analysis — it reads registers directly.

---

## Design Artifact Issues

### No NoC Cells Found
- Verify the design contains NoC IP.
- Check that a completed implementation run exists.
- Use `report_noc` (built-in Vivado 2026.1+) or `get_cells -hierarchical -filter {REF_NAME =~ NOC_NMU*}` for discovery.

### NCR Is Empty Or Too Small
1. Verify NoC configuration in the design.
2. Ensure the routed design (not synthesis) is open.
3. Re-extract: `vivado_execute("write_noc_solution -force design.ncr")`

### PDI Not Found
This skill does NOT build designs. User must complete implementation first.
