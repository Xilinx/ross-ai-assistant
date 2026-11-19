<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# IBERT Link Scan — Reference

## GT Transceiver Types

| Type | Device Families | Max Line Rate | Scan Type | MCP Tool |
|------|----------------|---------------|-----------|----------|
| **GTY** | UltraScale+, Versal | 32.75 Gbps | Eye Scan (BER) | `chipscope_ibert_eye_scan` |
| **GTYP** | Versal Premium | 32 Gbps | Eye Scan (BER) | `chipscope_ibert_eye_scan` |
| **GTM** | Versal Premium | 112 Gbps (PAM4) | YK Scan (SNR) — **chipscope-mcp only, no Vivado MCP fallback**[^1] | `chipscope_ibert_yk_scan` |
| **GTP** | 7 Series | 6.6 Gbps | Eye Scan (BER) | `chipscope_ibert_eye_scan` |
| **GTH** | 7 Series, UltraScale | 16.3 Gbps | Eye Scan (BER) | `chipscope_ibert_eye_scan` |

[^1]: Verified on real VPK120 hardware: `create_hw_sio_scan`'s `yk` type fails to parse, `1d_bathtub` is rejected per-object, and `2d_full_eye` is accepted at creation but fails when run (`Xicom 50-230 ...Eye scan is not supported!`).

## PRBS Patterns

| Pattern | Polynomial | Common Use |
|---------|-----------|------------|
| PRBS 7 | x^7 + x^6 + 1 | Quick test, short pattern |
| PRBS 9 | x^9 + x^5 + 1 | Short-medium pattern |
| PRBS 15 | x^15 + x^14 + 1 | Medium pattern |
| PRBS 23 | x^23 + x^18 + 1 | Long pattern, good ISI stress |
| PRBS 31 | x^31 + x^28 + 1 | Maximum stress, industry standard |

## Loopback Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `None` | No loopback — external path | Board/cable testing |
| `Near-End PMA` | TX PMA → RX PMA (analog) | Best for self-test, bypasses package/board |
| `Near-End PCS` | TX PCS → RX PCS (digital) | Digital path verification |
| `Far-End PMA` | RX PMA → TX PMA (analog) | Remote loopback |
| `Far-End PCS` | RX PCS → TX PCS (digital) | Remote digital loopback |

---

## chipscope_ibert Tool Reference

### Actions

| Action | Key Parameters | Description |
|--------|---------------|-------------|
| `list` | — | List IBERT cores with transceiver type |
| `analyze` | `ibert_name` | Discover GT groups and transceivers of one core |
| `create_link` | `ibert_name`, `gt_group`, `tx_channel`, `rx_channel` | Create TX/RX link pair |
| `delete_link` | `link_name` or `'all'` | Delete link(s) |
| `configure_link` | `link_name`, patterns, loopback, cursors | Set link configuration |
| `link_properties` | `link_name` | Read current values and valid options |
| `status` | `link_name` (optional) | Check BER, PLL lock, line rate |
| `reset` | `link_name` or core/group | Reset TX/RX counters |

`ibert_name` (on every `chipscope_ibert` action and both scan tools) selects the IBERT core; without it the first core is used.

### Eye Scan Parameters (chipscope_ibert_eye_scan)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `link_name` | string | required | Link from status |
| `horz_step` | int | 10 | Horizontal step 1-16 (smaller = finer, slower) |
| `vert_step` | int | 10 | Vertical step 1-16 (smaller = finer, slower) |
| `horz_range` | string | `-0.500 UI to 0.500 UI` | Horizontal sweep range |
| `vert_range` | string | `100%` | Vertical sweep range |
| `target_ber` | float | 1e-5 | Target BER for contour |
| `dwell_time` | string | null | Optional dwell per point |
| `max_wait_minutes` | float | 10.0 | Scan timeout |
| `export_path` | string | null | Save data to CSV/JSON |
| `save_plot` | string | null | Save plot to .svg/.png |
| `display` | string | `auto` | `ascii`/`chat`/`app`/`both` |

### YK Scan Parameters (chipscope_ibert_yk_scan)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `link_name` | string | required | GTM link from status |
| `scan_duration_seconds` | float | 10.0 | Slicer sample collection time |
| `export_path` | string | null | Save data (must be at scan time) |
| `export_format` | string | `csv` | `csv` or `json` |
| `display` | string | `auto` | `ascii`/`chat`/`app`/`both` |

---

## Eye Scan Metrics

| Metric | Unit | Pass Threshold |
|--------|------|----------------|
| Horizontal Opening | % | ≥ 50% |
| Vertical Opening | % | ≥ 70% |
| Open Percentage | % | ≥ 50% |
| BER at center | ratio | < 1e-12; with 0 errors, proven only after 1e12 bits (SKILL.md, Judging BER) |

**Note**: Both vivado-mcp and chipscope-mcp provide percentage-based eye metrics. vivado-mcp uses HORIZONTAL_PERCENTAGE, VERTICAL_PERCENTAGE, and OPEN_PERCENTAGE properties. chipscope-mcp provides equivalent percentage values in scan results.

## YK Scan Metrics

| Metric | Unit | Good | Marginal | Bad |
|--------|------|------|----------|-----|
| SNR | dB | > 25 | 15-25 | < 15 |

---

## TX Equalization Parameters

| Parameter | Description | Range |
|-----------|-------------|-------|
| TX Pre-Cursor | Pre-emphasis (leading edge) | 0-20 (device-dependent) |
| TX Post-Cursor | De-emphasis (trailing edge) | 0-20 (device-dependent) |
| TX Diff Swing | Differential voltage swing | Device-dependent |

Read valid ranges with:
```
chipscope_ibert(action='link_properties', link_name='<link>')
```

---

## Report JSON Schema

```json
{
  "schema_version": "hw-ibert-gt-debug/1.1.0",
  "timestamp": "2026-05-01T12:00:00Z",
  "device": { "part": "...", "dna": "..." },
  "ibert_cores": [
    { "name": "...", "type": "GTM", "gt_groups": 2, "channels_per_group": 4 }
  ],
  "links": [
    {
      "name": "Link_0",
      "tx_channel": "CH0",
      "rx_channel": "CH0",
      "pattern": "PRBS 31",
      "loopback": "Near-End PMA",
      "ber": 1.2e-12,
      "line_rate_gbps": 25.78125
    }
  ],
  "scans": [
    {
      "type": "eye_scan",
      "link": "Link_0",
      "horz_step": 10,
      "vert_step": 10,
      "target_ber": 1e-5,
      "eye_width_ui": 0.42,
      "eye_height_percent": 71.43,
      "status": "complete"
    }
  ]
}
```

---

## Report Template (REPORT.md)

```markdown
# IBERT Link Scan Report

**Device:** <part> | **Date:** <timestamp>

## IBERT Cores

| Core | Type | GT Groups | Channels |
|------|------|-----------|----------|
| <name> | <GTY/GTYP/GTM> | <n> | <n> |

## Link Status

| Link | TX | RX | Pattern | Loopback | PLL | BER | Line Rate |
|------|----|----|---------|----------|-----|-----|-----------|
| <name> | <ch> | <ch> | <pat> | <lb> | <locked?> | <ber> | <rate> Gbps |

## Eye Scan Results

| Link | Eye Width (UI) | Eye Height (mV) | BER Center | Assessment |
|------|---------------|-----------------|------------|------------|
| <link> | <width> | <height> | <ber> | <PASS/MARGINAL/FAIL> |

## Recommendations

- <recommendation>
```

---

## Protocol-Specific Eye Requirements

| Protocol | Min Eye Width (UI) | Min Eye Height (mV) | Notes |
|----------|--------------------|---------------------|-------|
| PCIe Gen3 | 0.3 | 100 | 8 GT/s NRZ |
| PCIe Gen4 | 0.3 | 80 | 16 GT/s NRZ |
| PCIe Gen5 | 0.3 | 60 | 32 GT/s NRZ |
| 10G Ethernet | 0.35 | 120 | 10.3125 Gbps |
| 25G Ethernet | 0.3 | 80 | 25.78125 Gbps |
| 100G Ethernet | 0.3 | 60 | 4x25.78125 Gbps |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No IBERT cores | Design lacks IBERT IP | Re-build with IBERT instantiated |
| PLL won't lock | Missing/wrong reference clock | Verify refclk source and frequency |
| High BER | SI issue, wrong equalization | Sweep TX emphasis, check board traces |
| Scan timeout | Very fine step size | Increase `max_wait_minutes` or use coarser steps |
| Closed eye | Severe SI problem | Check board, cables, connectors, impedance |
| YK scan no data | Wrong transceiver type | YK scan is GTM only. Use eye scan for GTY/GTYP. |
