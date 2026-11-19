# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
###########################################################################
## detect.tcl   (READ-ONLY - no design mutation)
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
##   -> written to fix.tcl (read_iphys_opt_tcl fix.tcl before place_design)
##
## Scope rules (from the ask):
##   * BRAM output register = DOA_REG / DOB_REG   (per port A/B)  -- must be ON
##   * URAM output register = OREG_A / OREG_B     (per port A/B)  -- must be ON
##   * ONLY non-cascade memories (CASCADE_ORDER_* == NONE and no CAS pins wired)
##   * output-side = paths STARTING at the memory's data-out pins
##
## Usage (design already open):
##   source detect.tcl
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

# ---- special hard-primitive endpoint detection -------------------------
# A memory whose registered output REACHES a special/hard primitive endpoint
# (GT, MRMAC, DCMAC, NoC, CPM, PCIe, PS, DDRMC, HBM, AI-Engine ...) should have its output
# register UNPACKED irrespective of slack: such primitives sit at fixed, often far
# die locations, so the packed-reg -> primitive route is intrinsically long. Pulling
# the register into fabric lets the placer relocate it next to the primitive.
# Patterns match (case-insensitive glob) against the endpoint cell REF_NAME; override
# the list with env SPECIAL_PRIMS, disable with SPECIAL_OFF=1, depth via SPECIAL_DEPTH.
proc _bur_special_pats {} {
    if {[info exists ::env(SPECIAL_PRIMS)] && $::env(SPECIAL_PRIMS) ne ""} { return $::env(SPECIAL_PRIMS) }
    return {GT* GTM* GTY* GTH* GTF* GTYP* GTHE* GTYE* MRMAC* DCMAC* NOC* NMU* NSU* NPS* NPI* CPM* PCIE* PS9* PS8* PSX* PS_* DDRMC* HBM* AIE* AIE_* AI_ENGINE* ILKNE* HSC*}
}
proc _bur_is_special {ref pats} {
    foreach p $pats { if {[string match -nocase $p $ref]} { return 1 } }
    return 0
}
# combinational cells we traverse THROUGH toward the real (sequential/primitive) endpoint
proc _bur_comb {ref} {
    return [expr {[string match LUT* $ref] || [string match CARRY* $ref] || [string match MUXF* $ref] || $ref eq "INV"}]
}
# Does the fanout of these output pins reach a special primitive as its endpoint?
# Forward BFS up to maxDepth, traversing ONLY combinational cells; it stops at any
# sequential/primitive sink (that sink IS the endpoint). Returns "" if none, else
# {<special_ref> <endpoint_cell>} for the first special primitive reached.
proc _bur_reaches_special {pins pats maxDepth} {
    array set seen {}
    set frontier $pins
    for {set d 0} {$d <= $maxDepth} {incr d} {
        set nextpins {}
        foreach op $frontier {
            foreach net [get_nets -quiet -of $op] {
                set t [get_property -quiet TYPE $net]
                if {$t eq "POWER" || $t eq "GROUND" || $t eq "CLOCK"} { continue }
                foreach sink [get_pins -quiet -leaf -of $net -filter {DIRECTION == IN}] {
                    set sc [get_cells -quiet -of $sink]
                    if {$sc eq ""} { continue }
                    set ref [get_property -quiet REF_NAME $sc]
                    if {[_bur_is_special $ref $pats]} { return [list $ref $sc] }
                    if {$d < $maxDepth && [_bur_comb $ref]} {
                        if {[info exists seen($sc)]} { continue }
                        set seen($sc) 1
                        foreach oo [get_pins -quiet -of $sc -filter {DIRECTION == OUT}] { lappend nextpins $oo }
                    }
                }
            }
        }
        if {[llength $nextpins] == 0} { break }
        set frontier $nextpins
    }
    return ""
}
# worst output-side setup path through a port's DOUT pins (for reporting): {slack ll clk ep}
proc _bur_port_slack {pins} {
    set pp [get_timing_paths -quiet -setup -through $pins -max_paths 1 -nworst 1]
    if {[llength $pp] == 0} { return [list "" "" "" ""] }
    set p0 [lindex $pp 0]
    return [list [get_property -quiet SLACK $p0] [get_property -quiet LOGIC_LEVELS $p0] [get_property -quiet ENDPOINT_CLOCK $p0] [get_property -quiet ENDPOINT_PIN $p0]]
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
    set SPECIAL_DEPTH [_bur_env SPECIAL_DEPTH 4]
    set SPECIAL_OFF   [_bur_env SPECIAL_OFF 0]
    set specialPats   [_bur_special_pats]

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

    # ---- 1b. special hard-primitive endpoint detection (slack-independent) ---
    # Flag reg-ON used ports whose output reaches a GT, MRMAC, DCMAC, NoC, CPM, PCIe,
    # PS, DDRMC, HBM, AIE... primitive endpoint -> unpack IRRESPECTIVE of slack.
    array unset SPECIAL     ;# "cell|port" -> special primitive REF_NAME
    array unset SPECIALEP   ;# "cell|port" -> special endpoint cell
    set nSpecial 0
    if {!$SPECIAL_OFF} {
        foreach c $candCells {
            lassign $REC($c) kind aOn bOn aUsed bUsed
            foreach pt {A B} {
                set has [expr {$pt eq "A" ? ($aOn && $aUsed) : ($bOn && $bUsed)}]
                if {!$has} { continue }
                set pins [expr {$pt eq "A" ? $APINS($c) : $BPINS($c)}]
                set hit [_bur_reaches_special $pins $specialPats $SPECIAL_DEPTH]
                if {$hit ne ""} {
                    set SPECIAL($c|$pt) [lindex $hit 0]
                    set SPECIALEP($c|$pt) [lindex $hit 1]
                    incr nSpecial
                }
            }
        }
    }
    puts "\[BUR\] special-primitive-endpoint reg-ON ports (unpack regardless of slack) = $nSpecial"


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
    puts $csv "mem_cell,kind,ref,DOA_or_OREGA,DOB_or_OREGB,portA_used,portB_used,crit_ports,worst_out_slack,logic_levels,endpoint_clock,endpoint_pin,reason,special_primitive"
    set apply [open $outdir/fix.tcl w]

    set half [expr {$SLACK_MAX/2.0}]
    array set SLBIN {neg 0 lo 0 hi 0}
    # union of flagged cells: slack-based (WORST) OR special-endpoint (SPECIAL)
    array unset FCELL
    foreach k [array names WORST]   { set FCELL($k) 1 }
    foreach k [array names SPECIAL] { set FCELL([lindex [split $k |] 0]) 1 }

    set rows {}
    foreach cell [array names FCELL] {
        lassign $REC($cell) kind aOn bOn aUsed bUsed
        set ref [get_property -quiet REF_NAME $cell]
        set opt [expr {$kind eq "URAM" ? "-uram_register_opt" : "-bram_register_opt"}]
        set cports {} ; set reasons {} ; set sprims {}
        set wsl ""; set wll ""; set wclk ""; set wep ""
        foreach pt {A B} {
            set isSlack   [info exists CPW($cell|$pt)]
            set isSpecial [info exists SPECIAL($cell|$pt)]
            if {!$isSlack && !$isSpecial} { continue }
            lappend cports $pt
            set pins [expr {$pt eq "A" ? $APINS($cell) : $BPINS($cell)}]
            lassign [_bur_port_slack $pins] psl pll pclk pep
            if {$isSlack && $isSpecial} { lappend reasons "${pt}:both" } elseif {$isSpecial} { lappend reasons "${pt}:special" } else { lappend reasons "${pt}:slack" }
            if {$isSpecial} { lappend sprims "${pt}=$SPECIAL($cell|$pt)" }
            if {$psl ne "" && ($wsl eq "" || $psl < $wsl)} { set wsl $psl; set wll $pll; set wclk $pclk; set wep $pep }
            set why [expr {$isSpecial ? "special=$SPECIAL($cell|$pt)" : [format "slack=%.3f" [expr {$psl eq {} ? 999 : $psl}]]}]
            puts $apply "# $cell  port $pt  ($why)"
            puts $apply "iphys_opt_design $opt -cell {$cell} -unpacking -port $pt"
        }
        if {[llength $cports] == 0} { continue }
        if {$wsl eq ""} { set wsl 999; set wll ""; set wclk ""; set wep "" }
        if {$wsl < 0} { incr SLBIN(neg) } elseif {$wsl < $half} { incr SLBIN(lo) } else { incr SLBIN(hi) }
        lappend rows [list $cell $kind $ref $aOn $bOn $aUsed $bUsed [join $cports {}] $wsl $wll $wclk $wep [join $reasons " "] [join $sprims " "]]
    }
    close $apply
    set rows [lsort -real -index 8 $rows]
    foreach r $rows { puts $csv [join $r ","] }
    close $csv

    set rpt [open $outdir/bram_uram_oreg_summary.rpt w]
    puts $rpt "########################################################################"
    puts $rpt "# BRAM/URAM output-register UNPACK candidates (non-cascade only)"
    puts $rpt "# Flag = embedded output reg ON (DOA_REG/DOB_REG | OREG_A/OREG_B) on a USED port AND"
    puts $rpt "#        EITHER worst output-side setup slack < $SLACK_MAX ns,"
    puts $rpt "#        OR the output reaches a special hard-primitive endpoint"
    puts $rpt "#        (GT, MRMAC, DCMAC, NoC, CPM, PCIe, PS, DDRMC, HBM, AIE ...) -> unpack regardless of slack"
    puts $rpt "# Action = iphys_opt_design -bram_register_opt/-uram_register_opt"
    puts $rpt "#          -cell {..} -unpacking -port <A|B>   (pull reg into fabric)"
    puts $rpt "#          emitted to fix.tcl (read_iphys_opt_tcl fix.tcl before place_design)"
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
    puts $rpt [format "  slack-flagged memories (slack < %s)      : %d" $SLACK_MAX [array size WORST]]
    puts $rpt [format "  special-endpoint reg-ON ports (any slack): %d" $nSpecial]
    puts $rpt [format "  TOTAL flagged memories (union)           : %d" [array size FCELL]]
    puts $rpt "  worst-output-slack histogram (flagged memories):"
    puts $rpt [format "    slack < 0 (failing)         : %d" $SLBIN(neg)]
    puts $rpt [format "    0.00 - %.2f ns             : %d" $half $SLBIN(lo)]
    puts $rpt [format "    %.2f - %.2f ns             : %d" $half $SLACK_MAX $SLBIN(hi)]
    puts $rpt ""
    puts $rpt "  TOP 25 candidates (worst output slack first):"
    puts $rpt [format "    %-9s %-6s %6s %-5s %-14s  %s" "slack" "kind" "ll" "port" "reason" "cell"]
    set n 0
    foreach r $rows {
        lassign $r cell kind ref aOn bOn aUsed bUsed cports sl ll clk ep reason sprim
        puts $rpt [format "    %-9.3f %-6s %6s %-5s %-14s  %s" $sl $kind $ll $cports $reason $cell]
        incr n; if {$n >= 25} break
    }
    close $rpt

    set dt [expr {[clock seconds]-$t0}]
    puts "\[BUR\] DONE in ${dt}s -> $outdir/bram_uram_oreg_summary.rpt (+ fix.tcl)"
    puts "\[BUR\] to apply before place_design:  set_param physynth.readIPhysOptRebuildShapes 1; read_iphys_opt_tcl $outdir/fix.tcl"
    set fh [open $outdir/bram_uram_oreg_summary.rpt r]; puts [read $fh]; close $fh

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
        set _rj_counts [_rj_obj [list \
            elapsed_s      n $dt \
            brams          n [llength $brams] \
            urams          n [llength $urams] \
            non_cascade    n $nNonCasc \
            cascade        n $nCascade \
            reg_a_on       n $REGSTAT(A_on) \
            reg_a_off      n $REGSTAT(A_off) \
            reg_b_on       n $REGSTAT(B_on) \
            reg_b_off      n $REGSTAT(B_off) \
            candidate_cells n [llength $candCells] \
            worst_count    n [array size WORST] \
            special_ports  n $nSpecial \
            flagged_total  n [array size FCELL] \
            slbin_neg      n $SLBIN(neg) \
            slbin_lo       n $SLBIN(lo) \
            slbin_hi       n $SLBIN(hi) \
        ]]
        set _rj_thresholds [_rj_obj [list slack_max n $SLACK_MAX special_depth n $SPECIAL_DEPTH special_off n $SPECIAL_OFF]]
        set _rj_ts [clock format [clock seconds] -format {%Y-%m-%dT%H:%M:%S}]
        set _rj_cond "[array size FCELL] of [llength $candCells] candidate memories (output-register ON, non-cascade) flagged: [array size WORST] with worst output-side setup slack < $SLACK_MAX ns, $nSpecial reg-ON port(s) reaching a special hard-primitive endpoint (any slack)"
        set _rj_changes [expr {[array size FCELL] > 0 ? "Wrote $outdir/fix.tcl with iphys_opt_design unpacking commands for [array size FCELL] flagged memory cell(s)" : "No changes applied (no memories met the slack window or reached a special endpoint)"}]
        set _rj_top [_rj_obj [list schema_version n 1 skill s "bram_uram_unpack_analysis" category s "early_resolution" \
            detected b [expr {[llength $candCells] > 0}] generated_at s $_rj_ts elapsed_seconds n $dt \
            trigger_condition s $_rj_cond design_changes s $_rj_changes]]
        set _rj_json "\{\n  [string range $_rj_top 1 end-1],\n  \"counts\": $_rj_counts,\n  \"thresholds\": $_rj_thresholds\n\}\n"
        set _rj_fh [open [file join $outdir "result.json"] w]
        puts -nonewline $_rj_fh $_rj_json
        close $_rj_fh
    } _rj_err]} {
        puts "\[BUR\] WARNING: could not write $outdir/result.json: $_rj_err"
    }
}

# Auto-run when sourced (consistent with other early_resolution skills)
if {[catch {current_design}] == 0} {
    run_bram_uram_oreg [_bur_env OUTDIR "."]
} else {
    puts "\[BUR\] no open design - open a checkpoint first, then: source detect.tcl"
}
