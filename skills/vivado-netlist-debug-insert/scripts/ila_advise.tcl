# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# ila_advise.tcl: read-only advice on a ROUTED design that already holds debug cores. It stands alone: source only this file.
#
#   ila::advise        print what the open routed design shows about its own debug logic, then hints tied to those facts
#
# It only reads the design (report_timing_summary, report_debug_core, get_timing_paths, get_cells, get_nets, get_pins, get_clocks,
# get_property, current_design). It changes no setting, runs no fix and writes no file. A design that is not routed is refused
# with ILA_ADVISE_NOT_ROUTED. Every hint is printed with the measured fact behind it and the source of the general rule.
# Limits: only the worst setup path decides; probe loading on your own nets is not measured; a few hundredths of a ns is noise.

namespace eval ila {
    variable advise_noise_ns 0.05

    proc advise_say {m} { puts $m }

    # ---- pure helpers (tested without Vivado) -------------------------------------------------------------------

    proc advise_str {obj} { set s ""; append s $obj; return $s }
    proc advise_row {line} { return [lmap c [lrange [split $line |] 1 end-1] {string trim $c}] }
    # the whole state text: "Routed", or "Physopt postRoute" after a post-route phys_opt_design (a routed design too)
    proc advise_parse_state {text} { if {[regexp {Design State[ \t]*:[ \t]*([^\n|]*)} $text -> s]} { return [string trim $s] }; return "" }

    # The Design Timing Summary table: wns tns tns_fail tns_total whs ths_fail. Empty values when the table is missing.
    proc advise_parse_timing {text} {
        set d [dict create wns "" tns "" tns_fail "" tns_total "" whs "" ths_fail ""]
        set lines [split $text "\n"]
        for {set i 0} {$i < [llength $lines]} {incr i} {
            if {[regexp {^\s*WNS\(ns\)\s+TNS\(ns\)} [lindex $lines $i]]} {
                set t [regexp -all -inline {\S+} [lindex $lines [expr {$i + 2}]]]
                if {[llength $t] >= 8} { return [dict create wns [lindex $t 0] tns [lindex $t 1] tns_fail [lindex $t 2] tns_total [lindex $t 3] whs [lindex $t 4] ths_fail [lindex $t 6]] }
            }
        }
        return $d
    }

    proc advise_core_kind {type} {
        if {[regexp -nocase {xsdbm|dbg_hub} $type]} { return hub }
        if {[regexp -nocase {ila} $type]} { return ila }
        return other
    }

    # report_debug_core -return_string -> dict core -> {type kind params channels freq}. channels: list of {port channel net}.
    # This table is how Vivado maps each probe bit to a net. A section that is not there stays empty.
    proc advise_parse_cores {text} {
        set cores [dict create]
        set mode ""; set core ""; set hdr 0; set port ""; set chcol 0
        foreach line [split $text "\n"] {
            if {[regexp {^\d+\.\d+ (\S+): \((\S+), } $line -> n t]} {
                if {![dict exists $cores $n]} { dict set cores $n [dict create type $t kind [advise_core_kind $t] params {} channels {} freq ""] }
                set mode ""; continue
            }
            if {[regexp {^Parameter Data for Debug Core "(.+)"} $line -> core]} { set mode params; continue }
            if {[regexp {^Channel Data for Debug Core "(.+)"} $line -> core]} { set mode chan; set hdr 0; set port ""; continue }
            if {[regexp {^Input Clk Freq of Debug Hub "(.+)"} $line -> core]} { set mode freq; continue }
            if {[string index $line 0] ne "|"} { if {[string index $line 0] ni {+ ""}} { set mode "" }; continue }
            if {$mode eq "" || ![dict exists $cores $core]} continue
            set c [advise_row $line]
            switch -- $mode {
                params { if {[llength $c] == 2 && [lindex $c 0] ne "Parameter"} { dict set cores $core params [lindex $c 0] [lindex $c 1] } }
                freq { if {[string is integer -strict [lindex $c 0]]} { dict set cores $core freq [lindex $c 0] } }
                chan {
                    if {[lindex $c 0] eq "Port Name"} { set chcol [lsearch -exact $c "Channel Name"]; set hdr 1; continue }
                    if {$hdr == 1} { set hdr 2; continue }
                    if {[lindex $c 0] ne ""} { set port [lindex $c 0] }
                    set net [lindex $c end]
                    if {$net ne ""} { dict set cores $core channels [linsert [dict get $cores $core channels] end [list $port [lindex $c $chcol] $net]] }
                }
            }
        }
        return $cores
    }

    proc advise_core_freq_hz {core} {
        if {[dict exists $core params C_CLK_INPUT_FREQ_HZ]} { return [dict get $core params C_CLK_INPUT_FREQ_HZ] }
        return [dict get $core freq]
    }
    proc advise_probe_bits {core} {
        set n 0
        foreach ch [dict get $core channels] { if {[regexp {^probe} [lindex $ch 0]]} { incr n } }
        return $n
    }
    proc advise_probe_ports {core} {
        set p {}
        foreach ch [dict get $core channels] { if {[regexp {^probe} [lindex $ch 0]]} { lappend p [lindex $ch 0] } }
        return [llength [lsort -unique $p]]
    }
    proc advise_clock_net {core} {
        foreach ch [dict get $core channels] { if {[lindex $ch 0] in {clk aclk}} { return [lindex $ch 2] } }
        return ""
    }
    proc advise_channel_net {core channel} {
        foreach ch [dict get $core channels] { if {[lindex $ch 1] eq $channel} { return [lindex $ch 2] } }
        return ""
    }
    proc advise_param {core name} { return [expr {[dict exists $core params $name] ? [dict get $core params $name] : ""}] }

    # The debug core a cell name is inside ("" when none), and the cell name under the core.
    proc advise_core_of {cell cores} {
        foreach c $cores { if {$cell eq $c || [string first "$c/" $cell] == 0} { return $c } }
        return ""
    }
    # hub, mem (capture memory control), regs (register interface to the hub), ila (other ILA logic)
    proc advise_cell_role {rel kind} {
        if {$kind eq "hub"} { return hub }
        if {[regexp {ila_trace_memory_inst|xsdb_memory_read_inst|mem_addr} $rel]} { return mem }
        if {[regexp {u_ila_regs|U_XSDB_SLAVE} $rel]} { return regs }
        return ila
    }
    proc advise_role_words {roles} {
        set w {hub "the debug hub" mem "capture memory control" regs "register interface to the hub" ila "other ILA logic"}
        return [join [lmap r $roles {dict get $w $r}] ", "]
    }
    # A cell name from a pin name: everything before the last slash.
    proc advise_cell_of_pin {pin} { return [string range $pin 0 [expr {[string last / $pin] - 1}]] }
    proc advise_mhz {hz} { return [format %g [expr {$hz / 1e6}]] }
    # " (period 1.100 ns, 909.1 MHz)" from the clock's period in the design; "" when the period is not a positive number. A clock's name says nothing about its frequency.
    proc advise_period_text {period} {
        if {![string is double -strict $period] || $period <= 0} { return "" }
        return " (period $period ns, [format %.1f [expr {1000.0 / $period}]] MHz)"
    }

    # ---- reads from the open design ---------------------------------------------------------------------------------

    # One setup or hold path: the numbers, the cells, the debug cores it enters and the probed nets it runs through.
    proc advise_path {kind cores} {
        set p [get_timing_paths -quiet -max_paths 1 -$kind]
        if {$p eq ""} { return {} }
        set start [advise_str [get_property STARTPOINT_PIN $p]]
        set end [advise_str [get_property ENDPOINT_PIN $p]]
        set clk [advise_str [get_property ENDPOINT_CLOCK $p]]
        set period ""
        if {$clk ne "" && ![catch {get_property PERIOD [get_clocks -quiet $clk]} v]} { set period $v }
        set cells {}
        foreach c [get_cells -quiet -of_objects $p] { lappend cells [get_property NAME $c] }
        set in {}
        foreach c $cells {
            set core [advise_core_of $c [dict keys $cores]]
            if {$core ne ""} { lappend in [list $c $core [advise_cell_role [string range $c [expr {[string length $core] + 1}] end] [dict get $cores $core kind]]] }
        }
        set end_role ""
        foreach d $in { if {[lindex $d 0] eq [advise_cell_of_pin $end]} { set end_role [lindex $d 2] } }
        set probed {}
        if {$kind eq "setup"} { set probed [advise_probed_nets $p $cores] }
        return [dict create slack [get_property SLACK $p] start $start end $end start_cell [advise_cell_of_pin $start] end_cell [advise_cell_of_pin $end] \
                end_role $end_role clock $clk period $period req [get_property REQUIREMENT $p] levels [get_property LOGIC_LEVELS $p] cells $cells debug $in probed $probed]
    }

    # The nets of a path that an ILA probe pin (<core>/probeN[i]) sits on. A path net is one segment of a net: Vivado's own
    # segments of it include the ILA's probe pin, so the probe is found by pin, not by net name (a Versal block design renames nets).
    proc advise_probed_nets {p cores} {
        set out {}
        set ilas {}
        foreach c [dict keys $cores] { if {[dict get $cores $c kind] eq "ila"} { lappend ilas $c } }
        if {[llength $ilas] == 0} { return {} }
        foreach n [get_nets -quiet -of_objects $p] {
            set segs [get_nets -quiet -segments $n]
            set hit ""
            foreach pin [get_pins -quiet -of_objects $segs] {
                set pn [get_property NAME $pin]
                foreach core $ilas {
                    if {[string first "$core/" $pn] == 0 && [regexp {^probe[0-9]+\[[0-9]+\]$} [string range $pn [expr {[string length $core] + 1}] end]]} {
                        set hit [list $core [string range $pn [expr {[string length $core] + 1}] end]]
                    }
                }
            }
            if {$hit eq ""} continue
            set drv {}; set loads {}
            foreach pin [get_pins -quiet -leaf -of_objects $n -filter {DIRECTION == OUT}] { lappend drv [advise_cell_of_pin [get_property NAME $pin]] }
            foreach pin [get_pins -quiet -leaf -of_objects $n -filter {DIRECTION == IN}] { lappend loads [advise_cell_of_pin [get_property NAME $pin]] }
            set incore 0
            foreach l $loads { if {[advise_core_of $l [dict keys $cores]] ne ""} { incr incore } }
            lappend out [dict create core [lindex $hit 0] channel [lindex $hit 1] net [advise_channel_net [dict get $cores [lindex $hit 0]] [lindex $hit 1]] \
                         driver [lindex $drv 0] loads [llength $loads] core_loads $incore]
        }
        return $out
    }

    # Everything the report and the hints are built from. Refuses a design that is not routed.
    proc advise_facts {} {
        set ts [report_timing_summary -no_detailed_paths -return_string]
        set state [advise_parse_state $ts]
        if {$state ne "Routed" && $state ne "Physopt postRoute"} {
            return -code error "ILA_ADVISE_NOT_ROUTED: the design state is '[expr {$state eq "" ? "unknown" : $state}]'. Advice reads a routed design only. Open a routed checkpoint (open_checkpoint <routed.dcp>) and ask for advice again (AI only: ila::advise)."
        }
        set t [advise_parse_timing $ts]
        set cores {}; set cerr ""
        if {[catch {report_debug_core -return_string} rdc]} { set cerr $rdc } else { set cores [advise_parse_cores $rdc] }
        set names [dict keys $cores]
        set ndbg 0; set ntop 0
        foreach p [get_timing_paths -quiet -max_paths 10 -nworst 1 -setup] {
            incr ntop
            foreach c [get_cells -quiet -of_objects $p] { if {[advise_core_of [get_property NAME $c] $names] ne ""} { incr ndbg; break } }
        }
        return [dict create design [advise_str [current_design]] part [get_property PART [current_design]] state $state timing $t cores $cores core_error $cerr \
                setup [advise_path setup $cores] hold [advise_path hold $cores] top_paths $ntop top_debug $ndbg]
    }

    # ---- the hints: each one is a rule over printed facts -------------------------------------------------------------

    # Returns a list of {HINT|NOTE|NO_HINT text}. A hint is only made from a fact in the setup path or the core table.
    proc advise_hints {f} {
        variable advise_noise_ns
        set out {}
        set s [dict get $f setup]
        if {[llength $s] == 0} { return {} }
        set cores [dict get $f cores]
        set slack [dict get $s slack]
        if {![string is double -strict $slack]} { return {} }
        if {$slack >= 0} { return [list [list NO_HINT "the worst setup path meets timing (slack $slack ns), so there is nothing to fix on it."]] }
        set miss [string trimleft $slack -]
        if {$miss < $advise_noise_ns} { lappend out [list NOTE "a miss of $miss ns is within noise: a few hundredths of a ns changes from run to run on this skill's ladder runs."] }
        set roles {}; set core_roles [dict create]
        foreach d [dict get $s debug] { lassign $d cell core role; dict lappend core_roles $core $role; lappend roles $role }
        set roles [lsort -unique $roles]
        set probed [dict get $s probed]
        set part [dict get $f part]
        # the part's own ARCHITECTURE decides (xcvh HBM parts are Versal too); the letters are only the fallback when the part cannot be read
        set versal [expr {[catch {get_property ARCHITECTURE [get_parts $part]} arch] ? [regexp -nocase {^x[cq]v[cmeph]} $part] : $arch eq "versal"}]
        set start_core [advise_core_of [dict get $s start_cell] [dict keys $cores]]
        set end_core [advise_core_of [dict get $s end_cell] [dict keys $cores]]
        set enters [expr {$start_core eq "" && $end_core ne "" && [dict get $cores $end_core kind] eq "ila"}]
        # hub
        foreach core [dict keys $core_roles] {
            if {[dict get $cores $core kind] ne "hub"} continue
            set hub [dict get $cores $core]
            set hz [advise_core_freq_hz $hub]; set net [advise_clock_net $hub]
            if {![string is integer -strict $hz] || $net eq ""} continue
            set mhz [advise_mhz $hz]
            if {$versal} {
                if {$hz >= 100000000 && $hz <= 250000000} {
                    lappend out [list NOTE "the hub clock $net is set to $mhz MHz in the debug core table (not measured), inside UG908's range of 100 MHz to 250 MHz for the AXI4-Debug Hub, so no clock hint."]
                } else {
                    lappend out [list HINT "the path is in the debug hub $core and its clock $net is set to $mhz MHz in the debug core table (not measured): for Versal, use a clock of 100 MHz to 250 MHz on the AXI4-Debug Hub (source: UG908, ILA Core and Timing Considerations)."]
                }
            } elseif {[advise_param $hub C_ENABLE_CLK_DIVIDER] eq "false"} {
                # the clock the path is timed on, from the design's constraints: the user compares it with the setting
                set timed ""
                if {[dict get $s clock] ne "" && [advise_period_text [dict get $s period]] ne ""} {
                    set timed " The path is timed on clock [dict get $s clock][advise_period_text [dict get $s period]], from your constraints."
                }
                lappend out [list HINT "the path is in the debug hub $core and its hub clock net $net: C_CLK_INPUT_FREQ_HZ is $mhz MHz (the core's setting, not measured from the clock).$timed Set C_CLK_INPUT_FREQ_HZ to the frequency of that clock and enable C_ENABLE_CLK_DIVIDER (now false), use a free-running clock, then implement again (source: UG908, ILA Core and Timing Considerations)."]
            } elseif {[advise_param $hub C_ENABLE_CLK_DIVIDER] eq "true"} {
                lappend out [list NOTE "C_ENABLE_CLK_DIVIDER is already true on $core, so no divider hint."]
            }
        }
        # ILA cores on the path
        foreach core [dict keys $core_roles] {
            set c [dict get $cores $core]
            if {[dict get $c kind] ne "ila"} continue
            set r [dict get $core_roles $core]
            set depth [advise_param $c C_DATA_DEPTH]; set pipe [advise_param $c C_INPUT_PIPE_STAGES]
            set bits [advise_probe_bits $c]; set ports [advise_probe_ports $c]
            if {"mem" in $r && [string is integer -strict $depth]} {
                set at [expr {[dict get $s end_role] eq "mem" && $end_core eq $core ? "ends at" : "runs through"}]
                if {$depth > 1024} {
                    lappend out [list HINT "the path $at the capture memory control of $core (depth $depth): a lower capture depth shortens it (source: UG908, ILA Core and Timing Considerations, choose data depth judiciously)."]
                } else {
                    lappend out [list NOTE "capture depth $depth is already the smallest the ILA offers, so no depth hint."]
                }
                if {[regexp -nocase {axis_ila} [dict get $c type]] && [advise_param $c C_MEMORY_TYPE] eq "0"} {
                    lappend out [list HINT "the path is in the capture memory control of $core and its storage is block RAM (C_MEMORY_TYPE 0): for the AXIS-ILA, UltraRAM storage can ease the block RAM control timing (source: UG908, ILA Core and Timing Considerations)."]
                }
            }
            if {$enters} {
                if {[string is integer -strict $pipe] && $pipe < 6} {
                    lappend out [list HINT "the path enters $core from your logic and its input pipe stages are $pipe (C_INPUT_PIPE_STAGES now $pipe, up to 6): more stages put registers between your logic and the capture logic (source: UG908, ILA Core and Timing Considerations)."]
                } elseif {[string is integer -strict $pipe]} {
                    lappend out [list NOTE "C_INPUT_PIPE_STAGES is already 6 on $core, the top of its range, so no pipe stage hint."]
                }
            } elseif {"mem" in $r} {
                lappend out [list NOTE "C_INPUT_PIPE_STAGES is not offered: the path is in the capture memory control, which input pipe stages did not move on this skill's measured runs (references/advise.md)."]
            } elseif {"ila" in $r} {
                lappend out [list NOTE "C_INPUT_PIPE_STAGES is not offered: the path starts and ends inside $core, and input pipe stages sit on the probe inputs, so they did not move such a path on this skill's measured runs (references/advise.md). UG908 lists them for any timing failure in the ILA."]
            }
            if {("mem" in $r || "ila" in $r || $enters) && $bits > 0} {
                lappend out [list HINT "$core has $bits probe bits on $ports probe [expr {$ports == 1 ? "port" : "ports"}]: fewer or narrower probes shrink its logic and its load on your nets (source: UG908, choose probe width judiciously; UG1388, limit the number and width of signals)."]
            }
            set clk [dict get $s clock]
            if {$clk ne ""} {
                set per [advise_period_text [dict get $s period]]
                lappend out [list HINT "$core captures on clock $clk$per, the path has a requirement of [dict get $s req] ns and misses by $miss ns: a slower capture clock gives it more time, if the probed logic allows it and the clock stays synchronous to the probed signals (general timing practice; UG908 asks for a synchronous capture clock)."]
            }
        }
        # probed net on the path
        if {[llength $probed] > 0} {
            set pn [lindex $probed 0]
            set what "[dict get $pn net], which [dict get $pn core] probes as [dict get $pn channel] ([dict get $pn loads] loads, [dict get $pn core_loads] inside [dict get $pn core])"
            if {[dict get $pn driver] eq [dict get $s start_cell]} {
                lappend out [list HINT "the path starts at your register [dict get $pn driver] that drives [dict get $pn channel] of [dict get $pn core] (net [dict get $pn net], [dict get $pn loads] loads, [dict get $pn core_loads] inside [dict get $pn core]): the probe adds load to this net. Add a register stage in your design, or probe the register output (design practice; probe loading is not measured here)."]
            } else {
                lappend out [list HINT "the path runs through your net $what: the probe adds load to this net. Add a register stage in your design, or probe the register output (design practice; probe loading is not measured here)."]
            }
        }
        set n 0
        foreach h $out { if {[lindex $h 0] eq "HINT"} { incr n } }
        if {$n == 0} {
            if {[llength $roles] == 0 && [llength $probed] == 0} {
                lappend out [list NO_HINT "the worst setup path is in your logic and touches no debug core. Whether the ILA made it worse is not measured: that needs a build without the ILA."]
            } else {
                lappend out [list NO_HINT "no rule matches the measured facts of this path."]
            }
        }
        return $out
    }

    # ---- the report ---------------------------------------------------------------------------------------------------

    proc advise_path_lines {label p f} {
        set cores [dict get $f cores]
        if {[llength $p] == 0} { return [list "$label: not measured (no constrained path found)"] }
        set l {}
        set per [advise_period_text [dict get $p period]]
        set by [dict create]
        foreach d [dict get $p debug] { lassign $d cell core role; dict lappend by $core $role }
        set where "it does not touch a debug core"
        if {[dict size $by] > 0} {
            set parts {}
            dict for {core roles} $by { lappend parts "inside $core: [advise_role_words [lsort -unique $roles]]" }
            set where "it is [join $parts {, and }]"
        }
        if {$label eq "worst setup path"} {
            lappend l "$label: slack [dict get $p slack] ns (requirement [dict get $p req] ns), clock [dict get $p clock]$per, [dict get $p levels] logic levels"
            lappend l "  start [dict get $p start]"
            lappend l "  end [dict get $p end]"
            lappend l "  $where"
        } else {
            lappend l "$label: slack [dict get $p slack] ns, clock [dict get $p clock], [dict get $p start] to [dict get $p end]; $where"
        }
        foreach pn [dict get $p probed] {
            set own [expr {[dict get $pn driver] eq [dict get $p start_cell] ? "; your register [dict get $pn driver] drives [dict get $pn channel] of [dict get $pn core]" : ""}]
            lappend l "  it runs through your net [dict get $pn net], probed as [dict get $pn core] [dict get $pn channel]$own ([dict get $pn loads] loads, [dict get $pn core_loads] inside [dict get $pn core])"
        }
        return $l
    }

    proc advise_report {f} {
        set cores [dict get $f cores]
        set t [dict get $f timing]
        set l [list "design [dict get $f design], part [dict get $f part], state [dict get $f state]"]
        if {[dict get $f core_error] ne ""} { lappend l "debug cores: not read (report_debug_core failed: [dict get $f core_error])" } elseif {[dict size $cores] == 0} {
            lappend l "debug cores: none"
        } else {
            set parts {}
            dict for {n c} $cores {
                switch -- [dict get $c kind] {
                    hub { set hz [advise_core_freq_hz $c]; lappend parts "$n (hub[expr {[string is integer -strict $hz] ? ", C_CLK_INPUT_FREQ_HZ [advise_mhz $hz] MHz" : ""}])" }
                    ila { lappend parts "$n (ILA, depth [advise_param $c C_DATA_DEPTH], [advise_probe_bits $c] probe bits on [advise_probe_ports $c] probe [expr {[advise_probe_ports $c] == 1 ? "port" : "ports"}], input pipe stages [advise_param $c C_INPUT_PIPE_STAGES])" }
                    default { lappend parts "$n ([dict get $c type])" }
                }
            }
            lappend l "debug cores: [join $parts {, }]"
        }
        lappend l "setup WNS [dict get $t wns] ns, [dict get $t tns_fail] of [dict get $t tns_total] endpoints fail; hold WHS [dict get $t whs] ns, [dict get $t ths_fail] fail"
        lappend l {*}[advise_path_lines "worst setup path" [dict get $f setup] $f]
        lappend l "debug logic on the 10 worst setup paths: [dict get $f top_debug] of [dict get $f top_paths]"
        lappend l {*}[advise_path_lines "worst hold path" [dict get $f hold] $f]
        set h [expr {[llength [dict get $f hold]] > 0 ? [dict get $f hold slack] : ""}]
        if {[string is double -strict $h] && $h < 0} { lappend l "NOTE: hold timing fails (slack $h ns): hold has no defined fix here, so no hint is given for it." }
        set n 0
        foreach h [advise_hints $f] {
            lassign $h kind text
            switch -- $kind { HINT { lappend l "HINT [incr n]: $text" } NOTE { lappend l "NOTE: $text" } NO_HINT { lappend l "NO HINT: $text" } }
        }
        lappend l "LIMITS: only the worst setup path decides; the 10 worst paths are counted, not read."
        lappend l "LIMITS: probe loading on your own nets is not measured; the load counts above are pin counts."
        lappend l "LIMITS: a few hundredths of a ns is noise (single runs); hold has no defined fix; nothing here was run or changed."
        lappend l "RULE: Report numbers exactly as printed, every digit (a printed 12.5 MHz is not 12 or 13). Do not predict a gain; this tool measures, it does not forecast."
        return $l
    }

    proc advise {} {
        foreach m [advise_report [advise_facts]] { advise_say "ILA ADVISE $m" }
        return ""
    }
}
