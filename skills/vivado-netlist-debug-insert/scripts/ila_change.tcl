# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# ila_change.tcl: change a setting of a core this skill inserted (ila::change), prove a setting from the built core (ila::prove_built),
# and the ownership record behind both, the design property ILA_SKILL_CORES. Sourced by ila_insert.tcl after ila_apply.tcl.

namespace eval ila {
    # ---- the ownership record --------------------------------------------------------------------------------------------------
    # Vivado gives a debug core no property of its own (Common 17-118) and its NAME is read only (17-107), so the record is a design
    # property, like ILA_SKILL_TOKEN (mark_design). One entry per core apply made: {name clock-driver-pin probe-bits crc32}, over the nets of every probe port. Measured
    # (CHG-3, both architectures and versions): a design property survives write_checkpoint, a new Vivado, a property change, opt_design
    # and a reopened post-opt checkpoint. It is not in the .ltx. In project mode save_constraints writes it into the XDC (Phase 0, RA1).
    # Flat designs only: a DFX apply writes no entry, so a DFX core is never changed.

    # The entries of the open design. A missing, empty or unreadable property, or one that is not a list, has none. A malformed entry is skipped.
    proc marker_read {} {
        if {[catch {get_property -quiet ILA_SKILL_CORES [current_design]} v] || [catch {llength $v}]} { return {} }
        set out {}
        foreach e $v {
            if {[catch {llength $e} n] || $n != 4 || ![string is integer -strict [lindex $e 2]]} continue
            lappend out $e
        }
        return $out
    }
    # Write the entries. When the design takes no property, nothing is kept: every change is then refused with ILA_CHANGE_NO_MARKER (the safe side).
    proc marker_set {entries} { catch {set_property ILA_SKILL_CORES $entries [current_design]} }
    # The entry of a core apply just made (after its probes and clock are connected). An old entry of the same name is replaced.
    # A core whose fingerprint cannot be read gets no entry, so it is never changed (the safe side); apply goes on.
    proc marker_write {name} {
        if {[catch {core_fingerprint $name} fp]} return
        set have [marker_read]
        if {[llength $have] == 0} { catch {create_property -quiet -class design ILA_SKILL_CORES} }
        set keep [lmap e $have {expr {[lindex $e 0] eq $name ? [continue] : $e}}]
        marker_set [linsert $keep end [list $name {*}$fp]]
    }
    # Drop the entries of deleted cores. The property is written only when an entry goes.
    proc marker_drop {names} {
        set have [marker_read]
        set keep [lmap e $have {expr {[lindex $e 0] in $names ? [continue] : $e}}]
        if {[llength $keep] != [llength $have]} { marker_set $keep }
    }
    # Put back the entries read before a -replace_existing delete (the deleted cores are being built again).
    proc marker_restore {entries} { if {[marker_read] ne $entries} { marker_set $entries } }

    # {clock-driver-pin probe-bits crc32} of a core, read the same way when apply writes the entry and when a change checks it. The probe nets
    # are the ones report_debug_core lists, sorted (Vivado can name a net otherwise than it was asked for). The clock is keyed on the driver
    # pin of its net: on Versal, save and reopen renames the clock net (cips_pl0_ref_clk becomes u_ila_0_cips_pl0_ref_clk); the driver stays (Phase 0, RA2).
    # Every port that carries nets is in it (Phase 4b): the bits are all the nets, the crc is over each port's sorted nets, ports in order, a blank line
    # between ports. A core with one such port has the crc of the first format (probe0 only), so an old entry is still read. A port's type is not in it.
    proc core_fingerprint {core} {
        set by [report_port_nets $core]
        set texts {}
        set bits 0
        foreach p [port_order [dict keys $by]] {
            set nets [dict get $by $p]
            incr bits [llength $nets]
            lappend texts [join [lsort $nets] \n]
        }
        return [list [clock_driver $core [lindex [connected_nets $core clk] 0]] $bits [zlib crc32 [join $texts \n\n]]]
    }
    # Port names (probe0, probe1, ...) in the order of their numbers
    proc port_order {names} {
        set keyed [lmap n $names {list [string range $n 5 end] $n}]
        return [lmap k [lsort -integer -index 0 $keyed] {lindex $k 1}]
    }
    # The nets of every probe port of a core, as a dict port -> nets in the order of the report. report_debug_core is the only source (see
    # connected_nets); it lists a port only when it carries nets, so a port with none is not a key. One report for all ports. With dfx 1 a core in
    # a partition matches the report's short name, as in connected_nets.
    proc report_port_nets {core {dfx 0}} {
        set by [dict create]
        set inside 0
        foreach line [split [report_debug_core -return_string] \n] {
            if {[regexp {^Channel Data for Debug Core "([^"]+)"} $line -> nm]} { set inside [expr {$nm eq $core || ($dfx && [string match "*/$nm" $core])}]; continue }
            if {!$inside} continue
            set f [lmap x [split $line |] {string trim $x}]
            foreach i [lsearch -all -regexp $f {^probe\d+\[\d+\]$}] {
                set net [lindex $f end-1]
                if {$net ne ""} { dict lappend by [lindex [split [lindex $f $i] \[] 0] $net }
            }
        }
        return $by
    }
    # The nets of every probe port as full names, a dict port -> nets. The report names a net by its leaf (count[16]) where a request names it by its
    # full name (bd_i/u_cnt/inst/count[16]), and on a Versal block design the net on a probe pin is a segment whose NAME is not the full name, but whose
    # PARENT is (case PN8, 2026.2). So: the PARENT of the net on each probe pin of the core cell; the report (report_port_nets) when the cell gives none.
    proc pin_port_nets {core} {
        set by [dict create]
        foreach p [get_pins -quiet -of_objects [get_cells -quiet $core] -filter {NAME =~ *probe*}] {
            if {![regexp {/(probe[0-9]+)(?:\[[0-9]+\])?$} [get_property NAME $p] -> pn]} continue
            set n [get_nets -quiet -of_objects $p]
            if {$n eq ""} continue
            set par [get_property -quiet PARENT $n]
            dict lappend by $pn [expr {$par ne "" ? $par : [get_property NAME $n]}]
        }
        if {[dict size $by] == 0} { return [report_port_nets $core] }
        return $by
    }
    # The driver pin of a core's clock net, "" when no net is found. The report gives the leaf name (cips_pl0_ref_clk for bd_i/cips_pl0_ref_clk on
    # Versal before a save), so: the name, then the net on the debug port, then the leaf name at any level (Phase 0, probe_fingerprint_v2.tcl).
    proc clock_driver {core clk} {
        set cn [get_nets -quiet $clk]
        if {[llength $cn] == 0 && [catch {get_nets -quiet -of_objects [get_debug_ports -quiet $core/clk]} cn]} { set cn {} }
        if {[llength $cn] == 0} { set cn [get_nets -quiet -hier $clk] }
        return [join [lsort [get_pins -quiet -leaf -of_objects $cn -filter {DIRECTION == OUT}]] " "]
    }
    # "" when <core> is a core this skill made in this design: its name is in the record and its fingerprint is the one apply wrote. Else it raises
    # ILA_CHANGE_NOT_SKILL_CORE, ILA_CHANGE_NO_MARKER or ILA_CHANGE_CORE_EDITED. Entries of cores that are gone are ignored and said in one line.
    proc change_owner {core} {
        if {[llength [get_debug_cores -quiet $core]] == 0} {
            return -code error "ILA_CHANGE_NOT_SKILL_CORE: there is no debug core named $core in this design. Nothing was changed."
        }
        set entries [marker_read]
        if {[llength $entries] == 0} {
            return -code error "ILA_CHANGE_NO_MARKER: this design has no record of cores made by this skill (a project reopened after save_constraints, or a design from an older skill). The skill changes only cores it can prove it made. Nothing was changed."
        }
        set gone [lmap e $entries {expr {[llength [get_debug_cores -quiet [lindex $e 0]]] ? [continue] : [lindex $e 0]}}]
        if {[llength $gone]} { say "NOTE: the skill's record lists [join $gone {, }]: not in the design any more, so ignored." }
        set e [lsearch -inline -index 0 -exact $entries $core]
        if {$e eq ""} {
            return -code error "ILA_CHANGE_NOT_SKILL_CORE: $core is not in this skill's record for this design. It may be your own core or the wizard's. Nothing was changed."
        }
        lassign $e - drv bits crc
        lassign [core_fingerprint $core] ndrv nbits ncrc
        if {$ndrv ne $drv || $nbits != $bits || $ncrc != $crc} {
            return -code error "ILA_CHANGE_CORE_EDITED: $core is in the skill's record, but its probes or clock changed since the skill made it (now $nbits probe bits and clock driver '$ndrv'; when the skill made it: $bits probe bits and clock driver '$drv'). Nothing was changed."
        }
        return ""
    }

    # ---- ila::change ------------------------------------------------------------------------------------------------------------
    # ila::change <core> ?-depth N? ?-memory_type bram|uram? ?-strg_qual 0|1? ?-adv_trigger 0|1? ?-comparators N? ?-pipe_stages N?
    #   ?-probe_type data|trigger|data_and_trigger? ?-probe_types {name type ...}? ?-dry_run 0|1?
    # No option: show the core (who made it, its settings, what can change). Else check every guard, then set the properties in place in
    # the order Vivado needs, read each one back, and print the user's next steps. In place keeps the probe nets, the clock and the name
    # (CHG-4). A core has one probe port per signal and probe type (unit_ports): -probe_type sets every port, -probe_types sets the port that
    # carries each named net (Q3a to Q3c). Vivado rebuilds the core at the user's next opt_design; the skill never runs it. -trigin and -trigout are refused (D2).
    proc change {args} { return [flat _change {*}$args] }

    # The values ila::change accepts, checked before any Vivado call. Raises ILA_BAD_OPTION, ILA_DEPTH_RANGE, ILA_OPTION_RANGE or ILA_OPTION_UNSUPPORTED.
    proc change_options {argl} {
        if {[llength $argl] == 0 || [string match -* [lindex $argl 0]]} {
            return -code error "ILA_BAD_OPTION: the core name must come first (AI only: ila::change <core> ?-depth N? ...). Nothing was changed."
        }
        array set o {-depth "" -memory_type "" -strg_qual "" -adv_trigger "" -comparators "" -pipe_stages "" -probe_type "" -probe_types "" -trigin "" -trigout "" -dry_run 0}
        check_options ila::change ILA_BAD_OPTION [array names o] [lrange $argl 1 end]
        array set o [lrange $argl 1 end]
        if {$o(-trigin) ni {"" 0} || $o(-trigout) ni {"" 0}} { return -code error [trigger_refusal] }
        if {$o(-depth) ne "" && [lsearch -exact [valid_depths] $o(-depth)] < 0} {
            return -code error "ILA_DEPTH_RANGE: depth $o(-depth) is not valid. Vivado accepts [valid_depths]. Nothing was changed."
        }
        foreach {k lo hi} {-pipe_stages 0 6 -comparators 1 16 -strg_qual 0 1 -adv_trigger 0 1 -dry_run 0 1} {
            if {$o($k) ne "" && (![regexp {^[0-9]+$} $o($k)] || $o($k) < $lo || $o($k) > $hi)} {
                return -code error "ILA_OPTION_RANGE: $k must be a whole number from $lo to $hi (got $o($k)). Nothing was changed."
            }
        }
        if {$o(-probe_type) ni {"" data data_and_trigger trigger}} {
            return -code error "ILA_OPTION_RANGE: -probe_type must be data or data_and_trigger (got $o(-probe_type)). Nothing was changed."
        }
        if {$o(-memory_type) ni {"" bram uram}} {
            return -code error "ILA_OPTION_RANGE: -memory_type must be bram or uram (got $o(-memory_type)). Nothing was changed."
        }
        probe_type_pairs $o(-probe_types)
        return [array get o]
    }

    # Trigger in and out are refused at insertion and at change (D2): US+ reads C_TRIGIN_EN true and builds 0 (IPX-Q4-trigprops-usp-*), Versal reads false.
    proc trigger_refusal {} {
        return "ILA_OPTION_UNSUPPORTED: trigger in and trigger out (C_TRIGIN_EN, C_TRIGOUT_EN) cannot be set by this skill. On UltraScale+ Vivado accepts the setting and builds no trigger ports; on Versal it reads back off. They need ports wired to your logic, which means an ILA instantiated in your HDL. Nothing was done."
    }
    # "b", "b and c", "b, c and d", else the first three and a count: the nets that share a port with the one asked about
    proc and_list {l} {
        if {[llength $l] == 1} { return [lindex $l 0] }
        if {[llength $l] <= 3} { return "[join [lrange $l 0 end-1] {, }] and [lindex $l end]" }
        return "[join [lrange $l 0 2] {, }] and [expr {[llength $l] - 3}] more"
    }
    # The type every probe port of a core gets, as a dict port -> type (Q3a to Q3c). whole is -probe_type ("" for none), pairs the checked
    # -probe_types as {{name type} ...}. A net takes the type named for it, else the whole-core type, else the type its port has now. A name is a
    # plain name, so a glob names every net it matches. The port of a net is found by reading the nets of each port (Vivado has no net-to-port
    # query, F17); a net the core does not probe is left out of a glob, and a name with no net on the core is refused. A port whose nets get
    # two types is refused (Q3b).
    proc change_port_types {core cur whole pairs} {
        set base [dict create]
        foreach pp [dict get $cur pports] { lassign $pp pn t -; dict set base $pn [expr {$whole ne "" ? $whole : $t}] }
        if {[llength $pairs] == 0} { return $base }
        set by [pin_port_nets $core]
        set owner [dict create]
        dict for {pn nets} $by { foreach n $nets { dict set owner $n $pn } }
        set asked [dict create]
        foreach pr $pairs {
            lassign $pr name type
            set found [resolve_item $name]
            if {[llength $found] == 0} {
                set why [expr {$::ila::last_refusal ne "" ? $::ila::last_refusal : "nothing in the netlist matches it. Name a register or net that is probed, with its path when it is inside a module."}]
                return -code error "ILA_BAD_ITEM: the probe type of '$name' was asked for, but $why Nothing was changed."
            }
            set hit 0
            foreach pair $found {   ;# foreach, not lmap: a net with a bracket stays an object
                lassign $pair net origin
                set names [list [get_property NAME $net]]
                set sgs [get_nets -quiet -segments $net]
                if {[llength $sgs] > 0} { lappend names {*}[get_property NAME $sgs] }
                foreach nm [lsort -unique $names] {
                    if {![dict exists $owner $nm]} continue
                    incr hit
                    if {[dict exists $asked $nm] && [dict get $asked $nm] ne $type} {
                        return -code error "ILA_BAD_ITEM: the net '$nm' is given two probe types, [type_words [dict get $asked $nm]] and [type_words $type]. Give each net one type. Nothing was changed."
                    }
                    dict set asked $nm $type
                    break
                }
            }
            if {$hit == 0} {
                return -code error "ILA_CHANGE_NET_NOT_ON_CORE: '$name' matches nets in the design, but $core does not probe any of them. Nothing was changed."
            }
        }
        set out $base
        dict for {pn nets} $by {
            set eff [lmap n $nets {expr {[dict exists $asked $n] ? [dict get $asked $n] : [dict get $base $pn]}}]
            set uniq [lsort -unique $eff]
            if {[llength $uniq] > 1} {
                set i [lsearch -exact [lmap n $nets {dict exists $asked $n}] 1]
                set mine [lindex $eff $i]
                set others [lmap n $nets t $eff {expr {$t eq $mine ? [continue] : $n}}]
                return -code error "ILA_CHANGE_SHARED_PORT: net [lindex $nets $i] shares a port with [and_list $others], and one port has one probe type. Each signal has its own port, so these are bits of one signal (or the core was built by an earlier version of the skill, which put several signals on one port); to give it its own type, plan again with replace. Nothing was changed. Do not plan again with replace unless the user asks: it deletes this core and its probes."
            }
            dict set out $pn [lindex $uniq 0]
        }
        return $out
    }
    # Placed cells, counted the way check_input does (PLACED cells, and FIXED cells that are not I/O when the Design State is past optimized).
    # ila::change counts them itself: it never honours the -allow_state_reset override of check_input (input_gate).
    proc change_placed {} {
        set n [llength [get_cells -quiet -hier -filter {IS_PRIMITIVE && STATUS == "PLACED"}]]
        if {[string tolower [design_state]] ni {synthesized optimized}} {
            incr n [llength [get_cells -quiet -hier -filter {IS_PRIMITIVE && STATUS == "FIXED" && PRIMITIVE_GROUP != "I/O"}]]
        }
        return $n
    }

    # The settings of a core as a dict: depth, memtype (bram or uram), strg, adv (0 or 1), mucnt, pipe, pports ({port type width} of every probe
    # port in port order; type data, data_and_trigger or trigger), ptype (the type of every port when they agree, else mixed), ports (probe ports),
    # width (probe bits of all ports), stored (the bits one sample stores: all but the trigger only ports). C_MEMORY_TYPE exists on Versal only (12-4444 elsewhere).
    proc core_settings {core arch} {
        set c [get_debug_cores $core]
        set mt [expr {$arch eq "versal" && [get_property C_MEMORY_TYPE $c] == 1 ? "uram" : "bram"}]
        set byname [dict create]
        foreach p [get_debug_ports -quiet $core/probe*] { dict set byname [file tail $p] $p }
        set pports {}
        set w 0
        set stored 0
        foreach pn [port_order [dict keys $byname]] {
            set p [dict get $byname $pn]
            set pw [get_property PORT_WIDTH $p]
            set t [string tolower [get_property PROBE_TYPE $p]]
            if {$t eq ""} { set t data_and_trigger }   ;# Vivado's default, which apply leaves unset
            lappend pports [list $pn $t $pw]
            incr w $pw
            if {$t ne "trigger"} { incr stored $pw }
        }
        return [dict create depth [get_property C_DATA_DEPTH $c] memtype $mt strg [string is true [get_property C_EN_STRG_QUAL $c]] \
            adv [string is true [get_property C_ADV_TRIGGER $c]] mucnt [get_property ALL_PROBE_SAME_MU_CNT $c] pipe [get_property C_INPUT_PIPE_STAGES $c] \
            pports $pports ptype [ports_ptype $pports] ports [llength $pports] width $w stored $stored]
    }
    # The one type of every port in a {port type width} list, or mixed
    proc ports_ptype {pports} {
        set types [lsort -unique [lmap pp $pports {lindex $pp 1}]]
        return [expr {[llength $types] == 1 ? [lindex $types 0] : ([llength $types] == 0 ? "data_and_trigger" : "mixed")}]
    }
    # core_settings with the port types of a dict port -> type (change_port_types): the ports, ptype and stored keys follow
    proc settings_typed {s ptypes} {
        set pports {}
        set stored 0
        foreach pp [dict get $s pports] {
            lassign $pp pn - w
            set t [dict get $ptypes $pn]
            lappend pports [list $pn $t $w]
            if {$t ne "trigger"} { incr stored $w }
        }
        dict set s pports $pports
        dict set s ptype [ports_ptype $pports]
        dict set s stored $stored
        return $s
    }
    # Plain words for one setting
    proc setting_text {key v} {
        switch $key {
            memtype { return [expr {$v eq "uram" ? "UltraRAM" : "block RAM"}] }
            strg - adv { return [expr {$v ? "on" : "off"}] }
            ptype { return [string map {_ " "} $v] }
        }
        return $v
    }
    # The probe type of a core in words: one type for all ports, else each port with its nets and its type
    proc ptype_text {s} {
        if {[dict get $s ptype] ne "mixed"} { return "probe type [setting_text ptype [dict get $s ptype]]" }
        return "probe types: [join [lmap pp [dict get $s pports] {lassign $pp pn t w; format {%s (%d %s) %s} $pn $w [expr {$w == 1 ? "net" : "nets"}] [type_words $t]}] {, }]"
    }
    proc change_show {core arch} {
        if {[catch {change_owner $core} err]} {
            if {[llength [get_debug_cores -quiet $core]] == 0} { return -code error $err }
            say "$core was not inserted by this skill, or that cannot be proven ([lindex [split $err :] 0]). It cannot be changed here."
        } else {
            lassign [core_fingerprint $core] drv bits
            say "$core was inserted by this skill (clock driver $drv, $bits probe bits, fingerprint matches)."
        }
        set s [core_settings $core $arch]
        say "settings now: depth [dict get $s depth], memory [setting_text memtype [dict get $s memtype]], capture control [setting_text strg [dict get $s strg]], advanced trigger [setting_text adv [dict get $s adv]], comparators [dict get $s mucnt], pipe stages [dict get $s pipe], [ptype_text $s]."
        if {$err eq ""} {
            say "you can change: depth ([valid_depths]),[expr {$arch eq "versal" ? " memory (block RAM, or UltraRAM at depth 4096 or more)," : ""}] capture control, advanced trigger, comparators (1 to 16, with advanced trigger or capture control), pipe stages (0 to 6), probe type ([expr {[dict get $s ports] > 1 ? "data, trigger only, or trigger and data: for the whole core, or for the port that carries a named net" : "data, or data and trigger"}])."
        }
        return {}
    }

    # Block RAM (or UltraRAM) tiles free for a core at a new depth: the free tiles, plus the core's own RAM cells (freed when Vivado rebuilds
    # it), minus the modelled tiles of the other unbuilt cores (existing_ila_ram counts this core too, so its own share is added back).
    # -1 when the part gives no count.
    proc change_free_tiles {core s uram arch} {
        if {$uram} {
            set free [free_uram_tiles]
            set own [uram_cells_used [get_cells -quiet -hier -filter "NAME =~ $core/* && (REF_NAME =~ URAM288*)"]]
            set model [uram_tiles [dict get $s depth] [dict get $s width]]
        } else {
            set free [free_bram_tiles]
            set own [ram_tiles_used [get_cells -quiet -hier -filter "NAME =~ $core/* && (REF_NAME =~ RAMB* || REF_NAME =~ FIFO*)"]]
            set model [bram_tiles [dict get $s depth] [dict get $s width] $arch]
        }
        if {$free < 0} { return -1 }
        set mine [expr {([dict get $s memtype] eq "uram") == $uram ? max(0, $model - $own) : 0}]
        return [expr {$free + $own - ([existing_ila_ram 0 "" $uram] - $mine)}]
    }
    proc change_tiles {depth width uram arch} { return [expr {$uram ? [uram_tiles $depth $width] : [bram_tiles $depth $width $arch]}] }

    proc _change {args} {
        array set o [change_options $args]
        set core [lindex $args 0]
        set arch [require_architecture]
        if {[llength $args] == 1} { return [change_show $core $arch] }
        if {[llength [dfx_cells]] > 0} {
            return -code error "ILA_CHANGE_DFX: this design has a reconfigurable partition (DFX). Changing a core setting there was not measured, so the skill does not do it. Nothing was changed."
        }
        if {[change_placed] > 0} {
            return -code error "ILA_REFUSED_PLACED: this design is already placed or routed. A setting change here makes Vivado drop the core's placement and routing without a message, and on Versal the design then cannot be placed again. Open the synthesized or opt_design checkpoint and ask again. Allowing a state reset does not change this. Nothing was changed (AI only: -allow_state_reset does not apply to a change)."
        }
        change_owner $core
        set cur [core_settings $core $arch]
        set new $cur
        foreach {k key} {-depth depth -memory_type memtype -strg_qual strg -adv_trigger adv -comparators mucnt -pipe_stages pipe} {
            if {$o($k) ne ""} { dict set new $key $o($k) }
        }
        set new [settings_typed $new [change_port_types $core $cur $o(-probe_type) [probe_type_pairs $o(-probe_types)]]]
        set extra {}
        # memory type: UltraRAM is Versal only, and only at depth 4096 or more (12-206). A depth under 4096 on an UltraRAM core takes block RAM first.
        if {$o(-memory_type) eq "uram" && $arch ne "versal"} {
            return -code error "ILA_OPTION_RANGE: -memory_type uram needs a Versal part (architecture here: '$arch'). Nothing was changed."
        }
        if {[dict get $new memtype] eq "uram" && [dict get $new depth] < 4096} {
            if {$o(-memory_type) eq "uram"} {
                return -code error "ILA_OPTION_RANGE: UltraRAM needs depth 4096 or more (depth would be [dict get $new depth]). Nothing was changed."
            }
            dict set new memtype bram
            lappend extra "UltraRAM needs depth 4096 or more, so the capture memory goes to block RAM first."
        }
        # comparators: only with advanced trigger or capture control (D8); capture control needs 2 or more (US+ fails opt_design 19-3458, Versal reads 1)
        if {$o(-comparators) ne "" && ![dict get $new adv] && ![dict get $new strg]} {
            return -code error "ILA_OPTION_RANGE: -comparators is offered only with advanced trigger or capture control, as in the Vivado wizard. Ask with -adv_trigger 1 or -strg_qual 1. Nothing was changed."
        }
        if {[dict get $new strg] && [dict get $new mucnt] < 2} {
            dict set new mucnt 2
            lappend extra "Capture control needs at least 2 comparators per probe. $core has [dict get $cur mucnt]. I will set comparators to 2 first, then turn on capture control."
        }
        set total [expr {[dict get $new mucnt] * [dict get $cur ports]}]
        if {$total > 1024} {
            return -code error "ILA_CHANGE_COMPARATOR_LIMIT: $core has [dict get $cur ports] probe ports x [dict get $new mucnt] comparators = $total comparators; Vivado allows at most 1024 (IP_Flow 19-3478 at opt_design). Nothing was changed."
        }
        if {[dict get $new ptype] eq "trigger"} {
            if {[dict get $cur ports] == 1} {
                return -code error "ILA_CHANGE_NEEDS_DATA_PROBE: $core has one probe port. Trigger only would leave no probe that stores data, and Vivado would fail at opt_design (IP_Flow 19-3478). Nothing was changed."
            }
            return -code error "ILA_CHANGE_NEEDS_DATA_PROBE: every one of the [dict get $cur ports] probe ports of $core would be trigger only. That leaves no probe that stores data, and Vivado would fail at opt_design (IP_Flow 19-3478). Nothing was changed."
        }
        # memory budget for the new depth, memory type or stored bits (a trigger only port stores nothing, so only more stored bits need more memory)
        set memline ""
        if {[dict get $new depth] != [dict get $cur depth] || [dict get $new memtype] ne [dict get $cur memtype] || [dict get $new stored] > [dict get $cur stored]} {
            set uram [expr {[dict get $new memtype] eq "uram"}]
            set unit [expr {$uram ? "UltraRAM" : "block RAM"}]
            set w [dict get $new stored]
            set need [change_tiles [dict get $new depth] $w $uram $arch]
            set avail [change_free_tiles $core $cur $uram $arch]
            if {$avail < 0} {
                say "WARNING W02 budget NOT checked: the part reports no $unit count, so depth [dict get $new depth] x $w bits ($need $unit tiles) was not compared with what is free. Vivado may fail at opt_design if it does not fit. Tell the user."
            } elseif {$need > $avail} {
                set fit [lmap d [valid_depths] {expr {[change_tiles $d $w $uram $arch] <= $avail && (!$uram || $d >= 4096) ? $d : [continue]}}]
                return -code error "ILA_OVER_BUDGET: depth [dict get $new depth] x $w bits needs $need $unit tiles, $avail are free. [expr {[llength $fit] ? "Largest depth that fits: [lindex $fit end]." : "No valid depth fits."}] Nothing was changed."
            } else {
                set memline " Memory: depth [dict get $new depth] x $w bits needs $need $unit tiles, $avail are free. It fits."
            }
        }
        set sets [change_sets $core $cur $new]
        if {[llength $sets] == 0} {
            say "nothing to change: $core already has the settings asked for."
            return {}
        }
        foreach x $extra { say $x }
        set what [lmap s $sets {expr {[lindex $s 2] eq "ALL_PROBE_SAME_MU" ? [continue] : [change_phrase $s]}}]
        if {$o(-dry_run)} {
            say "CHANGE (dry run, nothing changed): $core [join $what {, }]."
            say "probes and clock stay the same.$memline"
            say "after the change Vivado builds the core with these settings at the next opt_design."
            return [lmap s $sets {lassign $s kind obj p old v; expr {$p eq "ALL_PROBE_SAME_MU" ? [continue] : [list $p $old $v {*}[expr {$kind eq "port" ? [list $obj] : {}}]]}}]
        }
        return [change_apply $core $sets [list [dict get $cur depth] [dict get $cur memtype]]]
    }

    # A property, or one of its values, in the words the user sees (the plan's text for the change lines)
    proc change_word {p {v ""}} {
        set name [dict get {C_DATA_DEPTH depth C_MEMORY_TYPE {memory type} ALL_PROBE_SAME_MU_CNT comparators C_EN_STRG_QUAL {capture control} C_ADV_TRIGGER {advanced trigger} C_INPUT_PIPE_STAGES {pipe stages} PROBE_TYPE {probe type}} $p]
        if {$v eq ""} { return $name }
        switch $p {
            C_MEMORY_TYPE { return [expr {$v == 1 ? "UltraRAM" : "block RAM"}] }
            C_EN_STRG_QUAL - C_ADV_TRIGGER { return [expr {[string is true $v] ? "on" : "off"}] }
            PROBE_TYPE { return [string tolower [string map {_ " "} $v]] }
        }
        return $v
    }

    # A property of one set in words: "probe type of probe1" for a port of a core with several ports, else the name of the property
    proc change_label {kind obj p} {
        if {$kind eq "port" && [llength [get_debug_ports -quiet [parent_path $obj]/probe*]] > 1} { return "[change_word $p] of [file tail $obj]" }
        return [change_word $p]
    }
    # One set as the user reads it: "depth 1024 -> 8192"
    proc change_phrase {s} {
        lassign $s kind obj p old v
        return "[change_label $kind $obj $p] [change_word $p $old] -> [change_word $p $v]"
    }

    # Set the properties of a change in order, each read back (set_checked). A set that fails stops the change and says what was already set.
    # Then the probes and clock are read again: an in-place set keeps them (CHG-4), and a change that moved them is reported, not hidden.
    proc change_apply {core sets old0} {
        variable replay
        variable facts
        lassign [core_fingerprint $core] drv0 bits0 crc0
        set done {}
        set out {}
        foreach s $sets {
            lassign $s kind obj p old v
            set target [expr {$kind eq "port" ? [get_debug_ports $obj] : [get_debug_cores $obj]}]
            if {[catch {set_checked $target $p $v} err]} {
                return -code error "$err[expr {[llength $done] ? " Already set before it stopped: [join $done {, }]. Vivado builds the core with those values at the next opt_design; ask again to finish the change, or reopen the checkpoint to undo it." : " Nothing was changed."}]"
            }
            set got [get_property $p $target]
            lappend replay "set_property $p $v \[[expr {$kind eq "port" ? "get_debug_ports" : "get_debug_cores"}] [list $obj]\]"
            if {$p eq "ALL_PROBE_SAME_MU"} continue
            lappend done "[change_label $kind $obj $p] [change_word $p $v]"
            lappend out [list $p $old $got {*}[expr {$kind eq "port" ? [list $obj] : {}}]]
        }
        dict set facts change_from $core $old0   ;# {depth memtype} before the change: ila::prove_built compares the built memory with it
        lassign [core_fingerprint $core] drv bits crc
        if {$drv ne $drv0 || $bits != $bits0 || $crc != $crc0} {
            return -code error "ILA_CHANGE_CORE_EDITED: after the change $core reads $bits probe bits and clock driver '$drv', before it $bits0 and '$drv0'. Vivado moved the probes or the clock during the set. Check the core before opt_design."
        }
        say "CHANGED $core: [join [lmap x $out {lassign $x p old got obj; expr {"[change_label [expr {$obj eq "" ? "core" : "port"}] $obj $p] [change_word $p $old] -> [change_word $p $got] (read back [change_word $p $got])"}}] {, }]. Probes and clock unchanged: $bits nets, clock driver $drv."
        say "NOT DONE: nothing was saved, written or built. Vivado builds $core with these settings at the next opt_design. To keep the change, save the design before this session closes (AI only: write_checkpoint -force <path>, only to a path the user named)."
        say "NOTE: whether Vivado built $core with these settings can be checked only after opt_design has built it (AI only: ila::prove_built $core reads the built core and compares it with these settings)."
        return $out
    }

    # The property sets of a change, in the order Vivado needs, each {kind core-or-port property old new} (kind core or port). A port set per port whose type changes.
    # Only the settings that differ. Depth before C_MEMORY_TYPE 1, C_MEMORY_TYPE 0 before the depth (12-206: UltraRAM needs depth 4096 or more);
    # ALL_PROBE_SAME_MU true and the count before C_EN_STRG_QUAL (the experiments' working order, v.combo).
    proc change_sets {core cur new} {
        set tf {0 false 1 true}
        set out {}
        set dep [expr {[dict get $new depth] != [dict get $cur depth] ? [list [list core $core C_DATA_DEPTH [dict get $cur depth] [dict get $new depth]]] : {}}]
        if {[dict get $new memtype] ne [dict get $cur memtype]} {
            set mem [list core $core C_MEMORY_TYPE [expr {[dict get $cur memtype] eq "uram"}] [expr {[dict get $new memtype] eq "uram"}]]
            if {[dict get $new memtype] eq "uram"} { lappend out {*}$dep $mem } else { lappend out $mem {*}$dep }
        } else {
            lappend out {*}$dep
        }
        if {[dict get $new mucnt] != [dict get $cur mucnt]} {
            lappend out [list core $core ALL_PROBE_SAME_MU true true] [list core $core ALL_PROBE_SAME_MU_CNT [dict get $cur mucnt] [dict get $new mucnt]]
        }
        foreach {key p} {strg C_EN_STRG_QUAL adv C_ADV_TRIGGER} {
            if {[dict get $new $key] != [dict get $cur $key]} { lappend out [list core $core $p [dict get $tf [dict get $cur $key]] [dict get $tf [dict get $new $key]]] }
        }
        if {[dict get $new pipe] != [dict get $cur pipe]} { lappend out [list core $core C_INPUT_PIPE_STAGES [dict get $cur pipe] [dict get $new pipe]] }
        # a port's type: ports that store data first, trigger only ports last, so no step leaves a core with nothing that stores data
        set trig {}
        foreach cp [dict get $cur pports] np [dict get $new pports] {
            lassign $cp pn ot -
            set nt [lindex $np 1]
            if {$nt eq $ot} continue
            set st [list port $core/$pn PROBE_TYPE [string toupper $ot] [string toupper $nt]]
            if {$nt eq "trigger"} { lappend trig $st } else { lappend out $st }
        }
        lappend out {*}$trig
        return $out
    }

    # ---- ila::prove_built ----------------------------------------------------------------------------------------------------------
    # ila::prove_built <core> ?-ltx <probes file>?: after the user's opt_design, prove the settings from the built core, not from the core object
    # (a set that reads back can still be built otherwise: US+ C_TRIGIN_EN reads true and builds 0). Read only. Returns a dict: verdict (PROVEN,
    # NOT PROVABLE, FAILED), count (memory tiles under the core), range (of the core's depth), memory (block RAM or UltraRAM), failed (reasons),
    # dci (the parsed DEBUG_CORE_INFO), ports (per probe port: type of the core object, built (the type the built core records, "" when none was
    # read) and report (Versal: the type the report lists)).
    #  - UltraScale+: every setting in the DEBUG_CORE_INFO string of the ILA cell (checked on ipx_usp26{1,2}_A2: C_DATA_DEPTH=4096 after a change),
    #    and per port C_PROBE<n>_TYPE (0 trigger and data, 1 data, 2 trigger only).
    #  - Both: the memory count of the bits one sample stores (a trigger only port takes no capture memory). PROVEN only when it is inside the
    #    range of the core's depth and outside that of the depth before the change (ila::change records it in this session) or, with no record,
    #    of every other depth. Overlapping ranges: NOT PROVABLE.
    #  - Versal: DEBUG_CORE_INFO is never read (missing on 2026.2, stale on 2026.1 after a change, IPX-CHG5-dci-versal-*), so the port types come
    #    from the probes file the user wrote with write_debug_probes (-ltx) and from the PROBE_TYPE column of report_debug_core; the settings other
    #    than depth, memory type and port types are "read back only". The probes file is checked on UltraScale+ too when it is given.
    proc prove_built {core args} { return [flat _prove_built $core {*}$args] }

    # The probe type of each port a built core's report lists, as a dict port -> type in lower case. The PROBE_TYPE= column is on the first row of a port.
    proc prove_report_types {core} {
        set res [dict create]
        set inside 0
        foreach line [split [report_debug_core -return_string] \n] {
            if {[regexp {^Channel Data for Debug Core "([^"]+)"} $line -> nm]} { set inside [expr {$nm eq $core}]; continue }
            if {!$inside} continue
            set f [lmap x [split $line |] {string trim $x}]
            if {[regexp {^probe[0-9]+$} [lindex $f 1]] && [regexp {PROBE_TYPE=([A-Z_]+)} $line -> ty]} { dict set res [lindex $f 1] [string tolower $ty] }
        }
        return $res
    }
    # The probe type of each port of one core in the text of a probes file (.ltx), as a dict port -> type. The file says DATA, TRIGGER or DATA_TRIGGER.
    # The core's entry runs from its type and name to the entry of the next core.
    proc ltx_core_types {txt core} {
        set rx "\"type\":\\s*\"\[A-Z0-9_\]+_V\[0-9\]+\",\\s*\"name\":\\s*\"[regsub -all {[][{}()*+?.^$|\\]} $core {\\&}]\""
        if {![regexp -indices $rx $txt m]} { return {} }
        set from [lindex $m 1]
        set to [string length $txt]
        if {[regexp -indices -start $from {"type":\s*"[A-Z0-9_]+_V[0-9]+",\s*"name":} $txt n]} { set to [lindex $n 0] }
        set res [dict create]
        foreach {- pn ty} [regexp -all -inline {"name":\s*"(probe[0-9]+)"[^\{\}]*?"type":\s*"([A-Z_]+)"} [string range $txt $from [expr {$to - 1}]]] {
            set known {DATA data TRIGGER trigger DATA_TRIGGER data_and_trigger}
            dict set res $pn [expr {[dict exists $known $ty] ? [dict get $known $ty] : [string tolower $ty]}]
        }
        return $res
    }
    proc prove_ltx_types {file core} {
        if {[catch {set fh [open $file]; set txt [read $fh]; close $fh} err]} {
            return -code error "ILA_CHANGE_NO_PROBES_FILE: the probes file $file cannot be read ($err). The check needs a probes file written after opt_design. Nothing was checked."
        }
        return [ltx_core_types $txt $core]
    }

    # The key=value pairs of a DEBUG_CORE_INFO string ("u_ila_0,labtools_ila_v6_00_a,{ALL_PROBE_SAME_MU=true,...}") as a dict; {} when there are none.
    proc dci_values {s} {
        set out [dict create]
        if {![regexp {\{([^\}]*)\}} $s -> body]} { return $out }
        foreach kv [split $body ,] { if {[regexp {^([^=]+)=(.*)$} $kv -> k v]} { dict set out $k $v } }
        return $out
    }
    # {lo hi}: the memory tiles a built core of <depth> x <width> may hold. hi is the cost model (never below a measured point, ila_budget.tcl);
    # lo is the model minus a gap: 1 tile up to depth 16384, else 10 percent of the model, rounded up (every point of docs/lf2-limits.md lies
    # inside, test_prove_built.tcl). A built core holds at least 1 tile.
    proc prove_range {depth width uram arch} {
        set m [change_tiles $depth $width $uram $arch]
        set gap [expr {$depth <= 16384 ? 1 : int(ceil($m / 10.0))}]
        return [list [expr {max(1, $m - $gap)}] $m]
    }

    proc _prove_built {core args} {
        variable facts
        array set o {-ltx ""}
        check_options ila::prove_built ILA_BAD_OPTION [array names o] $args
        array set o $args
        set cell [get_cells -quiet $core]
        if {[llength [get_debug_cores -quiet $core]] == 0 || [llength $cell] == 0 || [string is true -strict [get_property IS_BLACKBOX $cell]]} {
            return -code error "ILA_CHANGE_NOT_BUILT: $core is not built yet: Vivado builds a debug core at opt_design, so the check can run only after opt_design (AI only: ila::prove_built $core). Nothing was checked."
        }
        set arch [require_architecture]
        set s [core_settings $core $arch]
        set d [dict get $s depth]
        set w [dict get $s stored]   ;# a trigger only port takes no capture memory
        set uram [expr {[dict get $s memtype] eq "uram"}]
        set unit [expr {$uram ? "UltraRAM" : "block RAM"}]
        if {$uram} {
            set n [uram_cells_used [get_cells -quiet -hier -filter "NAME =~ $core/* && (REF_NAME =~ URAM288*)"]]
        } else {
            set n [ram_tiles_used [get_cells -quiet -hier -filter "NAME =~ $core/* && (REF_NAME =~ RAMB* || REF_NAME =~ FIFO*)"]]
        }
        lassign [prove_range $d $w $uram $arch] lo hi
        set failed {}
        if {$n < $lo || $n > $hi} { lappend failed "the memory count is outside the range" }
        # the range it must not fall in: the core before the change, or every other depth
        set mem "$n $unit tiles under the core, the model range for $d x $w is $lo to $hi"
        set clash {}
        set old [expr {[dict exists $facts change_from $core] ? [dict get $facts change_from $core] : {}}]
        if {[llength $old] == 2 && $old ne [list $d [dict get $s memtype]]} {
            lassign $old od om
            set orange [expr {$om eq [dict get $s memtype] ? [prove_range $od $w $uram $arch] : {0 0}}]
            append mem " and for $od[expr {$om eq [dict get $s memtype] ? "" : " [expr {$om eq "uram" ? "UltraRAM" : "block RAM"}]"}] (before the change) is [lindex $orange 0] to [lindex $orange 1]"
            if {$n >= [lindex $orange 0] && $n <= [lindex $orange 1]} { lappend clash $od }
        } else {
            foreach od [valid_depths] {
                if {$od == $d || ($uram && $od < 4096)} continue
                lassign [prove_range $od $w $uram $arch] olo ohi
                if {$n >= $olo && $n <= $ohi} { lappend clash $od }
            }
            append mem [expr {[llength $clash] ? "; the range of depth [join $clash {, }] also holds it" : "; no other depth's range holds $n"}]
        }
        # UltraScale+: every setting the built core records
        set dci [dict create]
        set dfail 0
        set tcode {data_and_trigger 0 data 1 trigger 2}   ;# C_PROBE<n>_TYPE (measured, IPX-CHG1-ptype-data-*, ila-plain-phase1.md)
        if {$arch eq "versal"} {
            set dtext "DEBUG_CORE_INFO is not read on Versal (missing on 2026.2, stale on 2026.1 after a change)"
        } else {
            set dci [dci_values [get_property -quiet DEBUG_CORE_INFO $cell]]
            set want [list C_DATA_DEPTH $d C_EN_STRG_QUAL [expr {[dict get $s strg] ? "true" : "false"}] C_ADV_TRIGGER [expr {[dict get $s adv] ? "true" : "false"}] \
                ALL_PROBE_SAME_MU_CNT [dict get $s mucnt] C_INPUT_PIPE_STAGES [dict get $s pipe]]
            foreach pp [dict get $s pports] {
                lassign $pp pn t -
                if {[dict exists $tcode $t]} { lappend want C_PROBE[string range $pn 5 end]_TYPE [dict get $tcode $t] }
            }
            set seen {}
            foreach {k v} $want {
                if {![dict exists $dci $k]} continue
                lappend seen "$k=[dict get $dci $k]"
                if {![prop_same $v [dict get $dci $k]]} { lappend failed "DEBUG_CORE_INFO has $k=[dict get $dci $k], the core $v"; set dfail 1 }
            }
            set dtext [expr {[dict size $dci] == 0 ? "DEBUG_CORE_INFO is missing" : "DEBUG_CORE_INFO [expr {$dfail ? "disagrees" : "agrees"}] ([join $seen {, }])"}]
        }
        # the port types the built core records: the probes file (-ltx, both architectures) and, on Versal, the report
        set ltx {}
        if {$o(-ltx) ne ""} {
            set ltx [prove_ltx_types $o(-ltx) $core]
            if {[dict size $ltx] == 0} { lappend failed "the probes file does not list $core" }
        }
        set rep [expr {$arch eq "versal" ? [prove_report_types $core] : {}}]
        set ports [dict create]
        set tfail 0
        foreach pp [dict get $s pports] {
            lassign $pp pn t -
            set built ""
            set key C_PROBE[string range $pn 5 end]_TYPE
            if {[dict exists $dci $key]} {
                set rev {0 data_and_trigger 1 data 2 trigger}
                set built [expr {[dict exists $rev [dict get $dci $key]] ? [dict get $rev [dict get $dci $key]] : [dict get $dci $key]}]
            }
            foreach {src what} [list $ltx "the probes file" $rep "the report"] {
                if {![dict exists $src $pn]} continue
                if {$what eq "the probes file"} { set built [dict get $src $pn] }
                if {[dict get $src $pn] ne $t} {
                    lappend failed "$what lists $pn as [type_words [dict get $src $pn]], the core has [type_words $t]"
                    set tfail 1
                }
            }
            if {[dict size $ltx] > 0 && ![dict exists $ltx $pn]} { lappend failed "the probes file does not list $pn of $core"; set tfail 1 }
            dict set ports $pn [dict create type $t built $built report [expr {[dict exists $rep $pn] ? [dict get $rep $pn] : ""}]]
        }
        set verdict [expr {[llength $failed] ? "FAILED" : ([llength $clash] ? "NOT PROVABLE" : "PROVEN")}]
        say "PROVE $core: $dtext; $mem. [expr {$verdict eq "FAILED" ? "FAILED: [join $failed {; }]" : ($verdict eq "PROVEN" ? "PROVEN" : "NOT PROVABLE from the memory count")}]."
        if {!$tfail && ([dict size $ltx] > 0 || [dict size $rep] > 0)} {
            set who [expr {[dict size $ltx] == 0 ? "the report agrees; no probes file was given, so the built core is not checked" : ([dict size $rep] == 0 ? "the probes file agrees" : "the probes file and the report agree")}]
            say "$core ports: [join [lmap pp [dict get $s pports] {lassign $pp pn t -; format {%s %s} $pn [type_words $t]}] {, }]: $who."
        }
        if {$arch eq "versal"} {
            say "$core: capture control [setting_text strg [dict get $s strg]], advanced trigger [setting_text adv [dict get $s adv]], comparators [dict get $s mucnt], pipe stages [dict get $s pipe][expr {[dict size $ltx] > 0 ? "" : ", [ptype_text $s]"}]: read back only on Versal ([expr {[dict size $ltx] > 0 ? "the probes file holds the port types only" : "no built record the skill can read"}])."
        }
        return [dict create verdict $verdict count $n range [list $lo $hi] memory $unit failed $failed dci $dci ports $ports]
    }
}
