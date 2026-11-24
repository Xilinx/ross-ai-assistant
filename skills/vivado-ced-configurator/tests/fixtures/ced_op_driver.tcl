# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Runs one ced_op call against the fake helper, for tests/unit/test_ced_op_tcl.py.
# Everything ced_op::start does (finding Vivado's Python, the session log, the
# mirror) needs Vivado, so the pipe is opened here and handed to the namespace;
# the protocol loop under test is the whole of proc ced_op.
#
#   tclsh ced_op_driver.tcl <skill> <op json> <helper command...>

set skill [lindex $argv 0]
set json [lindex $argv 1]
set helper [lrange $argv 2 end]

source [file join $skill lib ced_op.tcl]

set ::ced_op::pipe [open |$helper r+]
fconfigure $::ced_op::pipe -buffering line -translation lf -encoding ascii -blocking 1

set rc [catch {ced_op $json} result]
puts "RC $rc"
puts "RESULT [ced_op::encode $result]"
