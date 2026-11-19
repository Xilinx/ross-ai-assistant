# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Runs one ipcfg_op call against the fake helper, for tests/unit/test_ipcfg_op_tcl.py.
# Everything ipcfg_op::start does (finding Vivado's Python and the session log)
# needs Vivado, so the pipe is opened here and handed to the namespace;
# the protocol loop under test is the whole of proc ipcfg_op.
#
#   tclsh ipcfg_op_driver.tcl <skill> <budget ms> <list of op jsons> <helper command...>
#
# Each op json is one ipcfg_op call, in order; each prints its RC and RESULT.

set skill [lindex $argv 0]
set budget [lindex $argv 1]
set calls [lindex $argv 2]
set helper [lrange $argv 3 end]

source [file join $skill lib ipcfg_op.tcl]
set ::ipcfg_op::budget $budget

set ::ipcfg_op::pipe [open |$helper r+]
fconfigure $::ipcfg_op::pipe -buffering line -translation lf -encoding ascii -blocking 1

foreach json $calls {
    set rc [catch {ipcfg_op $json} result]
    puts "RC $rc"
    puts "RESULT [ipcfg_op::encode $result]"
}
