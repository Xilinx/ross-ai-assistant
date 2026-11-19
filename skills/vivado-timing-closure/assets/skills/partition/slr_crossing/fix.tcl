# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Partition / SLR Crossing Fix
# =============================================================================
# Sourced at the beginning of a Stage 2 run to apply SLR crossing fixes.
# Expects the generated XDC from detect.tcl to be available.
#
# Usage: source this file in TCL with RUN_DIR and PARENT_RUN_DIR set.
# =============================================================================

if {[info exists env(PARENT_RUN_DIR)]} {
    set parent_dir $env(PARENT_RUN_DIR)
} else {
    set parent_dir [pwd]
}

# Look for the generated fix XDC
set fix_xdc [file join $parent_dir "partition_slr_crossing_fix.xdc"]
set slr_pblock_xdc [file join $parent_dir "suggested_slr_pblock_aware.xdc"]

if {[file exists $slr_pblock_xdc]} {
    puts "\[FIX\] Sourcing SLR pblock-aware XDC: $slr_pblock_xdc"
    if {[catch {read_xdc $slr_pblock_xdc} _err]} {
        puts "\[FIX\] WARNING: could not apply $slr_pblock_xdc ($_err)"
    }
} elseif {[file exists $fix_xdc]} {
    puts "\[FIX\] Sourcing SLR crossing fix XDC: $fix_xdc"
    if {[catch {read_xdc $fix_xdc} _err]} {
        puts "\[FIX\] WARNING: could not apply $fix_xdc ($_err)"
    }
} else {
    puts "\[FIX\] Warning: No partition fix XDC found in $parent_dir"
}
