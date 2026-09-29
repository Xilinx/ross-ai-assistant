<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

---
name: segmented-config-revision-control
description: Versal Segmented Configuration guidance — property capture, dual PDI generation, NoC export.
---

# Segmented Configuration Revision Control

Read this when Step 1 detects `is_segmented_config == 1` in flow_info.

## Key Concept

Segmented Configuration generates dual PDI files on Versal devices:
- **boot.pdi** — Platform Management Controller (PMC) + PS boot
- **pld.pdi** — Programmable Logic design

This enables independent PL updates without full device reconfiguration.

## Versal Device Families

| Family | Part Pattern | Segmented Config | Explicit Property Needed? |
|--------|-------------|-----------------|--------------------------|
| Versal Premium | xcve* | Optional | Yes |
| Versal Prime | xcvp* | Optional | Yes |
| Versal AI Micro | xcvm* | Optional | Yes |
| Versal Premium Gen2 | xcve2* | Always ON | No (implicit) |
| Versal Prime Gen2 | xcvp2* | Always ON | No (implicit) |
| Versal AI Micro Gen2 | xcvm2* | Always ON | No (implicit) |

## Key Requirements

### 1. SEGMENTED_CONFIGURATION Property

This property MUST be captured in project_settings.tcl and restored during
rebuild. Without it, only a single PDI is generated.

The `capture_project_settings` procedure captures this automatically when set.
For Gen2 devices, the property may not be explicitly set but the feature is
implicitly enabled — document this in your project README.

### 2. NoC Solution Export (Optional)

For designs using Network-on-Chip, the `export_all_sources` procedure searches
for `.ncr` files in common locations. If none exist at export time, they can
be generated post-implementation:
```tcl
write_noc_solution -force RevisionControl/Sources/NoC/noc_solution.ncr
```

Most IPI designs embed NoC configuration in the CIPS IP, so separate .ncr
export is usually not required.

### 3. Verification After Rebuild

After recreating the project from build.tcl, verify:
1. `get_property SEGMENTED_CONFIGURATION [current_project]` returns true
2. After bitstream generation: both boot.pdi and pld.pdi are created

## When to Use vivado_doc_search

- For SEGMENTED_CONFIGURATION property details
- For `write_noc_solution` command syntax
- For dual PDI generation flow (UG1281)
- For Versal device family identification
