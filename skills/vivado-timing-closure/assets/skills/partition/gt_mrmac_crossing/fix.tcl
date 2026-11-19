# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Partition / GT-MRMAC SLR Crossing Fix
# =============================================================================
# Sources the generated XDC that relocates MRMAC into same SLR as connected GT.
# =============================================================================

if {[info exists env(PARENT_RUN_DIR)]} {
    set parent_dir $env(PARENT_RUN_DIR)
} else {
    set parent_dir [pwd]
}

set fix_xdc [file join $parent_dir "mrmac_slr_suggestions.xdc"]

if {[file exists $fix_xdc]} {
    puts "\[FIX\] Sourcing GT-MRMAC relocation XDC: $fix_xdc"
    if {[catch {source $fix_xdc} _err]} {
        puts "\[FIX\] WARNING: could not apply $fix_xdc ($_err)"
    }
} else {
    puts "\[FIX\] Warning: mrmac_slr_suggestions.xdc not found in $parent_dir"
}
