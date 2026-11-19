# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Clocking / BUFGCE HARDSYNC CE Detection
# =============================================================================
# Detects BUFGCE cells whose CE pin is driven by a flop, where both the flop's
# clock buffer (Buffer1) and the BUFGCE (Buffer2) share the same MMCM output
# as their input source. In this case, setting CE_TYPE=HARDSYNC eliminates
# the large clock skew penalty on the flop→BUFGCE/CE path.
#
# IMPORTANT: Must be sourced BEFORE opt_design (after open_checkpoint).
#
# Output:
#   <run_dir>/hardsync_ce_detected.flag  (if qualifying BUFGCEs found)
#   <run_dir>/hardsync_ce_fix.xdc        (XDC with set_property commands)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] Scanning for BUFGCE cells requiring HARDSYNC..."

set hardsync_count 0
set xdc_lines [list]
set all_bufgce [get_cells -quiet -hier -filter {REF_NAME == BUFGCE}]
set num_bufgce [llength $all_bufgce]
puts "\[DETECT\] Found $num_bufgce BUFGCE cells"

if {$num_bufgce == 0} {
    puts "\[DETECT\] No BUFGCE cells found. Skipping."
    return
}

foreach buf2 $all_bufgce {
    # Check if CE_TYPE is SYNC
    set ce_type [get_property CE_TYPE $buf2]
    if {$ce_type ne "SYNC"} { continue }

    # Get CE pin and check if driven by a flop
    set ce_pin [get_pins -quiet -of_objects $buf2 -filter {REF_PIN_NAME == CE}]
    if {$ce_pin eq ""} { continue }
    set ce_net [get_nets -quiet -of_objects $ce_pin]
    if {$ce_net eq ""} { continue }
    set ce_driver [get_pins -quiet -leaf -of_objects $ce_net -filter {DIRECTION == OUT}]
    if {$ce_driver eq ""} { continue }

    # Check if driver is a flop (FDRE/FDSE/FDCE/FDPE)
    set ce_drv_cell [get_cells -quiet -of_objects $ce_driver]
    if {$ce_drv_cell eq ""} { continue }
    set ce_drv_ref [get_property REF_NAME $ce_drv_cell]
    if {![regexp {^FD} $ce_drv_ref]} { continue }

    # Step 1: Get the BUFGCE (Buffer1) driving the flop's clock pin
    set flop_c_pin [get_pins -quiet -of_objects $ce_drv_cell -filter {REF_PIN_NAME == C}]
    if {$flop_c_pin eq ""} { continue }
    set flop_clk_net [get_nets -quiet -of_objects $flop_c_pin]
    if {$flop_clk_net eq ""} { continue }
    set flop_clk_driver [get_pins -quiet -leaf -of_objects $flop_clk_net -filter {DIRECTION == OUT}]
    if {$flop_clk_driver eq ""} { continue }

    # Verify the clock driver is a BUFGCE
    set buf1 [get_cells -quiet -of_objects $flop_clk_driver]
    if {$buf1 eq ""} { continue }
    set buf1_ref [get_property REF_NAME $buf1]
    if {$buf1_ref ne "BUFGCE"} { continue }

    # Step 2: Get Buffer1's I pin → trace to MMCM output
    set buf1_ipin [get_pins -quiet -of_objects $buf1 -filter {REF_PIN_NAME == I}]
    if {$buf1_ipin eq ""} { continue }
    set buf1_inet [get_nets -quiet -of_objects $buf1_ipin]
    if {$buf1_inet eq ""} { continue }
    set mmcm_pin_a [get_pins -quiet -leaf -of_objects $buf1_inet -filter {DIRECTION == OUT}]
    if {$mmcm_pin_a eq ""} { continue }

    # Step 3: Get Buffer2's I pin → trace to MMCM output
    set buf2_ipin [get_pins -quiet -of_objects $buf2 -filter {REF_PIN_NAME == I}]
    if {$buf2_ipin eq ""} { continue }
    set buf2_inet [get_nets -quiet -of_objects $buf2_ipin]
    if {$buf2_inet eq ""} { continue }
    set mmcm_pin_b [get_pins -quiet -leaf -of_objects $buf2_inet -filter {DIRECTION == OUT}]
    if {$mmcm_pin_b eq ""} { continue }

    # Step 4: Compare — same MMCM output pin?
    set mmcm_a_name [get_property NAME $mmcm_pin_a]
    set mmcm_b_name [get_property NAME $mmcm_pin_b]
    if {$mmcm_a_name eq $mmcm_b_name} {
        incr hardsync_count
        set buf2_name [get_property NAME $buf2]
        set buf1_name [get_property NAME $buf1]
        set flop_name [get_property NAME $ce_drv_cell]
        puts "\[DETECT\]   HARDSYNC candidate: $buf2_name"
        puts "\[DETECT\]     CE driven by flop: $flop_name"
        puts "\[DETECT\]     Flop clocked by Buffer1: $buf1_name"
        puts "\[DETECT\]     Shared MMCM source: $mmcm_a_name"
        lappend xdc_lines "# Buffer2: $buf2_name (CE from flop $flop_name, clocked by $buf1_name)"
        lappend xdc_lines "# Shared MMCM source: $mmcm_a_name"
        lappend xdc_lines "set_property CE_TYPE HARDSYNC \[get_cells \{$buf2_name\}\]"
        lappend xdc_lines ""
    }
}

# Write flag + XDC if candidates found
if {$hardsync_count > 0} {
    puts "\[DETECT\] Found $hardsync_count BUFGCE(s) requiring HARDSYNC"

    # Write XDC
    set xdc_path [file join $run_dir "hardsync_ce_fix.xdc"]
    set fh [open $xdc_path w]
    puts $fh "# ============================================="
    puts $fh "# BUFGCE HARDSYNC Fix - Auto-generated"
    puts $fh "# Source BEFORE opt_design"
    puts $fh "# ============================================="
    puts $fh ""
    foreach line $xdc_lines {
        puts $fh $line
    }
    close $fh
    puts "\[DETECT\] Written XDC: $xdc_path"

    # Write flag
    set flag_file [file join $run_dir "hardsync_ce_detected.flag"]
    set fh [open $flag_file w]
    puts $fh "HARDSYNC_CE_DETECTED"
    puts $fh "count=$hardsync_count"
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
        set _rj_counts [_rj_obj [list hardsync_count n $hardsync_count num_bufgce n $num_bufgce]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "$hardsync_count of $num_bufgce BUFGCE cell(s) have CE driven by a flop sharing the same MMCM source as the flop's own clock buffer"
        set _rj_changes "Wrote hardsync_ce_fix.xdc setting CE_TYPE=HARDSYNC on $hardsync_count BUFGCE cell(s)"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "hardsync_ce" category s "clocking" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts\n\}\n"
        set _rj_fh [open [file join $run_dir "hardsync_ce_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write hardsync_ce_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] No qualifying BUFGCE found for HARDSYNC."
}
