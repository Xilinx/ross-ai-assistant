# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Per-design requirements survive stage retries, but not begin_design transitions.
# Assumptions: callers cover every required sink, run native propagation first,
# and supply independent source/IP/preset fingerprints and reset release intent.
# This gate proves declared BD wiring/metadata, not analog lock, CDC or timing.
namespace eval ::ipiasm {variable clock_contracts {}}

# One repair turn costs a model round trip plus a Vivado source; reporting one
# defect per attempt turns a malformed plan into a serial bisect. Contract gates
# collect every defect in a pass and raise them together.
proc ipiasm::_defects {defects} {
    set lines {}
    set index 0
    foreach defect $defects {lappend lines "[incr index]) $defect"}
    return "[llength $defects] defect(s): [join $lines {; }]"
}

# Read one property without throwing; returns {} when the object or property is
# absent, so a pass can record that as a defect instead of aborting the pass.
proc ipiasm::_probe {command args} {
    if {[catch {uplevel 1 [list $command {*}$args]} value]} {return {}}
    return $value
}

proc ipiasm::_clock_mhz {hz} {
    set hz [ipiasm::feasibility::uint $hz]
    if {$hz == 0} {error "Clock frequency must be positive"}
    return [format %d.%06d [expr {$hz / 1000000}] [expr {$hz % 1000000}]]
}

proc ipiasm::_configure_clock_candidate {cell config input source} {
    # apply_dict owns its configuration transaction. Never nest attempt_call:
    # the persistent ledger uses an exclusive lock, not a reentrant lock.
    set result [ipcfg::apply_dict $cell $config]
    if {![string match SUCCESS:* $result]} {error $result}
    if {[catch {ipiasm::feasibility::connected $cell/$input $source}]} {
        ipcfg::attempt_call $cell connect_reference {
            set pin [get_bd_pins $cell/$input]
            foreach net [get_bd_nets -quiet -of_objects $pin] {disconnect_bd_net $net $pin}
            connect_bd_net [ipiasm::feasibility::endpoint $source] $pin
        }
    }
    return SUCCESS:clock_candidate_configured
}

# Scratch only: instantiate candidate clocking-wizard configurations from a plan_clocks.py candidate plan.
proc ipiasm::realize_clock_candidates {plan source_pins evidence_dir {prefix clock_trial}} {
    # Scratch-only vector-wizard adapter. It has no board, frequency or grouping
    # constants. Unknown schemas fail before writes; indexed wizards are not yet
    # supported. Callers must reserve a fresh prefix, not overwrite live cells.
    set started [clock milliseconds]
    set code [catch {
        if {[dict get $plan status] ne "CANDIDATES_ONLY"} {error "candidate plan required"}
        if {![regexp {^[A-Za-z][A-Za-z0-9_]*$} $prefix]} {error "invalid cell prefix"}
        if {[file pathtype $evidence_dir] ne "absolute" || ![file isdirectory $evidence_dir]} {error "existing absolute evidence directory required"}
        if {[llength [get_bd_cells -quiet ${prefix}_*]]} {error "candidate prefix is already owned"}
        set candidates [dict get $plan candidates]
        if {![llength $candidates] || [llength $candidates] > 8} {error "one to eight candidates required (creation and reference wiring also consume attempts)"}
        set slots {}
        set attempts {}
        set trial 0
        set success {}
        foreach candidate $candidates {
            incr trial
            set slot 0
            set outputs {}
            foreach generator [dict get $candidate generators] {
                set cell ${prefix}_$slot
                set vlnv [dict get $generator vlnv]
                if {![dict exists $slots $cell]} {
                    set receipt [ipcfg::capture [file join $evidence_dir create_$slot.tcldict] \
                        [list ipcfg::create_cell $vlnv $cell] {SUCCESS:}]
                    if {[dict get $receipt status] ne "OK"} {error "generator creation failed: $receipt"}
                    dict set slots $cell $vlnv
                } elseif {[dict get $slots $cell] ne $vlnv} {error "candidate changes slot IP type"}
                set object [get_bd_cells $cell]
                foreach property {CONFIG.CLKOUT_PORT CONFIG.CLKOUT_USED CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY CONFIG.PRIM_IN_FREQ} {
                    if {$property ni [list_property $object]} {error "unsupported wizard schema: $property"}
                }
                set ports [split [get_property CONFIG.CLKOUT_PORT $object] ,]
                set rates [split [get_property CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY $object] ,]
                set used [lrepeat [llength $ports] false]
                set wanted [dict get $generator outputs]
                if {[llength $rates] != [llength $ports] || [llength $wanted] > [llength $ports]} {error "invalid discovered output capacity"}
                set output_index 0
                foreach output $wanted {
                    set port clk_out[expr {$output_index + 1}]
                    lset ports $output_index $port
                    lset rates $output_index [ipiasm::_clock_mhz [dict get $output requested_hz]]
                    lset used $output_index true
                    lappend outputs [dict merge $output [dict create pin $cell/$port source [dict get $generator source]]]
                    incr output_index
                }
                set config [dict create CONFIG.CLKOUT_PORT [join $ports ,] CONFIG.CLKOUT_USED [join $used ,] \
                    CONFIG.CLKOUT_REQUESTED_OUT_FREQUENCY [join $rates ,] \
                    CONFIG.PRIM_IN_FREQ [ipiasm::_clock_mhz [dict get $generator requested_input_hz]]]
                set inputs {}
                foreach pin [get_bd_pins -quiet $cell/*] {
                    if {[get_property TYPE $pin] eq "clk" && [get_property DIR $pin] eq "I"} {lappend inputs [file tail $pin]}
                }
                if {[llength $inputs] != 1} {error "wizard must expose one unambiguous reference input"}
                set source [dict get $source_pins [dict get $generator source]]
                set observed [ipiasm::feasibility::freq_hz [ipiasm::feasibility::endpoint $source]]
                if {$observed != [dict get $generator input_hz]} {error "reference frequency changed"}
                set receipt [ipcfg::capture [file join $evidence_dir candidate_${trial}_slot_$slot.tcldict] \
                    [list ipiasm::_configure_clock_candidate $cell $config [lindex $inputs 0] $source] {SUCCESS:}]
                if {[dict get $receipt status] ne "OK"} {error "candidate configuration failed: $receipt"}
                incr slot
            }
            # Propagation is a separate evidence boundary: validate may clear
            # older native messages. Severity counts must not increase.
            set before [ipcfg::_message_counts]
            set validation_code [catch {validate_bd_design} validation_result validation_options]
            set after [ipcfg::_message_counts]
            ipcfg::write_evidence [file join $evidence_dir validate_$trial.tcldict] \
                [dict create code $validation_code result $validation_result options $validation_options before $before after $after native_log $::ipcfg::native_log_path]
            if {$validation_code} {error "candidate validation failed: $validation_result"}
            foreach severity {WARNING {CRITICAL WARNING} ERROR} {
                if {[dict get $after $severity] != [dict get $before $severity]} {error "candidate validation emitted $severity"}
            }
            set observations {}
            set matched 1
            foreach output $outputs {
                set actual [ipiasm::feasibility::freq_hz [get_bd_pins [dict get $output pin]]]
                set matches [expr {abs($actual - [dict get $output hz]) <= [dict get $output tolerance_hz]}]
                if {!$matches} {set matched 0}
                lappend observations [dict merge $output [dict create actual_hz $actual matches $matches]]
            }
            lappend attempts [dict create candidate [dict get $candidate id] matched $matched observations $observations]
            ipcfg::write_evidence [file join $evidence_dir observed_$trial.tcldict] [lindex $attempts end]
            if {$matched} {
                set success [dict create status PASS candidate [dict get $candidate id] generators [dict get $candidate generators] observations $observations]
                break
            }
        }
        if {$success eq {}} {error "no candidate met all exact requirements; bounded search is not a silicon impossibility proof"}
        dict set success attempts $attempts
        dict set success elapsed_ms [expr {[clock milliseconds] - $started}]
        ipcfg::write_evidence [file join $evidence_dir realization.tcldict] $success
    } detail options]
    if {$code} {return "CLOCK_REALIZE_FAIL:$detail"}
    return $success
}

# Register the design's required clock frequencies and reset domains with their scratch fingerprint; immutable once set.
proc ipiasm::set_clock_contracts {checks resets sources fingerprint} {
    variable clock_contracts
    set code [catch {
        if {[ipiasm::context_identity] eq {}} {error "open project and BD required"}
        if {![llength $checks]} {error "empty clock contracts are not acceptance"}
        set defects {}
        set ids {}
        foreach check $checks {
            set id [expr {[dict exists $check id] ? [dict get $check id] : {<unnamed>}}]
            set missing {}
            foreach field {id kind subject expected source stage driver input reference reference_hz tolerance_hz} {
                if {![dict exists $check $field] || [dict get $check $field] eq ""} {lappend missing $field}
            }
            if {[llength $missing]} {
                lappend defects "clock $id: missing clock field [join $missing {, }]"
                continue
            }
            if {[dict get $check kind] ne "clock_frequency"} {lappend defects "clock $id: expected clock_frequency check"}
            if {[dict get $check stage] ne "clocks"} {lappend defects "clock $id: clock contract must belong to clocks"}
            if {$id in $ids} {lappend defects "clock $id: duplicate clock ID"}
            lappend ids $id
            set numeric 1
            foreach field {expected reference_hz tolerance_hz} {
                if {[catch {ipiasm::feasibility::uint [dict get $check $field]}]} {
                    lappend defects "clock $id: $field is not a non-negative integer"
                    set numeric 0
                }
            }
            if {$numeric && ([dict get $check expected] <= [dict get $check tolerance_hz]
                || [dict get $check reference_hz] == 0)} {
                lappend defects "clock $id: clock contract permits zero frequency"
            }
        }
        foreach reset $resets {
            set cell [expr {[dict exists $reset cell] ? [dict get $reset cell] : {<unnamed>}}]
            set missing {}
            foreach field {cell clock source source_polarity lock sinks} {
                if {![dict exists $reset $field]} {
                    lappend missing $field
                # An empty lock declares a clock with no lock output (a processing-system
                # reference clock, say); every other field needs a value.
                } elseif {$field ne "lock" && [dict get $reset $field] eq ""} {
                    lappend missing $field
                }
            }
            if {[llength $missing]} {
                lappend defects "reset $cell: missing reset field [join $missing {, }]"
                continue
            }
            if {[dict get $reset source_polarity] ni {ACTIVE_LOW ACTIVE_HIGH}} {
                lappend defects "reset $cell: invalid reset polarity [dict get $reset source_polarity]"
            }
            # A contract's reference is checked exactly like its driver (exact frequency,
            # tolerance 0), so a reset clocked by a reference clock is covered too.
            set known 0
            foreach check $checks {
                foreach field {driver reference} {
                    if {[dict exists $check $field] && [dict get $reset clock] eq [dict get $check $field]} {set known 1}
                }
            }
            if {!$known} {
                lappend defects "reset $cell: reset clock has no frequency contract ([dict get $reset clock])"
            }
            if {[catch {llength [dict get $reset sinks]}]} {
                lappend defects "reset $cell: sinks is not a well-formed list"
                continue
            }
            foreach sink [dict get $reset sinks] {
                if {[catch {llength $sink} width] || $width != 2
                    || [lindex $sink 1] ni {peripheral_aresetn peripheral_reset interconnect_aresetn bus_struct_reset}} {
                    lappend defects "reset $cell: reset sink must be {pin proc_sys_reset_output}, got {$sink}"
                }
            }
        }
        if {[catch {ipiasm::feasibility_fingerprint $sources} observed]} {
            lappend defects "fingerprint could not be computed: $observed"
        } elseif {$observed ne $fingerprint} {
            lappend defects "stale fingerprints"
        }
        if {[llength $defects]} {error [ipiasm::_defects $defects]}
    } detail]
    if {$code} {return "CLOCK_CONTRACT_FAIL:$detail"}
    set candidate [dict create checks $checks resets $resets sources $sources fingerprint $fingerprint context [ipiasm::context_identity]]
    # A retry must repair hardware, not weaken the requirements to fit it. Measured fields
    # (driver/input/reference, reference_hz, sources, fingerprint) may be corrected: a first
    # registration that guessed a reference wrong must not lock the design for good.
    set updated ""
    if {$clock_contracts ne {} && $clock_contracts ne $candidate} {
        set weakened [ipiasm::_clock_requirements_weakened $clock_contracts $candidate]
        if {$weakened ne ""} {
            return "CLOCK_CONTRACT_FAIL:contracts already registered; repair implementation, not requirements ($weakened)"
        }
        set updated " updated=measured_fields"
    }
    set clock_contracts $candidate
    # Kept across Vivado processes when ipcfg is loaded (it is, except in unit fixtures).
    if {[llength [info commands ::ipcfg::ledger_record]]} {ipcfg::ledger_record clock_contracts $clock_contracts}
    return "CLOCK_CONTRACTS:SET clocks=[llength $checks] resets=[llength $resets]$updated"
}

# Scratch: record the sources and feasibility fingerprint clock contracts are bound to, so the
# plan carries the scratch baseline (references/clock-planning.md) instead of the model finding
# board, preset and IP definition files by hand. ipiasm::scratch_bind calls it when the
# DesignSpec has clock_reset contracts; rerun it after editing the DesignSpec, which it hashes.
#   out    absolute plan folder; writes <out>/clock_fingerprint.tcldict {sources .. fingerprint ..}
#   spec   absolute design_spec.json
#   vlnvs  IP whose definition files to hash (clock generators, reset synchronizers); empty:
#          every clock wizard and proc_sys_reset in the open design
# Returns CLOCK_FINGERPRINT:OK file=<path> board=<n> ip=<n> or CLOCK_FINGERPRINT_FAIL:<reason>.
proc ipiasm::clock_fingerprint {out spec {vlnvs {}}} {
    if {[file pathtype $out] ne "absolute" || [file pathtype $spec] ne "absolute"} {
        return "CLOCK_FINGERPRINT_FAIL:absolute out and spec paths required"
    }
    if {![file isfile $spec]} {return "CLOCK_FINGERPRINT_FAIL:no DesignSpec $spec"}
    if {[catch {current_project} project] || $project eq ""} {
        return "CLOCK_FINGERPRINT_FAIL:open the scratch project (board and part set) first"
    }
    set board_part [get_property BOARD_PART [current_project]]
    if {$board_part eq ""} {
        return "CLOCK_FINGERPRINT_FAIL:no board part; a no-board flow hashes its own policy file as the\
            board source (references/clock-planning.md)"
    }
    set board_file [get_property -quiet FILE_NAME [lindex [get_board_parts -quiet $board_part] 0]]
    if {$board_file eq "" || ![file isfile $board_file]} {
        return "CLOCK_FINGERPRINT_FAIL:no board file for $board_part"
    }
    set board [list [file normalize $board_file]]
    # The preset file beside board.xml holds the defaults board automation applies.
    set preset [file join [file dirname $board_file] preset.xml]
    if {[file isfile $preset]} {lappend board [file normalize $preset]}
    if {![llength $vlnvs]} {
        foreach cell [get_bd_cells -quiet -hierarchical -filter {VLNV =~ "*:clk_wiz*" || VLNV =~ "*:clkx5_wiz:*"
                || VLNV =~ "*:proc_sys_reset:*"}] {
            lappend vlnvs [get_property VLNV $cell]
        }
    }
    set ip {}
    foreach vlnv [lsort -unique $vlnvs] {
        set parts [split $vlnv :]
        set pattern [expr {[llength $parts] >= 4 ? $vlnv : "[join [lrange $parts 0 2] :]:*"}]
        set definition [lindex [lsort -dictionary [get_ipdefs -quiet -all $pattern]] end]
        set xml [expr {$definition eq "" ? "" : [get_property -quiet XML_FILE_NAME $definition]}]
        if {$xml eq "" || ![file isfile $xml]} {return "CLOCK_FINGERPRINT_FAIL:no IP definition file for $vlnv"}
        lappend ip [file normalize $xml]
    }
    set ip [lsort -unique $ip]
    if {![llength $ip]} {
        return "CLOCK_FINGERPRINT_FAIL:no clock generator or reset synchronizer to hash; pass their VLNVs"
    }
    set sources [dict create spec [list [file normalize $spec]] board $board ip $ip]
    if {[catch {ipiasm::feasibility_fingerprint $sources} fingerprint]} {return "CLOCK_FINGERPRINT_FAIL:$fingerprint"}
    set path [file join $out clock_fingerprint.tcldict]
    if {[catch {
        set channel [open $path w]
        fconfigure $channel -encoding utf-8 -translation lf
        puts -nonewline $channel [dict create schema_version 1 sources $sources fingerprint $fingerprint]
        close $channel
    } detail]} {return "CLOCK_FINGERPRINT_FAIL:$detail"}
    return "CLOCK_FINGERPRINT:OK file=$path board=[llength $board] ip=[llength $ip]"
}

# The one driver of a clock or reset input, as an absolute path; errors when it has none or several.
proc ipiasm::_input_driver {path} {
    set pin [ipiasm::feasibility::endpoint $path]
    set drivers {}
    foreach peer [find_bd_objs -relation connected_to -thru_hier $pin] {
        if {[ipiasm::feasibility::is_driver $peer]} {lappend drivers /[string trimleft $peer /]}
    }
    set drivers [lsort -unique $drivers]
    if {[llength $drivers] != 1} {error "$path has [expr {[llength $drivers] ? "drivers $drivers" : "no driver"}]"}
    return [lindex $drivers 0]
}

# Plan operation (clocks stage, after the clock and reset wiring): register the DesignSpec's
# clock requirements, reading only the measured fields from the live design, then call
# ipiasm::set_clock_contracts. Each check names id and expected (Hz) and either subject (a cell
# clock pin) or cell (every driven clock input of that cell becomes one check, id <id>.<pin>);
# source defaults to the DesignSpec's clock_reset and tolerance_hz to 0. Not given: driver is the
# subject's driver; input/reference/reference_hz are a clock wizard's single clock input, its
# driver and that driver's FREQ_HZ, else the driver itself at the expected rate (a direct clock,
# references/clock-planning.md). Reset domains: each proc_sys_reset whose slowest_sync_clk is a
# contract driver, with the sinks whose polarity matches its outputs.
#   checks            list of check dicts as above
#   fingerprint_file  <out>/clock_fingerprint.tcldict from ipiasm::clock_fingerprint (scratch)
# Returns set_clock_contracts' CLOCK_CONTRACTS:SET line, or CLOCK_CONTRACT_FAIL:<every defect>.
proc ipiasm::register_clock_contracts {checks fingerprint_file} {
    if {[ipiasm::context_identity] eq {}} {return "CLOCK_CONTRACT_FAIL:open project and BD required"}
    if {[catch {
        set channel [open $fingerprint_file r]
        fconfigure $channel -encoding utf-8 -translation lf
        set record [read $channel]
        close $channel
        set sources [dict get $record sources]
        set fingerprint [dict get $record fingerprint]
    } detail]} {
        return "CLOCK_CONTRACT_FAIL:cannot read $fingerprint_file ($detail); run ipiasm::clock_fingerprint <out> <spec> in the scratch project"
    }
    set defects {}
    set full {}
    foreach check $checks {
        if {[catch {dict size $check}] || ![dict exists $check id] || ![dict exists $check expected]} {
            lappend defects "check {$check}: needs id, expected and subject or cell"
            continue
        }
        set id [dict get $check id]
        set subjects {}
        if {[dict exists $check subject]} {
            lappend subjects [list $id [dict get $check subject]]
        } elseif {[dict exists $check cell]} {
            set cell [get_bd_cells -quiet [dict get $check cell]]
            if {[llength $cell] != 1} {
                lappend defects "clock $id: no cell [dict get $check cell]"
                continue
            }
            # Undriven clock inputs are check_undriven_clocks' finding, not a frequency contract.
            foreach pin [lsort -dictionary [get_bd_pins -quiet -of_objects $cell -filter {TYPE == clk && DIR == I}]] {
                if {![catch {ipiasm::_input_driver $pin}]} {lappend subjects [list $id.[file tail $pin] /[string trimleft $pin /]]}
            }
            if {![llength $subjects]} {
                lappend defects "clock $id: [dict get $check cell] has no driven clock input; wire its clocks before this operation"
                continue
            }
        } else {
            lappend defects "clock $id: needs subject or cell"
            continue
        }
        foreach pair $subjects {
            lassign $pair sid subject
            set entry [dict merge [dict create kind clock_frequency stage clocks tolerance_hz 0 \
                source accepted-spec#/functional_contracts/clock_reset] $check [dict create id $sid subject $subject]]
            dict unset entry cell
            if {![dict exists $entry driver]} {
                if {[catch {ipiasm::_input_driver $subject} driver]} {
                    lappend defects "clock $sid: $driver"
                    continue
                }
                dict set entry driver $driver
            }
            if {![dict exists $entry reference]} {
                set driver [dict get $entry driver]
                set reference [list $driver $driver [dict get $entry expected]]
                set generator [get_bd_cells -quiet [file dirname $driver]]
                if {$generator ne "" && [regexp {:(clk_wiz|clkx5_wiz|clk_wizard):} [get_property VLNV $generator]]} {
                    set inputs {}
                    foreach pin [get_bd_pins -quiet -of_objects $generator -filter {TYPE == clk && DIR == I}] {
                        if {[string match clk_in* [file tail $pin]] && ![catch {ipiasm::_input_driver $pin} source]} {
                            lappend inputs [list /[string trimleft $pin /] $source]
                        }
                    }
                    # A differential board clock arrives on an interface, not a clk pin: direct form.
                    if {[llength $inputs] == 1} {
                        lassign [lindex $inputs 0] input source
                        set hz [ipiasm::_probe ipiasm::feasibility::freq_hz [ipiasm::feasibility::endpoint $source]]
                        if {[string is entier -strict $hz] && $hz > 0} {set reference [list $input $source $hz]}
                    }
                }
                lassign $reference input source hz
                dict set entry input $input
                dict set entry reference $source
                dict set entry reference_hz $hz
            }
            lappend full $entry
        }
    }
    set clocks {}
    foreach entry $full {lappend clocks [dict get $entry driver] [dict get $entry reference]}
    set resets {}
    foreach cell [lsort [get_bd_cells -quiet -hierarchical -filter {VLNV =~ "*:proc_sys_reset:*"}]] {
        set cell /[string trimleft $cell /]
        if {[catch {ipiasm::_input_driver $cell/slowest_sync_clk} clock] || $clock ni $clocks} {continue}
        if {[catch {ipiasm::_input_driver $cell/ext_reset_in} source]} {
            lappend defects "reset $cell: ext_reset_in is undriven"
            continue
        }
        set polarity [ipiasm::_probe ipiasm::feasibility::property [ipiasm::feasibility::endpoint $source] CONFIG.POLARITY]
        if {$polarity ni {ACTIVE_LOW ACTIVE_HIGH}} {
            lappend defects "reset $cell: source $source has no ACTIVE_LOW/ACTIVE_HIGH POLARITY"
            continue
        }
        set lock [expr {[catch {ipiasm::_input_driver $cell/dcm_locked} driven] ? "" : $driven}]
        set sinks {}
        foreach output {peripheral_aresetn interconnect_aresetn peripheral_reset bus_struct_reset} {
            set pin [get_bd_pins -quiet $cell/$output]
            if {$pin eq ""} {continue}
            set want [expr {[string match *aresetn $output] ? "ACTIVE_LOW" : "ACTIVE_HIGH"}]
            foreach peer [find_bd_objs -relation connected_to -thru_hier $pin] {
                if {[ipiasm::feasibility::is_driver $peer]} {continue}
                # A sink without the output's polarity would fail check_clock_contracts as a
                # contract, yet is legitimately wired (an inverter input, say): leave it out.
                if {[ipiasm::_probe ipiasm::feasibility::property $peer CONFIG.POLARITY] eq $want} {
                    lappend sinks [list /[string trimleft $peer /] $output]
                }
            }
        }
        if {[llength $sinks]} {
            lappend resets [dict create cell $cell clock $clock source $source source_polarity $polarity lock $lock sinks $sinks]
        }
    }
    if {[llength $defects]} {return "CLOCK_CONTRACT_FAIL:[ipiasm::_defects $defects]"}
    set result [ipiasm::set_clock_contracts $full $resets $sources $fingerprint]
    if {[string match "*stale fingerprints*" $result]} {
        append result " -> the DesignSpec or a hashed file changed since ipiasm::clock_fingerprint ran: rerun it in the scratch project"
    }
    return $result
}

# "" when the new contracts keep every registered requirement, else what they would weaken:
# the same clock ids, subjects, kinds, expected rates, sources and stages, no looser
# tolerance, and the same reset domains.
proc ipiasm::_clock_requirements_weakened {old new} {
    set before [dict create]
    foreach check [dict get $old checks] {dict set before [dict get $check id] $check}
    set after [dict create]
    foreach check [dict get $new checks] {dict set after [dict get $check id] $check}
    if {[lsort [dict keys $before]] ne [lsort [dict keys $after]]} {
        return "clock ids differ: [lsort [dict keys $before]] -> [lsort [dict keys $after]]"
    }
    dict for {id check} $before {
        set other [dict get $after $id]
        foreach field {kind subject expected source stage} {
            if {[dict get $check $field] ne [dict get $other $field]} {
                return "clock $id: $field [dict get $check $field] -> [dict get $other $field]"
            }
        }
        if {[dict get $other tolerance_hz] > [dict get $check tolerance_hz]} {
            return "clock $id: tolerance_hz [dict get $check tolerance_hz] -> [dict get $other tolerance_hz]"
        }
    }
    if {[dict get $old resets] ne [dict get $new resets]} {return "reset domains differ"}
    if {[dict get $old context] ne [dict get $new context]} {return "design context differs"}
    return ""
}

# Re-verify every registered clock frequency and reset domain on the live design; CLOCK_CONTRACTS:OK or CLOCK_CONTRACT_FAIL.
proc ipiasm::check_clock_contracts {} {
    variable clock_contracts
    # Compatibility for existing plans. New clock-planning flows must register
    # contracts and require OK explicitly; NA is not a clock acceptance result.
    if {$clock_contracts eq {}} {return "CLOCK_CONTRACTS:NA not registered"}
    set code [catch {ipiasm::feasibility::with_memo {
        if {[dict get $clock_contracts context] ne [ipiasm::context_identity]} {error "design context changed"}
        # No cached PASS: source propagation or later automation can change rates.
        set defects {}
        set result [ipiasm::check_feasibility [dict get $clock_contracts checks] clocks \
            [dict get $clock_contracts sources] [dict get $clock_contracts fingerprint]]
        if {![string match FEASIBILITY:OK* $result]} {lappend defects $result}
        set cells {}
        set sinks {}
        foreach reset [dict get $clock_contracts resets] {
            set cell [dict get $reset cell]
            if {$cell in $cells} {
                lappend defects "reset synchronizer reused by different contracts: $cell"
                continue
            }
            lappend cells $cell
            if {[regexp {[*?\[\]]} $cell] || [llength [get_bd_cells -quiet $cell]] != 1} {
                lappend defects "missing exact reset cell $cell"
                continue
            }
            if {![string match xilinx.com:ip:proc_sys_reset:* [get_property VLNV [get_bd_cells $cell]]]} {
                lappend defects "unsupported reset synchronizer $cell"
                continue
            }
            set source [dict get $reset source]
            set source_pin [ipiasm::_probe ipiasm::feasibility::endpoint $source]
            if {$source_pin eq ""} {
                lappend defects "reset $cell: no such reset source $source"
            } else {
                set type [ipiasm::_probe ipiasm::feasibility::property $source_pin TYPE]
                set polarity [ipiasm::_probe ipiasm::feasibility::property $source_pin CONFIG.POLARITY]
                if {$type ne "rst"} {lappend defects "reset $cell: source $source is TYPE $type, not rst"}
                if {$polarity ne [dict get $reset source_polarity]} {
                    lappend defects "reset $cell: source polarity mismatch:\
                        $source is $polarity, contract says [dict get $reset source_polarity]"
                }
            }
            # C_EXT_RESET_HIGH is propagated and read-only. Never fix it by write.
            set expected_high [expr {[dict get $reset source_polarity] eq "ACTIVE_HIGH"}]
            set observed_high [ipiasm::_probe ipiasm::feasibility::property [get_bd_cells $cell] CONFIG.C_EXT_RESET_HIGH]
            if {$observed_high ne "" && $observed_high != $expected_high} {
                lappend defects "reset $cell: reset input polarity has not propagated (C_EXT_RESET_HIGH=$observed_high, expected $expected_high)"
            }
            set checked [list $cell/slowest_sync_clk [dict get $reset clock] $cell/ext_reset_in $source]
            if {[dict get $reset lock] ne ""} {
                lappend checked $cell/dcm_locked [dict get $reset lock]
            } elseif {[catch {ipiasm::feasibility::undriven $cell/dcm_locked} detail]} {
                # No lock output: dcm_locked stays unconnected, which proc_sys_reset reads as locked.
                lappend defects "reset $cell: $detail"
            }
            foreach {sink driver} $checked {
                if {[catch {ipiasm::feasibility::connected $sink $driver} detail]} {
                    lappend defects "reset $cell: $detail"
                }
            }
            foreach sink [dict get $reset sinks] {
                lassign $sink target output
                if {$target in $sinks} {
                    lappend defects "reset sink has multiple owners: $target"
                    continue
                }
                lappend sinks $target
                if {[catch {ipiasm::feasibility::connected $target $cell/$output} detail]} {
                    lappend defects "reset $cell: $detail"
                }
                set want [expr {[string match *aresetn $output] ? "ACTIVE_LOW" : "ACTIVE_HIGH"}]
                set endpoint [ipiasm::_probe ipiasm::feasibility::endpoint $target]
                set got [expr {$endpoint eq "" ? {} : [ipiasm::_probe ipiasm::feasibility::property $endpoint CONFIG.POLARITY]}]
                if {$got ne $want} {
                    lappend defects "reset sink polarity mismatch: $target is $got, expected $want"
                }
            }
        }
        if {[llength $defects]} {error [ipiasm::_defects $defects]}
    }} detail]
    if {$code} {return "CLOCK_CONTRACT_FAIL:$detail"}
    return "CLOCK_CONTRACTS:OK clocks=[llength [dict get $clock_contracts checks]] resets=[llength $cells] modeled_hz_only=1"
}

# Read-only. Answer, before any cell is created or configured, the two questions
# a clock stage otherwise discovers one failed source at a time: what frequency
# does each driver actually report, and what polarity does each reset source
# actually carry. Pins that do not exist yet are PENDING, not defects - this is
# meant to be run against a partially built design.
proc ipiasm::preflight_clock_plan {checks resets} {
    if {[ipiasm::context_identity] eq {}} {return "CLOCK_PREFLIGHT_FAIL:open project and BD required"}
    set defects {}
    set pending 0
    set proven 0
    foreach check $checks {
        if {![dict exists $check driver] || ![dict exists $check expected]} {
            lappend defects "clock [ipiasm::_probe dict get $check id]: needs driver and expected to be pre-flighted"
            continue
        }
        set id [dict get $check driver]
        set pin [ipiasm::_probe ipiasm::feasibility::endpoint [dict get $check driver]]
        if {$pin eq ""} {incr pending; continue}
        set actual [ipiasm::_probe ipiasm::feasibility::freq_hz $pin]
        if {$actual eq ""} {incr pending; continue}
        set tolerance [expr {[dict exists $check tolerance_hz] ? [dict get $check tolerance_hz] : 0}]
        if {abs($actual - [dict get $check expected]) > $tolerance} {
            # The exact-match trap: a driver that reports 99999001 never satisfies
            # a contract written for 100000000, however the sink is configured.
            lappend defects "clock $id: driver reports ${actual}Hz, contract expects\
                [dict get $check expected]Hz +/-${tolerance} (delta [expr {$actual - [dict get $check expected]}])"
        } else {
            incr proven
        }
    }
    foreach reset $resets {
        if {![dict exists $reset source] || ![dict exists $reset source_polarity]} {
            lappend defects "reset [ipiasm::_probe dict get $reset cell]: needs source and source_polarity to be pre-flighted"
            continue
        }
        set cell [ipiasm::_probe dict get $reset cell]
        set pin [ipiasm::_probe ipiasm::feasibility::endpoint [dict get $reset source]]
        if {$pin eq ""} {incr pending; continue}
        set type [ipiasm::_probe ipiasm::feasibility::property $pin TYPE]
        set polarity [ipiasm::_probe ipiasm::feasibility::property $pin CONFIG.POLARITY]
        if {$type ne "rst"} {
            lappend defects "reset $cell: source [dict get $reset source] is TYPE $type, not rst"
        } elseif {$polarity ne [dict get $reset source_polarity]} {
            lappend defects "reset $cell: source carries $polarity, plan declares [dict get $reset source_polarity]\
                -- C_EXT_RESET_HIGH propagates from the source and cannot be written"
        } else {
            incr proven
        }
    }
    if {[llength $defects]} {return "CLOCK_PREFLIGHT_FAIL:[ipiasm::_defects $defects]"}
    return "CLOCK_PREFLIGHT:OK proven=$proven pending=$pending"
}

# Registered contracts live in an interpreter variable, so a new CLI turn with a
# new Vivado session loses them and a re-source trips the repair-the-hardware
# guard. Persist them next to the stage evidence and restore instead.
proc ipiasm::save_clock_contracts {path} {
    variable clock_contracts
    if {$clock_contracts eq {}} {return "CLOCK_CONTRACTS_SAVE_FAIL:nothing registered"}
    if {[file pathtype $path] ne "absolute"} {return "CLOCK_CONTRACTS_SAVE_FAIL:absolute path required"}
    if {[catch {
        set channel [open $path w]
        fconfigure $channel -encoding utf-8 -translation lf
        puts -nonewline $channel [dict create schema_version 1 data $clock_contracts]
        close $channel
    } detail]} {return "CLOCK_CONTRACTS_SAVE_FAIL:$detail"}
    return "CLOCK_CONTRACTS:SAVED [file tail $path] bytes=[file size $path]"
}

# Restore clock contracts saved by save_clock_contracts into this same design, without re-running the clock stage.
proc ipiasm::load_clock_contracts {path} {
    variable clock_contracts
    if {![file readable $path]} {return "CLOCK_CONTRACTS_LOAD_FAIL:cannot read $path"}
    if {[catch {
        set channel [open $path r]
        fconfigure $channel -encoding utf-8 -translation lf
        set record [read $channel]
        close $channel
        set restored [dict get $record data]
        foreach field {checks resets sources fingerprint context} {dict get $restored $field}
    } detail]} {return "CLOCK_CONTRACTS_LOAD_FAIL:$detail"}
    # Contracts describe one design. Restoring them into another is not a retry.
    if {[dict get $restored context] ne [ipiasm::context_identity]} {
        return "CLOCK_CONTRACTS_LOAD_FAIL:saved context is not the open design"
    }
    if {$clock_contracts ne {} && $clock_contracts ne $restored} {
        return "CLOCK_CONTRACTS_LOAD_FAIL:different contracts already registered"
    }
    set clock_contracts $restored
    # Kept across Vivado processes when ipcfg is loaded (it is, except in unit fixtures).
    if {[llength [info commands ::ipcfg::ledger_record]]} {ipcfg::ledger_record clock_contracts $clock_contracts}
    return "CLOCK_CONTRACTS:RESTORED clocks=[llength [dict get $restored checks]] resets=[llength [dict get $restored resets]]"
}

# --- not repeating a clock-contract pass nothing could have changed ---------
# Once the design has validated, ipiasm::checkpoint closes each stage with a full
# check_clock_contracts, and the next ipiasm::stage used to repeat it at once. The repeat is skipped only
# when the last passing check saw this same design: no BD- or project-mutating
# Vivado command has run since (an execution trace counts them whoever issued
# them - skill helper, plan, console line or sourced script), and the design
# context, object counts and registered contracts are unchanged.
# final_contract_gate and delivery_guard always run the full check.
namespace eval ipiasm {
    # A *_bd* command whose verb is one of these only reads the design. Every
    # other one, including any this list has never heard of, counts as a
    # mutation, as do these non-BD commands.
    variable bd_reader_verbs {get find report list current save write describe help}
    variable mutating_commands {set_property reset_property upgrade_ip update_module_reference
        create_project open_project close_project add_files remove_files import_files update_ip_catalog}
    variable mutation_epoch 0
    variable verified_clocks {}
}

proc ipiasm::_mutation_bump {args} {
    incr ::ipiasm::mutation_epoch
}

# Mutation count since load, or "unknown" when it cannot be trusted. Traces are
# (re)installed here, so a command defined or redefined after load is covered
# from its first use; installing one also bumps the count, because the command
# may already have run untraced.
proc ipiasm::_mutation_epoch {} {
    variable bd_reader_verbs
    variable mutating_commands
    variable mutation_epoch
    if {[info exists ::env(IPIASM_NO_VERIFY_CACHE)] && $::env(IPIASM_NO_VERIFY_CACHE) ni {"" 0}} {
        return unknown
    }
    set names $mutating_commands
    set reader "^([join $bd_reader_verbs |])_bd(_|\$)"
    foreach name [info commands ::*_bd*] {
        set name [string trimleft $name :]
        if {![regexp $reader $name]} {lappend names $name}
    }
    set callback ::ipiasm::_mutation_bump
    foreach name [lsort -unique $names] {
        if {![llength [info commands ::$name]]} {continue}
        if {[catch {trace info execution ::$name} traces]} {return unknown}
        if {[list enter $callback] in $traces} {continue}
        if {[catch {trace add execution ::$name enter $callback}]} {return unknown}
        incr mutation_epoch
    }
    return $mutation_epoch
}

# Everything a passing check depends on besides the design's properties, or
# {} when any part is unavailable (then the check always runs).
proc ipiasm::_clock_verification_state {} {
    set epoch [ipiasm::_mutation_epoch]
    set shape [ipiasm::_design_fp]
    if {$epoch eq "unknown" || $shape eq ""} {return {}}
    return [list $epoch [ipiasm::context_identity] $shape $::ipiasm::clock_contracts]
}

# caller "stage" may reuse the last passing result for an unchanged design;
# any other caller always runs the full check and records a pass for reuse.
proc ipiasm::_verified_clock_contracts {caller} {
    variable verified_clocks
    set state [ipiasm::_clock_verification_state]
    if {$caller eq "stage" && $state ne {} && [dict exists $verified_clocks state]
        && [dict get $verified_clocks state] eq $state} {
        return [dict get $verified_clocks result]
    }
    set verified_clocks {}
    set result [ipiasm::check_clock_contracts]
    # Recorded with the state from before the check: had the check itself
    # mutated anything, the epoch would differ next time and it runs again.
    if {$state ne {} && [string match CLOCK_CONTRACTS:OK* $result]} {
        set verified_clocks [dict create state $state result $result]
    }
    return $result
}

# The clock guidance (SKILL.md, references/clock-planning.md) runs ipcfg::reference_precision; a
# configurator without it gets this copy, one that has it keeps its own.
if {[info commands ::ipcfg::reference_precision] eq ""} {
namespace eval ::ipcfg {}
# Is a reference frequency in MHz a truncated or rounded repeating decimal? REF_PRECISION:ROUNDED with the full-precision value, or REF_PRECISION:OK.
proc ipcfg::reference_precision {mhz {max_denominator 12}} {
    if {![regexp {^([0-9]+)\.([0-9]+)$} [string trim $mhz] -> whole fraction]} {
        if {[regexp {^[0-9]+$} [string trim $mhz]]} {return "REF_PRECISION:OK value=$mhz integer"}
        return "REF_PRECISION:ERROR:expected a decimal MHz value, got '$mhz'"
    }
    set digits [string length $fraction]
    set scale [expr {10 ** $digits}]
    set stated [expr {[scan $whole %lld] * $scale + [scan $fraction %lld]}]
    if {$digits < 2} {return "REF_PRECISION:OK value=$mhz too few decimals to judge"}
    for {set q 2} {$q <= $max_denominator} {incr q} {
        set p [expr {($stated * $q + $scale / 2) / $scale}]
        set gap [expr {abs($p * $scale - $stated * $q)}]
        if {$gap == 0 || $gap >= $q} continue
        set a $p; set b $q
        while {$b} {set t [expr {$a % $b}]; set a $b; set b $t}
        if {$a != 1} continue
        set full [format %.9f [expr {double($p) / $q}]]
        set ppm [format %.3f [expr {double($gap) * 1e6 / ($p * $scale)}]]
        return "REF_PRECISION:ROUNDED value=$mhz full=$full ratio=$p/$q stated_error_ppm=$ppm\
 -> set the reference to full=$full (or another valid reference that divides the targets evenly),\
 record the choice as an assumption in plan.md, re-read the derived clocks; this is not a requirement change.\
 If a 1 Hz residual remains because the parameter keeps fewer digits, pick a legal integer-MHz reference\
 the targets divide evenly instead; do not stop to ask"
    }
    return "REF_PRECISION:OK value=$mhz terminating at its stated precision"
}
}
