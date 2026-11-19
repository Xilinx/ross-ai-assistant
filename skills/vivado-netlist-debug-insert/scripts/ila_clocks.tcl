# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_clocks.tcl: clock tracing, free-running checks, hub clock report and set. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # MMCM and PLL cells on a clock path text ("cell(REF) <- cell(REF) ..." as printed by clock_source), and what the netlist shows
    # about their LOCKED output. UG908 Debug Core Clocks: if the clock comes from an MMCM or PLL, LOCKED must be high before a debug
    # measurement. The netlist can only show whether LOCKED is connected to anything. It cannot show that it resets the probed logic.
    # Returns a list of {level text}.
    proc lock_notes {cpath clkname} {
        set out {}
        foreach {all cell ref} [regexp -all -inline {([^ ()]+)\(((?:MMCM|PLL|DPLL|XPLL)\w*)\)} $cpath] {
            set pin [get_pins -quiet -of_objects [get_cells -quiet $cell] -filter {REF_PIN_NAME == LOCKED}]
            set net [expr {$pin eq "" ? "" : [get_nets -quiet -of_objects $pin]}]
            set loads [expr {$net eq "" ? {} : [get_pins -quiet -of_objects $net -leaf -filter {DIRECTION == IN}]}]
            if {[llength $loads] == 0} {
                lappend out [list WARNING "C01 clock $clkname comes from $cell ($ref) and its LOCKED output is not connected to anything. UG908 (Debug Core Clocks): ensure LOCKED is high before any debug measurement. Nothing in the netlist holds the logic in reset until the clock is locked."]
            } else {
                lappend out [list NOTE "C01 clock $clkname comes from $cell ($ref). Its LOCKED output drives [llength $loads] pin(s), first [lindex [get_property NAME $loads] 0]. UG908 (Debug Core Clocks): LOCKED must be high before a debug measurement. The skill checks only that LOCKED is connected, not that it resets the probed logic."]
            }
        }
        return $out
    }

    # The debug hub clock and what the netlist shows about it. Prints facts, never numbers it did not read from a query.
    # UltraScale and earlier: dbg_hub/clk. Versal: the AXI debug hub cell that opt_design creates (none before that).
    # Returns the list of {level text} it printed.
    proc hub_clock_report {} {
        variable facts
        set arch [require_architecture]
        set out {}
        set hubnet ""
        if {$arch eq "versal"} {
            set hub [get_cells -quiet -hier -filter {REF_NAME =~ *dbg_hub* && IS_PRIMITIVE == 0}]
            if {[llength $hub] == 0} { set hub [get_cells -quiet -hier -filter {REF_NAME =~ *dbg_hub*}] }
            if {[llength $hub] == 0} {
                lappend out [list NOTE "C02 No Versal AXI debug hub cell is in the design yet, so its clock cannot be read now. Vivado adds the hub as an empty black box when the first core is inserted, and builds it at opt_design. Its clock must be free running and synchronous to the S_AXI master (UG1388). If timing fails after insertion, a clock of 100 to 250 MHz is advised (UG908). Check it after opt_design."]
            } else {
                # the AXI debug hub clock pin is aclk and is not flagged IS_CLOCK on the hub cell
                set cps [get_pins -quiet -of_objects [lindex $hub 0] -filter {IS_CLOCK && DIRECTION == IN}]
                if {[llength $cps] == 0} { set cps [get_pins -quiet -of_objects [lindex $hub 0] -filter {REF_PIN_NAME == aclk}] }
                if {[llength $cps] > 0} { set hubnet [lindex [top_nets [lindex $cps 0]] 0] }
                # Vivado 2026.2 leaves the pin with no net, or with an undriven net named aclk0_aclk: either way the clock is unknown
                if {$hubnet ne "" && [get_nets -quiet $hubnet] ne ""} {
                    lassign [clock_source $hubnet] cv0 csrc0 cpath0
                    if {$csrc0 eq "no driver" && $cpath0 eq ""} { set hubnet "" }
                }
                if {$hubnet eq ""} {
                    set hn [get_property NAME [lindex $hub 0]]
                    lappend out [list NOTE "C02 The AXI debug hub $hn exists but its clock pin could not be read: the pin has no net or a net with no driver (seen on Vivado 2026.2), so the hub clock cannot be determined. Do not take a net name such as aclk0_aclk for a clock. Check which clock feeds $hn/aclk in the opt_design netlist (report_clocks) before hardware."]
                }
            }
        } else {
            if {[get_debug_ports -quiet dbg_hub/clk] ne ""} { set hubnet [lindex [connected_nets dbg_hub clk] 0] }
            if {$hubnet eq ""} { lappend out [list NOTE "C02 There is no debug hub clock to report (no dbg_hub/clk port)."] }
        }
        if {$hubnet ne "" && [get_nets -quiet $hubnet] ne ""} {
            lassign [clock_source $hubnet] cv csrc cpath
            set per ""
            set clk [get_clocks -quiet -of_objects [get_nets $hubnet]]
            if {$clk ne ""} { set per [get_property PERIOD [lindex $clk 0]] }
            set pertxt [expr {$per eq "" ? "no clock constraint on this net, so the frequency is unknown" : "period $per ns ([format %.1f [expr {1000.0 / $per}]] MHz)"}]
            dict set facts hub_clock_source "$csrc; path: $cpath"
            lappend out [list NOTE "C02 debug hub clock $hubnet: $pertxt; source $cv ($csrc); path $cpath."]
            # Report, do not block: the trace reads the netlist and cannot prove a clock stops. A gated verdict is always a WARNING; an unknown one is a
            # WARNING only on a specific suspicion (no driver, fabric logic on the clock path, a control pin driven or set by logic), else a NOTE.
            if {$cv eq "gated"} {
                lappend out [list WARNING "C02 the hub clock $hubnet can stop: traced to $csrc, not provable from the netlist. UG908: the hub clock must be free running, or the debug hub becomes unreachable. The skill did not change it."]
            } elseif {$cv ne "free"} {
                lappend out [list [expr {[regexp {no driver|fabric logic|driven by|is set by} $csrc] ? "WARNING" : "NOTE"}] "C02 the hub clock $hubnet could not be traced to a free-running source: traced to $csrc, not provable from the netlist. UG908: the hub clock must be free running and stable. Check it before hardware."]
            }
            foreach n [lock_notes $cpath $hubnet] { lappend out $n }
            if {$per ne "" && $per > 0} {
                set mhz [expr {1000.0 / $per}]
                if {$arch eq "versal"} {
                    if {$mhz < 100 || $mhz > 250} { lappend out [list NOTE "C02 the Versal hub clock is [format %.1f $mhz] MHz. UG908 advises 100 to 250 MHz if timing fails after insertion. It is not a rule when timing passes."] }
                    lappend out [list NOTE "C02 UG1388: the Versal hub clock must be synchronous to the AXI master on S_AXI. The netlist cannot show that. Confirm it in the design."]
                } else {
                    if {$mhz > 100} { lappend out [list WARNING "C02 the hub clock is [format %.1f $mhz] MHz. UG908 advises about 100 MHz or less. If timing fails on the hub, the hub can run slower: a clock divider setting on the hub, or a slower free-running clock for it (AI only: the hub settings are C_CLK_INPUT_FREQ_HZ and C_ENABLE_CLK_DIVIDER on dbg_hub, and its clock pin is dbg_hub/clk; name them only when the user asks how)."] }
                    lappend out [list NOTE "C02 UG908: the JTAG clock must be 2.5 times slower than the hub clock. For a hub clock of [format %.1f $mhz] MHz that is at most [format %.1f [expr {$mhz / 2.5}]] MHz. Set it when connecting in Hardware Manager."]
                }
            }
        }
        foreach o $out { say "[lindex $o 0] [lindex $o 1]" }
        return $out
    }

    # ---- clock source verdict (spec SC3: "free-running" cannot be proven by a query) ----
    # Walk upstream from a clock net through clock buffers, input buffers and MMCM/PLL.
    # Returns {verdict source detail}. verdict is one of
    #   free      reaches an input port or a processor PL clock with no gate, clear, reset or power-down in between
    #   gated     a primitive on the way has a control pin that is not tied to its constant "running" level
    #   unknown   the walk could not finish, or a pin it must read is unconnected or sets the frequency; the skill must say so and ask
    # A primitive type this proc does not know is "unknown", never "free".
    #
    # Control pins that can stop a clock, with their documentation (pin names are NOT guessed):
    #   BUFGCE, BUFG_GT, BUFGCE_DIV   CE (enable, active High); CLR (clear, forces O to 0). BUFG_GT also DIV[2:0] (divide value).
    #       UG974 (UltraScale Libraries Guide) BUFGCE_DIV and BUFG_GT "Port Descriptions"; UG572 BUFGCE_DIV; UG1485 / UG1353 BUFG_GT (Versal).
    #   BUFGCTRL                      CE0 and S0 must both be High to select I0; CE1 and S1 for I1 (UG974 BUFGCTRL "Port Descriptions";
    #       UG572; UG190 "Selection of an input clock requires a select pair (S0 and CE0, or S1 and CE1) to be asserted High").
    #   MMCME3/4_ADV, PLLE3/4_ADV     RST (asynchronous reset), PWRDWN (power down); CLKINSEL picks CLKIN1 or CLKIN2 (UG572 "MMCM Ports",
    #       "PLLE#_ADV Ports"). Versal MMCME5 has RST, PWRDWN, CLKINSEL too (UG1344 MMCME5 instantiation template).
    #   IBUF family                   IBUFDISABLE (High disables the input buffer; AM010, UG953 IBUF_INTERMDISABLE). Not a clock input.
    # Each pin may have an inversion attribute IS_<pin>_INVERTED (UG974 "Programmable Inversion Attributes"); it is applied to the level.
    # It is read with get_property -quiet: a cell without the property would otherwise print a Vivado 12-4444 ERROR line (E22 found the same noise).
    # BUFGCE_DIV: CE and CLR are both Invertible TRUE (UG572 Table "BUFGCE_DIV Pins").
    # Not checked, named here so nobody assumes otherwise: IGNORE0/IGNORE1 (switching only), CEMASK/CLRMASK, the CEB pin of GT reference
    # buffers (not read from the documentation), and every primitive not in the walk (unknown).

    # What drives a pin: vcc, gnd, unconnected, undriven, "port <net>", or "logic <REF_NAME> <driver cell> <net>" (a Tcl list: the net and the driver
    # cell are what the warning for a clock that can stop names).
    proc pin_driver {pin} {
        set n [lindex [get_nets -quiet -of_objects $pin] 0]
        if {$n eq ""} { return unconnected }
        set d [get_pins -quiet -of_objects $n -leaf -filter {DIRECTION == OUT}]
        if {[llength $d] == 0} { return [expr {[get_ports -quiet -of_objects $n] ne "" ? [list port [get_property NAME $n]] : "undriven"}] }
        set dcell [get_cells -of_objects [lindex $d 0]]
        set ref [get_property REF_NAME $dcell]
        if {$ref eq "VCC"} { return vcc }
        if {$ref eq "GND"} { return gnd }
        return [list logic $ref [get_property NAME $dcell] [get_property NAME $n]]
    }

    # State of one control pin after the IS_<pin>_INVERTED attribute: on (constant 1 at the pin), off (constant 0), absent (no such pin),
    # unconnected, or "dyn <what drives it> (<cell and net>)". The pin name is the cell's REF_PIN_NAME; a bus pin matches with a glob (DIV*).
    proc pin_state {cell pin} {
        set rx [expr {[string first * $pin] >= 0 ? "=~" : "=="}]
        set ps [get_pins -quiet -of_objects $cell -filter "REF_PIN_NAME $rx $pin"]
        if {[llength $ps] == 0} { return absent }
        set st {}
        foreach p $ps {
            set d [pin_driver $p]
            switch -glob -- [lindex $d 0] {
                vcc - gnd {
                    set inv 0
                    if {![catch {get_property -quiet IS_${pin}_INVERTED $cell} v]} { set inv [expr {$v in {1 true TRUE 1'b1}}] }
                    lappend st [expr {($d eq "vcc") != $inv ? "on" : "off"}]
                }
                unconnected - undriven { lappend st unconnected }
                port { lappend st "dyn input port (net [lindex $d 1])" }
                default { lappend st "dyn [lindex $d 1] output (cell [lindex $d 2], net [lindex $d 3])" }
            }
        }
        foreach s $st { if {[string match dyn* $s]} { return $s } }
        foreach s $st { if {$s eq "unconnected"} { return $s } }
        return [expr {[lsearch -exact $st off] >= 0 ? "off" : "on"}]
    }

    # The gating verdict of a clock buffer, PLL or MMCM cell: {} when no control pin can stop or change the clock, else {verdict reason}.
    proc clock_controls {cell ref} {
        set name [get_property NAME $cell]
        if {[regexp {^BUFG} $ref]} {
            # enable and clear pins: BUFGCE/BUFG_GT/BUFGCE_DIV have CE and CLR, BUFGCTRL has CE0 and S0 for the I0 side
            foreach {pin want} {CE on CE0 on S0 on CLR off} {
                set s [pin_state $cell $pin]
                if {$s eq "absent" || $s eq $want} continue
                # a clear left unconnected is inactive (the primitive's default); an enable left unconnected is not known to be on
                if {$s eq "unconnected" && $want eq "off"} continue
                if {$s eq "unconnected"} { return [list unknown "$pin of clock buffer $name ($ref) is not connected, so it is not known whether the clock runs"] }
                if {[string match dyn* $s]} { return [list gated "clock buffer $name ($ref) has $pin driven by [string range $s 4 end]: it can stop the clock"] }
                return [list gated "clock buffer $name ($ref) has $pin tied to the stopped level"]
            }
            # BUFGCTRL second input: harmless when CE1 or S1 is tied off, otherwise logic may switch the output to I1
            set c1 [pin_state $cell CE1]; set s1 [pin_state $cell S1]
            if {!($c1 eq "absent" && $s1 eq "absent") && $c1 ne "off" && $s1 ne "off"} {
                return [list gated "clock buffer $name ($ref) can select its second input I1 (CE1 is $c1, S1 is $s1), so its clock can change or stop"]
            }
            # divide value set by logic (BUFG_GT DIV[2:0]): the clock keeps running, the frequency is not fixed
            set s [pin_state $cell DIV*]
            if {[string match dyn* $s]} { return [list unknown "the divide value DIV of $name ($ref) is set by [string range $s 4 end], so the clock frequency is not fixed"] }
        } elseif {[regexp {^(MMCM|PLL|DPLL|XPLL)} $ref]} {
            foreach pin {RST PWRDWN} {
                set s [pin_state $cell $pin]
                if {$s eq "absent" || $s eq "off" || $s eq "unconnected"} continue   ;# unconnected: inactive, as for CLR above
                if {[string match dyn* $s]} { return [list gated "$name ($ref) has $pin driven by [string range $s 4 end]: it can stop the clock"] }
                return [list gated "$name ($ref) has $pin tied to the asserted level"]
            }
            set s2 [pin_state $cell CLKIN2]
            if {$s2 ne "absent" && $s2 ne "unconnected"} {
                set s [pin_state $cell CLKINSEL]
                if {[string match dyn* $s]} { return [list unknown "$name ($ref) picks between CLKIN1 and CLKIN2 with CLKINSEL driven by [string range $s 4 end]: the source is not fixed"] }
            }
        }
        return {}
    }

    proc clock_source {clknet} {
        set net [get_nets $clknet]
        set path {}
        for {set i 0} {$i < 12} {incr i} {
            set drv [get_pins -quiet -of_objects $net -leaf -filter {DIRECTION == OUT}]
            if {[llength $drv] == 0} {
                set port [get_ports -quiet -of_objects $net]
                if {$port ne ""} { return [list free "input port $port" [join $path " <- "]] }
                return [list unknown "no driver" [join $path " <- "]]
            }
            set cell [get_cells -of_objects [lindex $drv 0]]
            set ref [get_property REF_NAME $cell]
            lappend path "[get_property NAME $cell]($ref)"
            if {[regexp {^BUFG(CE|CTRL|_GT|CE_DIV|_PS|_FABRIC)?$|^BUFGCE} $ref] || [regexp {^(MMCM|PLL|DPLL|XPLL)} $ref]} {
                set ctl [clock_controls $cell $ref]
                if {[llength $ctl] > 0} { return [list [lindex $ctl 0] [lindex $ctl 1] [join $path " <- "]] }
            }
            if {[regexp {^BUFG(CE|CTRL|_GT|CE_DIV|_PS|_FABRIC)?$|^BUFGCE} $ref]} {
                set inp [get_pins -quiet -of_objects $cell -filter {REF_PIN_NAME == I || REF_PIN_NAME == I0 || REF_PIN_NAME == CLK_IN}]
                if {$inp eq ""} { return [list unknown "no input pin on $cell" [join $path " <- "]] }
                set net [lindex [get_nets -of_objects [lindex $inp 0]] 0]
                continue
            }
            if {[regexp {^(IBUF|INBUF|DIFFINBUF|IBUFCTRL|OBUF)} $ref]} {
                # a single-ended IBUF is INBUF (PAD in, O out) + IBUFCTRL inside, as the e31 probe log on 2026.1 shows (IBUF => IBUFCTRL, INBUF),
                # so the leaf walk ends at INBUF. UG974 (UltraScale, UltraScale+) and UG1344 (Versal) document only the IBUF primitive (I in,
                # O out); INBUF is an internal leaf cell with no libraries guide page, so its PAD pin is found by the same input-pin loop.
                # an IBUFDS is DIFFINBUF + IBUFCTRL inside: keep walking input pins until a port is reached. IBUFDISABLE (High disables the
                # buffer) and INTERMDISABLE are controls, not the clock: a port on one of them is not the clock port.
                set dis [pin_state $cell IBUFDISABLE]
                if {$dis ne "absent" && $dis ne "off" && $dis ne "unconnected"} {
                    if {[string match dyn* $dis]} { return [list gated "input buffer $cell has IBUFDISABLE driven by [string range $dis 4 end]: it can stop the clock" [join $path " <- "]] }
                    return [list gated "input buffer $cell has IBUFDISABLE tied to the disabled level" [join $path " <- "]]
                }
                set nxt ""
                foreach ip [get_pins -quiet -of_objects $cell -filter {DIRECTION == IN}] {
                    if {[get_property REF_PIN_NAME $ip] in {IBUFDISABLE INTERMDISABLE CEB}} continue
                    set n [lindex [get_nets -quiet -of_objects $ip] 0]
                    if {$n eq ""} continue
                    if {[get_ports -quiet -of_objects $n] ne ""} { return [list free "input port [get_ports -of_objects $n]" [join $path " <- "]] }
                    if {$nxt eq ""} { set nxt $n }
                }
                if {$nxt eq ""} { return [list unknown "input buffer $cell has no connected input" [join $path " <- "]] }
                set net $nxt
                continue
            }
            if {[regexp {^(MMCM|PLL|DPLL|XPLL)} $ref]} {
                # CLKINSEL tied low picks CLKIN2 (high picks CLKIN1); pin_state applies IS_CLKINSEL_INVERTED. A dynamic select returned above.
                set inp {}
                if {[pin_state $cell CLKINSEL] eq "off" && [pin_state $cell CLKIN2] ni {absent unconnected}} {
                    set inp [get_pins -quiet -of_objects $cell -filter {REF_PIN_NAME == CLKIN2}]
                }
                if {$inp eq ""} { set inp [get_pins -quiet -of_objects $cell -filter {REF_PIN_NAME == CLKIN1 || REF_PIN_NAME == CLKIN}] }
                if {$inp eq ""} { return [list unknown "no CLKIN on $cell" [join $path " <- "]] }
                set net [lindex [get_nets -of_objects [lindex $inp 0]] 0]
                continue
            }
            if {[regexp {^PS|CIPS|PSXL|PSX} $ref]} { return [list free "processor PL clock from $ref" [join $path " <- "]] }
            # A fabric cell on the clock net makes a divided or gated clock, which can stop. Still "unknown", not "gated": the cell may be
            # a free-running divider. The words "stopped at <REF>" stay, the B12 case reads them.
            if {[regexp {^(LUT[1-6]|FD[CPRS]E?|LD(CE|PE|C|P)?|MUXF[7-9]|CARRY[48]|INV|SRL)} $ref]} { return [list unknown "stopped at $ref: fabric logic drives the clock net, so the clock may stop" [join $path " <- "]] }
            return [list unknown "stopped at $ref" [join $path " <- "]]
        }
        return [list unknown "walk limit" [join $path " <- "]]
    }

    # F2 second defect (e49 Fact 4). On a Vivado 2026.2 synthesized checkpoint create_debug_core leaves the Versal AXI debug hub clock on a net with no
    # driver (aclk0_aclk) and prints no Chipscope 16-570; opt_design then drops the hub, NoC and reset connections. On a 2026.1 checkpoint Vivado
    # connects the hub clock to the ILA clock itself. Do the same, only when the net has no driver, and say so. A hub that was already there is not touched.
    # H12 (Mike, 2026-11-01): this is the ONE narrow exception to "the skill never connects the hub". It runs only on Versal, only on an undriven pin
    # (never over an existing connection), and only in a checkpoint session (a project reopen lets Vivado reconnect the hub, and the connect is not saved).
    # It is removed when Vivado fixes the defect.
    proc versal_hub_aclk {groups} {
        variable facts
        variable replay
        variable hub_preexisting
        if {[info exists hub_preexisting] && [string is true -strict $hub_preexisting]} { return }
        if {[project_open_p]} { return }
        set port [lindex [get_debug_ports -quiet axi_dbg_hub/aclk] 0]
        if {$port ne "axi_dbg_hub/aclk" || [llength $groups] == 0} { return }
        set net [get_nets -quiet -of_objects [get_pins -quiet $port]]
        # no net at all is not this defect (that is what the report after opt_design says); an undriven net is
        if {$net eq "" || [llength [get_pins -quiet -of_objects $net -filter {DIRECTION == OUT}]] > 0} { return }
        set clk ""
        foreach g $groups { if {[dict get $g clock_verdict] eq "free"} { set clk [dict get $g clocknet]; break } }
        set notfree ""
        if {$clk eq ""} { set clk [dict get [lindex $groups 0] clocknet]; set notfree " That clock is not a free-running one, so check it (UG908: the hub clock must be free running)." }
        set vers unknown
        catch {set vers [version -short]}
        connect_debug_port $port [get_nets $clk]
        lappend replay "connect_debug_port [list $port] \[get_nets [replay_braced $clk]\]"
        dict set facts hub_clock $clk
        say "WARNING: workaround for a Vivado 2026.2 defect: Vivado left the AXI debug hub clock $port with no driver (net $net), so opt_design would drop the hub, NoC and reset connections without a message. The skill connected $port to the ILA clock $clk, as Vivado 2026.1 does.$notfree Check that this clock is free running. (The defect was measured on Vivado 2026.2; this session runs Vivado $vers.) (AI only: code C04)"
    }

    # The debug hub shares an ILA clock, and Vivado picks it (UltraScale and UltraScale+: the clock of the first ILA whose clk is connected). The skill
    # never moves or chooses the hub clock: apply connects a core on a free clock first (H3). Here it only records which clock the hub got and says so.
    proc set_hub_clock {groups} {
        variable facts
        variable hub_preexisting
        if {[require_architecture] eq "versal"} { versal_hub_aclk $groups; return }
        if {[get_debug_ports -quiet dbg_hub/clk] eq ""} { return }
        set hub [lindex [connected_nets dbg_hub clk] 0]
        if {[info exists hub_preexisting] && $hub_preexisting} {
            dict set facts hub_clock_preexisting 1
            dict set facts hub_clock $hub
            say "NOTE pre-existing debug hub left on $hub (ILA clocks: [join [lmap g $groups {dict get $g clocknet}] {, }])"
            set onfree 0
            foreach g $groups { if {[dict get $g clock_verdict] eq "free" && [dict get $g clocknet] eq $hub} { set onfree 1 } }
            # C02 (hub_clock_report) already traces the hub clock; C03 adds that we did not change it. Quiet when the clock is provably free.
            if {$hub eq ""} {
                say "NOTE the clock of the pre-existing debug hub could not be read, so it is not traced."
            } elseif {!$onfree && [lindex [clock_source $hub] 0] ne "free"} {
                say "WARNING C03 the pre-existing debug hub was left on $hub, which is not a free-running ILA clock, so it may stop and the hub link can die. The skill does not move a hub it did not create. Change dbg_hub/clk yourself if needed."
            }
            return
        }
        dict set facts hub_clock $hub
        say "debug hub clock: $hub (ILA clocks: [join [lmap g $groups {dict get $g clocknet}] {, }])"
    }
}
