# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# ila_inspect.tcl: say, in plain words, what a user can probe in the open design. Reads the design only; changes nothing and writes no file.
#
#   ila::inspect            the marked nets, the top-level modules, the clocks and the debug cores already in the design
#
# The proc returns the text (it does not print it), so the Vivado MCP shows it as the command result.
# ila_help.tcl sources this file; ila_insert.tcl sources ila_help.tcl. Source it by itself only for a quick look: with the library
# (ila_insert.tcl) loaded it also names every clock's source, and finds debug cores that Vivado does not list.
# Each section ends with the item the user can give to ila::plan, so the answer to "what can I probe" leads straight to a request.

namespace eval ila {
    # How many names each section shows before it says "and N more".
    variable inspect_max_names 12
    variable inspect_max_clocks 10
    variable inspect_max_clock_nets 500

    # "count[3]" and "count[2]" are one bus: {count 2}. A name with no index is its own bus.
    proc inspect_buses {names} {
        set d [dict create]
        foreach n $names {
            regexp {^(.*?)(\[[0-9]+\])?$} $n -> base idx
            dict incr d $base
        }
        return $d
    }

    # "a, b, c and 4 more" for a list of already formatted entries.
    proc inspect_some {items max} {
        if {[llength $items] <= $max} { return [join $items ", "] }
        return "[join [lrange $items 0 [expr {$max - 1}]] {, }] and [expr {[llength $items] - $max}] more"
    }

    proc inspect_marked {} {
        variable inspect_max_names
        # One get_property over the whole collection: a loop that passes each name back to get_property fails on a name such as bus[0]
        # (Vivado reads the name as a pattern: Common 17-161, seen on 2026.1).
        set marked [get_nets -quiet -hier -filter {MARK_DEBUG == 1}]
        set names [expr {[llength $marked] == 0 ? {} : [get_property NAME $marked]}]
        if {[llength $names] == 0} {
            set txt "Marked nets: none. No net in this design carries MARK_DEBUG (the debug flag set in the source, or the Debug tag in a block design)."
            if {[llength [info commands ::ila::mark_debug_mode]] && [ila::mark_debug_mode] in {disable synthesis_only}} {
                append txt " This session has config_flows -mark_debug set to [ila::mark_debug_mode]: that is a likely reason the marks are gone (the checkpoint alone cannot confirm it)."
            }
            return $txt
        }
        set by [dict create]
        foreach n [lsort $names] {
            set parts [split $n /]
            set scope [expr {[llength $parts] > 1 ? [join [lrange $parts 0 end-1] /] : ""}]
            dict lappend by $scope [lindex $parts end]
        }
        set txt "Marked nets: [llength $names] in [dict size $by] place[expr {[dict size $by] == 1 ? {} : {s}}]. These are nets someone flagged for debug (MARK_DEBUG in the source, or the Debug tag in a block design)."
        foreach scope [lsort [dict keys $by]] {
            set buses [inspect_buses [dict get $by $scope]]
            set shown [lmap b [dict keys $buses] {expr {[dict get $buses $b] > 1 ? "$b ([dict get $buses $b] bits)" : $b}}]
            set label [expr {$scope eq "" ? "top level" : $scope}]
            append txt "\n  - $label ([llength [dict get $by $scope]]): [inspect_some $shown $inspect_max_names]"
        }
        set where [lsearch -all -inline -not [dict keys $by] ""]
        append txt "\n  To probe them all, ask for all the marked nets."
        if {[llength $where] > 0} { append txt " For one module only, ask for the marked nets of that module (for example [lindex [lsort $where] 0])." }
        return $txt
    }

    # Debug cores in the design as {{name type} ...}: found by what they are when the library is loaded, else the ones Vivado lists.
    proc inspect_core_list {} {
        if {[llength [info commands ::ila::existing_debug_cores]]} { return [ila::existing_debug_cores] }
        return [lmap c [get_debug_cores -quiet] {list $c "debug core"}]
    }

    proc inspect_modules {} {
        variable inspect_max_names
        # the debug cores have their own section: a core is not one of the user's modules
        set corenames [lmap e [inspect_core_list] {lindex $e 0}]
        set cells {}
        foreach c [get_cells -quiet -filter {!IS_PRIMITIVE}] {
            if {[get_property NAME $c] ni $corenames} { lappend cells $c }
        }
        if {[llength $cells] == 0} {
            return "Modules: none. The design is flat (no sub-module under the top level), so every net is at the top level. Name signals directly, or give part of a name and I will look for matches."
        }
        set items {}
        foreach c $cells { lappend items [format "%s (%s)" [get_property NAME $c] [get_property REF_NAME $c]] }
        set items [lsort $items]
        set txt "Top-level modules: [llength $cells]. These are the main blocks of the design, instance name first and the module it is built from in brackets: [inspect_some $items $inspect_max_names]."
        append txt "\n  To probe everything a module registers, ask for \"the whole <instance>\". To probe one signal inside, name the signal and the module it is in."
        return $txt
    }

    proc inspect_by_count {a b} { return [expr {[lindex $a 1] - [lindex $b 1]}] }

    # 1 when the object name is a debug core or inside one (the core's own cells and nets are not the user's design).
    proc inspect_in_core {name corenames} {
        foreach c $corenames { if {$name eq $c || [string first "$c/" $name] == 0} { return 1 } }
        return 0
    }

    proc inspect_clocks {} {
        variable inspect_max_clocks
        variable inspect_max_clock_nets
        set pins [get_pins -quiet -hier -filter {IS_CLOCK && DIRECTION == IN && IS_LEAF}]
        if {[llength $pins] == 0} { return "Clocks: none found. No register or other clocked cell has a clock pin connected." }
        set nets [get_nets -quiet -of_objects $pins]
        set cut ""
        set seen 0
        if {[llength $nets] > $inspect_max_clock_nets} {
            set cut " Only the first $inspect_max_clock_nets clock nets (of the segments of each clock) were examined."
        }
        # One clock has a different net name in each module it crosses; group by the shortest name when the library is loaded.
        set count [dict create]
        set tied 0
        set corenames [lmap e [inspect_core_list] {lindex $e 0}]
        foreach n $nets {
            if {[incr seen] > $inspect_max_clock_nets} break
            set nname [get_property NAME $n]
            if {[inspect_in_core $nname $corenames]} continue
            # An unused clock input of a processor or NoC cell is tied to ground or power: it is not a clock anyone can probe.
            set drv [get_pins -quiet -of_objects $n -leaf -filter {DIRECTION == OUT}]
            if {[llength $drv] > 0 && [get_property REF_NAME [get_cells -of_objects [lindex $drv 0]]] in {GND VCC}} { incr tied; continue }
            set cp [get_pins -quiet -of_objects $n -leaf -filter {IS_CLOCK && DIRECTION == IN}]
            # the clock pins of the debug cores themselves are not the user's clocked cells
            if {[llength $corenames] > 0 && [llength $cp] > 0} {
                set mine {}
                foreach pn [get_property NAME $cp] { if {![inspect_in_core $pn $corenames]} { lappend mine $pn } }
                set nmine [llength $mine]
            } else {
                set nmine [llength $cp]
            }
            if {$nmine == 0} continue
            # The clock input of a buffer, input buffer or clock generator is part of the clock network, not a clocked design cell.
            # Only a net with a few pins is checked, so a big clock costs no per-cell query.
            if {$nmine <= 4} {
                set only_net 1
                foreach r [get_property REF_NAME [get_cells -of_objects $cp]] { if {![regexp {^(BUFG|BUFH|BUFR|BUFIO|BUFCE|IBUF|INBUF|DIFFINBUF|MMCM|PLL|DPLL|XPLL)} $r]} { set only_net 0 } }
                if {$only_net} continue
            }
            set top $nname
            if {[llength [info commands ::ila::top_nets]]} { catch {set top [lindex [ila::top_nets [lindex $cp 0]] 0]} }
            if {$top eq ""} { set top $nname }
            # -leaf answers the loads of the whole flat net for every segment, so a clock already counted under its top name is not added again.
            if {![dict exists $count $top]} { dict set count $top $nmine }
        }
        if {$tied > 0} { append cut " Clock pins tied to a constant ($tied) are left out: nothing drives them." }
        set order [lsort -decreasing -command ila::inspect_by_count [lmap k [dict keys $count] {list $k [dict get $count $k]}]]
        set txt "Clocks: [llength $order]. An ILA records on one clock, so signals on the same clock share one ILA and a signal on another clock gets its own.$cut"
        set i 0
        foreach e $order {
            if {$i >= $inspect_max_clocks} { append txt "\n  - and [expr {[llength $order] - $inspect_max_clocks}] more"; break }
            lassign $e clk n
            set line "\n  - $clk: $n clock pin[expr {$n == 1 ? {} : {s}}]"
            if {[llength [info commands ::ila::clock_source]]} {
                if {[catch {ila::clock_source $clk} src]} {
                    append line "; source not read"
                } else {
                    lassign $src kind why
                    switch -- $kind {
                        free { append line "; free-running ($why), fine for an ILA" }
                        gated { append line "; can stop ($why), so an ILA on it is planned with a warning (the debug hub shares an ILA clock)" }
                        default { append line "; source not certain ($why)" }
                    }
                }
            }
            append txt $line
            incr i
        }
        return $txt
    }

    proc inspect_cores {} {
        set ex [inspect_core_list]
        if {[llength $ex] == 0} { return "Debug cores already in the design: none. A new ILA starts clean." }
        set items [lmap e $ex {format "%s (%s)" [lindex $e 0] [lindex $e 1]}]
        return "Debug cores already in the design: [llength $ex]: [join $items {, }]. A new ILA is added next to them; ask me before replacing one."
    }

    proc inspect {} {
        if {[catch {set design [current_design]} err] || $design eq ""} {
            return "ILA inspect: no design is open. Open the synthesized design first (open_checkpoint <file>.dcp), then ask again. A design that is placed or routed cannot take a new ILA."
        }
        set txt "What you can probe in the open design $design"
        if {![catch {set part [get_property PART [current_design]]}] && $part ne ""} { append txt " (part $part)" }
        append txt ". Nothing was changed.\n"
        # four literal calls, one per section, in this order; a section that cannot be read says so and the others still print
        if {[catch {set part [inspect_marked]} err]} { set part "Marked: could not be read ($err)." }
        append txt "\n$part\n"
        if {[catch {set part [inspect_modules]} err]} { set part "Modules: could not be read ($err)." }
        append txt "\n$part\n"
        if {[catch {set part [inspect_clocks]} err]} { set part "Clocks: could not be read ($err)." }
        append txt "\n$part\n"
        if {[catch {set part [inspect_cores]} err]} { set part "Cores: could not be read ($err)." }
        append txt "\n$part\n"
        append txt "\nTell me which of these to probe, in plain words. I look up the nets, plan it, and report what the plan accepts and refuses."
        return $txt
    }
}
