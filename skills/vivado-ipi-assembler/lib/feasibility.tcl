# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

namespace eval ::ipiasm::feasibility {
    # Answers already read during the current with_memo pass; empty outside one.
    variable memo {}
    variable memo_depth 0
}

# Run script with endpoint and property reads remembered, so one read-only
# pass asks Vivado each question once instead of once per helper that needs
# it. The memo is emptied when the outermost scope exits, error or not, so
# no answer survives into a later pass or across a design change. Never wrap
# anything that mutates the design.
proc ::ipiasm::feasibility::with_memo {script} {
    variable memo
    variable memo_depth
    incr memo_depth
    try {
        set code [catch {uplevel 1 $script} result options]
    } finally {
        if {[incr memo_depth -1] == 0} {set memo {}}
    }
    if {$code == 0} {return $result}
    dict incr options -level
    return -options $options $result
}

proc ::ipiasm::feasibility::_remember {key script} {
    variable memo
    variable memo_depth
    if {$memo_depth && [dict exists $memo $key]} {return [dict get $memo $key]}
    set value [uplevel 1 $script]
    if {$memo_depth} {dict set memo $key $value}
    return $value
}

proc ::ipiasm::feasibility::endpoint {path} {
    return [_remember [list endpoint $path] [list ::ipiasm::feasibility::resolve $path]]
}

proc ::ipiasm::feasibility::resolve {path} {
    # Resolve afresh so native Vivado objects are not stringified in a cache;
    # endpoint keeps the object itself, and only for one with_memo pass.
    if {$path eq "" || [regexp {[*?\[\]]} $path]} {error "requires exact endpoint path"}
    # get_bd_pins also exposes a bd_pin view of a top-level port. Prefer the
    # actual port, whose input direction drives (rather than consumes) the BD.
    set ports [get_bd_ports -quiet $path]
    if {[llength $ports] == 1} {return $ports}
    if {[llength $ports]} {error "ambiguous endpoint $path"}
    set pins [get_bd_pins -quiet $path]
    if {[llength $pins] == 1} {return $pins}
    if {[llength $pins]} {error "ambiguous endpoint $path"}
    error "missing endpoint $path"
}

proc ::ipiasm::feasibility::is_driver {object} {
    # Top-level inputs drive the BD; cell outputs drive their consumers.
    # connected_to can return the bd_pin view even for a top-level port.
    set object [endpoint $object]
    set direction [property $object DIR]
    set kind [property $object CLASS]
    return [expr {($kind eq "bd_pin" && $direction eq "O") || ($kind eq "bd_port" && $direction eq "I")}]
}

proc ::ipiasm::feasibility::connected {sink source} {
    # Hierarchy-aware connectivity is stronger than equal net names/frequencies.
    set target [endpoint $sink]
    if {[is_driver $target]} {error "not an input endpoint: $sink"}
    if {![is_driver [endpoint $source]]} {error "not a source endpoint: $source"}
    set drivers {}
    foreach peer [find_bd_objs -relation connected_to -thru_hier $target] {
        if {[is_driver $peer]} {lappend drivers [string trimleft $peer /]}
    }
    if {[lsort -unique $drivers] ne [list [string trimleft $source /]]} {
        error "wrong or missing driver for $sink (expected $source; got $drivers)"
    }
}

proc ::ipiasm::feasibility::undriven {sink} {
    set target [endpoint $sink]
    if {[is_driver $target]} {error "not an input endpoint: $sink"}
    set drivers {}
    foreach peer [find_bd_objs -relation connected_to -thru_hier $target] {
        if {[is_driver $peer]} {lappend drivers [string trimleft $peer /]}
    }
    if {[llength $drivers]} {error "$sink is driven by [lsort -unique $drivers], expected no driver"}
}

proc ::ipiasm::feasibility::uint {value} {
    if {![string is entier -strict $value] || $value < 0 || $value >= 18446744073709551616} {
        error "FEASIBILITY_FAIL:expected unsigned 64-bit integer, got '$value'"
    }
    return [expr {$value + 0}]
}

proc ::ipiasm::feasibility::property {object name} {
    if {$name ni [_remember [list published $object] [list list_property $object]]} {
        error "FEASIBILITY_FAIL:property $name is not published on $object"
    }
    set value [_remember [list value $object $name] [list get_property $name $object]]
    if {$value eq ""} {error "FEASIBILITY_FAIL:empty property $name on $object"}
    return $value
}

# CONFIG.FREQ_HZ of a pin in whole hertz. Vivado prints a frequency it holds as a double in %g
# form (2026.2, lpddrmc pll_clkout0 at 266.525 MHz: 2.66525e+08), which uint refuses; that
# reading is what Vivado propagates, so it is rounded, not rejected.
proc ::ipiasm::feasibility::freq_hz {object} {
    set value [property $object CONFIG.FREQ_HZ]
    if {[string is entier -strict $value]} {return [uint $value]}
    if {[string is double -strict $value] && $value >= 0 && $value < 1e19} {
        return [uint [expr {round($value)}]]
    }
    error "FEASIBILITY_FAIL:expected a frequency in Hz on $object, got '$value'"
}

proc ::ipiasm::feasibility::scalar {type value} {
    switch -- $type {
        integer {return [uint $value]}
        boolean {
            if {![string is boolean -strict $value]} {error "FEASIBILITY_FAIL:invalid boolean"}
            return [expr {$value ? 1 : 0}]
        }
        string {
            if {[string trim $value] eq ""} {error "FEASIBILITY_FAIL:empty string"}
            return $value
        }
        default {error "FEASIBILITY_FAIL:unsupported scalar type $type"}
    }
}

# Tool, build, part, board part and SHA-256 of the spec/board/IP source files; the identity clock contracts are bound to.
proc ::ipiasm::feasibility_fingerprint {sources} {
    foreach required {spec board ip} {
        if {![dict exists $sources $required] || ![llength [dict get $sources $required]]} {
            error "FEASIBILITY_FAIL:missing fingerprint sources for $required"
        }
    }
    set result [dict create tool [version -short] build [version] part [get_property PART [current_project]] \
        board_part [get_property BOARD_PART [current_project]]]
    dict for {role paths} $sources {
        foreach path $paths {
            if {[file pathtype $path] ne "absolute" || ![file isfile $path]} {
                error "FEASIBILITY_FAIL:unreadable absolute fingerprint source $path"
            }
        }
        if {[llength [info commands ::ipiasm::_file_sha256s]]} {
            set shas [ipiasm::_file_sha256s $paths]
        } else {
            set shas [lmap path $paths {ipiasm::_file_sha256 $path}]
        }
        set digests [lmap path $paths sha $shas {list [file normalize $path] $sha}]
        dict set result sources $role $digests
    }
    return $result
}

# Check scratch-proven feasibility entries owned by one phase against the live design and matching fingerprints.
proc ::ipiasm::check_feasibility {checks phase sources expected_fingerprint} {
    set code [catch {ipiasm::feasibility::with_memo {
        if {$phase ni {scratch cells clocks connect qos address validate}} {error "invalid phase $phase"}
        if {[ipiasm::context_identity] eq {}} {error "open project and BD required"}
        if {[ipiasm::feasibility_fingerprint $sources] ne $expected_fingerprint} {error "stale fingerprints; repeat affected scratch probes"}
        if {![llength $checks]} {error "empty checks are not acceptance"}
        set count 0
        set ids {}
        foreach check $checks {
            foreach field {id kind subject expected source stage} {
                if {![dict exists $check $field] || [dict get $check $field] eq ""} {error "missing check field $field"}
            }
            set id [dict get $check id]
            if {$id in $ids} {error "duplicate check $id"}
            lappend ids $id
            set owner [dict get $check stage]
            if {$owner ni {cells clocks connect qos address validate}} {error "$id invalid delivery stage"}
            if {$phase ne "scratch" && $owner ne $phase} {continue}
            set subject [dict get $check subject]
            if {[regexp {[*?\[\]]} $subject]} {error "$id requires exact subject"}
            set wanted [dict get $check expected]
            set kind [dict get $check kind]
            set getter [dict get {config get_bd_cells pin_width get_bd_pins interface get_bd_intf_pins aperture_capacity get_bd_addr_segs clock_frequency get_bd_pins} $kind]
            set objects [$getter -quiet $subject]
            if {[llength $objects] != 1} {error "$id missing or ambiguous subject $subject"}
            foreach object $objects {
                switch -- $kind {
                    clock_frequency {
                        # CONFIG request values are not achieved clocks. Check the
                        # real driver and propagated sink metadata after validation.
                        # This is a BD model check, not a measured/routed frequency.
                        if {$owner ni {clocks connect qos address validate}} {error "$id clock readback belongs after clock connection"}
                        foreach field {driver input reference reference_hz tolerance_hz} {
                            if {![dict exists $check $field]} {error "$id missing clock field $field"}
                        }
                        set wanted [ipiasm::feasibility::uint $wanted]
                        set tolerance [ipiasm::feasibility::uint [dict get $check tolerance_hz]]
                        if {$wanted == 0} {error "$id expected frequency must be positive"}
                        if {$tolerance >= $wanted} {error "$id tolerance permits zero frequency"}
                        foreach {sink source expected_hz allowed_delta} [list \
                            [dict get $check input] [dict get $check reference] [dict get $check reference_hz] 0 \
                            $subject [dict get $check driver] $wanted $tolerance] {
                            foreach path [list $sink $source] {
                                set pin [ipiasm::feasibility::endpoint $path]
                                if {[ipiasm::feasibility::property $pin TYPE] ne "clk"} {error "$id not a clock: $path"}
                            }
                            set sink_pin [ipiasm::feasibility::endpoint $sink]
                            set source_pin [ipiasm::feasibility::endpoint $source]
                            if {![ipiasm::feasibility::is_driver $source_pin]} {error "$id clock source is not an output"}
                            if {$sink ne $source} {
                                ipiasm::feasibility::connected $sink $source
                            }
                            set expected_hz [ipiasm::feasibility::uint $expected_hz]
                            if {$expected_hz == 0} {error "$id reference frequency must be positive"}
                            set actual [ipiasm::feasibility::freq_hz $source_pin]
                            if {abs($actual - $expected_hz) > $allowed_delta} {error "$id achieved frequency mismatch: $source=$actual expected=$expected_hz tolerance=$allowed_delta"}
                            set sink_hz [ipiasm::feasibility::freq_hz $sink_pin]
                            if {$sink_hz != $actual} {error "$id stale sink frequency: $sink=$sink_hz driver=$actual"}
                        }
                    }
                    config {
                        set actual [ipiasm::feasibility::property $object [dict get $check property]]
                        set type [dict get $check type]
                        if {[ipiasm::feasibility::scalar $type $actual] ne [ipiasm::feasibility::scalar $type $wanted]} {error "$id value mismatch: $actual"}
                    }
                    pin_width {
                        if {$owner ni {connect qos address validate}} {error "$id derived width belongs after connection"}
                        set actual [expr {abs([ipiasm::feasibility::property $object LEFT] - [ipiasm::feasibility::property $object RIGHT]) + 1}]
                        if {$actual != [ipiasm::feasibility::uint $wanted]} {error "$id width mismatch: $actual"}
                        if {![llength [get_bd_nets -quiet -of_objects $object]]} {error "$id pin is unconnected"}
                    }
                    interface {
                        foreach property {VLNV MODE} {
                            if {[ipiasm::feasibility::property $object $property] ne [dict get $wanted $property]} {error "$id interface $property mismatch"}
                        }
                    }
                    aperture_capacity {
                        set selector [dict get $check selector]
                        set cells [get_bd_cells -quiet [dict get $selector cell]]
                        if {[llength $cells] != 1} {error "$id missing selector cell"}
                        foreach cell $cells {
                            if {[ipiasm::feasibility::property $cell [dict get $selector property]] ne [dict get $selector value]} {error "$id selector mismatch"}
                        }
                        set actual [ipiasm::feasibility::uint [ipiasm::feasibility::property $object RANGE]]
                        if {$actual < [ipiasm::feasibility::uint $wanted] || $wanted <= 0} {error "$id aperture too small: $actual"}
                    }
                }
            }
            incr count
        }
        if {!$count} {error "no checks owned by phase $phase"}
    }} detail]
    if {$code} {return "FEASIBILITY_FAIL:$detail"}
    return "FEASIBILITY:OK checks=$count phase=$phase"
}