# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Clocking / MBUFG_GROUP Detection
# =============================================================================
# Detects BUFG_GT cells that share the same driver (GT clock output pin).
# When found, writes an XDC file with set_property MBUFG_GROUP commands.
#
# This XDC must be sourced BEFORE opt_design in the fix run, because
# opt_design converts BUFG_GT -> MBUFGT and needs the grouping info.
#
# Output:
#   <run_dir>/mbufg_group_detected.flag  (if groups found)
#   <run_dir>/mbufg_group_fix.xdc        (XDC with set_property commands)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Scanning for BUFG_GT cells requiring MBUFG_GROUP..."

# Step 1: Get all BUFG_GT cells
set all_bufgts [get_cells -quiet -hier -filter {REF_NAME == BUFG_GT}]
set num_bufgts [llength $all_bufgts]
puts "\[DETECT\] Found $num_bufgts BUFG_GT cells"

if {$num_bufgts == 0} {
    puts "\[DETECT\] No BUFG_GT cells found. Skipping."
    return
}

# Step 2 & 3: Get the I pin leaf driver pin for each BUFG_GT, group by driver
array set driver_groups {}

foreach bufgt $all_bufgts {
    set i_pin [get_pins -quiet -of_objects $bufgt -filter {REF_PIN_NAME == I}]
    if {$i_pin eq ""} { continue }

    set i_net [get_nets -quiet -of_objects $i_pin]
    if {$i_net eq ""} { continue }

    # Trace to leaf driver pin
    set driver_pin [get_pins -quiet -leaf -of_objects $i_net -filter {DIRECTION == OUT}]
    if {$driver_pin eq ""} {
        set driver_pin [get_ports -quiet -of_objects $i_net -filter {DIRECTION == IN}]
    }
    if {$driver_pin eq ""} { continue }

    set driver_name [get_property NAME $driver_pin]

    if {![info exists driver_groups($driver_name)]} {
        set driver_groups($driver_name) [list]
    }
    lappend driver_groups($driver_name) $bufgt
}

# Step 4: For each group with >1 BUFG_GT, collect output nets
set group_num 0
set xdc_lines [list]
set total_grouped 0

foreach driver [lsort [array names driver_groups]] {
    set cells $driver_groups($driver)
    if {[llength $cells] < 2} { continue }

    incr group_num
    set group_name "MBUFG_GRP_${group_num}"

    # Get output nets of all BUFG_GTs in this group
    set output_nets [list]
    foreach bufgt $cells {
        set o_pin [get_pins -quiet -of_objects $bufgt -filter {REF_PIN_NAME == O}]
        if {$o_pin eq ""} { continue }
        set o_net [get_nets -quiet -of_objects $o_pin]
        if {$o_net ne ""} {
            lappend output_nets [get_property NAME $o_net]
        }
    }

    if {[llength $output_nets] > 0} {
        set net_list [join $output_nets " "]
        lappend xdc_lines "# Group $group_num: [llength $cells] BUFG_GTs driven by $driver"
        lappend xdc_lines "set_property MBUFG_GROUP $group_name \[get_nets \{ $net_list \}\]"
        lappend xdc_lines ""
        incr total_grouped [llength $output_nets]
        puts "\[DETECT\]   $group_name: [llength $cells] BUFG_GTs, [llength $output_nets] output nets (driver: $driver)"
    }
}

# Step 5: Write flag + XDC if groups found
if {$group_num > 0} {
    puts "\[DETECT\] Found $group_num MBUFG groups ($total_grouped nets total)"

    # Write XDC
    set xdc_path [file join $run_dir "mbufg_group_fix.xdc"]
    set fh [open $xdc_path w]
    puts $fh "# ============================================="
    puts $fh "# MBUFG_GROUP Fix - Auto-generated"
    puts $fh "# Source BEFORE opt_design"
    puts $fh "# ============================================="
    puts $fh ""
    foreach line $xdc_lines {
        puts $fh $line
    }
    close $fh
    puts "\[DETECT\] Written XDC: $xdc_path"

    # Write flag
    set flag_file [file join $run_dir "mbufg_group_detected.flag"]
    set fh [open $flag_file w]
    puts $fh "MBUFG_GROUP_DETECTED"
    puts $fh "groups=$group_num"
    puts $fh "nets_grouped=$total_grouped"
    close $fh
    puts "\[DETECT\] Written flag: $flag_file"

    # ---- additive, structured result.json (does not affect detection/fix logic) ----
    if {[catch {
        proc _rj_esc {s} { return [string map {\\ \\\\ \" \\\" \n \\n \r \\r \t \\t} $s] }
        proc _rj_str {v} { return "\"[_rj_esc $v]\"" }
        proc _rj_bool {v} { return [expr {$v ? "true" : "false"}] }
        proc _rj_obj {pairs} {
            set m {}
            foreach {k t v} $pairs {
                set vv [expr {$t eq "s" ? [_rj_str $v] : ($t eq "b" ? [_rj_bool $v] : $v)}]
                lappend m "\"$k\": $vv"
            }
            return "\{[join $m ", "]\}"
        }
        set _rj_counts [_rj_obj [list group_num n $group_num total_grouped n $total_grouped num_bufgts n $num_bufgts]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "$group_num BUFG_GT driver-group(s) found (>=2 BUFG_GTs sharing one driver) among $num_bufgts BUFG_GT cells"
        set _rj_changes "Wrote mbufg_group_fix.xdc tagging $total_grouped output net(s) across $group_num MBUFG_GROUP(s)"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "mbufg_group" category s "clocking" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts\n\}\n"
        set _rj_fh [open [file join $run_dir "mbufg_group_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write mbufg_group_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] No MBUFG groups needed (all BUFG_GTs have unique drivers)."
}
