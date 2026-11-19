<!--
  Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
  SPDX-License-Identifier: MIT
-->

# Using the baseline spreadsheets

`mac8_int8xint8.xlsx` and `mac16_int8xint8.xlsx` next to this skill's
`SKILL.md` are a **human** design surface: type parameters in, watch the lane
map update.

To get a lane map for new parameters, measure it with `probe/`; read
programmatically, the workbooks show only the maps they were last saved with
(see the recalculation caveat below).

Contents:
- [Which workbook](#which-workbook)
- [Cell map](#cell-map)
- [Reading the tables](#reading-the-tables)
- [Programmatic access](#programmatic-access)
- [Saved parameters](#saved-parameters)
- [Address wraparound in the `idx` column](#address-wraparound-in-the-idx-column)

## Which workbook

| Workbook | Covers |
|---|---|
| `mac8_int8xint8.xlsx` | `mul8` / `mac8` / `msc8` — 8 lanes x 16 columns |
| `mac16_int8xint8.xlsx` | `mul16` / `mac16` / `msc16` — 16 lanes x 8 columns |

Sheets in each: `zbuff_32`, `xbuff_64`, `xbuff_128`, `Legal Notice`. Pick the
x sheet matching your x-buffer width; the z sheet is shared.

## Cell map

Parameters, in `start` / `offsets` / `step` / `square` order:

| Workbook | Sheet | Cells |
|---|---|---|
| mac16 | `xbuff_64` | `M9` `M10` `M11` `M12` |
| mac16 | `xbuff_128` | `M13` `M14` `M15` `M16` |
| mac16 | `zbuff_32` | `N8` `N9` `N10` `N11` |
| mac8 | `xbuff_64` | `T9` `T10` `T11` `T12` |
| mac8 | `xbuff_128` | `T13` `T14` `T15` `T16` |
| mac8 | `zbuff_32` | `U9` `U10` `U11` `U12` |

Post-square lane map tables (the ones the hardware actually computes):

| Workbook | Sheet | Range | Shape |
|---|---|---|---|
| mac16 | `xbuff_64` | `P11:W26` | 16 lanes x 8 cols |
| mac16 | `xbuff_128` | `P15:W30` | 16 x 8 |
| mac16 | `zbuff_32` | `Q11:X26` | 16 x 8 |
| mac8 | `xbuff_64` | `W11:AL18` | 8 lanes x 16 cols |
| mac8 | `xbuff_128` | `W15:AL22` | 8 x 16 |
| mac8 | `zbuff_32` | `X11:AM18` | 8 x 16 |

These ranges are discoverable rather than magic: each sheet carries a
`ROWS(UNIQUE(TOCOL(<range>)))` sanity cell whose argument is the table.

## Reading the tables

Each sheet shows two side-by-side tables. The **left** is the lane map before
the square permutation; the **right** is after. Always design against the
right-hand table. Underneath sits a helper block whose columns
(`i, r, c, rx, rr, offset, step, idx, idx_sq`) compute the mapping step by step
— useful when a lane reads something unexpected and you want to see which term
caused it.

The helper formulas are transcribed in `references/addressing.md` (closed-form
mapping).

Recommended loop: set the parameter cells, read the after-square table, confirm
each lane reads the operands your algorithm needs, then transfer the values
into code and verify with `probe/` and `legality/`.

## Programmatic access

```bash
pip install openpyxl          # if not already present
python3 scripts/read_spreadsheet.py \
    mac16_int8xint8.xlsx mac8_int8xint8.xlsx --json sheet.json
```

### The recalculation caveat — read this before automating

openpyxl **cannot evaluate formulas**. It returns the value Excel cached when
the workbook was last saved. These workbooks additionally use Excel-365-only
dynamic array functions (`LET`, `TOCOL`, `UNIQUE`), which no Python formula
engine supports, and LibreOffice is not installed on the build host.

Therefore:

- Reading parameters and the tables **as last saved** is reliable.
- Writing new parameters with openpyxl and reading the tables back is **not**:
  the tables will still hold the previous result. A validation script built
  that way would compare stale numbers and pass regardless of correctness.

To sweep new parameter sets, either open the workbook in Excel and save it, or
skip the spreadsheet and measure directly with `probe/`. The probe is the
faster path and is ground truth anyway.

## Saved parameters

The workbooks are saved with these parameters (`start / offsets / step /
square`); their cached tables match x86sim measurements of the same
parameters. Re-run the comparison below after changing the workbooks.

| Workbook | Sheet | Parameters |
|---|---|---|
| mac16 | `xbuff_64` | `0 / 0x30303030 / 4 / 0x3210` |
| mac16 | `xbuff_128` | `0 / 0x06040200 / 4 / 0x1010` |
| mac16 | `zbuff_32` | `0 / 0x43322110 / 2 / 0x2110` |
| mac8 | `xbuff_64` | `0 / 0x30303030 / 4 / 0x3210` |
| mac8 | `xbuff_128` | `0 / 0x30303030 / 4 / 0x3210` |
| mac8 | `zbuff_32` | `0 / 0x43322110 / 2 / 0x2110` |

## Address wraparound in the `idx` column

The `idx` helper column on every sheet reduces the computed address modulo the
buffer length. Keep this when a sheet is copied or extended: without it, a
sliding window near the end of the buffer -- a normal FIR pattern -- displays
64, 65, ... instead of 0, 1, ..., and the `idx_sq` column fails its `INDEX`
lookup.

| Workbook | Sheet | First `idx` cell | Formula |
| --- | --- | --- | --- |
| `mac16_int8xint8.xlsx` | `xbuff_64` | `I31` | `=MOD($M$9 + G31 + H31, 64)` |
| `mac16_int8xint8.xlsx` | `xbuff_128` | `I35` | `=MOD($M$13 + G35 + H35, 128)` |
| `mac16_int8xint8.xlsx` | `zbuff_32` | `J43` | `=MOD($N$8+H43+I43,32)` |
| `mac8_int8xint8.xlsx` | `xbuff_64` | `I31` | `=MOD($T$9+G31+H31,64)` |
| `mac8_int8xint8.xlsx` | `xbuff_128` | `I35` | `=MOD($T$13+G35+H35,128)` |
| `mac8_int8xint8.xlsx` | `zbuff_32` | `J43` | `=MOD($U$9+H43+I43,32)` |

The formula fills down over the whole 128-row helper block, because the
`idx_sq` array formula indexes into all of it.

The hardware wraps addresses the same way (`references/addressing.md`).

After editing a workbook, re-run the comparison:

```bash
python3 scripts/read_spreadsheet.py mac16_int8xint8.xlsx mac8_int8xint8.xlsx --json sheet.json
make -C probe run
python3 scripts/compare_maps.py sheet.json probe/probe.log
```

## License

Copyright © 2025-2026 Advanced Micro Devices, Inc

SPDX-License-Identifier: MIT

[Terms and Conditions](https://www.amd.com/en/corporate/copyright)
