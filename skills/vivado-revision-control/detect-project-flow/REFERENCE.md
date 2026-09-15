<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

---
name: detect-project-flow
description: Step 1 — detects Vivado project flow type (Standard, DFX, Segmented Config, IPI BDC).
---

# Detect Project Flow (Step 1)

**Source file:** `helper-procedures/helper_scripts.tcl`
**Proc name:** `detect_project_flow`

## Procedure

```tcl
set flow_info [detect_project_flow]
```

Returns a dictionary with these keys:

| Key | Type | Description |
|-----|------|-------------|
| `project_name` | string | Project name |
| `device` | string | Part number |
| `flow_type` | string | "Standard", "DFX (Partial Reconfiguration)", "Segmented Configuration", "DFX + Segmented Configuration", with optional "+ IPI BDC" |
| `is_dfx` | boolean | 1 if PR_FLOW enabled or reconfigurable cells found |
| `is_segmented_config` | boolean | 1 if SEGMENTED_CONFIGURATION property set |
| `is_versal` | boolean | 1 if Versal device |
| `is_ipi_bdc` | boolean | 1 if Block Design Containers detected |
| `bdc_count` | integer | Number of BDC instances |
| `bd_file_count` | integer | Number of Block Design files |
| `pblock_count` | integer | Number of Pblocks |

## Detection Logic

**DFX detection** checks (any one sufficient):
- `PR_FLOW` project property is true
- Cells with `HD.RECONFIGURABLE == true` exist AND Pblocks exist

**Segmented Config detection**:
- `SEGMENTED_CONFIGURATION` project property is true
- Versal Gen2 devices (xcve2*, xcvp2*, xcvm2*) have implicit enablement

**IPI BDC detection**: Opens each Block Design and checks cells for
`CONFIG.ACTIVE_SYNTH_BD` or `CONFIG.LIST_SYNTH_BD` properties — these indicate
a Block Design Container (BD instantiated inside another BD).

## Edge Cases

- **No synthesized netlist needed** — detection works on the source project
- **Versal Gen2 implicit Segmented Config** — Gen2 devices always have it,
  even without the explicit property. The procedure does NOT set this
  implicitly; it only checks the property. You may want to note Gen2 status
  in your documentation.
- **Empty project** — returns "Standard" with zeroed counts; proceed with caution
- **`get_cells` returns empty pre-synthesis** — DFX detection falls back to
  `PR_FLOW` property alone

## When to Use vivado_doc_search

If the project uses an unfamiliar device family, use `vivado_doc_search` to
look up the part number and confirm whether it's Versal Gen1 or Gen2.
