# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Timing / CEW Limitation Fix
# =============================================================================
# Sources the generated CEW fix XDC (USER_CLOCK_EXPANSION_WINDOW constraints)
# into the fix run before place_design.
#
# Expects: PARENT_RUN_DIR env var pointing to the run where detect.tcl was run.
# =============================================================================

if {[info exists env(PARENT_RUN_DIR)]} {
    set parent_dir $env(PARENT_RUN_DIR)
} else {
    set parent_dir [pwd]
}

# Look for the generated CEW fix XDC
set cew_xdc [file join $parent_dir "cew_timing_report.xdc"]

if {[file exists $cew_xdc]} {
    puts "\[FIX\] Sourcing CEW expansion XDC: $cew_xdc"
    source $cew_xdc
    puts "\[FIX\] CEW constraints applied."
} else {
    puts "\[FIX\] Warning: CEW fix XDC not found at $cew_xdc"
}
