# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_net_state.tcl: buses, vectors, net state, notes. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # The declared width of a probed bus, and what the bits that are not probed are connected to. Returns "" when unknown.
    # Two sources, both of the net's own bus. (1) The port pin bus the probed nets feed (for example an IP port AXI_00_AWADDR[36:0]), only when
    # every probed bit sits on the pin with the same index and the pin bus is at least as wide as the net's bus; a carry or LUT pin bus fails
    # that test (count[31] feeds S[7] of a CARRY8). (2) The bus of the net names in the netlist (count[31:0]), only when it is complete from bit 0.
    # (The pin bus may be wider than a net bus that lost its constant bits, as in C14: nets 5 to 27 only, port AWADDR[36:0].)
    proc bus_declared {netname probed} {
        if {![regexp {^(.*)\[(\d+)\]$} $netname -> base hi]} { return "" }
        if {[get_nets -quiet $netname] eq ""} { return "" }
        set have [dict create]
        foreach bn [nets_glob "${base}\[*\]"] {
            if {[regexp {^(.*)\[(\d+)\]$} [get_property NAME $bn] -> b2 i2] && $b2 eq $base} { dict set have $i2 1 }
        }
        set nw [expr {[dict size $have] == 0 ? 0 : [lindex [lsort -integer [dict keys $have]] end] + 1}]
        if {$nw == 0} { return "" }
        set n [get_nets -quiet $netname]
        foreach lp [get_pins -quiet -of_objects $n -leaf -filter {DIRECTION == IN}] {
            set rp [get_property REF_PIN_NAME $lp]
            if {![regexp {^(.*)\[(\d+)\]$} $rp -> pb pi]} continue
            set c [get_cells -quiet -of_objects $lp]
            if {$c eq ""} continue
            set max -1; set info [dict create]
            foreach sp [get_pins -quiet -of_objects $c -filter "REF_PIN_NAME =~ \"${pb}*\""] {
                set r2 [get_property REF_PIN_NAME $sp]
                if {![regexp {^(.*)\[(\d+)\]$} $r2 -> b2 i2] || $b2 ne $pb} continue
                if {$i2 > $max} { set max $i2 }
                set sn [get_nets -quiet -of_objects $sp]
                set kind "unconnected"
                if {$sn ne ""} {
                    set ty [get_property TYPE $sn]
                    set kind [expr {$ty eq "GROUND" ? "tied to 0" : ($ty eq "POWER" ? "tied to 1" : "connected to another net, not probed")}]
                }
                dict set info $i2 $kind
            }
            if {$max < 1 || $max + 1 < $nw} continue
            # which pin indexes carry a probed net, and does each probed bit sit on the pin with its own index
            set pidx {}
            set same 1
            foreach pn $probed {
                set pnn [get_nets -quiet $pn]
                if {$pnn eq "" || ![regexp {^.*\[(\d+)\]$} $pn -> bi]} { set same 0; break }
                set found 0
                foreach l2 [get_pins -quiet -of_objects $pnn -leaf -filter {DIRECTION == IN}] {
                    if {[get_cells -quiet -of_objects $l2] eq $c && [regexp {^(.*)\[(\d+)\]$} [get_property REF_PIN_NAME $l2] -> b3 i3] && $b3 eq $pb} {
                        lappend pidx $i3
                        if {$i3 == $bi} { set found 1 }
                    }
                }
                if {!$found} { set same 0; break }
            }
            if {!$same} continue
            set rest {}
            for {set i 0} {$i <= $max} {incr i} {
                if {[lsearch -exact $pidx $i] >= 0} continue
                set kk "no pin"; if {[dict exists $info $i]} { set kk [dict get $info $i] }; lappend rest [list $i $kk]
            }
            # compress by kind
            set byk [dict create]
            foreach r $rest { dict lappend byk [lindex $r 1] [lindex $r 0] }
            set txt "declared width [expr {$max+1}] (pin bus $pb\[$max:0\] on [get_property NAME $c])"
            dict for {k idxs} $byk { append txt "; bits $idxs: $k" }
            return $txt
        }
        # the netlist proves a width only for a bus that holds every bit from 0 to its top
        if {[dict size $have] != $nw} { return "" }
        return "declared width $nw (net bus $base\[[expr {$nw-1}]:0\])"
    }

    # Driver state of one net: driven, undriven, const0, const1 or port (driven by an input port).
    proc net_state {net} {
        set drv [get_pins -quiet -of_objects $net -leaf -filter {DIRECTION == OUT}]
        if {[llength $drv] == 0} {
            return [expr {[get_ports -quiet -of_objects $net -filter {DIRECTION == IN}] ne "" ? "port" : "undriven"}]
        }
        set dcell [get_cells -of_objects [lindex $drv 0]]
        set ref [get_property REF_NAME $dcell]
        if {$ref eq "GND"} { return const0 }
        if {$ref eq "VCC"} { return const1 }
        # A keep buffer or LUT whose inputs are all constants, or all undriven, passes that on (synthesis puts a LUT1 on a kept wire).
        if {[regexp {^LUT\d$} $ref]} {
            set sts {}
            foreach ip [get_pins -quiet -of_objects $dcell -filter {DIRECTION == IN}] {
                set in [get_nets -quiet -of_objects $ip]
                lappend sts [expr {$in eq "" ? "undriven" : [lindex [net_state_direct $in] 0]}]
            }
            set u [lsort -unique $sts]
            if {[llength $u] == 1 && [lindex $u 0] eq "undriven"} { return undrivenfed }
            if {[llength $u] > 0 && [lsearch -not -regexp $u {^(const0|const1)$}] < 0} { return constfed }
        }
        return driven
    }
    # State of a net from its own driver only (no look-through), as a one-word list.
    proc net_state_direct {net} {
        set drv [get_pins -quiet -of_objects $net -leaf -filter {DIRECTION == OUT}]
        if {[llength $drv] == 0} { return [expr {[get_ports -quiet -of_objects $net -filter {DIRECTION == IN}] ne "" ? "port" : "undriven"}] }
        set ref [get_property REF_NAME [get_cells -of_objects [lindex $drv 0]]]
        return [expr {$ref eq "GND" ? "const0" : ($ref eq "VCC" ? "const1" : "driven")}]
    }

    # The bits of the vector a net belongs to, as a list of {index state marked}, sorted by index. {} when the net is not a vector bit.
    # One scan per vector per plan (vec_cache is cleared when a plan starts).
    proc vector_bits {netname} {
        variable vec_cache
        if {![info exists vec_cache]} { set vec_cache [dict create] }
        if {![regexp {^(.*)\[(\d+)\]$} $netname -> base ix]} { return {} }
        if {[dict exists $vec_cache $base]} { return [dict get $vec_cache $base] }
        set bits {}
        foreach n [nets_glob "${base}\[*\]"] {
            if {![regexp {^(.*)\[(\d+)\]$} [get_property NAME $n] -> b2 i2] || $b2 ne $base} continue
            set md [expr {[get_property MARK_DEBUG $n] in {1 true TRUE} ? 1 : 0}]
            lappend bits [list $i2 [net_state $n] $md]
        }
        set bits [lsort -integer -index 0 $bits]
        dict set vec_cache $base $bits
        return $bits
    }

    # "0-2" style ranges for a sorted list of integers.
    proc int_ranges {ints} {
        set out {}
        set i 0
        while {$i < [llength $ints]} {
            set lo [lindex $ints $i]; set hi $lo
            while {$i + 1 < [llength $ints] && [lindex $ints [expr {$i + 1}]] == $hi + 1} { incr i; set hi [lindex $ints $i] }
            lappend out [expr {$lo == $hi ? $lo : "$lo-$hi"}]
            incr i
        }
        return [join $out ,]
    }

    # Per-bit reason for a bit of a vector whose state is undriven or constant (N02, N05). "" when the net is not a vector bit.
    # AR 000040253 (Vivado 2025.1): MARK_DEBUG on a partially driven vector can give a non-equivalent netlist, because the undriven
    # bits become ground. So the reason lists every bit, and warns when some other bit is driven.
    proc vector_bit_reason {netname} {
        set bits [vector_bits $netname]
        if {[llength $bits] < 2} { return "" }
        regexp {^(.*)\[(\d+)\]$} $netname -> base ix
        set by [dict create]
        foreach b $bits { dict lappend by [lindex $b 1] [lindex $b 0] }
        set parts {}
        set label [dict create driven driven port "driven by an input port" undriven undriven const0 "constant 0" const1 "constant 1" \
                   constfed "fed only by constants (through a LUT)" undrivenfed "fed only by undriven nets (through a LUT)"]
        foreach st {driven port undriven undrivenfed const0 const1 constfed} {
            if {![dict exists $by $st]} continue
            lappend parts "[dict get $label $st]: bits [int_ranges [dict get $by $st]]"
        }
        set txt " Bit $ix is one of [llength $bits] bits of the vector $base ([join $parts {; }])."
        set bad [expr {[dict exists $by undriven] || [dict exists $by undrivenfed] || [dict exists $by const0] || [dict exists $by const1] || [dict exists $by constfed]}]
        set good [expr {[dict exists $by driven] || [dict exists $by port]}]
        if {$bad && $good} {
            append txt " The vector is partially driven. AR 000040253 (Vivado 2025.1): MARK_DEBUG on a partially driven vector can give a non-equivalent netlist, because the undriven bits become ground. Do not put MARK_DEBUG on this vector. The driven bits can be probed by name."
        }
        return $txt
    }

    # M03: an accepted net that carries MARK_DEBUG and sits in a partially driven vector.
    proc partial_vector_note {netname} {
        set bits [vector_bits $netname]
        set bad 0; set good 0
        foreach b $bits { if {[lindex $b 1] in {driven port}} { set good 1 } else { set bad 1 } }
        if {!($bad && $good)} { return "" }
        return "NOTE M03 $netname carries MARK_DEBUG and sits in a partially driven vector.[vector_bit_reason $netname] Check the vector in the RTL."
    }

    # M02: MARK_DEBUG on one bit of a vector marks the whole bus (UG901). Called with a dict base -> probed bit indexes. Returns notes.
    proc bus_mark_notes {busidx} {
        set out {}
        dict for {base ixs} $busidx {
            set bits [vector_bits "$base\[[lindex $ixs 0]\]"]
            set marked [lmap b $bits {if {![lindex $b 2]} continue; lindex $b 0}]
            if {[llength $marked] == 0 || [llength $ixs] >= [llength $bits]} continue
            lappend out [list $base "NOTE M02 MARK_DEBUG is on [llength $marked] of the [llength $bits] bits of the bus $base (UG901: MARK_DEBUG on one bit of a vector marks the whole bus). The request names [llength $ixs] of them (bits [int_ranges [lsort -integer $ixs]]). Vivado keeps all [llength $bits] bits through synthesis and implementation, which costs optimization on the bits that are not probed. The ILA probes only the bits asked for."]
        }
        return $out
    }

    # A02: one electrical net can have several names. The canonical name is the segment with the fewest hierarchy levels (ties by
    # name, like top_nets). Returns a note when the net has other names, "" otherwise. segnames is the list of segment names.
    proc alias_note {netname segnames} {
        if {[llength $segnames] < 2} { return "" }
        set best ""; set bd 99999
        foreach nm $segnames {
            set d [llength [split $nm /]]
            if {$d < $bd || ($d == $bd && [string compare $nm $best] < 0)} { set bd $d; set best $nm }
        }
        set others [lsearch -all -inline -not -exact [lsearch -all -inline -not -exact $segnames $best] $netname]
        if {$best eq $netname} { set txt "NOTE A02 $netname is the canonical name of this net." } else { set txt "NOTE A02 $netname is probed under this name. Canonical name of the net: $best." }
        if {[llength $others] > 0} { append txt " Other names of the same net: [join [lrange $others 0 5] {, }][expr {[llength $others] > 6 ? " and [expr {[llength $others] - 6}] more" : ""}]." }
        append txt " Any of the names is accepted in a request."
        return $txt
    }

    # Why a combinational net has no register behind it: its driver cell's inputs are constants or undriven. "" otherwise.
    proc comb_input_reason {dcell} {
        set sts {}
        foreach ip [get_pins -quiet -of_objects $dcell -filter {DIRECTION == IN}] {
            set in [get_nets -quiet -of_objects $ip]
            lappend sts "[get_property REF_PIN_NAME $ip] [expr {$in eq "" ? "unconnected" : [string map {const0 {constant 0} const1 {constant 1} port {an input port} driven {driven}} [net_state_direct $in]]}]"
        }
        if {[llength $sts] == 0} { return "" }
        return " The driver [get_property NAME $dcell] ([get_property REF_NAME $dcell]) has inputs: [join $sts {, }]."
    }

    # MARK_DEBUG flag (0 or 1) of each net in a list of names, in order. "" when Vivado returns a different number of nets.
    proc mark_debug_flags {names} {
        set objs [get_nets -quiet $names]
        if {[llength $objs] != [llength $names]} { return "" }
        return [lmap f [get_property MARK_DEBUG $objs] {expr {$f in {1 true TRUE} ? 1 : 0}}]
    }

    # What the plan adds after the groups are built (LF3). groups: the plan groups. segby: probe net name -> segment names.
    # Gives each group a "marked" count (how many probe nets carry MARK_DEBUG), and returns {groups notes}. Notes: M02 whole-bus
    # marking, M03 partially driven vector, A02 canonical name and aliases (the first 8 nets are named, then one summary line).
    proc net_rules_post {groups segby busidx} {
        set notes {}
        set newg {}
        set mnotes {}
        foreach g $groups {
            set names [dict get $g nets]
            set fl [mark_debug_flags $names]
            if {$fl eq ""} { dict set g marked "unknown"; lappend newg $g; continue }
            set mk [lmap n $names f $fl {if {!$f} continue; set n}]
            dict set g marked [list [llength $mk] [llength $names] [lrange $mk 0 2]]
            foreach n [lrange $mk 0 15] { set pn [partial_vector_note $n]; if {$pn ne ""} { lappend mnotes [list $n $pn] } }
            lappend newg $g
        }
        lappend notes {*}[bus_mark_notes $busidx]
        lappend notes {*}$mnotes
        set na 0
        dict for {n segs} $segby {
            set found 0
            foreach g $newg { if {[lsearch -exact [dict get $g nets] $n] >= 0} { set found 1; break } }
            if {!$found} continue
            set an [alias_note $n $segs]
            if {$an eq ""} continue
            incr na
            if {$na <= 8} { lappend notes [list $n $an] }
        }
        if {$na > 8} { lappend notes [list aliases "NOTE A02 [expr {$na - 8}] more probe nets also have other names (the first 8 are listed above). Any of the names is accepted in a request."] }
        return [list $newg $notes]
    }
}
