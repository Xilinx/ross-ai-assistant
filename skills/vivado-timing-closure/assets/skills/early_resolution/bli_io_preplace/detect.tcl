# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# =============================================================================
# Early Resolution / BLI IO — Move IOB-adjacent Registers to BLI (Pre-Place)
# =============================================================================
# Finds registers directly connected to IO buffers (OBUF/IBUF) and sets
# BLI=TRUE to place them closer to IO pads. This improves IO timing by
# reducing routing delay between the flop and the pad.
#
# Purely structural — no timing data needed. Works pre-place.
#
# Criteria:
#   - Output: Flops whose Q pin drives an OBUF input (1 logic level to IO)
#   - Input: Flops whose D pin is driven by an IBUF output (1 logic level from IO)
#   - Only FDRE/FDCE with INIT=1'b0 (BLI site compatibility)
#
# IMPORTANT: Source BEFORE place_design (after open_checkpoint).
#
# Output:
#   <run_dir>/bli_io_preplace_detected.flag  (if qualifying flops found)
#   <run_dir>/bli_io_preplace_fix.xdc        (set_property BLI TRUE commands)
# =============================================================================

if {[info exists env(RUN_DIR)]} {
    set run_dir $env(RUN_DIR)
} else {
    set run_dir [pwd]
}

puts "\[DETECT\] BLI IO pre-place: finding IOB-adjacent registers..."

set bli_flops [list]

# ===== Output Registers: Flop → OBUF =====
set output_ports [get_ports -quiet -filter {DIRECTION==OUT || DIRECTION==INOUT}]
if {[llength $output_ports] > 0} {
    # Get nets driving output ports (through OBUFs)
    set obuf_cells [get_cells -quiet -of \
        [get_pins -quiet -leaf -filter {REF_NAME=~*OBUF*} -of \
            [get_nets -quiet -of $output_ports]]]
    if {[llength $obuf_cells] > 0} {
        # Get nets feeding OBUF inputs
        set obuf_in_nets [get_nets -quiet -of \
            [get_pins -quiet -filter {DIRECTION==IN} -of $obuf_cells]]
        if {[llength $obuf_in_nets] > 0} {
            # Find driving registers on those nets
            set out_regs [get_cells -quiet -of \
                [get_pins -quiet -leaf -filter {REF_NAME=~FD* && DIRECTION==OUT} -of $obuf_in_nets]]
            # Filter: only FDRE/FDCE with INIT=1'b0 (BLI compatible)
            if {[llength $out_regs] > 0} {
                set out_regs [filter -quiet $out_regs {(REF_NAME==FDRE || REF_NAME==FDCE) && INIT==1'b0}]
            }
            foreach reg $out_regs {
                set rname [get_property NAME $reg]
                if {[lsearch -exact $bli_flops $rname] == -1} {
                    lappend bli_flops $rname
                }
            }
        }
    }
}
puts "\[DETECT\]   Output registers (Flop→OBUF): [llength $bli_flops]"

# ===== Input Registers: IBUF → Flop =====
set input_count_before [llength $bli_flops]
set input_ports [get_ports -quiet -filter {DIRECTION==IN || DIRECTION==INOUT}]
if {[llength $input_ports] > 0} {
    # Get IBUFs driven by input ports
    set ibuf_cells [get_cells -quiet -of \
        [get_pins -quiet -leaf -filter {REF_NAME=~I*BUF*} -of \
            [get_nets -quiet -of $input_ports]]]
    if {[llength $ibuf_cells] > 0} {
        # Get nets coming out of IBUFs
        set ibuf_out_nets [get_nets -quiet -of \
            [get_pins -quiet -filter {DIRECTION==OUT} -of $ibuf_cells]]
        if {[llength $ibuf_out_nets] > 0} {
            # Find receiving registers on those nets (D pin)
            set in_regs [get_cells -quiet -of \
                [get_pins -quiet -leaf -filter {REF_PIN_NAME==D && REF_NAME=~FD* && DIRECTION==IN} -of $ibuf_out_nets]]
            # Filter: only FDRE/FDCE with INIT=1'b0 (BLI compatible)
            if {[llength $in_regs] > 0} {
                set in_regs [filter -quiet $in_regs {(REF_NAME==FDRE || REF_NAME==FDCE) && INIT==1'b0}]
            }
            foreach reg $in_regs {
                set rname [get_property NAME $reg]
                if {[lsearch -exact $bli_flops $rname] == -1} {
                    lappend bli_flops $rname
                }
            }
        }
    }
}
set input_count [expr {[llength $bli_flops] - $input_count_before}]
puts "\[DETECT\]   Input registers (IBUF→Flop): $input_count"

# ===== Write output =====
if {[llength $bli_flops] > 0} {
    puts "\[DETECT\] Found [llength $bli_flops] IOB-adjacent register(s) for BLI"

    # Write XDC
    set xdc_path [file join $run_dir "bli_io_preplace_fix.xdc"]
    set fh [open $xdc_path w]
    puts $fh "# ============================================="
    puts $fh "# BLI IO Pre-Place Fix - Auto-generated"
    puts $fh "# Moves IOB-adjacent registers to BLI"
    puts $fh "# Source BEFORE place_design"
    puts $fh "# ============================================="
    puts $fh ""
    foreach flop [lsort $bli_flops] {
        puts $fh "set_property BLI TRUE \[get_cells \{$flop\}\]"
    }
    close $fh
    puts "\[DETECT\] Written XDC: $xdc_path"

    # Write flag
    set flag_file [file join $run_dir "bli_io_preplace_detected.flag"]
    set fh [open $flag_file w]
    puts $fh "BLI_IO_PREPLACE_DETECTED"
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
        set _rj_counts [_rj_obj [list flops n [llength $bli_flops] input_count n $input_count]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "[llength $bli_flops] flop(s) found directly adjacent to IO buffers (Flop->OBUF or IBUF->Flop, FDRE/FDCE with INIT=1'b0)"
        set _rj_changes "Wrote bli_io_preplace_fix.xdc setting BLI=TRUE on [llength $bli_flops] flop(s)"
        set _rj_top [_rj_obj [list schema_version n 1 skill s "bli_io_preplace" category s "early_resolution" \
            detected b 1 generated_at s $_rj_ts trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts\n\}\n"
        set _rj_fh [open [file join $run_dir "bli_io_preplace_result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[DETECT\] WARNING: could not write bli_io_preplace_result.json: $_rj_err"
    }
} else {
    puts "\[DETECT\] No IOB-adjacent registers found for BLI."
}
