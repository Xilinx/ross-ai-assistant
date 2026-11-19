# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# fix.tcl — PCIE Clock Root fix (sources cached XDC from parent run)
#
# NOTE: as of this writing, detect.tcl for this skill applies
# USER_CLOCK_ROOT as a live, in-session design mutation only -- it never
# writes pcie_clock_root_fix.xdc (see its own result.json "design_changes"
# text: "... live design mutation, no XDC file"). That means the
# [file exists $_xdc] check below is always false in current production
# use, making this fix.tcl permanently a no-op -- flagged here rather than
# silently reworked, since deciding whether PCIE clock root should also
# persist an XDC for future-run reapplication (vs. staying intentionally
# live-session-only) is a design decision, not a mechanical bug fix.
if {[info exists env(PARENT_RUN_DIR)]} {
    set _parent_dir $env(PARENT_RUN_DIR)
} else {
    set _parent_dir [pwd]
}
set _xdc [file join $_parent_dir "pcie_clock_root_fix.xdc"]
if {[file exists $_xdc]} {
    if {[catch {source $_xdc} _err]} {
        puts "\[FIX\] WARNING: could not apply PCIE Clock Root constraints from $_xdc ($_err)"
    } else {
        puts "\[FIX\] PCIE Clock Root constraints applied from $_xdc"
    }
} else {
    puts "\[FIX\] No PCIE Clock Root XDC found — skipping"
}
