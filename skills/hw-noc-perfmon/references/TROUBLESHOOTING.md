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

## `chipscope_noc` / `chipscope_noc_perfmon` Issues

### `chipscope_noc_perfmon` Rejects the Call
Requires `authorize_monitor_configuration=true`. This is intentional — configuring
performance monitors changes device state. Get explicit user approval (see
"Perfmon Monitor Authorization" in SKILL.md) before setting it.

### Unknown `element_name`
1. Run `chipscope_noc(action="discover", limit=50)` first — never guess element names.
2. Use pagination (`offset`) if the design has more than 50 elements.
3. Cross-reference against `noc_report.json` to confirm the element belongs to the
   connection you intend to measure.

### Hierarchy Label Lookup Fails
1. Confirm the user supplied `noc_report.json` and that it matches the programmed PDI.
2. Re-check the site-to-hierarchy mapping in that file.
3. If a site still does not resolve, keep the raw hardware site name (for example
   `NOC_NMU512_X0Y0`) and continue — do not invent a hierarchy label.

### User Needs an Easier Site Selection Prompt
Present enabled sites as a numbered list, then ask for a multi-select reply:
1. `[1] hierarchy/path (NOC_NMU...)`
2. `[2] hierarchy/path (DDRMC...)`
3. Ask for comma-separated numbers or ranges (`1,3,5-7`) or `all`.
This is the preferred CLI fallback when real checkboxes are unavailable.

### More Than 8 Elements Needed
`chipscope_noc_perfmon` accepts 1–8 `element_names` per call.
1. Do **not** split site-monitor setup across multiple `measure` calls.
2. Ask the user to narrow the selection to a final ≤8-site monitor set.
3. If the user asks for guidance, prioritize sites on the suspect path or by highest
   configured bandwidth requirement.
4. Report any deferred sites in the output — do not silently drop coverage.

### Fewer Samples Returned Than Requested
Expected behavior: the burst ends when the *fastest* element in the batch completes,
so slower elements may return fewer samples.
1. Note the actual sample count returned per element in the report.
2. Treat elements with a truncated sample count as lower-confidence; consider a
   dedicated `measure` call with just that element and a longer `timeout_seconds`
   if higher confidence is needed.

### DDRMC Element Missing `ddrmc_freq_mhz`
1. `ddrmc_freq_mhz` is required for any DDRMC-classified element — the tool cannot
   infer the controller clock.
2. Take it from a supplied design artifact if it is stated there.
3. Otherwise ask the user rather than guessing — a wrong frequency silently
   corrupts the bandwidth calculation.

### `chipscope_noc_perfmon` Times Out
1. Default `timeout_seconds` is 30 (max 60). Increase it for a longer `sample_count`
   or a slow board link.
2. Confirm the device is still responsive: `chipscope_device(action="program_log")`.
3. Retry once; if it still times out, report that element as inconclusive rather
   than fabricating a value.

### Live Plot Stops Early
1. If the user pressed Escape, treat it as a normal early-stop condition.
2. If raw Escape capture is unavailable in the client, switch to the fallback prompt:
   `Type esc and press Enter to stop early.`
3. Keep the already collected samples and summarize them; do not discard the session.

### `chipscope_noc(action="read_registers")` Returns No Bandwidth/Latency Data
Expected — `read_registers` validates element state only. It does not measure
bandwidth or latency; use `chipscope_noc_perfmon(action="measure")` for that.

---

## Design Artifact Issues

### User Has No `noc_report.json` / `.ncr` / `.nts`
Not a failure. Continue in hardware-only mode:
1. Use the ChipScoPy Vivado site names verbatim (e.g., `NOC_NMU512_X0Y0`).
2. Do **not** try to resolve design instance or hierarchy names by any other route.
3. State in the report that no configured bandwidth/QoS baseline was available.

### Supplied Artifact Path Is Unreadable
1. Report the exact path and error to the user.
2. Ask for a corrected path, or for permission to continue without that artifact.
3. Never substitute a different file or regenerate one — this skill does not build artifacts.

### Site Not Found in `noc_report.json`
1. Confirm the artifact matches the PDI that is actually programmed.
2. If it does not match, ask the user for the matching artifact set.
3. Otherwise fall back to the raw hardware site name for that site only.

### `.nts` Has No Entry for a Measured Connection
Report the measured numbers and state that no requirement was configured for that
connection. Do not invent a target bandwidth or QoS class.

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
1. Update to a version emitting the current unified `SubsystemResult` schema
   (`schema_version: "subsystem.1.0.0"`).
2. Re-run analysis.

### `findings_count == 0` Despite Expected Errors
1. Confirm the correct PDI was programmed.
2. Check `program_log` for EAM errors — if present, error may have been captured and cleared.
3. Wait 5 seconds and re-run `sysdbg_noc(action="analyze", output_format="json")`.
4. If still zero, the design may not have triggered the error condition — ask user to confirm the error scenario.

### `program_log` Does Not Show Runtime NoC Error
Expected. `chipscopy_device(action="program_log")` captures boot/program-time context only.
Use `sysdbg_noc` for runtime analysis — it reads registers directly.
