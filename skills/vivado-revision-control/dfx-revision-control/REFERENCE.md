<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

---
name: dfx-revision-control
description: DFX (Partial Reconfiguration) specific guidance — RM ordering, pblocks, DCPs, pr_verify, Git LFS.
---

# DFX Revision Control

Read this when Step 1 detects `is_dfx == 1` in flow_info.

## Key Requirements

### 1. RM Sourcing Order in build.tcl

In DFX projects using Block Design Containers (BDC), the static design's BDCs
reference Reconfigurable Module (RM) Block Designs by name. The RMs must exist
before the static BD is sourced.

**Correct order in build.tcl Step 8:**
1. Source all RM BD scripts (e.g., rp1rm1.tcl, rp1rm2.tcl, rp2rm1.tcl)
2. Source the static design BD script (e.g., design_1.tcl)

If the order is wrong:
```
ERROR: [BD 41-1279] Block Container 'rp1_container' is referencing
an instance 'rp1rm1' that does not exist in the design.
```

After `generate_build_script` runs, review the BD sourcing section and manually
reorder if needed. A simple heuristic: files with "rm" in the name go first.

### 2. Pblocks Constraint File

Pblocks define the physical FPGA regions for reconfigurable partitions. The
`export_all_sources` procedure searches for XDC files containing "pblock" in
the name or content. Verify the exported pblocks.xdc contains:
- `create_pblock` commands
- `add_cells_to_pblock` assignments
- `resize_pblock` region definitions
- `IS_RECONFIGURABLE true` property

### 3. Locked Static DCP

After implementation, the locked static DCP is needed for `pr_verify` and
multi-RM implementation. Export it to Sources/Checkpoints/:
```tcl
write_checkpoint -force RevisionControl/Sources/Checkpoints/static_locked.dcp
```

### 4. Git LFS for DCPs

DCP files are large binaries (often 50-500MB). Track them with Git LFS:
```bash
git lfs install
git lfs track "*.dcp"
git add .gitattributes
```

### 5. pr_verify Validation

After rebuilding from build.tcl, validate the DFX configuration:
```tcl
pr_verify -full_check
```

The `helper_scripts.tcl` includes a `pr_verify` utility procedure that checks
PR_FLOW, reconfigurable cells, and Pblocks.

## When to Use vivado_doc_search

- For `pr_verify` command syntax and options
- For `HD.RECONFIGURABLE` property behavior
- For Block Design Container workflow documentation (UG994)
- For DFX tutorial and flow details (UG896)
