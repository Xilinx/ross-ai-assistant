# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Timing / BLI IO — Flop-to-IO and IO-to-Flop BLI Fix
# =============================================================================
# Detects failing timing paths between flops and IO ports.
# If the flop has sufficient margin on its other side (pre-path or post-path > 1ns),
# it is safe to move the flop to BLI (closer to IO) to fix the IO path.
#
# Case 1: Flop → IO failing, pre-path margin > 1ns → BLI on source flop
# Case 2: IO → Flop failing, post-path margin > 1ns → BLI on dest flop
#
# Applied as a Stage 2 fix: detect after post-route physopt, apply before place_design
# in the fix run.
#
# Output:
#   <run_dir>/bli_io_detected.flag   (if qualifying flops found)
#   <run_dir>/bli_io_fix.xdc         (set_property BLI TRUE commands)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] BLI IO analysis — checking flop↔IO failing paths..."

# Parameters
set SLACK_THRESHOLD -0.050     ;# Only check paths failing by > 50ps
set MARGIN_THRESHOLD 1.000     ;# Pre/post-path margin must be > 1ns
set MAX_PATHS 500

set bli_flops [list]

# ===== Case 1: Flop → IO (output paths) =====
puts "\[DETECT\] Checking Flop → IO paths..."

set output_ports [get_ports -quiet -filter {DIRECTION==OUT || DIRECTION==INOUT}]
if {[llength $output_ports] > 0} {
    set crit_paths [get_timing_paths -quiet \
        -to $output_ports \
        -max_paths $MAX_PATHS \
        -slack_lesser_than $SLACK_THRESHOLD \
        -delay_type max]

    foreach path $crit_paths {
        set sp_pin [get_property -quiet STARTPOINT_PIN $path]
        set src_cell [get_cells -quiet -of [get_pins -quiet $sp_pin]]
        if {$src_cell eq ""} { continue }

        # Must be a register
        set ref [get_property -quiet REF_NAME $src_cell]
        if {![regexp {^FD} $ref]} { continue }

        # Skip if already in list
        set src_name [get_property NAME $src_cell]
        if {[lsearch -exact $bli_flops $src_name] != -1} { continue }

        # Check pre-path margin (timing arriving at flop's D pin)
        set d_pin [get_pins -quiet -of $src_cell -filter {REF_PIN_NAME == D}]
        if {$d_pin eq ""} { continue }
        set pre_path [get_timing_paths -quiet -to $d_pin -max_paths 1]
        if {[llength $pre_path] == 0} { continue }
        set pre_slack [get_property -quiet SLACK $pre_path]
        if {$pre_slack eq "" || $pre_slack < $MARGIN_THRESHOLD} { continue }

        # Qualifies
        lappend bli_flops $src_name
        puts "\[DETECT\]   Flop→IO: $src_name (path_slack=[format %.3f [get_property SLACK $path]]ns, pre_margin=[format %.3f $pre_slack]ns)"
    }
}

# ===== Case 2: IO → Flop (input paths) =====
puts "\[DETECT\] Checking IO → Flop paths..."

set input_ports [get_ports -quiet -filter {DIRECTION==IN || DIRECTION==INOUT}]
if {[llength $input_ports] > 0} {
    set crit_paths [get_timing_paths -quiet \
        -from $input_ports \
        -max_paths $MAX_PATHS \
        -slack_lesser_than $SLACK_THRESHOLD \
        -delay_type max]

    foreach path $crit_paths {
        set ep_pin [get_property -quiet ENDPOINT_PIN $path]
        set dst_cell [get_cells -quiet -of [get_pins -quiet $ep_pin]]
        if {$dst_cell eq ""} { continue }

        # Must be a register
        set ref [get_property -quiet REF_NAME $dst_cell]
        if {![regexp {^FD} $ref]} { continue }

        # Skip if already in list
        set dst_name [get_property NAME $dst_cell]
        if {[lsearch -exact $bli_flops $dst_name] != -1} { continue }

        # Check post-path margin (timing leaving flop's Q pin)
        set q_pin [get_pins -quiet -of $dst_cell -filter {REF_PIN_NAME == Q}]
        if {$q_pin eq ""} { continue }
        set post_path [get_timing_paths -quiet -from $q_pin -max_paths 1]
        if {[llength $post_path] == 0} { continue }
        set post_slack [get_property -quiet SLACK $post_path]
        if {$post_slack eq "" || $post_slack < $MARGIN_THRESHOLD} { continue }

        # Qualifies
        lappend bli_flops $dst_name
        puts "\[DETECT\]   IO→Flop: $dst_name (path_slack=[format %.3f [get_property SLACK $path]]ns, post_margin=[format %.3f $post_slack]ns)"
    }
}

# ===== Write output =====
if {[llength $bli_flops] > 0} {
    puts "\[DETECT\] Found [llength $bli_flops] flop(s) qualifying for BLI"

    # Write XDC
    set xdc_path [file join $run_dir "bli_io_fix.xdc"]
    set fh [open $xdc_path w]
    puts $fh "# ============================================="
    puts $fh "# BLI IO Fix - Auto-generated"
    puts $fh "# Moves qualifying flops to BLI (closer to IO)"
    puts $fh "# Source BEFORE place_design"
    puts $fh "# ============================================="
    puts $fh ""
    foreach flop [lsort $bli_flops] {
        puts $fh "set_property BLI TRUE \[get_cells \{$flop\}\]"
    }
    close $fh
    puts "\[DETECT\] Written XDC: $xdc_path"

    # Write fix.tcl (sources the XDC)
    set fix_path [file join $run_dir "bli_io_fix.tcl"]
    set fh [open $fix_path w]
    puts $fh "## fix.tcl - BLI IO fix (auto-generated by detect.tcl)"
    puts $fh "source \{$xdc_path\}"
    close $fh

    # Write flag
    set flag_file [file join $run_dir "bli_io_detected.flag"]
    set fh [open $flag_file w]
    puts $fh "BLI_IO_DETECTED"
    puts $fh "flops=[llength $bli_flops]"
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
        set _rj_counts [_rj_obj [list flops n [llength $bli_flops]]]
        set _rj_thresholds [_rj_obj [list slack_threshold n $SLACK_THRESHOLD margin_threshold n $MARGIN_THRESHOLD max_paths n $MAX_PATHS]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "[llength $bli_flops] flop(s) on a failing Flop<->IO path (slack < $SLACK_THRESHOLD ns) have >$MARGIN_THRESHOLD ns margin on their other side"
        set _rj_changes "Wrote bli_io_fix.xdc + bli_io_fix.tcl setting BLI=TRUE on [llength $bli_flops] flop(s)"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "bli_io" category s "timing" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts,\n  \"thresholds\": $_rj_thresholds\n\}\n"
        set _rj_fh [open [file join $run_dir "bli_io_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write bli_io_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] No qualifying flops found for BLI IO fix."
}
