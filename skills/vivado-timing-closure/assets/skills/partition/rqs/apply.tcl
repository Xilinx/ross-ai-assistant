# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Partition — Apply RQS Suggestions
# =============================================================================
# Sources RQS suggestions specific to partition/SLR-related fixes.
# Expects RQS report to have been generated in the parent run.
# =============================================================================

if {[info exists env(PARENT_RUN_DIR)]} {
    set parent_dir $env(PARENT_RUN_DIR)
} else {
    set parent_dir [pwd]
}

# Look for RQS suggestion file
set rqs_file [file join $parent_dir "rqs_suggestions.rqs"]

if {![file exists $rqs_file]} {
    puts "\[RQS\] No RQS file found at $rqs_file — skipping."
    return
}

puts "\[RQS\] Applying partition-related RQS suggestions from $rqs_file"
if {[catch {read_qor_suggestions $rqs_file} _rqs_err]} {
    puts "\[RQS\] WARNING: could not apply RQS suggestions from $rqs_file ($_rqs_err) -- continuing without them."
}
