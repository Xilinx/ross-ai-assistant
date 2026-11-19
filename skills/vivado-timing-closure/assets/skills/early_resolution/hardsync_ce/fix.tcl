# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# fix.tcl — HARDSYNC CE fix (sources cached XDC from parent run)
if {[info exists env(PARENT_RUN_DIR)]} {
    set _parent_dir $env(PARENT_RUN_DIR)
} else {
    set _parent_dir [pwd]
}
set _xdc [file join $_parent_dir "hardsync_ce_fix.xdc"]
if {[file exists $_xdc]} {
    if {[catch {source $_xdc} _err]} {
        puts "\[FIX\] WARNING: could not apply HARDSYNC CE constraints from $_xdc ($_err)"
    } else {
        puts "\[FIX\] HARDSYNC CE constraints applied from $_xdc"
    }
} else {
    puts "\[FIX\] No HARDSYNC CE XDC found — skipping"
}
