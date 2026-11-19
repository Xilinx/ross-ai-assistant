# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Clocking / MBUFG_GROUP Fix
# =============================================================================
# Sources the generated mbufg_group_fix.xdc BEFORE opt_design.
# This sets MBUFG_GROUP property so opt_design can merge BUFG_GTs into MBUFGTs
# with proper grouping (same clock source → same MBUFG).
#
# Expects: PARENT_RUN_DIR env var pointing to the run where detect.tcl was run.
# =============================================================================

if {[info exists env(PARENT_RUN_DIR)]} {
    set parent_dir $env(PARENT_RUN_DIR)
} else {
    set parent_dir [pwd]
}

set mbufg_xdc [file join $parent_dir "mbufg_group_fix.xdc"]

if {[file exists $mbufg_xdc]} {
    puts "\[FIX\] Sourcing MBUFG_GROUP XDC: $mbufg_xdc"
    source $mbufg_xdc
    puts "\[FIX\] MBUFG_GROUP constraints applied."
} else {
    puts "\[FIX\] Warning: MBUFG_GROUP XDC not found at $mbufg_xdc"
}
