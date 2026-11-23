# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# [split] ila_plan.tcl: plan, plan_commit, _plan. Sourced by ila_insert.tcl; every proc here was moved from it without edits.

namespace eval ila {
    # The core options of ila::plan beyond depth, pipe and memory: trigger in and out (refused), -comparators N (1 to 16, only with advanced trigger or
    # capture control, D8; capture control needs 2 or more, so 1 becomes 2 and it is said; a list of names and counts is refused, X6) and -probe_type
    # (data or data_and_trigger: with no per-name types every probe port gets it and one must store data, IP_Flow 19-3478; typed is 1 when -probe_types gives names their own types, and then
    # trigger is valid too, because the names decide which port stores data). Returns the count apply sets (-1: the rule of mu_count). Raises
    # ILA_OPTION_UNSUPPORTED or ILA_OPTION_RANGE. Moved here from ila_change.tcl in Phase 4a.
    proc plan_core_options {trigin trigout adv strg comparators ptype {typed 0}} {
        if {$trigin || $trigout} { return -code error [trigger_refusal] }
        if {[llength $comparators] > 1} {
            return -code error "ILA_OPTION_UNSUPPORTED: different comparator counts for different probes cannot be set. Vivado's netlist insertion cannot give different probes different comparator counts (it builds one comparator per probe whatever is asked); one count applies to the whole core. Two cores on the same clock can do it: each core has its own count, and this skill plans one core per clock, so it does not build them. Nothing was done."
        }
        if {$comparators ne "-1" && (![regexp {^[0-9]+$} $comparators] || $comparators < 1 || $comparators > 16)} {
            return -code error "ILA_OPTION_RANGE: -comparators must be a whole number from 1 to 16 (got $comparators)."
        }
        if {$comparators ne "-1" && !$adv && !$strg} {
            return -code error "ILA_OPTION_RANGE: -comparators is offered only with advanced trigger or capture control, as in the Vivado wizard. Plan with -adv_trigger 1 or -strg_qual 1."
        }
        if {$typed && $ptype ni {data trigger data_and_trigger}} {
            return -code error "ILA_OPTION_RANGE: -probe_type must be data, trigger or data_and_trigger (got $ptype)."
        }
        if {!$typed && $ptype ni {data data_and_trigger}} {
            return -code error "ILA_OPTION_RANGE: -probe_type must be data or data_and_trigger (got $ptype). Every probe port of the core would get it: trigger only would leave no probe that stores data, and Vivado fails at opt_design (IP_Flow 19-3478)."
        }
        if {$strg && $comparators eq "1"} {
            say "NOTE: capture control needs at least 2 comparators per probe, so the core gets 2 (asked for 1)."
            return 2
        }
        return $comparators
    }

    # ---- probe type per port (Phase 4a) --------------------------------------------------------------------------------
    # One core has one port per signal and probe type (unit_ports). The types: data (stores the bits, no trigger), trigger (triggers, stores nothing), trigger and data.
    proc type_words {t} { return [dict get {data "data only" trigger "trigger only" data_and_trigger "trigger and data"} $t] }
    # -probe_types {name type ...} as {{name type} ...}. Checked before any net is looked at.
    proc probe_type_pairs {l} {
        if {[llength $l] % 2 != 0} {
            return -code error "ILA_OPTION_RANGE: the probe types are a name and a type, over and over, but the list has [llength $l] words, an odd number. Give every name its type. Nothing was changed."
        }
        set out {}
        foreach {n t} $l {
            if {$t ni {data trigger data_and_trigger}} { return -code error "ILA_OPTION_RANGE: the probe type of '$n' must be data, trigger or data_and_trigger (got $t). Nothing was changed." }
            old_form_check $n
            lappend out [list $n $t]
        }
        return $out
    }
    # The nets of the typed names, by every name each net has: {type-by-name pair-numbers-by-name}. A name is a plain name, so a glob types every match.
    # A net given two types, or a name that matches nothing, is refused.
    proc resolve_probe_types {pairs} {
        variable last_refusal
        set tyby [dict create]
        set tyidx [dict create]
        set k 0
        foreach pr $pairs {
            lassign $pr name type
            set found [resolve_item $name]
            if {[llength $found] == 0} {
                # a name that exists only elsewhere: the sentence of the resolver lists the places (V6)
                set why [expr {$last_refusal ne "" ? $last_refusal : "nothing in the netlist matches it. Name a register or net that is probed, with its path when it is inside a module."}]
                return -code error "ILA_BAD_ITEM: the probe type of '$name' was asked for, but $why Nothing was changed."
            }
            foreach pair $found {   ;# foreach, not lmap: a net with a bracket stays an object
                lassign $pair net origin
                set names [list [get_property NAME $net]]
                set sgs [get_nets -quiet -segments $net]
                if {[llength $sgs] > 0} { lappend names {*}[get_property NAME $sgs] }
                foreach nm [lsort -unique $names] {
                    if {[dict exists $tyby $nm] && [dict get $tyby $nm] ne $type} {
                        return -code error "ILA_BAD_ITEM: the net '$nm' is given two probe types, [type_words [dict get $tyby $nm]] and [type_words $type]. Give each net one type. Nothing was changed."
                    }
                    dict set tyby $nm $type
                    dict lappend tyidx $nm $k
                }
            }
            incr k
        }
        return [list $tyby $tyidx]
    }
    # The signal a planned net belongs to, as {name kind}: a bus bit (name[3]) is its bus (name), kind bus; any other net is its own signal,
    # kind net, so a net x and a bus x[3:0] stay apart. One trailing index only: mem[3][7] is a bit of mem[3].
    proc net_signal {n} {
        if {[regexp {^(.*)\[(\d+)\]$} $n -> base idx]} { return [list $base bus] }
        return [list $n net]
    }
    # The ports of one core from its nets: {type nets} pairs, one port per signal and probe type, as the Set Up Debug wizard does
    # (UG908: only AND and NAND triggers work across signals that share a port). A net takes the type given to it under any of its names,
    # else deftype (-probe_type). Ports follow the request: the lowest item number (netord: net -> item) of their nets, then the signal
    # name, a net before a bus of the same name, then data, trigger, data and trigger. Bits in a port are in dictionary order. The input
    # order does not matter. Pure Tcl (no Vivado call), so it is checked offline. usedvar collects the numbers of the typed names that
    # reached a planned net.
    proc unit_ports {nets deftype tyby tyidx segby usedvar netord} {
        upvar 1 $usedvar used
        set by [dict create]    ;# {signal kind type} -> nets
        set first [dict create] ;# {signal kind type} -> lowest item number
        foreach n $nets {
            set t $deftype
            foreach sn [concat [list $n] [expr {[dict exists $segby $n] ? [dict get $segby $n] : {}}]] {
                if {![dict exists $tyby $sn]} continue
                set t [dict get $tyby $sn]
                foreach i [dict get $tyidx $sn] { dict set used $i 1 }
            }
            set key [concat [net_signal $n] [list $t]]
            dict lappend by $key $n
            set io [expr {[dict exists $netord $n] ? [dict get $netord $n] : 1000000}]
            if {![dict exists $first $key] || $io < [dict get $first $key]} { dict set first $key $io }
        }
        set rank {data 0 trigger 1 data_and_trigger 2}
        set keyed [lmap key [dict keys $by] {
            lassign $key sig kind t
            list [dict get $first $key] $sig [expr {$kind eq "net" ? 0 : 1}] [dict get $rank $t] $key
        }]
        # lsort is stable: sort by the least significant key first
        foreach {i how} {3 -integer 2 -integer 1 -dictionary 0 -integer} { set keyed [lsort $how -index $i $keyed] }
        return [lmap k $keyed { list [lindex $k 4 2] [lsort -dictionary [dict get $by [lindex $k 4]]] }]
    }
    # The ports of a core in words for the log: "probe0 <signal> <n> bits (<type>)", the first 16, then "and N more".
    proc port_words {ports} {
        set out {}
        set pi 0
        foreach p $ports {
            if {$pi == 16} { lappend out "and [expr {[llength $ports] - 16}] more"; break }
            set n [llength [lindex $p 1]]
            lappend out "probe$pi [lindex [net_signal [lindex $p 1 0]] 0] $n bit[expr {$n == 1 ? "" : "s"}] ([type_words [lindex $p 0]])"
            incr pi
        }
        return [join $out {, }]
    }
    # Does any port store data? A core with only trigger ports has no probe that stores data and Vivado fails at opt_design (IP_Flow 19-3478).
    proc ports_store_data {ports} {
        foreach p $ports { if {[lindex $p 0] ne "trigger"} { return 1 } }
        return 0
    }
    # The bits one sample stores: every probe bit except those on trigger only ports, which take no capture memory (measured: half the block RAM when half the bits are trigger only).
    proc ports_stored {ports} {
        set n 0
        foreach p $ports { if {[lindex $p 0] ne "trigger"} { incr n [llength [lindex $p 1]] } }
        return $n
    }
    # "" when every port is inside the family width limit and the core inside the family total, else the N10 sentence. A port is one signal
    # and probe type (unit_ports), so the width limit is per signal: 1024 bits on Versal, 4096 on UltraScale and UltraScale+ (Q6).
    proc port_limits {arch ports} {
        set lim [family_limits $arch]
        set fam [dict get $lim family]
        set tot 0
        foreach p $ports {
            set w [llength [lindex $p 1]]
            incr tot $w
            if {$w > [dict get $lim max_width]} {
                set sig [lindex [net_signal [lindex $p 1 0]] 0]
                return "$w probe bits of $sig ([type_words [lindex $p 0]]) exceed the $fam limit of [dict get $lim max_width] bits for one probe port ([dict get $lim source]). Probe fewer bits of it or ask the user which to keep."
            }
        }
        set mt [dict get $lim max_total_bits]
        if {$mt ne "" && $tot > $mt} {
            return "$tot probe bits on one clock exceed the $fam limit of $mt probe bits for one ILA core ([dict get $lim source]). Probe fewer bits or ask the user which to keep."
        }
        return ""
    }
    # At most 1024 comparators in one core: probe ports times the comparators of each (PG172, PG357). Each signal is a port, so this also
    # caps the signals on one clock: 1024 at one comparator, 512 with capture control (2), 64 at 16.
    proc check_comparator_limit {nports mucnt} {
        set total [expr {$nports * $mucnt}]
        if {$total > 1024} { return "$nports probe ports (one per signal) x $mucnt comparators = $total comparators on one core; Vivado allows at most 1024 (IP_Flow 19-3478 at opt_design). Probe fewer signals on this clock or ask for fewer comparators." }
        return ""
    }

    # ---- 4. plan ---------------------------------------------------------
    # ila::plan {name name ...} ?-combinational {name ...}? ?-filter <Vivado net filter expression>? ?-probe_types {name type ...}? ?-depth 1024? ?-name_prefix u_ila_?
    # A name is a plain Vivado name pattern (ila_resolve.tcl); the old typed item forms are refused (old_form_check).
    proc plan {items args} {
        # nothing asked for: no name, no -filter and no -combinational (an option list with a missing value is left for _plan to refuse)
        if {[llength $items] == 0 && [llength $args] % 2 == 0} {
            array set a [list -filter "" -combinational {} {*}$args]
            if {$a(-filter) eq "" && [llength $a(-combinational)] == 0} {
                return -code error "ILA_BAD_ITEM: nothing to probe, because no name was given. Name the registers or nets to probe, or ask for the nets that carry MARK_DEBUG."
            }
        }
        return [flat _plan $items {*}$args]
    }
    # Take the result of one item into the plan (called from _plan, which owns the variables). Every refused net is kept by name, once per
    # code, so the plan lists them all; the printed list is capped in write_plan_summary. A probed net is recorded here, and only here,
    # so an alias of it in a later item is "probed once" while a net some item refused is never lost to a later item.
    proc plan_commit {item stage irej iwarn ino} {
        upvar 1 rejected rejected warns warns accepted accepted periods periods combnets combnets seen seen segseen segseen segby segby rejkeys rejkeys aliasof aliasof netord netord
        foreach r $irej {
            set key "[lindex $r 0] [lindex $r 1]"
            if {[dict exists $rejkeys $key]} continue
            dict set rejkeys $key 1
            lappend rejected $r
        }
        lappend warns {*}$iwarn
        foreach e $stage {
            lassign $e cn nm per w segn
            set dup ""
            foreach sn [concat [list $nm] $segn] { if {[dict exists $segseen $sn]} { set dup [dict get $segseen $sn]; break } }
            if {$dup ne ""} {
                if {$dup ne $nm} {
                    dict set aliasof $nm $dup
                    lappend warns [list $nm "A01 alias of $dup (same electrical net), probed once"]
                    say "ALIAS $nm is the same net as $dup; probed once"
                }
                continue
            }
            foreach sn [concat [list $nm] $segn] { dict set segseen $sn $nm }
            dict set seen $nm 1
            dict set segby $nm $segn
            dict set netord $nm $ino   ;# the request item that probed it: ports follow the order of the names (Q12)
            dict lappend accepted $cn $nm
            if {$per ne ""} { dict set periods $cn $per }
            if {$w ne ""} { lappend warns [list $nm $w]; lappend combnets $nm }
        }
    }
    proc _plan {items args} {
        variable plan
        variable facts
        variable last_rung
        variable last_refusal
        set plan [dict create]   ;# a plan that raises must not leave the previous plan applicable
        input_gate plan
        array set o {-depth 1024 -name_prefix u_ila_ -max_probe_bits 4096 -allow_duplicate 0 -replace_existing 0 -pipe_stages -1 -strg_qual 0 -adv_trigger 0 -trigin 0 -trigout 0 -broad_limit 1024 -time_budget 300 -memory_type bram -comparators -1 -probe_type data_and_trigger -combinational {} -filter {} -probe_types {}}
        check_options ila::plan ILA_BAD_OPTION [array names o] $args
        array set o $args
        if {![regexp {^[A-Za-z0-9_]+$} $o(-name_prefix)]} { return -code error "ILA_OPTION_RANGE: -name_prefix '$o(-name_prefix)' is not accepted. A core name prefix may hold only letters, digits and underscore (for example u_ila_), because the name becomes the debug core name in Vivado. Nothing was changed: ask again with a prefix made of those characters." }
        # A typed old item form is refused before anything is planned. -filter and -combinational then become internal items (their labels are
        # frozen, N6): filt: is every net the filter gives, comb:<name> takes the combinational nets of that name only. A name may not be in both the name list and -combinational.
        foreach item [concat $items $o(-combinational)] { old_form_check $item }
        set ptpairs [probe_type_pairs $o(-probe_types)]   ;# per-name probe types (typed is 0 when no name has its own type)
        set typed [expr {[llength $ptpairs] > 0}]
        variable cur_filter
        set cur_filter $o(-filter)
        if {$o(-filter) ne ""} { lappend items filt: }
        foreach g $o(-combinational) {
            if {[lsearch -exact $items $g] >= 0} { return -code error "ILA_BAD_ITEM: '$g' is asked for twice, as an ordinary name and as a combinational net. Ask for it once: as a combinational net it takes combinational nets only, as an ordinary name it takes what the name says." }
            lappend items "comb:$g"
        }
        if {[dfx_gate $items] eq "nothing"} {
            # every item targets a partition that holds no logic (greybox or black box): a success with nothing to insert
            set msg "this design is a DFX configuration and the requested partition holds no logic (greybox or black box). There is no net to probe, so nothing is inserted."
            say "NOTHING TO INSERT: $msg"
            set rej [lmap i $items {list $i N18 "$msg"}]
            set plan [dict create dfx 1 groups {} rejected $rej warnings {} depth $o(-depth) replace_existing $o(-replace_existing) combnets {} opts [dict create pipe $o(-pipe_stages) strg $o(-strg_qual) adv $o(-adv_trigger) trigin $o(-trigin) trigout $o(-trigout)] bram_tiles_needed 0 bram_tiles_free -1 budget_ok 1]
            write_plan_summary
            return 0
        }
        set dfxrps [lmap r [dfx_cells] {dict get $r cell}]   ;# empty on a flat design: nothing below changes
        set m05on [expr {[llength $dfxrps] > 0 && [require_architecture] ne "versal" && [dfx_insert_proceeds]}]   ;# static cores probing partition nets: M05
        set vd [valid_depths]
        if {[lsearch -exact $vd $o(-depth)] < 0} {
            say "REFUSED depth $o(-depth): Vivado accepts only $vd"
            return -code error "ILA_DEPTH_RANGE: depth $o(-depth) is not valid. Vivado accepts: $vd. Choose one of those or ask the user."
        }
        foreach {k lo hi} {-pipe_stages -1 6 -strg_qual 0 1 -adv_trigger 0 1 -trigin 0 1 -trigout 0 1 -allow_duplicate 0 1} {
            if {![string is integer -strict $o($k)] || $o($k) < $lo || $o($k) > $hi} { return -code error "ILA_OPTION_RANGE: $k must be a whole number from $lo to $hi (got $o($k))" }
        }
        # -max_probe_bits, -broad_limit and -time_budget are whole numbers. Without this Tcl compared the text (a string against a number), so a typo such as abc turned the limit off
        # without a word, and a leading zero would be read as octal (review A finding 10). A plain decimal is taken as typed. -time_budget 0 means no limit.
        foreach {k lo} {-max_probe_bits 1 -broad_limit 1 -time_budget 0} {
            if {![regexp {^(0|[1-9][0-9]*)$} $o($k)] || $o($k) < $lo} { return -code error "ILA_OPTION_RANGE: $k must be a whole number of $lo or more, written without a sign, a decimal point or a leading zero (got '$o($k)')" }
        }
        set o(-memory_type) [string tolower $o(-memory_type)]
        if {$o(-memory_type) ni {bram uram}} { return -code error "ILA_OPTION_RANGE: -memory_type must be bram or uram (got $o(-memory_type))" }
        if {$o(-memory_type) eq "uram"} {
            set march [require_architecture]
            if {$march ne "versal"} { return -code error "ILA_OPTION_RANGE: -memory_type uram needs a Versal part. The ILA core has C_MEMORY_TYPE only on Versal (architecture here: '$march')." }
            set uvd [lmap d $vd {expr {$d >= 4096 ? $d : [continue]}}]
            if {[lsearch -exact $uvd $o(-depth)] < 0} { return -code error "ILA_OPTION_RANGE: -memory_type uram needs depth [join $uvd { }] (Vivado: C_MEMORY_TYPE 1 only at those depths; got $o(-depth))." }
        }
        # trigger in and out (refused, D2), the comparator count (D8) and the probe type (ila_change.tcl)
        if {[catch {plan_core_options $o(-trigin) $o(-trigout) $o(-adv_trigger) $o(-strg_qual) $o(-comparators) $o(-probe_type) $typed} r]} { say "REFUSED $r"; return -code error $r }
        set o(-comparators) $r
        set accepted [dict create]   ;# clocknet -> list of nets
        set periods  [dict create]   ;# clocknet -> clock period in ns (when the clock is constrained)
        set rejected {}
        set warns {}
        set seen [dict create]      ;# nets that are PROBED (accepted), by name: a refused net must stay visible to later items
        set segseen [dict create]   ;# every name of a probed net -> the name it is probed under
        set rejsegs [dict create]   ;# refused net -> its names, to drop a refusal when another item probes the same net
        set combnets {}
        set wildpending {}
        set tstart [clock seconds]
        cache_reset
        lassign [resolve_probe_types $ptpairs] tyby tyidx   ;# empty when no name has its own type
        set tyused [dict create]
        # Nets an existing debug core already probes: skipped as N20 unless -allow_duplicate 1.
        set replnames [expr {$o(-replace_existing) ? [replace_target_names [expr {[llength $dfxrps] > 0}]] : {}}]
        set probed [expr {$o(-allow_duplicate) ? [dict create] : [probed_nets $o(-replace_existing)]}]
        set n20more [expr {!$o(-replace_existing) && [llength [get_debug_cores -quiet u_ila_*]] > 0 ? " To change what the old core probes, plan again and replace the old core." : ""}]
        set segby [dict create]    ;# probe net name -> its segment names (LF3 alias notes)
        set netord [dict create]   ;# probe net name -> the number of the request item that probed it (port order)
        set rejkeys [dict create]  ;# "net code" of the refusals kept, so a net refused twice is listed once
        set mdmode [mark_debug_mode]
        if {$mdmode in {disable synthesis_only}} { lappend warns [list MARK_DEBUG [lindex [mark_debug_mode_text $mdmode] 1]] }
        # For the REQUEST lines (request_lines): the nets each name matched, the refusal of a name as a whole, the probed net an alias stands for,
        # and the refusal of a core's clock (N10) that leaves its nets out. Names only: nothing here asks Vivado anything.
        set reqnets [dict create]; set reqrej [dict create]; set aliasof [dict create]; set clockrej [dict create]
        set itemno -1
        foreach item $items {
            incr itemno
            # A plan that runs past the budget stops with a message instead of leaving the session busy for an hour.
            if {$o(-time_budget) > 0 && [clock seconds] - $tstart > $o(-time_budget)} {
                say "REFUSED: planning ran over $o(-time_budget) s and stopped before '$item'."
                return -code error "ILA_PLAN_TIMEOUT: the plan took more than $o(-time_budget) s (stopped before name '$item', [llength $items] names asked for). Ask for fewer or more specific names, or allow more planning time."
            }
            set nq 0
            set ncombskip 0
            # Item kinds: a plain name, or one of the two internal items (comb:<name> from -combinational, filt: from -filter). A "broad" item matches the
            # whole design (a bare *, or comb:*). A plain name with a wildcard in its last part takes register outputs only (N16 for the rest). A combinational
            # net is probed when the user names it exactly, or lists it in -combinational.
            if {[regexp {^(comb|filt):} $item]} {
                set kind [lindex [split $item :] 0]
                set pat [string range $item [expr {[string length $kind] + 1}] end]
            } else {
                set kind plain; set pat $item   ;# a plain name
            }
            set broad [expr {$kind in {comb plain} && [regexp {^\*+$} $pat]}]
            set wild  [expr {$kind eq "plain" && [regexp {[*?]} [lindex [split $pat /] end]]}]
            # Only an unscoped wildcard is limited by -broad_limit. A pattern scoped to a module or path (its first path segment is a plain
            # instance name with no * or ?, e.g. u_dma/data_reg*) is limited only by N10 and the block RAM budget (Mike, 2026-10-29).
            # A first segment such as *a or u* names no instance, so *a/* is as broad as *.
            set unscoped [expr {[regexp {[*?]} $pat] && ([string first / $pat] < 0 || [regexp {[*?]} [lindex [split $pat /] 0]])}]
            # A path glob that ends in a plain name (*/x, u_a/*/x) names one signal, which -broad_limit never limited.
            if {$kind eq "plain" && [string first / $pat] >= 0 && ![regexp {[*?]} [lindex [split $pat /] end]]} { set unscoped 0 }
            if {$kind eq "comb" && $broad} {
                # refused before any per-net query: a combinational * would qualify every net of the design first
                lappend rejected [list $item N17 "request too broad: '$pat' asks for every combinational net in the design. If the user named a specific net, name it, or give a combinational name with a module path. Otherwise stop and ask the user which module, register or signals to probe. Do not pick for them."]
                dict set reqrej $item [lindex $rejected end]
                continue
            }
            set found [resolve_item $item]
            if {$last_rung ne ""} { say "RULE '$item': $last_rung" }   ;# which rule matched, in words (plain names and the filter only)
            # A wildcard name that matches far more than the limit is refused at once, before the slow per-net checks.
            if {$kind eq "plain" && $unscoped && [llength $found] > 4 * $o(-broad_limit)} {
                lappend rejected [list $item N17 "request too broad: '$item' matches [llength $found] nets, far over the limit of $o(-broad_limit). If the user named a specific register, name it with its path (for example u_dma/count) instead. Otherwise stop and ask the user which module, register or signals to probe. Do not pick for them."]
                dict set reqrej $item [lindex $rejected end]
                continue
            }
            if {[llength $found] == 0 && $last_refusal ne ""} {
                lappend rejected [list $item N01 $last_refusal]   ;# a name that exists only elsewhere: the sentence lists the places
                dict set reqrej $item [lindex $rejected end]
                continue
            }
            if {[llength $found] == 0} {
                set hint [expr {$kind eq "filt" ? "" : [suggest $item]}]
                # Nothing of that name is left, so the netlist cannot tell why: name the causes, not only combinational merging (a register loaded
                # with a constant is removed too, and "probe at a register output" makes no sense for it). Known causes have their own codes:
                # a constant net is N05, an undriven one N02, a name that exists elsewhere is the "exists in" sentence above.
                set why " Synthesis may have renamed or removed it: a register with a constant value or with no loads is removed, and a net between combinational operators is often merged into a LUT (UG903). The netlist alone cannot tell which. For a combinational net, probe at a hierarchy boundary or at a register output instead (UG949, Choosing Debug Nets)."
                if {$kind eq "filt"} {
                    set why " Check the filter, or name the signals to probe."
                    if {[string match *MARK_DEBUG* $o(-filter)]} {
                        set why " No marked net found: no net carries MARK_DEBUG (or a block-design Debug tag). To probe a signal anyway, ask for it by name, with its path when it sits in a module (for example u_top/count)."
                    }
                    if {$mdmode in {disable synthesis_only}} {
                        set why " config_flows -mark_debug is $mdmode in this session, so MARK_DEBUG nets may have been optimized away. Name the nets instead."
                    }
                }
                if {$mdmode in {disable synthesis_only}} {
                    # M01 hint: the checkpoint cannot tell why the net is gone, so this names the setting and claims no cause.
                    append why " If this net should have been kept, check the config_flows -mark_debug setting ($mdmode drops it from the netlist); the checkpoint alone cannot tell the cause."
                }
                lappend rejected [list $item N01 "nothing in the netlist matches [expr {$kind eq "filt" ? "the filter {$o(-filter)}" : "'$pat'"}].$hint$why"]
                dict set reqrej $item [lindex $rejected end]
                continue
            }
            set stage {}      ;# accepted entries of this item: {clocknet name period warn}
            set irej {}       ;# rejections of this item
            set iwarn {}
            set bndn {}       ;# accepted nets on a DFX partition boundary (M04 note)
            set insn {}       ;# accepted nets inside a DFX partition, static core (M05 warning)
            set inets {}      ;# the nets of this item, for its REQUEST line
            foreach pair $found {
                lassign $pair net origin
                lappend inets "$net"
                if {[dict exists $seen $net]} continue   ;# the same net, already probed under this name
                if {$o(-time_budget) > 0 && [incr nq] % 16 == 0 && [clock seconds] - $tstart > $o(-time_budget)} {
                    say "REFUSED: planning ran over $o(-time_budget) s and stopped inside '$item'."
                    return -code error "ILA_PLAN_TIMEOUT: the plan took more than $o(-time_budget) s (stopped inside name '$item', [llength $found] nets matched). Ask for fewer or more specific names, or allow more planning time."
                }
                # One electrical net has several names (wr_cnt[0] and u_fifo/din[0]). Probe it once and say so.
                set segn {}
                set sgs [get_nets -quiet -segments $net]
                if {[llength $sgs] > 0} { set segn [get_property NAME $sgs] }   ;# one call for all segments, not one per segment
                # Only a net that is PROBED hides a later alias. A net an earlier item refused is not probed, so a later item still gets its turn.
                set dup ""
                foreach sn $segn { if {[dict exists $segseen $sn]} { set dup [dict get $segseen $sn]; break } }
                if {$dup ne ""} {
                    dict set aliasof "$net" $dup
                    lappend iwarn [list [get_property NAME $net] "A01 alias of $dup (same electrical net), probed once"]
                    say "ALIAS [get_property NAME $net] is the same net as $dup; probed once"
                    continue
                }
                set q [qualify $net]
                if {![dict get $q ok]} {
                    lappend irej [list [get_property NAME $net] [dict get $q code] [dict get $q evidence]]
                    dict set rejsegs [get_property NAME $net] $segn
                    continue
                }
                if {$kind eq "comb" && ![dict exists $q warn]} { incr ncombskip; set inets [lrange $inets 0 end-1]; continue }   ;# comb: keeps combinational nets only (a register output was not asked for)
                if {[dict exists $q warn] && ($wild || $broad) && $kind ne "comb"} {
                    lappend irej [list [get_property NAME $net] N16 "combinational net (not a register output). A wildcard or match-all request does not include it, whatever words the user used (everything, whole module). Report it as an excluded class. Probe it only if the user named this net or said combinational. Reason (UG949, Choosing Debug Nets; UG903): MARK_DEBUG on a net in the middle of a combinatorial path stops the implementation optimizations (worse timing), and synthesis often merges such nets into LUTs and removes them. Probe at a hierarchy boundary, or at a register output (a synchronous net gives cycle-accurate capture)."]
                    dict set rejsegs [get_property NAME $net] $segn
                    continue
                }
                # N20: an existing debug core already probes this net (or another name of it). Skipped, not probed twice.
                set pcore ""
                if {[dict size $probed] > 0} {
                    foreach sn [concat [list [get_property NAME $net]] $segn] { if {[dict exists $probed $sn]} { set pcore [dict get $probed $sn]; break } }
                }
                if {$pcore ne ""} {
                    lappend irej [list [get_property NAME $net] N20 "[get_property NAME $net] is already probed by $pcore. Skipped, so the net is not captured twice (a second probe costs block RAM and renames the bus in the .ltx). To probe it again anyway, plan again and allow a second probe on the same net, but ask the user first.$n20more"]
                    dict set rejsegs [get_property NAME $net] $segn
                    continue
                }
                set b04 [dfx_boundary_reason $net]; if {$b04 ne ""} { lappend bndn $b04 }
                if {$m05on} { set m05 [dfx_inside_reason $net]; if {$m05 ne ""} { lappend insn [list $net $m05] } }
                set per ""; if {[dict exists $q period]} { set per [dict get $q period] }
                set w ""; if {[dict exists $q warn]} { set w [dict get $q warn] }
                lappend stage [list [dict get $q clocknet] [get_property NAME $net] $per $w $segn]
            }
            dict lappend reqnets $item {*}$inets
            if {$kind eq "comb" && $ncombskip > 0 && [llength $stage] == 0 && [llength $irej] == 0} {
                lappend rejected [list $item N01 "'$pat' matched only register outputs ($ncombskip nets), no combinational net. Asking for combinational nets lists combinational nets only. To probe a register output, name it without asking for combinational."]
                dict set reqrej $item [lindex $rejected end]
            }
            # Every net of the name refused as a hardened or unclocked path (N15, N07): when the same signal name is on a fabric path elsewhere, the
            # first refusal says where, as N01 does for a name that exists only elsewhere.
            if {$kind eq "plain" && [llength $stage] == 0 && [llength $irej] > 0 && [llength [lsearch -all -not -regexp [lmap r $irej {lindex $r 1}] {^N(07|15)$}]] == 0} {
                set fab [fabric_elsewhere [lmap r $irej {lindex $r 0}]]
                if {$fab ne ""} { set ev [lindex $irej 0 2]; if {[string index $ev end] ne "."} { append ev . }; lset irej 0 2 "$ev$fab" }
            }
            if {[llength $insn] > 0} {
                lappend iwarn [list $item "WARNING M05 [llength $insn] probed net(s) are inside the partition [lindex $insn 0 1], not on its boundary. First: [get_property NAME [lindex $insn 0 0]]. Vivado adds one port to the partition for each, so the static design links only the RM that was probed: a second RM stops with Netlist 29-77 (measured). Probe nets on the partition boundary to keep every RM linkable, or tell the user this static design belongs to this RM."]
            }
            if {[llength $bndn] > 0} { lappend iwarn [list $item "NOTE M04 [llength $bndn] probed net(s) on a DFX partition boundary. First: [lindex $bndn 0]"] }
            # Unscoped wildcard names are summed after the loop (a match-all split into several globs is still match-all).
            if {$kind eq "plain" && $unscoped} {
                lappend wildpending [list $item $stage $irej $iwarn $broad $wild $itemno]
                continue
            }
            plan_commit $item $stage $irej $iwarn $itemno
        }
        # unscoped wildcard names together: over the limit means the request is too broad. The text says what works: a named family of modules
        # (24 g_bank[*].u_bank instances, exploratory trial 2026-11-04) is planned module by module, each a scoped name.
        set wn {}
        foreach P $wildpending { foreach e [lindex $P 1] { set nm [lindex $e 1]; if {![dict exists $seen $nm]} { lappend wn $nm } } }
        set wildtotal [llength [lsort -unique $wn]]   ;# one net named by two globs counts once
        if {$wildtotal > $o(-broad_limit)} {
            foreach P $wildpending {
                lassign $P item stage irej iwarn broad wild ino
                lappend rejected [list $item N17 "request too broad: the unscoped wildcard names together match $wildtotal probe nets, over the limit of $o(-broad_limit) for one request. Asking per module, or per group of modules, works: a name that starts with the path of one module (for example u_dma/* or g_bank\[0\].u_bank/*) is not held to this limit, only to the probe width and the memory budget. If the user named a specific register, name it with its path (for example u_dma/count). If the user asked for everything, with no module or names, ask which module, register or signals to probe; do not pick for them."]
                dict set reqrej $item [lindex $rejected end]
            }
            set wildpending {}
        }
        foreach P $wildpending {
            lassign $P item stage irej iwarn broad wild ino
            plan_commit $item $stage $irej $iwarn $ino
        }
        # A net refused by one item and probed by another (named exactly, or under another name) is probed: drop its refusal.
        set rejected [lmap r $rejected {
            set hit 0
            foreach nm [concat [list [lindex $r 0]] [expr {[dict exists $rejsegs [lindex $r 0]] ? [dict get $rejsegs [lindex $r 0]] : {}}]] { if {[dict exists $segseen $nm]} { set hit 1; break } }
            if {$hit} continue
            set r
        }]
        # order bits: natural sort so x[2] comes before x[10]
        set groups {}
        set idx 0
        # Units of one core each: {region clock-net-for-the-checks clock-net-for-the-core nets}. A flat design has one unit per clock.
        # A DFX design has one per (region, clock): the nets of one RP, or of the static part, on one clock, and the RP clock is the RP-side name.
        # UltraScale+ on 2026.2 and newer: no core is named inside an RP (Vivado refuses it, 12-727, with no S_BSCAN ports and no hub there).
        # Every core goes in the static part and probes RP nets across the boundary, as the E10 flow check did. One unit per clock.
        set uarch [require_architecture]
        set static_only [expr {[llength $dfxrps] > 0 && $uarch ne "versal" && [dfx_insert_proceeds]}]
        set units {}
        foreach clknet [lsort [dict keys $accepted]] {
            if {[llength $dfxrps] == 0 || $static_only} { lappend units [list "" $clknet $clknet [dict get $accepted $clknet]]; continue }
            set byreg [dict create]
            foreach n [lsort -dictionary [dict get $accepted $clknet]] { dict lappend byreg [dfx_region $n $dfxrps] $n }
            foreach reg [lsort [dict keys $byreg]] {
                set eff $clknet
                if {$reg ne ""} {
                    set eff [dfx_rp_clock $reg $clknet]
                    if {$eff eq ""} {
                        foreach n [dict get $byreg $reg] { lappend rejected [list $n N19 "clock net $clknet has no name inside the reconfigurable partition $reg, so an ILA in the partition cannot take it as its clock. Not inserted. Ask the user which clock inside the partition to use."] }
                        continue
                    }
                }
                lappend units [list $reg $clknet $eff [dict get $byreg $reg]]
            }
        }
        set idxs [dict create]
        foreach u $units {
            lassign $u reg clknet effclk unets
            set nets [lsort -dictionary $unets]
            set maxb $o(-max_probe_bits)
            set arch [require_architecture]
            # N10: the family limit first (Versal 1024 bits per probe port, UltraScale/UltraScale+ 4096 and 65,536 bits per core),
            # then the user's own -max_probe_bits.
            # One port per signal and probe type, in the order of the names. A core needs one port that stores data.
            set ports [unit_ports $nets $o(-probe_type) $tyby $tyidx $segby tyused $netord]
            if {![ports_store_data $ports]} {
                return -code error "ILA_OPTION_RANGE: every net on clock net $clknet would be trigger only, so its core would have no probe that stores data, and Vivado fails at opt_design (IP_Flow 19-3478). Give at least one net on this clock the type data, or trigger and data. Nothing was changed."
            }
            set famlim [port_limits $arch $ports]
            if {$famlim eq ""} { set famlim [check_comparator_limit [llength $ports] [mu_count $o(-adv_trigger) $o(-strg_qual) $o(-comparators)]] }
            if {$famlim ne ""} {
                lappend rejected [list $clknet N10 $famlim]
                foreach n $nets { dict set clockrej $n [lindex $rejected end] }
                continue
            }
            if {[llength $nets] > $maxb} {
                lappend rejected [list $clknet N10 "[llength $nets] probes on one clock exceeds the limit of $maxb bits set for this plan. Probe fewer bits, raise that limit (the [dict get [family_limits $arch] family] limit is [dict get [family_limits $arch] max_width] bits for one probe port) or ask the user which to keep."]
                foreach n $nets { dict set clockrej $n [lindex $rejected end] }
                continue
            }
            lassign [clock_source $clknet] cv csrc cpath
            foreach ln [lock_notes $cpath $clknet] { lappend warns [list $clknet [expr {[lindex $ln 0] eq "NOTE" ? "NOTE " : ""}][lindex $ln 1]] }
            # Report, do not block: the trace reads the netlist and cannot prove a clock stops, so the probes are planned and the user is told.
            if {$cv eq "gated"} { lappend warns [list $clknet "clock net $clknet can stop ($csrc). The skill read this from the netlist and cannot prove the clock stops. A clock that stops makes this core stop capturing, and makes the debug hub unreachable if the hub is on this clock (UltraScale and UltraScale+ put the hub on a free ILA clock when the plan has one). The probes are planned. Check the clock before hardware."] }
            set per ""; if {[dict exists $periods $clknet]} { set per [dict get $periods $clknet] }
            # name: first free core name, unless the user asked to replace the existing probe set. A core in an RP is named <rp>/<prefix>N.
            set pre [expr {$reg ne "" ? "$reg/" : ""}]
            # DFX: one counter for all regions, and a number is also skipped when any core's name ends in it. The report lists a core in an
            # RP by its short name, so two cores with the same short name (u_ila_0 and rp1/u_ila_0) could not be told apart.
            set ik [expr {[llength $dfxrps] > 0 ? "" : $reg}]
            set idx 0; if {[dict exists $idxs $ik]} { set idx [dict get $idxs $ik] }
            # With -replace_existing the cores it deletes do not hold a name; a core that stays does (review g03 C-03).
            while {[name_taken "$pre$o(-name_prefix)$idx" $replnames] || ([llength $dfxrps] > 0 && [name_taken "*$o(-name_prefix)$idx" $replnames])} { incr idx }
            set nets [concat {*}[lmap p $ports {lindex $p 1}]]   ;# the nets in port order, probe0 first
            set gd [dict create name "$pre$o(-name_prefix)$idx" region $reg clocknet $effclk nets $nets width [llength $nets] \
                            clock_verdict $cv clock_source $csrc clock_path $cpath period $per]
            dict set gd ports $ports; dict set gd stored [ports_stored $ports]
            lappend groups $gd
            dict set idxs $ik [expr {$idx + 1}]
        }
        # A typed name none of whose nets is probed (not asked for, or refused) is refused: the user asked for a type for nothing.
        for {set i 0} {$i < [llength $ptpairs]} {incr i} {
            if {![dict exists $tyused $i]} {
                return -code error "ILA_BAD_ITEM: the probe type of '[lindex $ptpairs $i 0]' was asked for, but none of its nets is probed. Name only nets that are probed; a refused net is listed in the rejections. Nothing was changed."
            }
        }
        # Versal: one AXI Debug Hub takes 64 debug cores (PG361). Refuse a plan that would not fit, naming the count.
        if {[require_architecture] eq "versal" && [llength $groups] > 0} {
            set hubmax [dict get [family_limits versal] hub_cores]
            set hubused [hub_core_count $o(-replace_existing)]
            if {$hubused + [llength $groups] > $hubmax} {
                say "REFUSED: the AXI Debug Hub takes $hubmax debug cores and has $hubused; this plan adds [llength $groups]."
                return -code error "ILA_HUB_CORE_LIMIT: the Versal AXI Debug Hub connects at most $hubmax debug cores (PG361). The design already has $hubused and this plan adds [llength $groups] (one per clock). Probe fewer clock domains or remove existing cores."
            }
        }
        # Say which bits of each indexed bus are in the probe set, so the report can name the bits that are not.
        set busidx [dict create]
        foreach g $groups {
            foreach n [dict get $g nets] {
                if {[regexp {^(.*)\[(\d+)\]$} $n -> base ix]} { dict lappend busidx $base $ix }
            }
        }
        dict for {base ixs} $busidx {
            set ixs [lsort -integer -unique $ixs]
            set lo [lindex $ixs 0]; set hi [lindex $ixs end]
            set miss {}
            for {set i $lo} {$i <= $hi} {incr i} { if {[lsearch -exact $ixs $i] < 0} { lappend miss $i } }
            set note "probed [llength $ixs] bits in \[$hi:$lo\]"
            if {$lo > 0} { append note "; bits \[[expr {$lo-1}]:0\] are not probed" }
            if {[llength $miss] > 0} { append note "; missing inside the range: $miss" }
            if {$lo > 0 || [llength $miss] > 0} {
                set decl ""
                set derr ""
                if {[catch { set decl [bus_declared "$base\[$hi\]" [lmap i $ixs {set _ "$base\[$i\]"}]] } derr]} { set decl ""; set derr " (reading it failed: $derr)" } else { set derr "" }
                if {$decl ne ""} {
                    say "BUS $base: $note. $decl. Report these bits and why they are not probed."
                } else {
                    say "BUS $base: $note. Declared width unknown$derr: say which bits you cannot probe, including any above $hi, and why."
                }
            }
        }
        lassign [net_rules_post $groups $segby $busidx] groups rnotes
        lappend warns {*}$rnotes
        # The groups as the memory budget sees them: a trigger only port takes no capture memory, so a group counts its stored bits (the plan keeps the probe width).
        set sgroups [lmap g $groups {if {[dict exists $g stored]} { dict set g width [dict get $g stored] }; set g}]
        # memory cost by formula (bram_tiles), summed over the cores: each core rounds up on its own
        set tiles 0
        set march [require_architecture]
        foreach g $sgroups { incr tiles [bram_tiles $o(-depth) [dict get $g width] $march] }
        set free [free_bram_tiles]
        # block RAM that existing ILA cores will take when they are built (the cells exist only after opt_design); a core -replace_existing deletes is not counted
        # Clamped at 0: a negative result would read as "unknown" (-1) below. Existing cores that take more than is free leave nothing, so the budget fails.
        set free_measured $free; set modelled 0
        if {$free >= 0} { set modelled [existing_ila_tiles $o(-replace_existing)]; set free [expr {max(0, $free - $modelled)}] }
        set utiles 0; set ufree -1
        if {$o(-memory_type) eq "uram"} {
            # the capture memory sits in UltraRAM; block RAM is not used by the core
            foreach g $sgroups { incr utiles [uram_tiles $o(-depth) [dict get $g width]] }
            set tiles 0
            set ufree [free_uram_tiles]
            if {$ufree >= 0} { set ufree [expr {max(0, $ufree - [existing_ila_uram_tiles $o(-replace_existing)])}] }   ;# unbuilt UltraRAM cores already in the design (review A finding 8)
        }
        # DFX: a core in a reconfigurable partition can use only the RAMB36 sites (UltraRAM: the URAM288 sites) of that partition's pblock, so its
        # budget is the pblock (sites minus what the RM already uses and minus what the debug cores already in the partition will take). A core
        # in the static part cannot use the room the pblocks hold, so that room is taken off the device count. A partition without a pblock stays
        # on the device count (W02). Failing region: the first partition that does not fit, else the static part. Its numbers are the ones reported.
        set bgroups $sgroups; set bregion ""; set bnote ""
        set isuram [expr {$o(-memory_type) eq "uram"}]
        set mfree [expr {$isuram ? $ufree : $free}]
        set mname [expr {$isuram ? "UltraRAM" : "block RAM"}]
        if {$mfree >= 0 && [llength $dfxrps] > 0 && [llength $groups] > 0} {
            set pneed [dict create]; set sneed 0; set room 0; set pbs [dict create]; set pfree 0
            foreach g $sgroups {
                set t [expr {$isuram ? [uram_tiles $o(-depth) [dict get $g width]] : [bram_tiles $o(-depth) [dict get $g width] $march]}]
                set r [dict get $g region]
                if {$r ne "" && [dict exists $pneed $r]} { dict incr pneed $r $t } elseif {$r ne ""} { dict set pneed $r $t } else { incr sneed $t }
            }
            foreach rp $dfxrps {
                set pf [expr {$isuram ? [partition_uram $rp] : [partition_bram $rp]}]
                if {[llength $pf] == 0} {
                    if {[dict exists $pneed $rp]} { lappend warns [list W02 "partition budget NOT checked: partition $rp has no pblock (or its $mname sites cannot be read), so its cores were compared with the free count of the static part (the whole device when no partition has a pblock). The plan is not refused for it. Tell the user; Vivado may fail at implementation if the partition has too little $mname."]; incr sneed [dict get $pneed $rp]; dict unset pneed $rp }
                    continue
                }
                lassign $pf pbn pbtot pbused
                # block RAM only: the existing debug cores inside this partition that are not built yet have no RAM cell, so the model counts them
                if {!$isuram} { incr pbused [existing_ila_tiles $o(-replace_existing) $rp] }
                set pbf [expr {max(0, $pbtot - $pbused)}]
                dict set pbs $rp [list $pbn $pbtot $pbused $pbf]
                incr room $pbf
            }
            if {[dict size $pbs] > 0} {
                set sfree [expr {max(0, $mfree - $room)}]
                set ntiles 0; set pfree 0
                foreach rp [dict keys $pbs] {
                    if {![dict exists $pneed $rp]} continue
                    lassign [dict get $pbs $rp] pbn pbtot pbused pbf
                    incr ntiles [dict get $pneed $rp]; incr pfree $pbf
                    if {$bregion eq "" && [dict get $pneed $rp] > $pbf} {
                        set bregion $rp; set bneed [dict get $pneed $rp]; set bfree $pbf
                        set bnote "partition $rp (pblock $pbn) holds $pbtot $mname tiles, $pbused are used by the design inside it, $pbf are free for the debug core."
                        set bgroups [lmap g $sgroups {expr {[dict get $g region] eq $rp ? $g : [continue]}}]
                    }
                }
                if {$sneed > 0} { incr ntiles $sneed; incr pfree $sfree }
                if {$bregion eq "" && $sneed > $sfree} {
                    set bregion static; set bneed $sneed; set bfree $sfree
                    set bnote "the static part has $sfree $mname tiles free: $room of the $mfree free on the device are inside the partition pblocks and a static core cannot use them."
                    set bgroups [lmap g $sgroups {expr {[dict get $g region] eq "" || ![dict exists $pbs [dict get $g region]] ? $g : [continue]}}]
                }
                if {$bregion ne ""} { set ntiles $bneed; set nfree $bfree } else { set nfree $pfree }
                if {$isuram} { set utiles $ntiles; set ufree $nfree } else { set tiles $ntiles; set free $nfree }
            }
        }
        set budget_ok [expr {($free < 0 || $tiles <= $free) && ($ufree < 0 || $utiles <= $ufree)}]
        # An unknown free count (-1) lets the check pass. Say so: criterion 4 is "not silently attempted". Nothing is refused, because there is
        # no number to refuse on; the user is told, and the facts record budget_checked 0.
        set budget_checked [expr {$o(-memory_type) eq "uram" ? $ufree >= 0 : $free >= 0}]
        dict set facts budget_checked $budget_checked
        if {!$budget_checked && [llength $groups] > 0} {
            set what [expr {$o(-memory_type) eq "uram" ? "UltraRAM count (ULTRA_RAMS)" : "block RAM count (BLOCK_RAMS)"}]
            lappend warns [list W02 "budget NOT checked: the part reports no $what, so the capture memory ([expr {$o(-memory_type) eq "uram" ? $utiles : $tiles}] tiles at depth $o(-depth)) was not compared with what is free. The plan is not refused. Tell the user the memory budget was not checked; Vivado may fail at opt_design if it does not fit."]
        }
        set plan [dict create dfx [expr {[llength $dfxrps] > 0}] groups $groups rejected $rejected warnings $warns depth $o(-depth) replace_existing $o(-replace_existing) combnets $combnets opts [dict create pipe $o(-pipe_stages) strg $o(-strg_qual) adv $o(-adv_trigger) trigin $o(-trigin) trigout $o(-trigout) memtype $o(-memory_type) comparators $o(-comparators) ptype $o(-probe_type)] \
                  bram_tiles_needed $tiles bram_tiles_free $free bram_tiles_free_measured $free_measured bram_tiles_modelled $modelled uram_tiles_needed $utiles uram_tiles_free $ufree budget_ok $budget_ok budget_checked $budget_checked budget_region $bregion \
                  requested [dict create items $items filter $o(-filter) nets $reqnets refused $reqrej alias $aliasof clock $clockrej]]
        if {!$budget_ok && [llength $groups] > 0} {
            set wtot 0; foreach g $bgroups { incr wtot [dict get $g width] }
            set fit "none"
            if {$o(-memory_type) eq "uram"} {
                foreach d [lsort -integer -decreasing $uvd] {
                    set u 0; foreach g $bgroups { incr u [uram_tiles $d [dict get $g width]] }
                    if {$u <= $ufree} { set fit $d; break }
                }
                say "BUDGET: depth $o(-depth) x $wtot bits needs $utiles URAM tiles, $ufree are free. Largest valid depth that fits at this width: $fit. Quote these numbers."
                if {$bnote ne ""} { say "BUDGET: $bnote" }
            } else {
                foreach d [lsort -integer -decreasing $vd] {
                    set u 0; foreach g $bgroups { incr u [bram_tiles $d [dict get $g width] $march] }
                    if {$u <= $free} { set fit $d; break }
                }
                say "BUDGET: depth $o(-depth) x $wtot bits needs $tiles block RAM tiles, $free are free. Largest valid depth that fits at this width: $fit. Quote these numbers."
                if {$bnote ne ""} { say "BUDGET: $bnote" }
            }
            # An agent once switched the memory to UltraRAM by itself and saved (exploratory trial, 2026-11-04). The choice is the user's: say so here.
            set other [expr {$march ne "versal" ? "" : ($o(-memory_type) eq "uram" ? ", or block RAM for the capture memory" : ", or UltraRAM for the capture memory (depth 4096 or more)")}]
            say "STOP: nothing can be applied over the memory budget. Do not change the depth, the signals or the memory type yourself. Tell the user the numbers and the choices in words (the largest depth that fits, fewer signals$other) and wait for their pick."
        }
        write_plan_summary
        foreach g $groups {
            if {![dict exists $g ports]} continue
            say "core [dict get $g name] has [llength [dict get $g ports]] probe port[expr {[llength [dict get $g ports]] == 1 ? "" : "s"}], one per signal: [port_words [dict get $g ports]]."
        }
        return [expr {[llength $groups] > 0 && $budget_ok}]
    }
}
