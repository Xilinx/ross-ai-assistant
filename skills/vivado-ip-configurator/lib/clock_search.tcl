# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Clock-generator configuration search for vector CLKOUT_* wizards.
#
# ipcfg::search_clock_config tries distinct generator configurations for
# integer-Hz targets under a generator-count constraint and reads back each
# target's propagated FREQ_HZ. It never widens a tolerance or edits a target.
# FAIL means no tried configuration met the contract: evidence for review, not
# a silicon impossibility proof. Axes explored:
#   input     PRIM_IN_FREQ propagated from the source vs written explicitly
#   grouping  partitions of targets over 1..max_generators generators
#   derive    exact /2 /4 /8 relations served from one buffer's divided taps
#   encoding  nominal, exact-MHz or kHz-rounded requested rates
#   order     given vs descending-rate output order
#   companion spare outputs carrying 1x/2x source-rate companions
# The planner is pure Tcl; only the live_* adapters touch Vivado.

namespace eval ipcfg {}
namespace eval ipcfg::clocksearch {
    # divide -> MBUFGCE tap index (o1 is the undivided output)
    variable taps {1 1 2 2 4 3 8 4}
}

proc ipcfg::clocksearch::mhz {hz} {
    set text [format %d.%06d [expr {$hz / 1000000}] [expr {$hz % 1000000}]]
    return [string trimright [string trimright $text 0] .]
}

proc ipcfg::clocksearch::normalize_targets {targets} {
    set result {}
    set ids {}
    foreach target $targets {
        foreach field {id hz} {
            if {![dict exists $target $field]} {error "clock target missing $field"}
        }
        set id [dict get $target id]
        if {$id in $ids} {error "duplicate clock target $id"}
        lappend ids $id
        set hz [dict get $target hz]
        if {![string is wideinteger -strict $hz] || $hz <= 0} {error "clock target $id hz must be a positive integer"}
        set tolerance [expr {[dict exists $target tolerance_hz] ? [dict get $target tolerance_hz] : 0}]
        if {![string is wideinteger -strict $tolerance] || $tolerance < 0} {error "clock target $id tolerance_hz invalid"}
        set nominal [expr {[dict exists $target nominal_hz] ? [dict get $target nominal_hz] : $hz}]
        if {![string is wideinteger -strict $nominal] || $nominal <= 0} {error "clock target $id nominal_hz invalid"}
        lappend result [dict create id $id hz $hz tolerance_hz $tolerance nominal_hz $nominal]
    }
    if {![llength $result]} {error "at least one clock target is required"}
    return $result
}

proc ipcfg::clocksearch::partitions {count groups} {
    # Set partitions as restricted-growth label strings, fewest groups first.
    set result {}
    set stack [list [list {} 0]]
    while {[llength $stack]} {
        lassign [lindex $stack end] labels used
        set stack [lrange $stack 0 end-1]
        if {[llength $labels] == $count} {
            lappend result [list $used $labels]
            continue
        }
        for {set label [expr {min($used, $groups - 1)}]} {$label >= 0} {incr label -1} {
            lappend stack [list [concat $labels $label] [expr {max($used, $label + 1)}]]
        }
    }
    return [lmap item [lsort -integer -index 0 [lsort -index 1 $result]] {lindex $item 1}]
}

proc ipcfg::clocksearch::ratio_affinity {a b} {
    # Small-integer frequency ratios tend to share one VCO solution.
    set x [dict get $a hz]
    set y [dict get $b hz]
    for {set p 1} {$p <= 16} {incr p} {
        for {set q 1} {$q <= 16} {incr q} {
            if {$x * $q == $y * $p} {return [expr {($p == 1 || $q == 1) ? 3 : 2}]}
        }
    }
    return 0
}

proc ipcfg::clocksearch::by_rate {order outputs} {
    if {$order eq "given"} {return $outputs}
    return [lsort -command {apply {{a b} {
        set x [dict get $a hz]
        set y [dict get $b hz]
        expr {$y > $x ? 1 : $y < $x ? -1 : 0}
    }}} $outputs]
}

proc ipcfg::clocksearch::physical_outputs {group derive} {
    # Each output: {target drive taps}; taps maps tap index -> target id.
    variable taps
    set descending [ipcfg::clocksearch::by_rate descending $group]
    set served {}
    set outputs {}
    foreach base $group {
        set id [dict get $base id]
        if {$id in $served} {continue}
        set mapping [dict create 1 $id]
        if {$derive} {
            foreach candidate $descending {
                if {[dict get $candidate hz] <= [dict get $base hz]} {continue}
                set parent_id [dict get $candidate id]
                if {$parent_id in $served} {continue}
                foreach {divide tap} $taps {
                    if {$divide > 1 && [dict get $candidate hz] == [dict get $base hz] * $divide} {
                        set mapping {}
                        break
                    }
                }
                if {$mapping eq {}} {break}
            }
            if {$mapping eq {}} {continue}
            foreach other $descending {
                set other_id [dict get $other id]
                if {$other_id eq $id || $other_id in $served} {continue}
                foreach {divide tap} $taps {
                    if {$divide > 1 && [dict get $base hz] == [dict get $other hz] * $divide && ![dict exists $mapping $tap]} {
                        dict set mapping $tap $other_id
                        break
                    }
                }
            }
        }
        dict for {tap served_id} $mapping {lappend served $served_id}
        lappend outputs [dict create target $base drive [expr {[dict size $mapping] > 1 ? "MBUFGCE" : "BUFG"}] taps $mapping]
    }
    return $outputs
}

proc ipcfg::clocksearch::variants {} {
    # Full product of axes, fewest departures from the defaults first.
    set defaults {input propagate derive 1 encoding nominal order given companion none}
    set axes {input {propagate explicit} derive {1 0} encoding {nominal exact khz} order {given descending} companion {none multiples}}
    set combos [list {}]
    foreach {axis values} $axes {
        set next {}
        foreach combo $combos {
            foreach value $values {lappend next [dict merge $combo [dict create $axis $value]]}
        }
        set combos $next
    }
    set scored {}
    set rank 0
    foreach combo $combos {
        set distance 0
        dict for {axis value} $combo {
            if {$value ne [dict get $defaults $axis]} {incr distance}
        }
        lappend scored [list $distance [incr rank] $combo]
    }
    return [lmap item [lsort -integer -index 0 [lsort -integer -index 1 $scored]] {lindex $item 2}]
}

proc ipcfg::clocksearch::requested_hz {target encoding} {
    set hz [dict get $target hz]
    switch -- $encoding {
        nominal {return [dict get $target nominal_hz]}
        exact {return $hz}
        khz {return [expr {(($hz + 500) / 1000) * 1000}]}
    }
    error "unknown encoding $encoding"
}

proc ipcfg::clocksearch::topologies {targets max_generators top_partitions} {
    set by_count {}
    foreach labels [ipcfg::clocksearch::partitions [llength $targets] $max_generators] {
        set groups {}
        foreach target $targets label $labels {dict lappend groups $label $target}
        set score 0
        foreach members [dict values $groups] {
            for {set i 0} {$i < [llength $members]} {incr i} {
                for {set j [expr {$i + 1}]} {$j < [llength $members]} {incr j} {
                    incr score [ipcfg::clocksearch::ratio_affinity [lindex $members $i] [lindex $members $j]]
                }
            }
        }
        dict lappend by_count [dict size $groups] [list $score $labels [dict values $groups]]
    }
    set result {}
    foreach count [lsort -integer [dict keys $by_count]] {
        set ranked [lsort -integer -decreasing -index 0 [lsort -index 1 [dict get $by_count $count]]]
        foreach item [lrange $ranked 0 [expr {$top_partitions - 1}]] {lappend result [lindex $item 2]}
    }
    return $result
}

proc ipcfg::clocksearch::candidates {targets source_hz max_generators max_outputs {limit 24} {top_partitions 3} {supports_taps 1}} {
    # Pure planner: returns distinct candidates, never touches Vivado.
    set targets [ipcfg::clocksearch::normalize_targets $targets]
    foreach {name value minimum} [list max_generators $max_generators 1 max_outputs $max_outputs 1 limit $limit 1 source_hz $source_hz 1] {
        if {![string is wideinteger -strict $value] || $value < $minimum} {error "$name must be an integer >= $minimum"}
    }
    set result {}
    set seen {}
    set topologies [ipcfg::clocksearch::topologies $targets $max_generators $top_partitions]
    foreach variant [ipcfg::clocksearch::variants] {
        if {[dict get $variant derive] && !$supports_taps} {continue}
        foreach groups $topologies {
            set generators {}
            set feasible 1
            foreach group $groups {
                set requested {}
                foreach output [ipcfg::clocksearch::physical_outputs [ipcfg::clocksearch::by_rate [dict get $variant order] $group] [dict get $variant derive]] {
                    dict set output requested_hz [ipcfg::clocksearch::requested_hz [dict get $output target] [dict get $variant encoding]]
                    dict unset output target
                    lappend requested $output
                }
                if {[dict get $variant companion] eq "multiples"} {
                    foreach multiple {1 2} {
                        if {[llength $requested] < $max_outputs} {
                            lappend requested [dict create drive BUFG taps {} requested_hz [expr {$source_hz * $multiple}]]
                        }
                    }
                }
                if {[llength $requested] > $max_outputs} {set feasible 0; break}
                lappend generators $requested
            }
            if {!$feasible} {continue}
            set key [list [dict get $variant input] $generators]
            if {$key in $seen} {continue}
            lappend seen $key
            lappend result [dict create id [llength $result] input [dict get $variant input] variant $variant generators $generators]
            if {[llength $result] >= $limit} {return $result}
        }
    }
    return $result
}

proc ipcfg::clocksearch::config {generator capacity input_mode source_hz} {
    set outputs [dict get $capacity outputs]
    set used [lrepeat $outputs false]
    set rates [lrepeat $outputs 100]
    set drives [lrepeat $outputs BUFG]
    set index 0
    foreach output $generator {
        lset used $index true
        lset rates $index [ipcfg::clocksearch::mhz [dict get $output requested_hz]]
        lset drives $index [dict get $output drive]
        incr index
    }
    set config [dict create CONFIG.CLKOUT_USED [join $used ,] CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY [join $rates ,]]
    if {[dict get $capacity taps]} {dict set config CONFIG.CLKOUT_DRIVES [join $drives ,]}
    if {$input_mode eq "explicit" && [dict get $capacity input]} {
        dict set config CONFIG.PRIM_IN_FREQ [ipcfg::clocksearch::mhz $source_hz]
    }
    return $config
}

# --- Live adapters: the only procs that call Vivado (unit tests replace them) ---
proc ipcfg::clocksearch::live_source_hz {source} {
    set object [get_bd_ports -quiet $source]
    if {![llength $object]} {set object [get_bd_pins -quiet $source]}
    if {[llength $object] != 1} {error "clock source $source not found"}
    return [get_property CONFIG.FREQ_HZ $object]
}

proc ipcfg::clocksearch::live_capacity {vlnv cell} {
    set receipt [ipcfg::create_cell $vlnv $cell]
    if {![string match SUCCESS:* $receipt]} {error $receipt}
    set object [get_bd_cells $cell]
    set properties [list_property $object]
    foreach property {CONFIG.CLKOUT_USED CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY CONFIG.CLKOUT_PORT} {
        if {$property ni $properties} {error "unsupported wizard schema: $property"}
    }
    return [dict create outputs [llength [split [get_property CONFIG.CLKOUT_USED $object] ,]] \
        taps [expr {"CONFIG.CLKOUT_DRIVES" in $properties}] input [expr {"CONFIG.PRIM_IN_FREQ" in $properties}]]
}

proc ipcfg::clocksearch::live_configure {vlnv cell config source} {
    set receipt [ipcfg::create_cell $vlnv $cell]
    if {![string match SUCCESS:* $receipt]} {error $receipt}
    set receipt [ipcfg::apply_dict $cell $config]
    if {![string match SUCCESS:* $receipt]} {error $receipt}
    set inputs [get_bd_pins -quiet -of_objects [get_bd_cells $cell] -filter {DIR==I && TYPE==clk}]
    if {[llength $inputs] != 1} {error "wizard must expose one clock input, found [llength $inputs]"}
    set endpoint [get_bd_ports -quiet $source]
    if {![llength $endpoint]} {set endpoint [get_bd_pins $source]}
    connect_bd_net $endpoint [lindex $inputs 0]
    return [split [get_property CONFIG.CLKOUT_PORT [get_bd_cells $cell]] ,]
}

proc ipcfg::clocksearch::live_validate {} {
    set before [ipcfg::_message_counts]
    set code [catch {validate_bd_design} result]
    return [dict create code $code result $result before $before after [ipcfg::_message_counts]]
}

proc ipcfg::clocksearch::live_pin_hz {cell port tap} {
    # MBUFGCE outputs appear as <port>_o1.._o4; BUFG outputs as <port>.
    set names [lmap pin [get_bd_pins -quiet -of_objects [get_bd_cells $cell] -filter {DIR==O}] {file tail $pin}]
    if {"${port}_o$tap" in $names} {
        set name ${port}_o$tap
    } elseif {$tap == 1 && $port in $names} {
        set name $port
    } else {
        error "cannot resolve output $port tap $tap on $cell"
    }
    return [list $cell/$name [get_property CONFIG.FREQ_HZ [get_bd_pins $cell/$name]]]
}

proc ipcfg::clocksearch::live_delete {cells} {
    foreach cell $cells {
        set object [get_bd_cells -quiet $cell]
        if {[llength $object]} {delete_bd_objs $object}
    }
}

proc ipcfg::clocksearch::live_evidence {path data} {ipcfg::write_evidence $path $data}

# Candidates are judged in a temporary BD so unrelated cells in the caller's
# design (unconnected PS/NoC clocks, missing masters) cannot fail every trial.
proc ipcfg::clocksearch::live_isolate_begin {prefix source_hz} {
    set original [current_bd_design -quiet]
    if {$original eq ""} {error "an open block design is required"}
    set name ${prefix}_isolated_[clock clicks -milliseconds]
    create_bd_design $name
    set port [create_bd_port -dir I -type clk ${prefix}_source]
    set_property CONFIG.FREQ_HZ $source_hz $port
    return [dict create original [get_property NAME $original] isolated $name port ${prefix}_source]
}

proc ipcfg::clocksearch::live_isolate_end {context} {
    set isolated [get_files -quiet -all [dict get $context isolated].bd]
    current_bd_design [dict get $context isolated]
    close_bd_design [current_bd_design]
    if {[llength $isolated]} {
        set path [get_property NAME [lindex $isolated 0]]
        remove_files -quiet $isolated
        file delete -force [file dirname $path]
    }
    set original [get_files -quiet -all [dict get $context original].bd]
    if {![llength [get_bd_designs -quiet [dict get $context original]]]} {open_bd_design $original}
    current_bd_design [dict get $context original]
}

proc ipcfg::clocksearch::live_place {vlnv prefix generators capacity input source_hz source} {
    set cells {}
    set slot 0
    foreach generator $generators {
        set cell ${prefix}_$slot
        set config [ipcfg::clocksearch::config $generator $capacity $input $source_hz]
        ipcfg::clocksearch::live_configure $vlnv $cell $config $source
        lappend cells $cell
        incr slot
    }
    return $cells
}

proc ipcfg::clocksearch::try_candidate {vlnv source targets candidate capacity source_hz prefix index} {
    set cells {}
    set observations {}
    set ports {}
    set slot 0
    foreach generator [dict get $candidate generators] {
        set cell ${prefix}_${index}_$slot
        lappend cells $cell
        set config [ipcfg::clocksearch::config $generator $capacity [dict get $candidate input] $source_hz]
        dict set ports $cell [ipcfg::clocksearch::live_configure $vlnv $cell $config $source]
        incr slot
    }
    set validation [ipcfg::clocksearch::live_validate]
    if {[dict get $validation code]} {return [list $cells {} "validation failed: [dict get $validation result]"]}
    foreach severity {{CRITICAL WARNING} ERROR} {
        if {[dict get $validation after $severity] > [dict get $validation before $severity]} {
            return [list $cells {} "validation emitted $severity"]
        }
    }
    set required [dict create]
    foreach target $targets {dict set required [dict get $target id] $target}
    foreach cell $cells generator [dict get $candidate generators] {
        set position 0
        foreach output $generator {
            dict for {tap id} [dict get $output taps] {
                lassign [ipcfg::clocksearch::live_pin_hz $cell [lindex [dict get $ports $cell] $position] $tap] pin actual
                set target [dict get $required $id]
                set error_hz [expr {$actual - [dict get $target hz]}]
                lappend observations [dict create id $id pin $pin required_hz [dict get $target hz] \
                    actual_hz $actual error_hz $error_hz matches [expr {abs($error_hz) <= [dict get $target tolerance_hz]}]]
            }
            incr position
        }
    }
    return [list $cells $observations {}]
}

proc ipcfg::search_clock_config {vlnv source targets evidence_dir args} {
    # Options: -max_generators N (1) -min_attempts N (10) -max_attempts N (24)
    # -prefix NAME (clksearch) -isolate 0|1 (1). With -isolate 1 candidates are
    # tried in a temporary BD fed at the source rate, then only the winning
    # configuration is placed in the caller's design, wired to the real source.
    # Details are left in ::ipcfg::last_clock_search.
    set options [dict merge {-max_generators 1 -min_attempts 10 -max_attempts 24 -prefix clksearch -isolate 1} $args]
    set started [clock milliseconds]
    set isolation {}
    set code [catch {
        set prefix [dict get $options -prefix]
        if {![regexp {^[A-Za-z][A-Za-z0-9_]*$} $prefix]} {error "invalid cell prefix"}
        if {[file pathtype $evidence_dir] ne "absolute"} {error "absolute evidence directory required"}
        set minimum [dict get $options -min_attempts]
        set maximum [dict get $options -max_attempts]
        if {![string is integer -strict $minimum] || $minimum < 1 || $maximum < $minimum} {
            error "require 1 <= -min_attempts <= -max_attempts"
        }
        file mkdir $evidence_dir
        set targets [ipcfg::clocksearch::normalize_targets $targets]
        set source_hz [ipcfg::clocksearch::live_source_hz $source]
        set trial_source $source
        if {[dict get $options -isolate]} {
            set isolation [ipcfg::clocksearch::live_isolate_begin $prefix $source_hz]
            set trial_source [dict get $isolation port]
        }
        set capacity [ipcfg::clocksearch::live_capacity $vlnv ${prefix}_capacity]
        ipcfg::clocksearch::live_delete [list ${prefix}_capacity]
        set plan [ipcfg::clocksearch::candidates $targets $source_hz [dict get $options -max_generators] \
            [dict get $capacity outputs] $maximum 3 [dict get $capacity taps]]
        set attempts {}
        set selected {}
        foreach candidate $plan {
            set index [llength $attempts]
            if {[catch {ipcfg::clocksearch::try_candidate $vlnv $trial_source $targets $candidate $capacity $source_hz $prefix $index} outcome]} {
                set names {}
                for {set slot 0} {$slot < [llength [dict get $candidate generators]]} {incr slot} {lappend names ${prefix}_${index}_$slot}
                set outcome [list $names {} $outcome]
            }
            lassign $outcome cells observations failure
            set matched [expr {$failure eq {} && [llength $observations] == [llength $targets]}]
            foreach observation $observations {
                if {![dict get $observation matches]} {set matched 0}
            }
            set attempt [dict create attempt [expr {$index + 1}] candidate $candidate matched $matched \
                observations $observations failure $failure cells $cells]
            lappend attempts $attempt
            ipcfg::clocksearch::live_evidence [file join $evidence_dir attempt_[expr {$index + 1}].tcldict] $attempt
            if {$matched} {set selected $attempt; break}
            ipcfg::clocksearch::live_delete $cells
            if {[llength $attempts] >= $maximum} {break}
        }
        set count [llength $attempts]
        set status [expr {$selected ne {} ? "PASS" : "FAIL"}]
        set placed {}
        if {$isolation ne {}} {
            ipcfg::clocksearch::live_isolate_end $isolation
            set isolation {}
            if {$status eq "PASS"} {
                set placed [ipcfg::clocksearch::live_place $vlnv ${prefix}_selected \
                    [dict get $selected candidate generators] $capacity [dict get $selected candidate input] $source_hz $source]
                dict set selected cells $placed
            }
        }
        set result [dict create status $status vlnv $vlnv source $source source_hz $source_hz \
            isolated [dict get $options -isolate] targets $targets \
            max_generators [dict get $options -max_generators] candidates_available [llength $plan] \
            attempts $attempts selected $selected elapsed_ms [expr {[clock milliseconds] - $started}]]
        ipcfg::clocksearch::live_evidence [file join $evidence_dir search.tcldict] $result
        set ::ipcfg::last_clock_search $result
        if {$status eq "PASS"} {
            set line "CLOCK_SEARCH:PASS attempts=$count cells=[join [dict get $selected cells] ,] input=[dict get $selected candidate input]"
            if {[dict get $options -isolate]} {
                append line " isolated_proof=exact caller_readback=pending_until_caller_bd_validates\
 -> connect the placed outputs, validate_bd_design cleanly, then ipcfg::clock_placement_status"
            }
        } elseif {$count < $minimum} {
            set line "CLOCK_SEARCH:FAIL attempts=$count only $count distinct candidates exist under -max_generators [dict get $options -max_generators] (<$minimum)"
        } else {
            set scores [lmap attempt $attempts {
                set total 0
                foreach observation [dict get $attempt observations] {incr total [expr {abs([dict get $observation error_hz])}]}
                list [expr {[dict get $attempt failure] eq {} ? $total : 1 << 62}] [dict get $attempt attempt]
            }]
            lassign [lindex [lsort -integer -index 0 $scores] 0] total best
            set line "CLOCK_SEARCH:FAIL attempts=$count best_attempt=$best total_abs_error_hz=$total"
        }
    } detail]
    if {$isolation ne {}} {catch {ipcfg::clocksearch::live_isolate_end $isolation}}
    if {$code} {return "CLOCK_SEARCH:ERROR:$detail"}
    return $line
}

# Placed-cell readback. IP integrator propagates parameters only during a
# validate_bd_design that completes (UG994); until then a wizard's input clock
# keeps its DEFAULT value and every output reads a rate derived from it.
#   inputs:  {<cell> {<hz> <value_src>} ...}
#   outputs: {{id <id> pin <pin> required_hz <hz> actual_hz <hz> tolerance_hz <hz>} ...}
proc ipcfg::clocksearch::placement_verdict {inputs outputs} {
    set pending {}
    dict for {cell reading} $inputs {
        lassign $reading hz source
        if {[string toupper $source] in {"" DEFAULT}} { lappend pending "$cell=${hz}(${source})" }
    }
    if {[llength $pending]} {
        return [list PENDING $pending]
    }
    set bad {}; set good {}
    foreach output $outputs {
        set error [expr {[dict get $output actual_hz] - [dict get $output required_hz]}]
        set item "[dict get $output id]=[dict get $output actual_hz]"
        if {abs($error) > [dict get $output tolerance_hz]} {
            lappend bad "${item}(want [dict get $output required_hz])"
        } else {
            lappend good $item
        }
    }
    return [expr {[llength $bad] ? [list FAIL $bad] : [list OK $good]}]
}

proc ipcfg::clocksearch::live_input_clock {cell} {
    set pin [lindex [get_bd_pins -quiet -filter {DIR == I && TYPE == clk} $cell/*] 0]
    if {$pin eq ""} { error "no clock input on $cell" }
    return [list [get_property -quiet CONFIG.FREQ_HZ $pin] [get_property -quiet CONFIG.FREQ_HZ.VALUE_SRC $pin]]
}

proc ipcfg::clocksearch::live_pin_freq {pin} {
    set object [get_bd_pins -quiet $pin]
    if {$object eq ""} { error "no pin $pin" }
    return [get_property -quiet CONFIG.FREQ_HZ $object]
}

proc ipcfg::clocksearch::live_blockers {} {
    if {[llength [info commands ::ipiasm::check_undriven_clocks]]} {
        return [lindex [split [::ipiasm::check_undriven_clocks] \n] 0]
    }
    return ""
}

# Public: are the cells placed by the last (or a given) clock search at their
# required rates in the caller's design? Returns CLOCK_PLACEMENT:OK|PENDING|FAIL|ERROR.
proc ipcfg::clock_placement_status {{search ""}} {
    if {[catch {
        if {$search eq ""} {
            if {![info exists ::ipcfg::last_clock_search]} { error "no clock search has run" }
            set search $::ipcfg::last_clock_search
        }
        if {[dict get $search status] ne "PASS"} { error "the clock search did not pass" }
        set selected [dict get $search selected]
        set cells [dict get $selected cells]
        set tolerance [dict create]
        foreach target [dict get $search targets] { dict set tolerance [dict get $target id] [dict get $target tolerance_hz] }
        set inputs [dict create]
        foreach cell $cells { dict set inputs $cell [ipcfg::clocksearch::live_input_clock $cell] }
        set outputs {}
        foreach observation [dict get $selected observations] {
            lassign [split [dict get $observation pin] /] trial port
            set slot [lindex [split $trial _] end]
            set pin [lindex $cells $slot]/$port
            lappend outputs [dict create id [dict get $observation id] pin $pin \
                required_hz [dict get $observation required_hz] actual_hz [ipcfg::clocksearch::live_pin_freq $pin] \
                tolerance_hz [dict get $tolerance [dict get $observation id]]]
        }
        lassign [ipcfg::clocksearch::placement_verdict $inputs $outputs] verdict items
    } detail]} {
        return "CLOCK_PLACEMENT:ERROR:$detail"
    }
    switch -- $verdict {
        PENDING {
            set blockers [ipcfg::clocksearch::live_blockers]
            return "CLOCK_PLACEMENT:PENDING inputs={[join $items { }]} -> parameters have not propagated\
(input clock still DEFAULT); a validate_bd_design that fails does not propagate. Make the design\
validate cleanly (drive every clock input), then re-check.[expr {$blockers eq {} ? {} : " Blocking: $blockers"}]"
        }
        OK { return "CLOCK_PLACEMENT:OK n=[llength $items] {[join $items { }]}" }
        default { return "CLOCK_PLACEMENT:FAIL [llength $items] {[join $items { }]} (inputs propagated; outputs differ)" }
    }
}
