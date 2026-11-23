# SPDX-License-Identifier: MIT
# List program_flash -target_name values and JTAG clock rates on a hw_server.
# Usage: xsdb flash_targets.tcl SERVER     (SERVER: local or tcp:<host>:<port>)
# Read-only: no reset, halt or memory access.

proc main {server} {
    if {$server eq "local"} { connect } else { connect -url $server }
    try {
        # Target discovery is asynchronous right after connecting.
        after 1000
        foreach t [jtag targets -target-properties] {
            set ctx [dict get $t target_ctx]
            if {[dict get $t level] == 0} {
                jtag targets -set [dict get $t node_id]
                set rates [lsort -integer [jtag frequency -list]]
                puts "CABLE $ctx | current_hz=[jtag frequency] | max_hz=[lindex $rates end] | supported_hz=$rates"
                continue
            }
            set name [dict get $t name]
            if {$name eq "arm_dap"} {
                set role arm_dap
            } elseif {[dict exists $t is_fpga] && [dict get $t is_fpga]} {
                set role fpga
            } else {
                set role other
            }
            puts "DEVICE $name | role=$role | target_name=$ctx"
        }
    } finally {
        catch {disconnect}
    }
}

if {[llength $argv] != 1} {
    puts stderr "Usage: xsdb flash_targets.tcl SERVER"
    exit 1
}
if {[catch {main [lindex $argv 0]} message]} {
    puts stderr "ERROR: $message"
    exit 1
}
