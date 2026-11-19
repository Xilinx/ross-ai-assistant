# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Congestion — Apply RQS Suggestions
# =============================================================================
# Sources RQS suggestions specific to congestion-related fixes.
# =============================================================================

if {[info exists env(PARENT_RUN_DIR)]} {
    set parent_dir $env(PARENT_RUN_DIR)
} else {
    set parent_dir [pwd]
}

set rqs_file [file join $parent_dir "rqs_suggestions.rqs"]

if {![file exists $rqs_file]} {
    puts "\[RQS\] No RQS file found at $rqs_file — skipping."
    return
}

puts "\[RQS\] Applying congestion-related RQS suggestions from $rqs_file"
if {[catch {read_qor_suggestions $rqs_file} _rqs_err]} {
    puts "\[RQS\] WARNING: could not apply RQS suggestions from $rqs_file ($_rqs_err) -- continuing without them."
}
