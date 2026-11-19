# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# fix.tcl — GT-MRMAC Pre-Place fix (sources cached XDC from parent run)
if {[info exists env(PARENT_RUN_DIR)]} {
    set _parent_dir $env(PARENT_RUN_DIR)
} else {
    set _parent_dir [pwd]
}
set _xdc [file join $_parent_dir "gt_mrmac_preplace_fix.xdc"]
if {[file exists $_xdc]} {
    if {[catch {source $_xdc} _err]} {
        puts "\[FIX\] WARNING: could not apply GT-MRMAC LOC constraints from $_xdc ($_err)"
    } else {
        puts "\[FIX\] GT-MRMAC LOC constraints applied from $_xdc"
    }
} else {
    puts "\[FIX\] No GT-MRMAC XDC found — skipping"
}
