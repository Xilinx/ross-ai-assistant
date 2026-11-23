# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_resolve.tcl: items to nets: globs, resolve_item, top_nets, suggestions. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # What the last resolve_item matched, in words (empty for an internal item), the sentence that refuses a name that exists only elsewhere,
    # and the Vivado net filter expression of the request (-filter). _plan sets cur_filter; the internal item filt: resolves it.
    variable last_rung ""
    variable last_refusal ""
    variable cur_filter ""

    # Nets whose full hierarchical name matches the glob. get_nets -hier sees only the leaf name.
    proc nets_glob {g} {
        set leafq [string map {[ ? ] ?} [lindex [split $g /] end]]
        set rx [glob_to_regexp $g]
        set out {}
        foreach n [get_nets -quiet -hier $leafq] { if {[regexp $rx [get_property NAME $n]]} { lappend out $n } }
        return $out
    }

    # glob to regexp: * any, ? one char, [ ] literal (Vivado names have brackets), everything else literal
    proc glob_to_regexp {g} {
        set o "^"
        foreach ch [split $g ""] {
            switch -- $ch {
                * { append o ".*" } ? { append o "." }
                default { append o [rx_quote $ch] }
            }
        }
        return "$o\$"
    }

    # ---- 2. resolve what the user asked for -----------------------------
    # A request is a list of plain names (resolve_plain). The old item prefixes (sig:, reg:, net:, comb:, marked) are gone: old_form_check refuses them.
    # Two items are internal, made by _plan and never typed: filt:, every net the request's filter expression gives (resolve_filter), and
    # comb:<glob>, made from -combinational (the combinational nets of that name only). Returns a list of {net origin} pairs.
    proc old_form_check {item} {
        if {[regexp {^(net|reg|sig|comb|marked|filt):} $item]} {
            return -code error "ILA_BAD_ITEM: '$item' is not a signal name. Name the register or net itself, with its path when it is inside a module (for example u_dma/count), and use * for any characters. The library finds a register or a net on its own, so nothing needs to be written before the name."
        }
    }
    proc resolve_item {item} {
        variable last_rung; variable last_refusal
        set last_rung ""; set last_refusal ""
        if {$item eq "filt:"} { return [resolve_filter] }
        if {[regexp {^comb:(.*)$} $item -> g]} {
            # An empty pattern would be retried as "*" and match the whole design with no limit.
            if {$g eq ""} { return -code error "ILA_BAD_ITEM: the combinational list holds an empty name. Name the combinational nets to take." }
            # get_nets -hier matches only the leaf name. Take the leaf (the brackets of a bus bit are literal in a Vivado glob, as in the
            # filter; the ? is only a safe over-match), then keep the nets whose FULL name matches the glob (brackets literal).
            set hits [nets_glob $g]
            if {[llength $hits] == 0 && [string index $g 0] ne "*"} { set hits [nets_glob "*/$g"] }
            set out {}
            foreach n $hits { lappend out [list $n "comb:$g"] }
            return $out
        }
        old_form_check $item
        return [resolve_plain $item]
    }

    # The instance a name lives in: everything before its last slash, "" at the top level.
    proc parent_path {name} {
        set i [string last / $name]
        return [expr {$i < 0 ? "" : [string range $name 0 [expr {$i - 1}]]}]
    }

    # A plain name is a Vivado name pattern, matched against the full hierarchical name as -filter {NAME =~ <name>} does: * and ? match any
    # characters including /, [ ] are literal, anchored at both ends, so a bare name is the top level only (rule V1). Cells are asked with that
    # filter; nets through nets_glob (the leaf query, then the exact full-name match), which gives the same nets and is what the old rungs used.
    #   a * or ? in the last part: the register cells that match plus the nets that match, register outputs only for the cells (V5)
    #   otherwise registers first: <path>/<base>_reg<index>... and their output nets; if none, the nets <path>/<base><index>... (V3, V4)
    #   nothing found: no retry below the top; when the name exists elsewhere the refusal says where (V6)
    # Vivado objects stay objects (foreach and lappend, never lmap): get_property NAME on a bracket name string is Vivado error 17-161.
    proc resolve_plain {name} {
        variable last_rung; variable last_refusal
        set out {}
        set path [parent_path $name]
        set leaf [string range $name [expr {[string last / $name] + 1}] end]
        if {$leaf eq ""} { return -code error "ILA_BAD_ITEM: the name '$name' is empty or ends with a slash. Name a register or a net." }
        if {[regexp {[*?]} $leaf]} {
            set cells [get_cells -quiet -hier -filter "IS_SEQUENTIAL && NAME =~ \"$name\""]
            set nets [nets_glob $name]
            set have [dict create]
            foreach c $cells {
                foreach q [get_pins -quiet -of_objects $c -filter {DIRECTION == OUT && !IS_CLOCK}] {
                    foreach n [get_nets -quiet -of_objects $q] { if {[dict exists $have $n]} continue; dict set have $n 1; lappend out [list $n "reg:[get_property NAME $c]"] }
                }
            }
            set nreg [llength $out]
            foreach n $nets { if {[dict exists $have $n]} continue; dict set have $n 1; lappend out [list $n "net:$name"] }
            set last_rung "a wildcard name: [llength $cells] register cells and [llength $nets] nets match, [llength $out] distinct nets ($nreg from registers)"
            return $out
        }
        # a bus bit is part of the name: count[3] is base count with the index [3]
        if {![regexp {^([^\[\]]+)((?:\[[0-9]+\])*)$} $leaf -> base sfx]} { set base $leaf; set sfx "" }
        set prx [expr {$path eq "" ? "^" : "[string range [glob_to_regexp $path] 0 end-1]/"}]
        set pglob [expr {$path eq "" ? "" : "$path/"}]
        set rrx "${prx}[rx_quote $base]_reg[rx_quote $sfx]((\\\[\[0-9\]+\\\])*)\$"
        set nrx "${prx}[rx_quote $base][rx_quote $sfx]((\\\[\[0-9\]+\\\])*)\$"
        set places {}
        foreach c [get_cells -quiet -hier -filter "IS_SEQUENTIAL && NAME =~ \"${pglob}${base}_reg*\""] {
            set cn [get_property NAME $c]
            if {![regexp $rrx $cn]} continue
            lappend places [parent_path $cn]
            foreach q [get_pins -quiet -of_objects $c -filter {DIRECTION == OUT && !IS_CLOCK}] {
                foreach n [get_nets -quiet -of_objects $q] { lappend out [list $n "reg:$cn"] }
            }
        }
        set kind "registers named ${base}_reg"
        if {[llength $out] == 0} {
            set kind "nets named $base"
            foreach n [concat [nets_glob "${pglob}${base}${sfx}"] [nets_glob "${pglob}${base}${sfx}\[*\]"]] {
                set nn [get_property NAME $n]
                if {![regexp $nrx $nn]} continue
                lappend out [list $n "net:$name"]
                lappend places [parent_path $nn]
            }
        }
        if {[llength $out] > 0} {
            set last_rung "$kind: [llength $out] nets in [llength [lsort -unique $places]] places"
            return $out
        }
        # nothing: say where the leaf exists (registers or nets), so the AI can ask the user or retry with a path
        set elsewhere {}
        set hr "(^|/)[rx_quote $base](_reg)?[rx_quote $sfx]((\\\[\[0-9\]+\\\])*)\$"
        foreach c [get_cells -quiet -hier -filter "IS_SEQUENTIAL && NAME =~ \"*${base}_reg*\""] {
            set cn [get_property NAME $c]
            if {[regexp $hr $cn]} { lappend elsewhere [expr {[parent_path $cn] eq "" ? "the top level" : [parent_path $cn]}] }
        }
        foreach n [concat [nets_glob "*${base}${sfx}"] [nets_glob "*${base}${sfx}\[*\]"]] {
            set nn [get_property NAME $n]
            if {[regexp $hr $nn]} { lappend elsewhere [expr {[parent_path $nn] eq "" ? "the top level" : [parent_path $nn]}] }
        }
        set elsewhere [lsort -unique $elsewhere]
        set last_rung "nothing matches; the name exists in [llength $elsewhere] other places"
        if {[llength $elsewhere] > 0} {
            set below [lsearch -all -inline -exact -not $elsewhere "the top level"]
            set way [expr {[llength $below] > 0 ? "Name it with its path (for example [lindex $below 0]/$leaf), or */$leaf for every instance." : "Name it without a path for the top level one."}]
            set last_refusal "nothing in the netlist matches '$name'[expr {$path eq "" ? " at the top level" : ""}]. $base exists in: [join [lrange $elsewhere 0 9] {, }][expr {[llength $elsewhere] > 10 ? " and [expr {[llength $elsewhere] - 10}] more" : ""}]. $way"
        }
        return {}
    }

    # A name whose every net was refused as a hardened or unclocked path (N15, N07): where the same signal name sits on a fabric net that can be
    # probed, in the words of the N01 "exists in" sentence, or "" when there is no such place. The name is the leaf without a bit index and
    # without the _<n> suffix synthesis adds. A synthesis name made of two pin names (pmc_axi_noc0_awaddr_S00_AXI_awaddr_1, PS to NoC) is also
    # tried by each tail after an underscore, longest first, so the fabric side noc_decode_i/axi_noc_0/S00_AXI_awaddr is found. Bounded: the
    # first 4 names, and 8 candidate nets per tail; a candidate counts only when qualify accepts it.
    proc fabric_elsewhere {names} {
        set skip [dict create]
        foreach n $names { dict set skip $n 1 }
        set bases {}
        foreach n $names {
            set leaf [lindex [split $n /] end]
            regsub {(\[[0-9]+\])+$} $leaf {} leaf
            regsub {_[0-9]+$} $leaf {} leaf
            if {$leaf ne "" && $leaf ni $bases} { lappend bases $leaf }
            if {[llength $bases] >= 4} break
        }
        foreach b $bases {
            set parts [split $b _]
            set last [expr {[llength $parts] > 2 ? [llength $parts] - 2 : 0}]
            for {set i 0} {$i <= $last} {incr i} {
                set tail [join [lrange $parts $i end] _]
                set rx "(^|/)[rx_quote $tail](\\\[\[0-9\]+\\\])*\$"
                set places {}
                set tried 0
                foreach n [concat [nets_glob "*$tail"] [nets_glob "*$tail\[*\]"]] {
                    set nn [get_property NAME $n]
                    if {[dict exists $skip $nn] || ![regexp $rx $nn]} continue
                    if {[incr tried] > 8} break
                    if {[dict get [qualify $n] ok]} { lappend places [expr {[parent_path $nn] eq "" ? "the top level" : [parent_path $nn]}] }
                }
                set places [lsort -unique $places]
                if {[llength $places] == 0} continue
                set below [lsearch -all -inline -exact -not $places "the top level"]
                set way [expr {[llength $below] > 0 ? "Name it with its path (for example [lindex $below 0]/$tail)." : "Name it without a path for the top level one."}]
                return " $tail exists on a fabric path, which can be probed, in: [join [lrange $places 0 9] {, }][expr {[llength $places] > 10 ? " and [expr {[llength $places] - 10}] more" : ""}]. $way"
            }
        }
        return ""
    }

    # The internal item filt: is every net the request's Vivado net filter expression (-filter, rule V8) gives, added to the nets the names give.
    proc resolve_filter {} {
        variable cur_filter; variable last_rung
        if {$cur_filter eq ""} { return -code error "ILA_BAD_ITEM: filt: is internal. Give a Vivado net filter expression as -filter." }
        set nets [get_nets -quiet -hier -filter $cur_filter]
        set last_rung "the filter {$cur_filter}: [llength $nets] nets"
        set out {}
        foreach n $nets { lappend out [list $n "filter"] }   ;# foreach keeps the objects; lmap would give name strings
        return $out
    }

    # A clock reaches registers through hierarchy, so one electrical net has several names (u_aux/clk, u_keep/clk, clk).
    # Group by one canonical name: the segment with the fewest hierarchy levels.
    proc top_nets {pin} {
        set out {}
        foreach n [get_nets -quiet -of_objects $pin] {
            # A clock pin tied to ground or power (the unused clock inputs of PS9 and NoC cells) has no clock. Its net also has
            # thousands of segments, one per tied pin of the whole design.
            if {[get_property TYPE $n] in {GROUND POWER}} continue
            set segs [get_nets -quiet -segments $n]
            if {[llength $segs] == 0} { set segs [list $n] }
            set best ""; set bd 99999
            # one get_property for the whole collection: a ground or power net has thousands of segments, and one call per segment
            # took 1.5 s to 35 s per clock pin in the MCP session
            foreach nm [get_property NAME $segs] {
                set d [llength [split $nm /]]
                if {$d < $bd || ($d == $bd && [string compare $nm $best] < 0)} { set bd $d; set best $nm }
            }
            lappend out $best
        }
        return $out
    }

    # ---- suggestions for a request that matched nothing ----------------------
    proc lev {a b} {
        set la [string length $a]; set lb [string length $b]
        for {set j 0} {$j <= $lb} {incr j} { lappend prev $j }
        for {set i 1} {$i <= $la} {incr i} {
            set cur [list $i]
            for {set j 1} {$j <= $lb} {incr j} {
                set c [expr {[string index $a [expr {$i-1}]] eq [string index $b [expr {$j-1}]] ? 0 : 1}]
                lappend cur [expr {min([lindex $prev $j]+1, [lindex $cur [expr {$j-1}]]+1, [lindex $prev [expr {$j-1}]]+$c)}]
            }
            set prev $cur
        }
        return [lindex $prev $lb]
    }
    # Names of registers close to what the user typed (edit distance 2 or substring). Read only.
    proc suggest {item} {
        set term $item
        regsub {^comb:} $term {} term   ;# the internal item of -combinational
        set term [lindex [split $term /] end]
        regsub {(\[[0-9]+(:[0-9]+)?\])+$} $term {} term   ;# a bit index or a slice someone typed is not part of the name
        regsub -all {[\[\]*]} $term {} term
        regsub {_reg$} $term {} term
        if {$term eq ""} { return "" }
        # The list of register base names is the same for every unmatched item of one plan: build it once.
        variable suggest_seen
        if {![info exists suggest_seen]} {
            set suggest_seen {}
            set sc [get_cells -quiet -hier -filter {IS_SEQUENTIAL}]
            # one get_property for the whole collection, not one per register
            foreach cname [expr {[llength $sc] == 0 ? {} : [get_property NAME $sc]}] {
                set base [lindex [split $cname /] end]
                regsub {\[[0-9]+\]$} $base {} base
                regsub {_reg(_[0-9]+)?$} $base {} base
                if {[lsearch -exact $suggest_seen $base] >= 0} continue
                lappend suggest_seen $base
                if {[llength $suggest_seen] > 4000} break
            }
        }
        set seen $suggest_seen
        set near {}
        foreach b $seen {
            if {[string first [string tolower $term] [string tolower $b]] >= 0 || [lev [string tolower $term] [string tolower $b]] <= 2} { lappend near $b }
        }
        if {[llength $near] == 0} { return "" }
        return " Did you mean: [join [lrange [lsort $near] 0 5] {, }]?"
    }
}
