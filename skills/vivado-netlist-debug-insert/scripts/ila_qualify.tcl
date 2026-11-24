# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_qualify.tcl: per-net rules, family limits. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # ---- 3. qualify one net ----------------------------------------------
    # Returns a dict: ok 0|1, code, evidence, clocknet, clock, warn.
    proc qualify {net} {
        set net [get_nets -quiet $net]
        if {$net eq ""} { return [dict create ok 0 code N01 evidence "net not in the netlist. A net between combinational operators is often merged into a LUT and removed by synthesis (UG903). Probe at a hierarchy boundary or at a register output instead (UG949, Choosing Debug Nets)."] }
        set name [get_property NAME $net]
        if {[llength [get_pins -quiet -of_objects $net -leaf -filter {IS_CLOCK && DIRECTION == IN}]] > 0} {
            return [dict create ok 0 code N04 evidence "$name drives clock pins, it is a clock net"]
        }
        if {[get_property TYPE $net] ne "SIGNAL"} {
            return [dict create ok 0 code N03 evidence "net type is [get_property TYPE $net], not SIGNAL"]
        }
        set oport [get_ports -quiet -of_objects $net -filter {DIRECTION == OUT}]
        if {$oport ne ""} {
            return [dict create ok 0 code N12 evidence "$name is the net on output port $oport. Vivado silently refuses to connect a probe to a port-boundary net. Probe the register or logic net that feeds the port."]
        }
        set drv [get_pins -quiet -of_objects $net -leaf -filter {DIRECTION == OUT}]
        set port [get_ports -quiet -of_objects $net -filter {DIRECTION == IN}]
        if {[llength $drv] == 0 && $port eq ""} {
            return [dict create ok 0 code N02 evidence "$name has no driver.[vector_bit_reason $name]"]
        }
        if {[llength $drv] > 0} {
            set dcell [get_cells -of_objects [lindex $drv 0]]
            set ref [get_property REF_NAME $dcell]
            if {[string match "GND" $ref] || [string match "VCC" $ref]} {
                return [dict create ok 0 code N05 evidence "$name is a constant ($ref).[vector_bit_reason $name]"]
            }
            if {[in_debug_core [get_property NAME $dcell]] || [regexp {debug_hub|labtools} $ref]} {
                return [dict create ok 0 code N06 evidence "$name is inside a debug core"]
            }
            # A net inside a hardened connection (CIPS to NoC) has no fabric route. Vivado does not refuse it, but routing then
            # fails (Route 35-19) and no image is written.
            if {[regexp {^(PS9|PS11|NOC)} $ref]} {
                set lrefs {}
                foreach lp [get_pins -quiet -of_objects $net -leaf -filter {DIRECTION == IN}] { lappend lrefs [get_property REF_NAME [get_cells -of_objects $lp]] }
                set fab 0
                foreach r $lrefs { if {![regexp {^(PS9|PS11|NOC)} $r]} { set fab 1 } }
                if {!$fab && [llength $lrefs] > 0} {
                    return [dict create ok 0 code N15 evidence "$name is a hardened connection ($ref to [lindex $lrefs 0]) with no fabric route. A probe on it fails at route_design and no image is written."]
                }
            }
            if {![get_property IS_SEQUENTIAL $dcell]} {
                # combinational driver: follow the fan-in to its register startpoints
                set sp [all_fanin -quiet -flat -startpoints_only -only_cells $net]
                set clks {}
                foreach c $sp {
                    foreach cp [get_pins -quiet -of_objects $c -filter {IS_CLOCK && DIRECTION == IN}] {
                        lappend clks {*}[top_nets $cp]
                    }
                }
                set clks [lsort -unique $clks]
                if {[llength $clks] == 0} {
                    # A kept wire is a LUT1 in synthesis (AR 000040253): a LUT fed only by constants or undriven nets is a constant or undriven bit.
                    set st [net_state $net]
                    if {$st eq "undrivenfed"} {
                        return [dict create ok 0 code N02 evidence "$name has no driver: it is a LUT fed only by undriven nets.[comb_input_reason $dcell][vector_bit_reason $name]"]
                    }
                    if {$st eq "constfed"} {
                        return [dict create ok 0 code N05 evidence "$name is a constant: it is a LUT fed only by constants.[comb_input_reason $dcell][vector_bit_reason $name]"]
                    }
                    return [dict create ok 0 code N07 evidence "$name is combinational with no clocked startpoint.[comb_input_reason $dcell][vector_bit_reason $name]"]
                }
                if {[llength $clks] > 1} {
                    return [dict create ok 0 code N08 evidence "$name is driven from more than one clock net: $clks"]
                }
                return [dict create ok 1 code W01 evidence "combinational net, clock net [lindex $clks 0]" clocknet [lindex $clks 0] warn "combinational net: the captured value can glitch"]
            }
            # A net between two ASYNC_REG registers (a synchronizer): Vivado refuses it (Chipscope 16-3) and
            # drops it from the probe port with no error from connect_debug_port.
            set dar [get_property ASYNC_REG $dcell]
            if {$dar in {1 true TRUE}} {
                foreach lp [get_pins -quiet -of_objects $net -leaf -filter {DIRECTION == IN}] {
                    set lc [get_cells -quiet -of_objects $lp]
                    if {$lc ne "" && [get_property ASYNC_REG $lc] in {1 true TRUE}} {
                        return [dict create ok 0 code N13 evidence "$name is between synchronizer registers ([get_property NAME $dcell] to [get_property NAME $lc], ASYNC_REG). Vivado refuses to debug it (Chipscope 16-3). Probe the last synchronizer stage instead."]
                    }
                }
            }
            set cpins [get_pins -quiet -of_objects $dcell -filter {IS_CLOCK && DIRECTION == IN}]
            set clknets {}
            foreach cp $cpins { lappend clknets {*}[top_nets $cp] }
            set clknets [lsort -unique $clknets]
            if {[llength $clknets] == 0} {
                return [dict create ok 0 code N07 evidence "driver [get_property NAME $dcell] has no connected clock pin"]
            }
            if {[llength $clknets] > 1} {
                return [dict create ok 0 code N08 evidence "driver has more than one clock net: $clknets"]
            }
            set clkobj [get_clocks -quiet -of_objects [lindex $cpins 0]]
            set per ""
            if {$clkobj ne ""} { set per [get_property PERIOD [lindex $clkobj 0]] }
            return [dict create ok 1 code OK evidence "driver [get_property NAME $dcell] ($ref), clock net [lindex $clknets 0], clock [join $clkobj ,]" clocknet [lindex $clknets 0] clock [join $clkobj ,] period $per]
        }
        # driven by an input port
        return [dict create ok 0 code N07 evidence "$name comes straight from input port $port: no capture clock"]
    }

    # ---- 3c. per-family limits (LF2) -------------------------------------------------------------------------------
    # One table for both families. max_width: bits of one probe port. max_total_bits: sum of all probe ports of one core ("" = no
    # documented cap). max_ports: probe ports per core. hub_cores: debug cores one AXI Debug Hub takes (0 = not a limit here).
    # UltraScale and UltraScale+ (and 7 series): PG172 "probe port width is in the range of 1 to 4,096", "the total number of bits
    # (sum of all probe ports) cannot exceed 65,536 bits", "The maximum number of Probe ports is 1,024".
    # Versal: PG357 C_PROBE<n>_WIDTH 1 to 1024. Live 2026.1: PORT_WIDTH accepts 4096 but implement_debug_core fails with
    # IP_Flow 19-3458 "Value '4096' is out of the range (1,1024)". PG361: the AXI Debug Hub connects at most 64 cores.
    # port_limits (ila_plan.tcl) checks max_width per port (one signal) and max_total_bits per core; each signal is a port, so the total is reachable.
    proc family_limits {arch} {
        if {$arch eq "versal"} {
            return [dict create family Versal max_width 1024 max_total_bits "" max_ports 1024 hub_cores 64 \
                    source "PG357 probe width 1 to 1024; Vivado IP_Flow 19-3458 refuses more at implement_debug_core"]
        }
        return [dict create family "UltraScale/UltraScale+" max_width 4096 max_total_bits 65536 max_ports 1024 hub_cores 0 \
                source "PG172 probe width 1 to 4096 and at most 65,536 probe bits per ILA core"]
    }

    # Debug cores that already hang on the Versal AXI Debug Hub (PG361: at most 64). This counts the debug cores Vivado lists, minus
    # the hub itself, minus the u_ila_* cores a -replace_existing plan deletes, plus the instantiated AXI-Stream debug IPs
    # (axis_ila, axis_vio) and PCIe link debug blocks that a cell name shows. The last group is a name match, so it is a hint:
    # a miscount matters only within a few cores of 64.
    proc hub_core_count {replace} {
        set n 0
        set targets [replace_deleted $replace]
        foreach c [get_debug_cores -quiet] {
            if {[regexp {(^|/)(axi_)?dbg_hub} $c]} continue
            if {$replace && $c in $targets} continue
            incr n
        }
        # Only the cell of the IP itself is a core. The cells inside it (the ila_lib, the cores of a block design) also carry the name, and
        # were counted too: a Versal DFX design with 6 cores read as 237. A block design wraps the IP, so the reference name ends in the IP name.
        set names {}
        foreach c [get_cells -quiet -hier -filter {!IS_PRIMITIVE && (REF_NAME =~ *axis_ila* || REF_NAME =~ *axis_vio* || REF_NAME =~ *pcie*link*debug*)}] {
            if {![regexp {(^|_)(axis_ila|axis_vio)(_[0-9]+)*(_CV)?$|pcie.*link.*debug} [get_property REF_NAME $c]]} continue
            lappend names [get_property NAME $c]
        }
        foreach nm $names {
            set inner 0
            foreach o $names { if {$o ne $nm && [string first "$o/" $nm] == 0} { set inner 1; break } }
            if {!$inner} { incr n }
        }
        return $n
    }

    # ALL_PROBE_SAME_MU_CNT (comparators per probe) the library sets. The range is 1 to 16. Storage qualification (capture control) takes
    # one comparator, so it needs at least 2; advanced trigger alone works with 1 (UG908 Table 1). Live 2026.1, opt_design on xcvu095:
    # adv=1 strg=0 with 1 or 2 builds; strg=1 or adv=1 strg=1 with 1 fails (Chipscope 16-338); strg=1 with 2 builds.
    # The library used 2 for advanced trigger alone before; 1 is enough, so the default is now the documented minimum.
    # want: the user's count (ila::plan -comparators, 1 to 16), or -1 for the rule (mu_min). It is never below the rule.
    proc mu_count {adv strg {want -1}} { return [expr {max($want, [mu_min $adv $strg])}] }
    proc mu_min {adv strg} { return [expr {$strg ? 2 : 1}] }
}
