<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# DDRMC Debug — Reference

## Tool Signatures (pinned to chipscope-mcp v1.5.0)

### chipscope_ddr actions

| Action | Key Parameters | Returns |
|--------|---------------|---------|
| `list` | — | Controller names + `enabled` + `calibration_status` (auto-scans) |
| `status` | `ddr_name` | Basic DDR status |
| `calibration` | `ddr_name` | Overall calibration PASS/FAIL |
| `health` | `ddr_name` | Health + diagnostics |
| `report` | `ddr_name` | Status + calibration + config in one call |
| `config` | `ddr_name` | Memory interface, width, ranks, frequency, MC location |
| `stages` | `ddr_name` | Per-stage PASS/FAIL, **stage names dynamic** |
| `margins` | `ddr_name` | Per byte/nibble margins as `[taps, pS]` |
| `properties` | `ddr_name`, `property_group` | Raw DDR property group(s) as JSON |
| `eye_scan` | `ddr_name`, `mode`, `steps`, `unit_index` | Run 2D eye scan |
| `eye_scan_data` | `ddr_name`, `output_format`, `export_path` | Get/export scan results |
| `eye_scan_defaults` | `ddr_name`, `mode` | Default VRef + scan settings |

### Eye-scan parameters (`chipscope_ddr` eye actions and `chipscope_ddr_eye_scan`)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ddr_name` | string | first | Controller name |
| `mode` | string | — | `'read'` or `'write'` |
| `pattern` | string | — | `'simple'`, `'complex'`, `'prbs23'`, `'prbs10'`, `'prbs7'` |
| `rank` | int | 0 | Memory rank 0-3 |
| `vref_min_pct` / `vref_max_pct` | float | auto | VRef sweep bounds (percent) |
| `steps` | int | 15 | VRef steps (1-1024) |
| `unit_index` | int | 0 | Nibble (read) or byte (write) index |
| `max_wait_minutes` | float | 5.0 | Scan timeout |
| `edge` (eye_scan tool) | string | `'rising'` | Read mode: `'rising'`/`'falling'` |
| `display` (eye_scan tool) | string | auto | `'ascii'`/`'chat'`/`'app'`/`'both'` |

`chipscope_ddr(action='list')` auto-runs the DDR core scan. A zero result is only
authoritative after a scan has completed (or `debug_ready: true`).

---

## `report_hw_ddrmc` Output Structure

`report_hw_ddrmc -return_string <ddrmc>` (Vivado path) returns text with these
blocks. Parse them; do not rely on fixed line counts.

1. **DDRMC Status** — `Calibration Status:` (PASS/FAIL), `Overall Health:`,
   `DQS Gate Status:`, `Message:`.
2. **Status Registers** — `DDRMC ISR Table` and `UB ISR Table` (all should read 0
   on a healthy link).
3. **Memory Configuration** — `Memory Interface` (e.g. DDR4), `Device Type`,
   `Slots`, `Ranks`, `Data Width`, `Bytes`, `Nibbles`, `Bits per Byte`,
   `Memory Frequency(F0)`, `MC Location`.
4. **Calibration Stages Information** — one `CAL_STAGE.<nn>_<name>: <Pass|Fail>`
   line per stage that ran. **Dynamic** (see below).
5. **Calibration Window Margin Analysis** — sections per Read/Write × Simple/Complex
   pattern (read also × rising/falling edge). Each line:
   `Byte <b> Nibble <n> - Left Margin: <pS> (<taps>) Center Point: <pS> (<taps>) Right Margin: <pS> (<taps>)`
   (write margins are per byte, no nibble).

The same values are also available as `get_property` fields on the hw_ddrmc object
(e.g. `CAL_STATUS`, `CAL_STAGE.*`, `F0_*_MARGIN_BYTE*`), which is a more structured
read when parsing text is undesirable.

---

## Calibration Stages (dynamic — do NOT hardcode)

Stage names and count are **memory-type and frequency dependent**. Read whatever
`CAL_STAGE.*` lines / `stages` keys are present. Examples:

- **DDR4** (observed, vck190, 1600 MHz) — 12 stages:
  `01_F0_PHY_BISC`, `02_F0_MEM_INIT`, `03_F0_DQS_GATE_CAL`, `04_F0_WRITE_LEVELING`,
  `05_F0_READ_DQ_CAL`, `06_F0_WRITE_DQ_DBI_CAL`, `07_F0_WRITE_LATENCY_CAL`,
  `08_F0_READ_DQ_DBI_CAL_COMPLEX`, `09_F0_WRITE_DQ_DBI_CAL_COMPLEX`,
  `10_EN_VT_TRACK`, `11_READ_DQS_TRACK`, `12_CAL_DONE`.
- **DDR5 / LPDDR5** — a different (often longer) set, per-frequency
  (`CAL_STAGE.NN_F0_*`, `..._F1_*`). Never assume the DDR4 list applies.

Overall pass/fail: prefer the flat `CAL_STATUS` property (`PASS`/`FAIL`); if
absent, a controller passes only when every present stage reads Pass.

### Calibration Failure Decision Tree

Map the **first non-Pass** stage (by keyword in its name) to likely causes:

| Stage keyword | Common causes | Debug steps |
|---------------|---------------|-------------|
| `PHY_BISC` / `MEM_INIT` | Supply/PHY init, DIMM seating | Check VCCINT/VCC_DDR (sysmon), reseat memory |
| `DQS_GATE` | DQS preamble, DQS-vs-CK trace length | Check `tDQSCK`, board DQS/CK matching |
| `WRITE_LEVELING` | CK-DQS alignment, rank select | Check CK/CK# routing, `tDQSS` |
| `READ_DQ` / `READ_DQS` | Read SI, ISI, jitter | Read eye scan on failing nibble, per-bit margins |
| `WRITE_DQ` / `WRITE_LATENCY` | Write path SI, VRef | Write eye scan, check VRef |
| `DBI` | DM/DBI setting mismatch | Confirm DM/DBI config vs board |

---

## Margin Interpretation

### Fields (per byte or nibble)

| Field | Meaning |
|-------|---------|
| Left / Right Margin | Window edge distance from center, in pS and taps |
| Center Point | Calibrated center, in pS and taps |
| Total window | Left + Right (compute if not given) |

### Assessment — heuristic, NOT absolute

Tap counts are **frequency-dependent** (a tap is a fixed delay; its fraction of
the UI grows as frequency rises), so fixed tap thresholds do not transfer across
speeds. Assess **relatively** within the same interface/frequency:

- **Weakest lane** — smallest total window among bytes/nibbles is the first
  failure risk under voltage/temperature drift.
- **Asymmetry** — large left/right imbalance (e.g. ratio outside ~0.5-2.0)
  suggests an SI/centering issue.
- **Zero margin** — the lane did not calibrate.

Only use absolute tap thresholds if the design's frequency-specific budget is
known; otherwise report values and relative ranking, not a fixed grade.

---

## Eye Scan Interpretation (ChipScoPy path)

- **Read eye:** X = timing delay (taps), Y = VRef (%), color = pass/fail per
  point; largest open region = available margin. `unit_index` selects the nibble.
- **Write eye:** same axes for the write path; `unit_index` selects the byte.
- Calibration must report PASS before an eye scan is meaningful.

| Scenario | steps | pattern | max_wait |
|----------|-------|---------|----------|
| Quick | 10 | simple | 3 min |
| Standard | 15 | prbs23 | 5 min |
| Thorough | 25 | prbs23 | 15 min |

The Vivado path has no Vref-swept eye; it provides the calibration-window margins
(`ddr_margins.csv` + `ddr_margin_window.png`) instead.

---

## Report JSON Schema

```json
{
  "schema_version": "hw-ddrmc-debug/1.1.0",
  "timestamp": "2026-08-26T12:00:00Z",
  "device": { "part": "xcvc1902", "dna": "0x...", "tool_version": "2026.2" },
  "backend": "vivado-mcp | chipscope-mcp",
  "controllers": [
    {
      "name": "DDRMC_1",
      "enabled": true,
      "config": {
        "memory_interface": "DDR4",
        "device_type": "UDIMM",
        "ranks": 1,
        "data_width": 64,
        "bytes": 8,
        "nibbles": 16,
        "frequency_mhz": 1600,
        "mc_location": "x0y0"
      },
      "calibration": {
        "overall": "PASS",
        "stages": [
          { "name": "CAL_STAGE.01_F0_PHY_BISC", "status": "Pass" },
          { "name": "CAL_STAGE.12_CAL_DONE", "status": "Pass" }
        ]
      },
      "margins": {
        "units": [
          { "mode": "f0_wr_simp", "index": 0, "kind": "byte",
            "left_taps": 83, "left_ps": 164, "right_taps": 70, "right_ps": 138 }
        ],
        "weakest": { "mode": "f0_wr_comp", "index": 7, "kind": "byte", "total_taps": 134 }
      }
    }
  ],
  "eye_scans": [],
  "recommendations": []
}
```

Notes: `stages[]` carries whatever stage names the hardware reported (dynamic).
`margins.units[]` records both taps and pS; a null edge (e.g. chipscope left-only)
is recorded as null, never 0.

## Report Template (REPORT.md)

```markdown
# DDRMC Debug Report

**Device:** <part> | **Backend:** <vivado-mcp|chipscope-mcp> | **Tool:** <version> | **Date:** <timestamp>

## Controller Summary

| Controller | Enabled | Interface | Config | Freq | Cal Status |
|-----------|---------|-----------|--------|------|------------|
| DDRMC_1 | yes | DDR4 | x64, 1 rank | 1600 MHz | PASS |

## Calibration Stages — DDRMC_1

| Stage | Status |
|-------|--------|
| CAL_STAGE.01_F0_PHY_BISC | Pass |
| ... | ... |

## Per-Byte/Nibble Margins — DDRMC_1

| Unit | Mode | Left (taps/pS) | Right (taps/pS) | Note |
|------|------|----------------|-----------------|------|
| Byte 0 | f0_wr_simp | 83 / 164 | 70 / 138 | |

## Recommendations

1. <recommendation>
```

---

## Version Compatibility

| Component | Minimum | Notes |
|-----------|---------|-------|
| Vivado (primary path) | 2026.1 | `version -short` |
| ChipScoPy / chipscope-mcp (fallback) | 2026.1 | `chipscope_version` |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No DDR cores after scan | Design has no DDRMC | Verify bitstream includes a DDRMC |
| Controller disabled / no `CAL_STATUS` | Present but no memory attached | Report disabled, not FAIL |
| All stages FAIL | No memory / not seated | Check DIMM/component seating |
| Eye scan empty | Calibration incomplete | Fix calibration first |
| Margins vary run-to-run | Temperature/voltage drift | Compare at controlled conditions |
