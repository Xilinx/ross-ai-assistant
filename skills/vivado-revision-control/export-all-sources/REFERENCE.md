<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

---
name: export-all-sources
description: Step 3 — exports all project components (RTL, constraints, IP, BD, simulation, data) to an organized directory.
---

# Export All Sources (Step 3)

**Source file:** `helper-procedures/helper_scripts.tcl`
**Proc name:** `export_all_sources`

## Procedure

```tcl
export_all_sources "RevisionControl" $flow_info
```

**Parameters:**
- `export_dir` (string) — output directory, e.g. "RevisionControl"
- `flow_info` (dict, optional) — output from Step 1. Enables DFX and Segmented Config-specific exports. If omitted, assumes Standard project.

**Returns:** dictionary of export counts (rtl_files, constraint_files, ip_files, bd_files, sim_files, data_files, hook_scripts, dfx_dcp_files, pblock_files, noc_files).

## What Gets Exported

| Category | Source | Destination | Method |
|----------|--------|-------------|--------|
| RTL | Verilog/SV/VHDL/VHDL2019/VH/SVH in .srcs/ | Sources/RTL/ | file copy |
| Constraints | XDC in .srcs/ (not _ooc.xdc) | Sources/Constraints/ | file copy |
| Standalone IP | IPs NOT inside Block Designs | Sources/IP/ | `write_ip_tcl` |
| Block Designs | User-created BDs in .srcs/ | Sources/BD/ | `write_bd_tcl` |
| Simulation | Files from sim filesets | Sources/Simulation/ | file copy |
| Data | .mem, .mif, .coe, .hex, .elf in project dir | Sources/Data/ | file copy |
| Hook Scripts | Tcl pre/post hooks on synth/impl runs | Sources/Scripts/ | file copy |
| Pblocks (DFX) | XDC containing pblock defs | Pblocks/ | file copy |
| DCPs (DFX) | Locked static checkpoints | Checkpoints/ | file copy |
| NoC (Seg.Config) | .ncr solution files | NoC/ | file copy |

## Critical Filtering Rules

**Skip generated files:** Any file with `IS_GENERATED == true` or in a `.gen/` directory is excluded. These are auto-produced by Vivado and should not be version-controlled.

**Skip BD-embedded IPs:** IPs that are part of a Block Design (`IS_BD_CONTEXT == true`) are NOT exported via `write_ip_tcl` — they're already captured by `write_bd_tcl`. Attempting to export them separately produces warnings and redundant scripts.

**Skip auto-generated BDs:** BDs in `.gen/` or outside `.srcs/` are excluded. Only user-created BDs from the project's `.srcs/` directory are exported.

**generate wrapper for BD:** If BD wrapper is created in `.gen/`. Check for .v or .vhd with name `bd_name_wrapper`. If `bd_name_wrapper` is added to the top hierarchy. If above both are true, Then generate flag bd_name_wrapper_flag = 1. repeat the same for all the User-created BDs in .srcs
## Edge Cases

- **Encrypted IP** — `write_ip_tcl` may fail; use Remote strategy instead
- **Flat RTL hierarchy** — all files land in Sources/RTL/ regardless of original subfolder structure
- **Multiple constraint filesets** — all XDC files from all constraint filesets are exported
- **DFX without checkpoints** — if no locked DCPs exist yet, a note is printed; user must export after implementation

## When to Use vivado_doc_search

- If `write_ip_tcl` or `write_bd_tcl` produces unexpected output, search for their documentation
- If unsure about IP file types or BD properties, search for `get_property IS_BD_CONTEXT` or `get_ips`
