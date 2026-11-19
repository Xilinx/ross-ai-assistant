---
name: hw-ibert-gt-debug
description: >
  Debug serial transceiver links and run eye/YK scans on live FPGA/SoC devices via
  ChipScoPy MCP tools. Supports GTY/GTYP eye scans (BER heatmap) and GTM YK scans
  (SNR waveform). Create and configure loopback or external links, sweep TX/RX
  equalization, run 2D eye scans, and assess link margin. Produces inline PNG
  visualizations and exportable scan data. Use when user asks to "run eye scan",
  "check signal quality", "create IBERT link", "show eye diagram", "check BER",
  "IBERT status", "GT link health", "tune TX emphasis", "run YK scan",
  "check SNR", "IBERT loopback test", or "serial link debug".
license: MIT
compatibility: Requires ChipScoPy 2026.1+ and access to live hardware containing an IBERT core; supports Versal, UltraScale+, UltraScale, and 7 Series devices.
metadata:
  version: "1.3.3"
  stage: beta
---

<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->
# IBERT Eye Scan & Link Tuning (EA)

> **Early Access** — this skill may change before general availability.

Debugs serial transceiver links and runs eye/YK scans on live hardware via **ChipScoPy MCP**. Three tools cover the full workflow:

- **`chipscope_ibert`** — discover GT cores, create/configure/delete links, check status
- **`chipscope_ibert_eye_scan`** — 2D eye scan with inline PNG heatmap (GTY/GTYP)
- **`chipscope_ibert_yk_scan`** — YK scan with inline PNG waveform (GTM, 56+ Gbps)

See [REFERENCE.md](REFERENCE.md) for GT transceiver types, link configuration options, scan parameters, and report schemas.

**Routing:** GTY/GTYP eye scan can also be driven through Vivado MCP (`vivado_execute` + `create_hw_sio_scan ... 2d_full_eye`/`1d_bathtub`, real Hardware Manager Tcl) — try that first if a Vivado MCP session is available, falling back to `chipscope_ibert_eye_scan` otherwise; both paths are proven on real hardware (VCK190). **GTM YK scan has no vivado-mcp fallback — `chipscope_ibert_yk_scan` is the only path.** Do not attempt `create_hw_sio_scan` against a GTM link expecting it to work; see the error table in Workflow B below for exactly how and where each Vivado scan type fails on GTM.

**Device scoping note:** the frontmatter `device_families` list (versal, ultrascale-plus, ultrascale, 7series) applies to GTY/GTYP eye scan. GTM (YK scan) is narrower — Versal **Premium/HBM-series only** (e.g. VPK120/180), per REFERENCE.md's transceiver table. Do not assume YK scan works on a general Versal part just because "versal" is in the device list.

## Tools Used

| Tool | Purpose |
|------|---------|
| `vivado_start` | Start new Vivado session (vivado-mcp path). |
| `vivado_execute` | Execute Hardware Manager Tcl commands (vivado-mcp path). |
| `vivado_list_sessions` | Discover active Vivado sessions (vivado-mcp path). |
| `vivado_status` | Check Vivado session health (vivado-mcp path). |
| `chipscope_ibert` | List IBERT cores, discover GT groups, create/delete/configure links, check link status and BER, reset (chipscope-mcp path). |
| `chipscope_ibert_eye_scan` | Run 2D eye scan on GTY/GTYP link. Returns inline PNG heatmap. Supports export (chipscope-mcp path). |
| `chipscope_ibert_yk_scan` | Run YK scan on GTM link. Returns inline PNG waveform + SNR data. Export at scan time only (chipscope-mcp path). |
| `chipscope_session` | Connect to hw_server + cs_server (chipscope-mcp path). |
| `chipscope_device` | List/select devices, check resources, program device (chipscope-mcp path). |
| `chipscope_scan` | Discover debug cores including IBERT (chipscope-mcp path). |
| Agent file tools | Write output files (report_data.json, REPORT.md). |

---

## Efficiency Guidelines

- **Check link type first** — GTM uses YK scan, GTY/GTYP uses eye scan. The IBERT core listing indicates transceiver type.
- **Create link before scanning** — eye/YK scan requires an active link. Use `chipscope_ibert(action='create_link')`.
- **Loopback for self-test** — use Near-End PMA loopback for board-independent testing.
- **Export at scan time for YK** — GTM YK scan data is transient; use `export_path` during scan, not after.
- **Do NOT** run scans without a configured link — the tool will error.

---

## Workflow A: Eye Scan (GTY/GTYP)

### vivado-mcp Path (Hardware Manager Tcl)

**Tool Availability Check:** Before proceeding, verify that `vivado_execute` is available. If not available, fall back to the chipscope-mcp path below.

When a Vivado MCP session is available, use Hardware Manager Tcl commands via `vivado_execute(session_id=<id>, command=<tcl>)`.

**Step 1: Connect to Hardware Manager**

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
open_hw_manager
connect_hw_server -allow_non_jtag -url <hw_server_url>
current_hw_target [get_hw_targets */xilinx_tcf/Xilinx/*]
open_hw_target
```

**Step 2: Select FPGA Device (not ARM DAP)**

First, discover available devices:

```tcl
get_hw_devices
```

Returns list like: `arm_dap_0 xcvp1202_1`

Then select the FPGA device (filter out ARM DAP):

```tcl
# Use Tcl filter to select FPGA device (exclude arm_dap*)
set fpga_devs [get_hw_devices -filter {NAME !~ "arm_dap*"}]
if {[llength $fpga_devs] == 0} {
    error "No FPGA devices found on JTAG chain"
} elseif {[llength $fpga_devs] > 1} {
    puts "Multiple FPGAs found: [join [get_property NAME $fpga_devs] {, }]"
    error "Multiple FPGA devices found - please specify target device"
}
set fpga_dev [lindex $fpga_devs 0]
current_hw_device $fpga_dev
puts "Selected device: [get_property NAME [current_hw_device]]"

refresh_hw_device [current_hw_device]
```

**Critical:** Always verify `get_hw_devices` output and select the FPGA device explicitly. Do NOT use `[lindex [get_hw_devices] 0]` blindly - it may select the ARM Debug Access Port instead of the FPGA.

**Step 3: Discover IBERT Cores**

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
# Discover IBERT cores and get both name and type
foreach core [get_hw_sio_iberts] {
    puts "[get_property NAME $core]: [get_property DISPLAY_NAME $core]"
}
# Prints core name and transceiver type together: "IBERT_0: IBERT Versal GTM", "IBERT_1: IBERT Versal GTYP", etc.
# This preserves the mapping needed for later report generation
```

**Step 4: Discover GT Groups and Channels**

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
puts "GT groups: [get_hw_sio_gtgroups]"
# Returns GT quad groups: IBERT_0.Quad_204, IBERT_0.Quad_205, etc.

puts "GT channels: [get_hw_sio_gts]"
# Returns individual GT channel objects
```

**Expected Output Example** (Versal Premium VP1202):
- Device: `xcvp1202_1`
- IBERT Cores: `IBERT_0` (Versal GTM), `IBERT_1` (Versal GTYP)
- GT Groups: `IBERT_0.Quad_204`, `IBERT_0.Quad_205`, `IBERT_1.Quad_200`, `IBERT_1.Quad_201`
- Total Channels: 16

**Step 5: Create and Configure Link** (vivado-mcp)

For loopback testing, create a link between TX and RX on the same channel.

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
set gt [lindex [get_hw_sio_gts <pattern>] 0]
set tx [get_hw_sio_txs -of_objects $gt]
set rx [get_hw_sio_rxs -of_objects $gt]
set link [create_hw_sio_link -description "Test_Link" $tx $rx]
# Returns: IBERT_X.Quad_Y.CH_Z.TX->IBERT_X.Quad_Y.CH_Z.RX

# Extract channel number from GT name (e.g., "IBERT_0.Quad_200.CH_2" -> "2")
set gt_name [get_property NAME $gt]
set ch_num [string range $gt_name [expr {[string last "_" $gt_name] + 1}] end]
# Now ch_num contains the channel number (0, 1, 2, or 3)
```

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
set_property CH${ch_num}_TX_PATTERN {PRBS 31} $link
set_property CH${ch_num}_RX_PATTERN {PRBS 31} $link
set_property CH${ch_num}_LOOPBACK {Near-End PMA} $link
commit_hw_sio $link
```

**Important:** Set channel-prefixed properties on the LINK object and commit the link. Properties use CH${ch_num}_ prefix (e.g., CH0_TX_PATTERN for channel 0).

Then, after any Far-End PMA TX reset (the "vivado-mcp TX Reset" block below), clear errors counted during the change: set `CH${ch_num}_RX_RESET` to 1 on `$gt`, `commit_hw_sio $gt`, set it to 0, `commit_hw_sio $gt`.

Common PRBS patterns: `PRBS 7`, `PRBS 9`, `PRBS 15`, `PRBS 23`, `PRBS 31`. Loopback modes: `User Design` (off), `Near-End PMA`, `Near-End PCS`, `Far-End PMA`, `Far-End PCS`.

**Step 6: Check Link Status** (vivado-mcp)

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
refresh_hw_sio [get_hw_sio_iberts]
puts [get_property CH${ch_num}_RX_STATUS $gt]
# Prints line rate (e.g., "24.998 Gbps") or "No link"

puts [get_property CH${ch_num}_RX_PRBS_BER $gt]
# Prints BER (e.g., "8.105091e-15")

puts "[get_property CH${ch_num}_RX_PRBS_ERR_CNT $gt] errors (hex) in [get_property CH${ch_num}_RX_RECEIVED_BIT_COUNT.NORMALIZED $gt] bits"
```

**Important:** Call `refresh_hw_sio` to read current values from hardware before checking status. Without it, you see stale cached values.

**Expected Results**:
- Link Status: ~25 Gbps (GTYP example)
- BER: judged with the error and bit counts, see [Judging BER](#judging-ber)
- Configuration: PRBS 31, Near-End PMA loopback

For eye scans, continue using Hardware Manager `hw_sio_*` commands. See UG908 Chapter on IBERT for complete Tcl reference.

### chipscope-mcp Path

When chipscope-mcp is available (or vivado-mcp is not), use the following tools.

**Step 1: Discover IBERT Cores**

```
chipscope_ibert(action='list')
```

Returns IBERT cores with transceiver type hints. Identify **GTY** or **GTYP** cores.

**More than one core** (e.g. VPK120: `IBERT Versal GTM` and `IBERT Versal GTYP`): every call goes to the **first** core unless `ibert_name` names another. Choose the core by `transceiver_type` / `scan_type_hint` (`eye_scan` for GTY/GTYP, `yk_scan` for GTM), or for a named GT group the core whose `analyze` lists it, and pass its `name` as `ibert_name` on every later call (`analyze`, `create_link` and `reset` of a GT group or core fail or reach the wrong core without it).

**Step 2: Analyze GT Groups**

```
chipscope_ibert(action='analyze', ibert_name='<ibert_name>')
```

Discovers GT groups and available transceivers within the named IBERT core.

**Step 3: Create a Link** (chipscope-mcp)

`create_link` takes `gt_group` (e.g. `'Quad_205'`, from Step 2's `analyze` output) plus integer `tx_channel`/`rx_channel` indices (0-3) — not string channel names. Confirmed against `chipscope-mcp/src/chipscope_mcp/tools/chipscope_ibert.py`.

```
chipscope_ibert(action='create_link',
    ibert_name='<ibert_name>',
    gt_group='<gt_group>',
    tx_channel=<tx_channel_index>,
    rx_channel=<rx_channel_index>)
```

For loopback testing (TX feeds back to RX on same channel):
```
chipscope_ibert(action='create_link',
    ibert_name='<ibert_name>',
    gt_group='<gt_group>',
    tx_channel=0,
    rx_channel=0)
```

For a cross-quad link, pass `rx_gt_group` if the RX channel is on a different GT group than TX.

For vivado-mcp, use `create_hw_sio_link` command - see UG908 IBERT chapter for Tcl syntax.

**Step 4: Configure Link** (chipscope-mcp)

```
chipscope_ibert(action='configure_link',
    link_name='<link_name>',
    tx_pattern='PRBS 31',
    rx_pattern='PRBS 31',
    loopback='Near-End PMA')
```

Common patterns: `PRBS 7`, `PRBS 15`, `PRBS 23`, `PRBS 31`. Loopback modes: `None`, `Near-End PMA`, `Near-End PCS`, `Far-End PMA`, `Far-End PCS`.

Then reset the link to clear errors counted during the change (separate from the Far-End PMA TX reset below): `chipscope_ibert(action='reset', target='link', ibert_name='<ibert_name>', link_name='<link_name>')`.

For vivado-mcp, use channel-prefixed properties (`CH${ch_num}_TX_PATTERN`, `CH${ch_num}_RX_PATTERN`, `CH${ch_num}_LOOPBACK`) on the link object, then `commit_hw_sio $link` to apply.

**Important:** When entering or exiting Far-End PMA loopback mode, issue a TX reset (GTTXRESET) after changing the loopback property. Per Xilinx architecture manuals (AM002/AM017), this reset is required for proper GT operation in far-end PMA loopback.

**vivado-mcp TX Reset (after loopback change):**
```tcl
# Assert TX reset (pulse high then low)
set_property CH${ch_num}_TX_RESET 1 $gt
commit_hw_sio $gt
set_property CH${ch_num}_TX_RESET 0 $gt
commit_hw_sio $gt
```

**chipscope-mcp TX Reset:**  
ChipScoPy's `configure_link` API handles the required TX reset internally when changing to/from Far-End PMA loopback — no explicit reset call needed.

**Step 5: Check Link Status** (chipscope-mcp)

```
chipscope_ibert(action='status', ibert_name='<ibert_name>', link_name='<link_name>')
```

Verify: PLL locked, link up, BER check PASS or NOT PROVEN ([Judging BER](#judging-ber)) before scanning.

For vivado-mcp, use `refresh_hw_sio` then `get_property` on GT objects (not link objects) - status properties like `CH${ch_num}_RX_STATUS` and `CH${ch_num}_RX_PRBS_BER` are read from the GT channel. See Step 6 vivado-mcp section for example commands.

### Step 6: Run Eye Scan

**vivado-mcp Path (Hardware Manager Tcl)**

When a Vivado MCP session is available, use Hardware Manager Tcl for 2D eye scan.

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
# Create 2D eye scan object on the specific link from Step 5
# Reuse $link variable from link creation, or select by description filter
set scan [create_hw_sio_scan -description "2D_Eye_Scan" 2d_full_eye $link]
# Returns: SCAN_0

# Run the scan
run_hw_sio_scan $scan

# Wait for completion
wait_on_hw_sio_scan $scan

# Get eye metrics
report_property $scan
```

**Key Properties from Eye Scan:**
- `OPEN_PERCENTAGE` — Overall eye opening (%)
- `HORIZONTAL_OPENING` — Horizontal eye width (UI steps)
- `HORIZONTAL_PERCENTAGE` — Horizontal opening (%)
- `VERTICAL_OPENING` — Vertical eye height (mV steps)
- `VERTICAL_PERCENTAGE` — Vertical opening (%)
- `OPEN_AREA` — Total open area (square units)
- `STATUS` — "Done" when complete
- `PROGRESS` — "100%" when complete

**Export scan data:**

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
write_hw_sio_scan -force <output_file.csv> $scan
```

**Proven Results** (GTYP Quad_200 CH_0, Near-End PMA, PRBS 31):
- Eye Opening: 64%
- Horizontal: 41 steps (66.67%)
- Vertical: 241 steps (100%)
- Scan Time: ~1.5 seconds
- Data Points: 558

**chipscope-mcp Path**

When chipscope-mcp is available (or vivado-mcp is not):

```
chipscope_ibert_eye_scan(
    link_name='<link_name>',
    horz_step=10,
    vert_step=10,
    target_ber=1e-5)
```

Returns inline PNG heatmap showing eye opening.

**With export:**
```
chipscope_ibert_eye_scan(
    link_name='<link_name>',
    export_path='eye_scan_data.csv',
    save_plot='eye_scan.png')
```

**Display modes:** `display='chat'` (inline only), `display='app'` (structured content), `display='both'` (default), `display='ascii'` (text-only).

### Step 7: Generate Reports

**vivado-mcp Path**

After completing the eye scan, collect all metrics and generate reports.

**Collect Device and IBERT Core Metrics:**

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
# Device information
puts [get_property PART [current_hw_device]]
puts [get_property FAMILY [current_hw_device]]
puts [get_property REGISTER.DNA [current_hw_device]]
puts [current_hw_target]

# IBERT core discovery
set cores [get_hw_sio_iberts]
foreach core $cores { report_property $core }
# Extract: DISPLAY_NAME (e.g., "IBERT Versal GTM", "IBERT Versal GTYP")

# GT topology
get_hw_sio_gtgroups
```

**Collect Link Status:**

Run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
refresh_hw_sio [get_hw_sio_iberts]

# Get the link created in Step 5 by description or index
# If you created link with -description "Test_Link", filter by that:
set link [lindex [get_hw_sio_links -filter {DESCRIPTION == "Test_Link"}] 0]
# Or if only one link exists, use: set link [lindex [get_hw_sio_links] 0]
report_property $link
# Extract link name, TX/RX endpoints

# Get the GT used in Step 5 (same pattern as before)
set gt [lindex [get_hw_sio_gts <pattern>] 0]
# Extract channel number from GT name
set gt_name [get_property NAME $gt]
set ch_num [string range $gt_name [expr {[string last "_" $gt_name] + 1}] end]

# Refresh this specific GT to get current values from hardware
refresh_hw_sio $gt

puts [get_property CH${ch_num}_RX_STATUS $gt]
# Prints line rate (e.g., "24.994 Gbps")
puts [get_property CH${ch_num}_RX_PRBS_BER $gt]
# Prints BER (e.g., "2.080110e-15")
puts "[get_property CH${ch_num}_RX_PRBS_ERR_CNT $gt] errors (hex) in [get_property CH${ch_num}_RX_RECEIVED_BIT_COUNT.NORMALIZED $gt] bits"
puts [get_property CH${ch_num}_TX_RESET_DONE $gt]
puts [get_property CH${ch_num}_RX_RESET_DONE $gt]
# Prints reset done status: "Yes" or "No"
puts [get_property CH${ch_num}_RX_PRBS_LOCKED $gt]
# Prints PRBS lock status: "Locked" or "Not locked"
```

**Collect Eye Scan Results:**

After `wait_on_hw_sio_scan` completes, run via `vivado_execute(session_id=<id>, command=<tcl below>)`:

```tcl
report_property $scan
```

Key properties to extract:
- `OPEN_PERCENTAGE` — Overall eye opening (%)
- `HORIZONTAL_OPENING` — Horizontal width (steps)
- `HORIZONTAL_PERCENTAGE` — Horizontal opening (%)
- `VERTICAL_OPENING` — Vertical height (steps)
- `VERTICAL_PERCENTAGE` — Vertical opening (%)
- `OPEN_AREA` — Total open area
- `STATUS` — "Done" when complete
- `PROGRESS` — "100%" when complete

Scan parameters (from same `report_property` output):
- `DWELL` — Dwell mode (e.g., "BER")
- `DWELL_BER` — Target BER (e.g., "1e-5")
- `HORIZONTAL_RANGE` — Sweep range (e.g., "-0.500 UI to 0.500 UI")
- `HORIZONTAL_INCREMENT` — Step size
- `VERTICAL_RANGE` — Sweep range (e.g., "100%")
- `VERTICAL_INCREMENT` — Step size

**Generate report_data.json:**

Write structured JSON with all metrics. Use the canonical schema from REFERENCE.md ("Report JSON Schema" section).

**Converting Vivado metrics to canonical schema units:**
- `eye_width_ui`: Multiply `HORIZONTAL_PERCENTAGE` (as decimal) by the UI range from `HORIZONTAL_RANGE` (e.g., if HORIZONTAL_PERCENTAGE=71.43% and range is 1.0 UI, then eye_width_ui = 0.7143)
- `eye_height_percent`: Use `VERTICAL_PERCENTAGE` directly (e.g., 71.43 for 71.43%).

```json
{
  "schema_version": "hw-ibert-gt-debug/1.1.0",
  "timestamp": "<ISO8601 timestamp>",
  "device": {
    "part": "<part number>",
    "dna": "<device DNA>"
  },
  "ibert_cores": [
    {
      "name": "<IBERT_N>",
      "type": "GTY|GTYP|GTM",
      "gt_groups": <number>,
      "channels_per_group": <number>
    }
  ],
  "links": [
    {
      "name": "<link name>",
      "tx_channel": "CH0",
      "rx_channel": "CH0",
      "pattern": "PRBS 31",
      "loopback": "Near-End PMA",
      "ber": <actual BER>,
      "line_rate_gbps": <actual value>
    }
  ],
  "scans": [
    {
      "type": "eye_scan",
      "link": "<link name>",
      "horz_step": <steps>,
      "vert_step": <steps>,
      "target_ber": <BER>,
      "eye_width_ui": <HORIZONTAL_PERCENTAGE / 100 * horizontal_range_ui>,
      "eye_height_percent": <VERTICAL_PERCENTAGE>,
      "status": "complete"
    }
  ]
}
```

**Note**: This matches the schema defined in REFERENCE.md. Do NOT use alternative schemas with `metadata`, singular `link`/`eye_scan`, or `pass_fail` fields - those diverge from the canonical format.

**Generate REPORT.md:**

Write all requested output files with agent file tools before giving any chat summary. Use this human-readable markdown template:

```markdown
# IBERT GT Debug Report

**Generated**: <timestamp>
**Device**: <part> (<family>)
**Assessment**: PASS/FAIL

## Executive Summary

<Brief description of what was tested>

## Device Information

- **Part**: <part>
- **Family**: <family>
- **hw_server**: <url>
- **Target**: <target path>

## IBERT Cores Discovered

| Core Name | Display Name | Type | Quads |
|-----------|-------------|------|-------|
| <name> | <display name> | <type> | <quad list> |

## Link Configuration

**Link**: <full link path>
**Description**: <description>

### Settings
- **TX Pattern**: <pattern>
- **RX Pattern**: <pattern>
- **Loopback**: <mode>
- **Line Rate**: <rate> Gbps

### Status
- **RX Status**: <status>
- **BER**: <ber> (<BER check>)
- **Errors / bits received**: <error_count> / <bit_count>
- **TX Reset Done**: Yes|No
- **RX Reset Done**: Yes|No
- **RX PRBS Lock**: Locked|Not locked

## 2D Eye Scan Results

**Scan**: <scan name> (<description>)
**Status**: <status>
**Progress**: <progress>

### Eye Opening Metrics

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| Open Percentage | <val>% | ≥50% | PASS/FAIL ✓|✗ |
| Horizontal Opening (steps) | <val> | — | (informational) |
| Horizontal Percentage | <val>% | ≥50% | PASS/FAIL ✓|✗ |
| Vertical Opening (steps) | <val> | — | (informational) |
| Vertical Percentage | <val>% | ≥70% | PASS/FAIL ✓|✗ |
| Open Area | <val> | — | (informational) |

## Assessment

**Overall**: PASS/FAIL

<Summary of results with specific values>

## Commands Executed

<List of key Tcl commands run>

## Files Generated

- REPORT.md (this file)
- report_data.json (structured data)
```

**Assessment Logic:**

Apply resolution-independent percentage thresholds (aligned with REFERENCE.md):
- Open Percentage: ≥50% = PASS
- Horizontal Percentage: ≥50% = PASS  
- Vertical Percentage: ≥70% = PASS

Overall assessment:
- **PASS**: All percentage metrics meet their thresholds AND the BER check is PASS or NOT PROVEN
- **FAIL**: Any percentage metric fails its threshold OR the BER check is FAIL

#### Judging BER

With 0 errors the reported BER is only `1 / bits received`, so an error-free link reads above 1e-12 until 1e12 bits have arrived (40 s at 25 Gbps). Judge it with the link state, error count and bit count (chipscope-mcp `status`: `status`, `error_count`, `bit_count`; vivado-mcp Step 6: `RX_STATUS`, `RX_PRBS_ERR_CNT`, `RX_RECEIVED_BIT_COUNT.NORMALIZED`), from a read taken after the link reset that follows the last pattern or loopback change.

| Reading (use the first row that matches) | BER check |
|---------|-----------|
| Link down (`No link`; BER reads 1.0) | FAIL |
| 0 errors, bit count ≥ 1e12 | PASS: "no errors in N bits (BER < 1/N)" |
| 0 errors, bit count < 1e12 | NOT PROVEN, never FAIL: wait once for `(1e12 - bits) / (line rate in bit/s)` seconds (25 Gbps = 2.5e10 bit/s), read once more without resetting, and judge that read. If the wait is over about two minutes, do not wait: keep NOT PROVEN and report "no errors in N bits (BER < 1/N)" |
| Errors > 0 | PASS if BER < 1e-12, otherwise FAIL |

**Note**: Use percentage metrics only. Raw step counts and open area depend on scan resolution and produce inconsistent assessments across different scan configurations.

**Note**: The canonical schema (REFERENCE.md) does not include overall assessment fields. If assessment is needed, document it in REPORT.md only.

**chipscope-mcp Path**

When chipscope-mcp is available (or vivado-mcp is not), the report generation workflow is similar but metrics come from chipscope tool outputs:

```
chipscope_ibert(action='status', ibert_name='<ibert_name>', link_name='<link_name>')  # Returns link status, BER, line rate
chipscope_ibert_eye_scan(...)     # Returns eye metrics directly
```

Extract metrics from tool responses and populate the JSON/markdown schema. **Both backends return percentage-based metrics:**
- Use `eye_height_percent` field (from VERTICAL_PERCENTAGE in vivado-mcp or vertical percentage in chipscope-mcp output)
- Use percentage-based assessment thresholds: HORIZONTAL_PERCENTAGE ≥ 50%, VERTICAL_PERCENTAGE ≥ 70%, OPEN_PERCENTAGE ≥ 50%, and the BER check from [Judging BER](#judging-ber)

Output directory: `vivado_agentic_ai_reports/hw-ibert-gt-debug/`

| File | Format | Content |
|------|--------|---------|
| `report_data.json` | JSON | Link config, scan parameters, eye metrics |
| `REPORT.md` | Markdown | Summary with eye opening assessment |
| `eye_scan.png` | PNG | Eye diagram (if `save_plot` used) |
| `eye_scan_data.csv` | CSV | Raw scan data (if `export_path` used) |

---

## Workflow B: YK Scan (GTM, 56+ Gbps)

**Proven end-to-end on real hardware (VPK120, 2026-07-01).** GTM transceivers exist only on Versal Premium/HBM-series devices (e.g. VPK120/180). `chipscope_ibert_yk_scan` was run against a real GTM link on VPK120 (Quad_204 CH_0, 56.4 Gbps, Near-End PMA, PRBS 31) and returned SNR 16.75 dB. Workflow A (GTY/GTYP eye scan) has been proven on real hardware (VCK190).

**GTM YK scan is chipscope-mcp-only — confirmed at all three Vivado Hardware Manager Tcl layers, not just documented.** `yk`, `1d_bathtub`, and `2d_full_eye` were each tried against a real GTM link on VPK120 and every one fails, at a different layer:

| `scan_type` | `create_hw_sio_scan` (object creation) | `run_hw_sio_scan` (hardware execution) |
|---|---|---|
| `yk` | **Rejected** — `ERROR: [Labtools 27-1835] Unknown Scan Type: yk.` (not a recognized enum value) | n/a |
| `1d_bathtub` | **Rejected** — `ERROR: [Labtools 27-3784] Performing 1D Bathtub scan is not supported for <RX>!` (valid enum, but rejected per-object; also rejected on GTYP on this board) | n/a |
| `2d_full_eye` | **Accepted** — returns a scan object cleanly (object creation doesn't validate GT capability) | **Rejected** — `ERROR: [Xicom 50-230] ChipScope Server: ChipScope Service IBERT start_eye_scan: OD test Eye scan is not supported!` |

Do not treat `1d_bathtub` and `yk` as interchangeable or as testing the same thing — they fail for different reasons (unrecognized type vs. unsupported-for-this-GT), and neither is equivalent to a YK scan. `2d_full_eye` is the most dangerous case: it looks like it worked (a named `SCAN_N` object comes back) but produces zero real data once run — always check `run_hw_sio_scan`'s return, not just `create_hw_sio_scan`'s, before trusting a Vivado-mcp-path GTM scan result.

### Steps 1-5: Same as Workflow A

**CRITICAL: GTM requires chipscope-mcp for YK scans.** If using vivado-mcp for Steps 1-5 (link setup), you CANNOT use `chipscope_ibert_yk_scan` in Step 6 - the ChipScoPy link object does not exist. For GTM workflows requiring YK scans, use chipscope-mcp path for ALL steps.

Discover, create link, configure, check status. Use a **GTM** IBERT core.

### Step 6: Run YK Scan

```
chipscope_ibert_yk_scan(
    link_name='<link_name>',
    scan_duration_seconds=10.0)
```

Returns inline PNG waveform + SNR statistics.

**With export (MUST be done at scan time — data is transient):**
```
chipscope_ibert_yk_scan(
    link_name='<link_name>',
    scan_duration_seconds=10.0,
    export_path='yk_scan_data.csv')
```

### Step 7: Analyze SNR Results

- **SNR** — signal-to-noise ratio in dB; higher = better
- **Slicer waveform** — shows signal quality at receiver

---

## TX/RX Tuning Workflow

For link optimization, sweep equalization parameters:

### Read Current Settings
```
chipscope_ibert(action='link_properties', link_name='<link_name>')
```

Returns current values and valid options for TX/RX parameters.

### Adjust TX Emphasis
```
chipscope_ibert(action='configure_link',
    link_name='<link_name>',
    tx_pre_cursor=<value>,
    tx_post_cursor=<value>,
    tx_swing=<value>)
```

### Sweep and Compare

1. Capture baseline eye scan
2. Adjust one parameter
3. Re-run eye scan
4. Compare eye width/height
5. Repeat until optimal

---

## Multi-Lane Analysis

For multi-lane links (PCIe x4/x8/x16, multi-lane Ethernet):

1. Create links on each lane
2. Run eye scan on each lane
3. Compare eye openings across lanes
4. Identify weak lanes (smallest eye)
5. Focus tuning on weak lanes

---

## Design-Specific Rules

| Rule | Wrong | Correct |
|------|-------|---------|
| Link status | "link is up" | "Link_0: PLL locked, 0 errors in 2.26e12 bits (BER < 4.4e-13), line rate 25.78125 Gbps" |
| Eye opening | "eye looks good" | "Eye width: 0.42 UI, height: 72% at BER 1e-5" |
| Transceiver | "found IBERT" | "IBERT Versal GTM: 2 GT groups, 4 channels per group, 56.42 Gbps" |
| Scan type | "running scan" | "Running 2D eye scan: horz_step=10, vert_step=10, target_ber=1e-5" |

---

## Error Handling

| Error | Symptom | Action |
|-------|---------|--------|
| No IBERT cores | list returns empty | Design has no IBERT IP. Check bitstream. |
| Wrong scan type | eye_scan on GTM | GTM uses YK scan. Use `chipscope_ibert_yk_scan`. |
| Vivado-mcp on GTM | `create_hw_sio_scan ... yk` errors `Unknown Scan Type: yk`; `1d_bathtub` errors `27-3784 not supported`; `2d_full_eye` creates a scan object but `run_hw_sio_scan` errors `Xicom 50-230 ...Eye scan is not supported!` | None of Vivado Hardware Manager's Tcl scan types work on GTM at any layer (parse, per-object, or execution). Route to chipscope-mcp's `chipscope_ibert_yk_scan` for any GTM link — do not fall back to vivado-mcp for this one capability. |
| No link created | scan fails | Create link first with `chipscope_ibert(action='create_link')` |
| Wrong IBERT core | `ELEMENT_NOT_FOUND: GT group '<name>' not found`, listing another core's GT groups | The call went to the first core. Pass the `ibert_name` of the core that owns the GT group. |
| PLL not locked | status shows unlocked | Check: reference clock? loopback mode? board connections? |
| Scan timeout | max_wait exceeded | Increase `max_wait_minutes`. Check link stability. |
| High BER | status shows `error_count` > 0 | Tune TX emphasis, check SI, verify pattern match. BER above 1e-12 with 0 errors is not high BER: see [Judging BER](#judging-ber). |
| Export after YK | no data available | YK data is transient. Must export during scan with `export_path`. |

---

## Examples

**"Run an eye scan on GT channel 0"** → List IBERT → Create loopback link on CH0 → Configure PRBS 31 → Run eye scan → Report eye width/height.

**"Show me a YK scan on GTM"** → Find GTM IBERT core → Create link → Configure PRBS 31 + PMA loopback → `chipscope_ibert_yk_scan(scan_duration_seconds=10)` → Report SNR.

**"Compare eye before and after tuning"** → Baseline eye scan → Adjust TX pre-cursor → Re-run eye scan → Report: "Eye width improved from 0.35 UI to 0.42 UI (+20%)".

**"Check all IBERT links"** → `chipscope_ibert(action='status')` → Report BER, lock status, line rate for each link.

---

## Integration

**Upstream:** `chipscope_session` (connection), `chipscope_device` (programming)
**Complementary:** `hw-pcie-link-debug` (PCIe uses GT transceivers — eye scan validates SI), `hw-sysmon` (supply voltage affects eye quality)
**Downstream:** Tuning recommendations → design constraint changes or board-level SI fixes

---

## Metadata

**Keywords:** IBERT, eye scan, YK scan, GTY, GTYP, GTM, BER, SNR, serial link, transceiver, loopback, PRBS, equalization, eye diagram, margin
**Confidence Threshold:** 0.80
