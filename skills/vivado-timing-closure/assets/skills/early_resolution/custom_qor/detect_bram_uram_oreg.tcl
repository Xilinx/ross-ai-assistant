# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
###########################################################################
## detect_bram_uram_oreg.tcl   (READ-ONLY - no design mutation)
##
## Find NON-CASCADE BRAM / URAM whose EMBEDDED OUTPUT register is ENABLED and
## whose OUTPUT-SIDE paths (memory data-out -> downstream capture) are failing or
## near-critical. For these, PULL THE REGISTER OUT of the hard block (UNPACK it
## into a fabric SLICE flop) so the placer/retimer can relocate it and close the
## output path. A register stuck inside the block cannot move; once unpacked it
## can be placed next to its load / retimed. Latency-preserving (relocated, not
## removed). Only a port whose output register is ON has a register to unpack.
##
## Apply command emitted per flagged candidate:
##   BRAM: iphys_opt_design -bram_register_opt -cell {<cell>} -unpacking -port <A|B>
##   URAM: iphys_opt_design -uram_register_opt -cell {<cell>} -unpacking -port <A|B>
##   -> written to iphysOptRAM2FF.tcl (read_iphys_opt_tcl before place_design)
##
## Scope rules (from the ask):
##   * BRAM output register = DOA_REG / DOB_REG   (per port A/B)  -- must be ON
##   * URAM output register = OREG_A / OREG_B     (per port A/B)  -- must be ON
##   * ONLY non-cascade memories (CASCADE_ORDER_* == NONE and no CAS pins wired)
##   * output-side = paths STARTING at the memory's data-out pins
##
## Usage (design already open):
##   source detect_bram_uram_oreg.tcl
##   run_bram_uram_oreg <outdir>
## Env tunables:
##   SLACK_MAX   near-critical/failing window (ns), default 0.500  (flag slack < this)
##   MAXPATHS    cap on timing paths pulled in the single query, default 200000
###########################################################################

proc _bur_env {name def} {
    if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
    return $def
}

# enabled? accept 1 / TRUE (Versal E5 uses integer 0/1; be liberal)
proc _bur_on {v} { return [expr {$v eq "1" || [string toupper $v] eq "TRUE"}] }

# port of an output pin name: B if the port field is B, else A
proc _bur_port {rp} {
    if {[regexp {^DOUTP?B|^DOB|_B$|_B\[} $rp]} { return B }
    return A
}

# cascade? Non-cascade requires EVERY CASCADE_ORDER* property == NONE.
# Different primitive variants expose different names - some use
# CASCADE_ORDER_A/B, others CASCADE_ORDER_CTRL_A/B + CASCADE_ORDER_DATA_A/B.
# Scanning all CASCADE_ORDER* properties covers every variant (BRAM & URAM).
# CAS* pins wired to a real net also indicate a cascade chain.
proc _bur_cascade {cell} {
    foreach p {CASCADE_ORDER_A CASCADE_ORDER_B CASCADE_ORDER_CTRL_A CASCADE_ORDER_CTRL_B CASCADE_ORDER_DATA_A CASCADE_ORDER_DATA_B} {
        set v [get_property -quiet $p $cell]
        if {$v ne "" && [string toupper $v] ne "NONE"} { return 1 }
    }
    foreach pin [get_pins -quiet -of $cell -filter {REF_PIN_NAME =~ CAS*}] {
        set net [get_nets -quiet -of $pin]
        if {$net eq ""} { continue }
        set t [get_property -quiet TYPE $net]
        if {$t ne "POWER" && $t ne "GROUND"} { return 1 }
    }
    return 0
}

# quick single-cell inspector - validate cascade/oreg/pins/timing on one cell.
proc bur_debug_cell {cell} {
    set c [get_cells -quiet $cell]
    if {$c eq ""} { puts "no such cell"; return }
    set ref [get_property -quiet REF_NAME $c]
    set kind [expr {[string match URAM* $ref] ? "URAM" : "BRAM"}]
    if {$kind eq "URAM"} { set ra OREG_A; set rb OREG_B } else { set ra DOA_REG; set rb DOB_REG }
    puts "cell = $cell"
    puts "  REF=$ref kind=$kind cascade=[_bur_cascade $c]"
    puts "  $ra=[get_property -quiet $ra $c] (on=[_bur_on [get_property -quiet $ra $c]])"
    puts "  $rb=[get_property -quiet $rb $c] (on=[_bur_on [get_property -quiet $rb $c]])"
    set aP [get_pins -quiet -of $c -filter {DIRECTION == OUT && (REF_PIN_NAME =~ "DOUTA*" || REF_PIN_NAME =~ "DOUTPA*" || REF_PIN_NAME =~ "DOUT_A*" || REF_PIN_NAME =~ "DOADO*")}]
    set bP [get_pins -quiet -of $c -filter {DIRECTION == OUT && (REF_PIN_NAME =~ "DOUTB*" || REF_PIN_NAME =~ "DOUTPB*" || REF_PIN_NAME =~ "DOUT_B*" || REF_PIN_NAME =~ "DOBDO*")}]
    set au [expr {[llength $aP] > 0 && [llength [get_nets -quiet -of $aP]] > 0}]
    set bu [expr {[llength $bP] > 0 && [llength [get_nets -quiet -of $bP]] > 0}]
    puts "  portA out-pins=[llength $aP] used=$au"
    puts "  portB out-pins=[llength $bP] used=$bu"
    set pp [get_timing_paths -quiet -setup -from $c -max_paths 3 -nworst 1]
    puts "  -from cell: [llength $pp] paths"
    foreach p0 $pp {
        set ep [get_property -quiet ENDPOINT_PIN $p0]
        set epc [get_cells -quiet -of $ep]
        puts "    slack=[get_property -quiet SLACK $p0] ll=[get_property -quiet LOGIC_LEVELS $p0] start=[get_property -quiet NAME [get_property -quiet STARTPOINT_PIN $p0]]"
        puts "      -> endpoint=[get_property -quiet NAME $ep] endref=[get_property -quiet REF_NAME $epc] clk=[get_property -quiet ENDPOINT_CLOCK $p0]"
    }
}

proc run_bram_uram_oreg {outdir {cellsOverride ""}} {
    file mkdir $outdir
    set t0 [clock seconds]
    set SLACK_MAX [_bur_env SLACK_MAX 0.500]
    set MAXPATHS  [_bur_env MAXPATHS 200000]

    # ---- 1. collect memories -------------------------------------------
    if {$cellsOverride ne ""} {
        set brams [filter $cellsOverride {REF_NAME =~ RAMB*}]
        set urams [filter $cellsOverride {REF_NAME =~ URAM288*}]
    } else {
        set brams [get_cells -hierarchical -quiet -filter {REF_NAME =~ RAMB*}]
        set urams [get_cells -hierarchical -quiet -filter {REF_NAME =~ URAM288*}]
    }
    puts "\[BUR\] BRAM=[llength $brams]  URAM=[llength $urams]"

    array unset REC        ;# cell -> {kind aOn bOn aUsed bUsed}
    array unset APINS       ;# cell -> port-A data-out pins (ON+used)
    array unset BPINS       ;# cell -> port-B data-out pins (ON+used)
    set candCells {}
    set nNonCasc 0; set nCascade 0
    array set REGSTAT {A_on 0 A_off 0 B_on 0 B_off 0}

    foreach {kind cells oregA oregB} [list BRAM $brams DOA_REG DOB_REG URAM $urams OREG_A OREG_B] {
        foreach c $cells {
            if {[_bur_cascade $c]} { incr nCascade; continue }
            incr nNonCasc
            set aOn [_bur_on [get_property -quiet $oregA $c]]
            set bOn [_bur_on [get_property -quiet $oregB $c]]
            # per-PORT data-out pins (whole bus at once - NO per-bit iteration);
            # a port is "used" iff its DOUT bus connects to >=1 net. All decisions
            # (used / reg-ON / timing / the emitted unpack) are per cell+PORT.
            set aPins [get_pins -quiet -of $c -filter {DIRECTION == OUT && (REF_PIN_NAME =~ "DOUTA*" || REF_PIN_NAME =~ "DOUTPA*" || REF_PIN_NAME =~ "DOUT_A*" || REF_PIN_NAME =~ "DOADO*")}]
            set bPins [get_pins -quiet -of $c -filter {DIRECTION == OUT && (REF_PIN_NAME =~ "DOUTB*" || REF_PIN_NAME =~ "DOUTPB*" || REF_PIN_NAME =~ "DOUT_B*" || REF_PIN_NAME =~ "DOBDO*")}]
            set aUsed [expr {[llength $aPins] > 0 && [llength [get_nets -quiet -of $aPins]] > 0}]
            set bUsed [expr {[llength $bPins] > 0 && [llength [get_nets -quiet -of $bPins]] > 0}]
            if {$aUsed} { if {$aOn} {incr REGSTAT(A_on)} else {incr REGSTAT(A_off)} }
            if {$bUsed} { if {$bOn} {incr REGSTAT(B_on)} else {incr REGSTAT(B_off)} }
            # candidate startpoints = used ports whose output register is ON
            # (only a packed register can be unpacked / pulled out).
            set cp {}
            if {$aUsed && $aOn} { set cp [concat $cp $aPins]; set APINS($c) $aPins }
            if {$bUsed && $bOn} { set cp [concat $cp $bPins]; set BPINS($c) $bPins }
            set REC($c) [list $kind $aOn $bOn $aUsed $bUsed]
            if {[llength $cp] > 0} { lappend candCells $c }
        }
    }
    puts "\[BUR\] non-cascade=$nNonCasc  cascade(excluded)=$nCascade"
    puts "\[BUR\] used ports OREG ON: A=$REGSTAT(A_on) B=$REGSTAT(B_on) ; OFF: A=$REGSTAT(A_off) B=$REGSTAT(B_off)"
    puts "\[BUR\] candidate memories (>=1 used reg-ON port) = [llength $candCells]"

    # ---- 2. timing: prefilter cells with -from, refine per reg-ON port -
    # A data-out PIN is not a valid -from startpoint; the CELL is (the startpoint
    # pin is the port clock, e.g. CLKBWRCLK). -from candCells finds cells with a
    # near-critical output path; then -through each reg-ON port's DOUT pins
    # attributes the slack to the exact port (works for BRAM split clocks and
    # URAM's shared clock alike).
    array unset WORST   ;# cell -> worst slack over its reg-ON ports
    array unset WEP     ;# cell -> endpoint pin of worst
    array unset WLL     ;# cell -> logic levels of worst
    array unset WCLK    ;# cell -> capture clock of worst
    array unset CPW     ;# "cell|port" -> worst slack for that port
    set nPaths 0; set hitCells {}
    if {[llength $candCells] > 0} {
        puts "\[BUR\] prefiltering output paths from [llength $candCells] memories (slack < $SLACK_MAX) ..."
        set paths [get_timing_paths -quiet -setup -from $candCells \
                       -max_paths $MAXPATHS -nworst 1 -slack_lesser_than $SLACK_MAX -unique_pins]
        set nPaths [llength $paths]
        array unset SEEN
        foreach pth $paths {
            set sp [get_property -quiet STARTPOINT_PIN $pth]
            if {$sp eq ""} { continue }
            set cell [get_property -quiet PARENT_CELL $sp]
            if {$cell eq "" || ![info exists REC($cell)]} { continue }
            if {![info exists SEEN($cell)]} { set SEEN($cell) 1; lappend hitCells $cell }
        }
        puts "\[BUR\] prefilter paths=$nPaths ; cells with near-critical output = [llength $hitCells]"
        foreach cell $hitCells {
            lassign $REC($cell) kind aOn bOn aUsed bUsed
            set onp {}
            if {$aOn && $aUsed} { lappend onp A }
            if {$bOn && $bUsed} { lappend onp B }
            foreach pt $onp {
                set pins [expr {$pt eq "A" ? $APINS($cell) : $BPINS($cell)}]
                set pp [get_timing_paths -quiet -setup -through $pins -max_paths 1 -nworst 1 -slack_lesser_than $SLACK_MAX]
                if {[llength $pp] == 0} { continue }
                set p0 [lindex $pp 0]
                set sl [get_property -quiet SLACK $p0]
                set CPW($cell|$pt) $sl
                if {![info exists WORST($cell)] || $sl < $WORST($cell)} {
                    set WORST($cell) $sl
                    set WEP($cell) [get_property -quiet ENDPOINT_PIN $p0]
                    set WLL($cell) [get_property -quiet LOGIC_LEVELS $p0]
                    set WCLK($cell) [get_property -quiet ENDPOINT_CLOCK $p0]
                }
            }
        }
    }
    puts "\[BUR\] flagged memories (reg-ON port, slack < $SLACK_MAX) = [array size WORST]"

    # diagnostic: actual WORST output-side slacks (no window) so a 0-flag result
    # is interpretable and confirms the query works.
    if {[llength $candCells] > 0} {
        set dpaths [get_timing_paths -quiet -setup -from $candCells -max_paths 12 -nworst 1 -unique_pins]
        puts "\[BUR\] DIAGNOSTIC worst output-side slacks (no window), [llength $dpaths] shown:"
        foreach pth $dpaths {
            set sp [get_property -quiet STARTPOINT_PIN $pth]
            set cell [get_property -quiet PARENT_CELL $sp]
            puts [format "\[BUR\]   slack=%.3f  %s" [get_property -quiet SLACK $pth] $cell]
        }
    }

    # ---- 3. reports + apply script -------------------------------------
    set csv [open $outdir/bram_uram_oreg_candidates.csv w]
    puts $csv "mem_cell,kind,ref,DOA_or_OREGA,DOB_or_OREGB,portA_used,portB_used,crit_ports,worst_out_slack,logic_levels,endpoint_clock,endpoint_pin"
    set apply [open $outdir/iphysOptRAM2FF.tcl w]

    set half [expr {$SLACK_MAX/2.0}]
    array set SLBIN {neg 0 lo 0 hi 0}
    set rows {}
    foreach cell [array names WORST] {
        lassign $REC($cell) kind aOn bOn aUsed bUsed
        set ref [get_property -quiet REF_NAME $cell]
        set sl $WORST($cell)
        set ep $WEP($cell)
        set cports {}
        foreach pt {A B} { if {[info exists CPW($cell|$pt)]} { lappend cports $pt } }
        lappend rows [list $cell $kind $ref $aOn $bOn $aUsed $bUsed [join $cports {}] $sl $WLL($cell) $WCLK($cell) $ep]
        if {$sl < 0} { incr SLBIN(neg) } elseif {$sl < $half} { incr SLBIN(lo) } else { incr SLBIN(hi) }
        set opt [expr {$kind eq "URAM" ? "-uram_register_opt" : "-bram_register_opt"}]
        foreach pt $cports {
            puts $apply "iphys_opt_design $opt -cell {$cell} -unpacking -port $pt"
        }
    }
    close $apply
    set rows [lsort -real -index 8 $rows]
    foreach r $rows { puts $csv [join $r ","] }
    close $csv

    set rpt [open $outdir/bram_uram_oreg_summary.rpt w]
    puts $rpt "########################################################################"
    puts $rpt "# BRAM/URAM output-register UNPACK candidates (non-cascade only)"
    puts $rpt "# Flag = embedded output reg ON (DOA_REG/DOB_REG | OREG_A/OREG_B)"
    puts $rpt "#        on a USED port AND worst output-side setup slack < $SLACK_MAX ns"
    puts $rpt "# Action = iphys_opt_design -bram_register_opt/-uram_register_opt"
    puts $rpt "#          -cell {..} -unpacking -port <A|B>   (pull reg into fabric)"
    puts $rpt "#          emitted to iphysOptRAM2FF.tcl (read_iphys_opt_tcl before place_design)"
    puts $rpt "########################################################################"
    puts $rpt ""
    puts $rpt [format "  BRAM cells                         : %d" [llength $brams]]
    puts $rpt [format "  URAM cells                         : %d" [llength $urams]]
    puts $rpt [format "  non-cascade (analyzed)             : %d" $nNonCasc]
    puts $rpt [format "  cascade (excluded)                 : %d" $nCascade]
    puts $rpt ""
    puts $rpt "  used ports with output register ON (unpackable):"
    puts $rpt [format "    port A ON=%d  OFF=%d" $REGSTAT(A_on) $REGSTAT(A_off)]
    puts $rpt [format "    port B ON=%d  OFF=%d" $REGSTAT(B_on) $REGSTAT(B_off)]
    puts $rpt [format "  candidate memories (oreg-ON, used) : %d" [llength $candCells]]
    puts $rpt ""
    puts $rpt [format "  FLAGGED memories (oreg ON + slack < %s) : %d" $SLACK_MAX [array size WORST]]
    puts $rpt "  worst-output-slack histogram:"
    puts $rpt [format "    slack < 0 (failing)         : %d" $SLBIN(neg)]
    puts $rpt [format "    0.00 - %.2f ns             : %d" $half $SLBIN(lo)]
    puts $rpt [format "    %.2f - %.2f ns             : %d" $half $SLACK_MAX $SLBIN(hi)]
    puts $rpt ""
    puts $rpt "  TOP 25 candidates (worst output slack first):"
    puts $rpt [format "    %-9s %-6s %6s %-5s  %s" "slack" "kind" "ll" "port" "cell"]
    set n 0
    foreach r $rows {
        lassign $r cell kind ref aOn bOn aUsed bUsed cports sl ll clk ep
        puts $rpt [format "    %-9.3f %-6s %6s %-5s  %s" $sl $kind $ll $cports $cell]
        incr n; if {$n >= 25} break
    }
    close $rpt

    set dt [expr {[clock seconds]-$t0}]
    puts "\[BUR\] DONE in ${dt}s -> $outdir/bram_uram_oreg_summary.rpt (+ iphysOptRAM2FF.tcl)"
    set fh [open $outdir/bram_uram_oreg_summary.rpt r]; puts [read $fh]; close $fh
}
