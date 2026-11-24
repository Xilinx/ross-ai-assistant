# Lite design-mode adapters. The bundled ipcfg.tcl (lib/ipcfg/) is reused without modification.
# Re-sourcing must never reset pinned intent or retry counters.
namespace eval lite_ced {}
set ::lite_ced::source_candidate [::apply {{path} {
    set ch [open $path r]
    try {set content [read $ch]} finally {close $ch}
    return [list [file normalize $path] [zlib crc32 $content]]
}} [info script]]
if {[info exists ::lite_ced::loaded_signature]} {
    if {$::lite_ced::loaded_signature ne $::lite_ced::source_candidate} {
        unset ::lite_ced::source_candidate
        error "LIBRARY_FAIL:lite changed; use a fresh session"
    }
    unset ::lite_ced::source_candidate
    return "SKIP:lite already loaded; ledger preserved"
}
namespace eval lite_ced {
    variable dir [file dirname [file normalize [info script]]]
    variable root ""
    variable records {}
    variable serial 0
    variable metadata_cache {}
    variable help_cache {}
    variable gui_help_watches {}
    variable context_range_unavailable {}
    # part -> {vlnv {status count examples}} seeded from the release database (seed_support).
    variable support_cache {}
    variable session_mode owned
}

proc lite_ced::init {outdir part native_log {bd design_1}} {
    variable dir; variable root; variable records; variable session_mode
    if {$root ne ""} {error "INIT_FAIL:already initialized; keep the current ledger"}
    if {[file pathtype $outdir] ne "absolute"} {error "INIT_FAIL:absolute scratch directory required"}
    if {[file pathtype $native_log] ne "absolute" || ![file isfile $native_log]} {error "INIT_FAIL:provide log_file from vivado_start"}
    if {[string first / $part] >= 0 || [string trim $part] ne $part || $part eq ""} {
        error "INIT_FAIL:use the exact target part from the prompt, without a slash; retry lite_ced::init"
    }
    set candidate [file normalize $outdir]
    set project [current_project -quiet]
    # Captured before the reopen-from-disk step below can turn a "" into this engine's
    # own project: whether some other actor -- a prompt arriving to a still-open Vivado,
    # most commonly the CED configurator handing off a cell of a design it just built --
    # already had a project open when lite_ced::init was called at all. That, not where the
    # project happens to live on disk, is what "owned" vs "borrowed" must track: the CED
    # skill's own scratch project lives at the exact same <outdir>/proj convention this
    # engine uses for projects it creates itself, so comparing paths mislabels a CED
    # hand-off as "owned" and then adopted() (structured_config.py) rejects it outright
    # (see ROSS-146 / agentic-ai-suite#1788).
    set project_was_open [expr {$project ne ""}]
    set current_bd ""
    # A design is built up over many prompts, and a prompt can arrive in a fresh Vivado
    # (a restart, or a new session) while the project it extends sits on disk.  Reopen
    # that project instead of creating over it; a different part is refused, never replaced.
    set existing [file join $candidate proj proj.xpr]
    if {$project eq "" && [file isfile $existing]} {
        open_project $existing
        set project [current_project -quiet]
        set saved_part [get_property PART $project]
        if {$saved_part ne $part} {
            close_project
            error "INIT_FAIL:existing project $existing targets $saved_part not $part; use that part or another directory"
        }
        set saved [get_files -quiet -of_objects [get_filesets sources_1] $bd.bd]
        if {[llength $saved]} {open_bd_design [lindex $saved 0]}
    }
    if {$project ne ""} {
        if {[get_property PART $project] ne $part} {
            error "INIT_FAIL:existing project must use the requested target part"
        }
        set current_bd [current_bd_design -quiet]
        if {$current_bd ne "" && $current_bd ne $bd} {
            error "INIT_FAIL:existing block design must match the requested design"
        }
        if {$current_bd eq "" && [llength [get_files -quiet *.bd]]} {
            error "INIT_FAIL:cannot adopt an unopened existing block design"
        }
        # Reopening our own prior scratch project from disk (project_was_open false, the
        # branch above just opened it) is this engine continuing across a Vivado restart:
        # "owned". A project that was already open before lite_ced::init ran -- wherever it
        # lives -- was handed to us by someone else: "borrowed", so finish leaves it and
        # Vivado running instead of closing them.
        set session_mode [expr {$project_was_open ? "borrowed" : "owned"}]
        # Cells already in the design are earlier prompts' work; the ledger is scoped to the
        # requested cell, so they are kept and never re-applied.
    }
    foreach d {proj evidence cache} {file mkdir [file join $candidate $d]}
    # Bundled, unmodified copy of the original skill's library (lib/ipcfg/README.md).
    set upstream [file normalize [file join $dir ipcfg ipcfg.tcl]]
    uplevel #0 [list source -notrace $upstream]
    ipcfg_ced::set_mode assemble
    set ::ipcfg_ced::cache_file [file join $candidate cache learned_params.json]
    if {$project eq ""} {create_project proj [file join $candidate proj] -part $part}
    if {$current_bd eq ""} {create_bd_design $bd}
    set root $candidate
    set records {}
    set ::ipcfg_ced::native_log_path [file normalize $native_log]
    return [list [expr {$session_mode eq "borrowed" ? "INIT:ADOPTED" : "INIT:OK"}] \
        bd $bd evidence [file join $root evidence] session_mode $session_mode]
}

# The configured MCP places each native log under working_dir/.vivado-ai.
proc lite_ced::begin {part native_log {bd ""}} {
    variable root; variable session_mode
    if {[file tail [file dirname $native_log]] ne ".vivado-ai"} {
        error "INIT_FAIL:expected the exact log_file from vivado_start"
    }
    set workdir [file dirname [file dirname $native_log]]
    if {$bd eq ""} {
        set bd design_1
        if {![catch {set open_bd [current_bd_design -quiet]}] && $open_bd ne ""} {
            set bd $open_bd
        }
    }
    if {$root ne ""} {
        set expected [file normalize [file dirname $workdir]]
        if {$session_mode eq "borrowed"} {set expected [file normalize $workdir]}
        set project [current_project -quiet]
        if {$root ne $expected || $project eq "" || [get_property PART $project] ne $part ||
            [current_bd_design -quiet] ne $bd} {
            error "INIT_FAIL:retry must preserve project, part and block design"
        }
        if {$session_mode eq "owned" &&
            [file normalize [get_property DIRECTORY $project]] ne [file join $expected proj]} {
            error "INIT_FAIL:retry must preserve the owned scratch project"
        }
        return [list INIT:REUSED bd $bd evidence [file join $root evidence] session_mode $session_mode]
    }
    set outdir [file dirname $workdir]
    if {![catch {set open_project [current_project -quiet]}] && $open_project ne ""} {set outdir $workdir}
    return [init $outdir $part $native_log $bd]
}

proc lite_ced::canonical_dict {data} {
    dict size $data
    set result {}
    foreach key [lsort [dict keys $data]] {dict set result $key [dict get $data $key]}
    return $result
}

proc lite_ced::requirements_check {requirements} {
    if {![llength $requirements]} {error "REQUIREMENTS_FAIL:empty ledger"}
    set ids {}
    set canonical {}
    foreach r $requirements {
        foreach field {id text key wanted doc} {
            if {![dict exists $r $field]} {error "REQUIREMENTS_FAIL:missing $field"}
            if {$field ni {key wanted} && [string trim [dict get $r $field]] eq ""} {
                error "REQUIREMENTS_FAIL:empty $field"
            }
        }
        set id [dict get $r id]
        if {$id in $ids} {error "REQUIREMENTS_FAIL:duplicate id $id"}
        lappend ids $id
        set key [dict get $r key]
        if {$key ne "" && $key ne "__CELL_NAME__" && ![regexp {^CONFIG\.[A-Za-z0-9_]+$} $key]} {
            error "REQUIREMENTS_FAIL:invalid top-level key $key; use a separate subkey field"
        }
        lappend canonical [canonical_dict $r]
    }
    return $canonical
}

proc lite_ced::normal {value} {
    set value [string tolower [string trim $value]]
    if {[string first , $value] >= 0} {
        return [join [lmap v [split $value ,] {lite_ced::normal $v}] ,]
    }
    if {$value eq "true"} {return 1}
    if {$value eq "false"} {return 0}
    if {[string is double -strict $value] && ![regexp -nocase {nan|inf} $value]} {
        return [format %.15g $value]
    }
    return $value
}

proc lite_ced::boolean {value} {
    switch -nocase -- $value {
        1 - true {return 1}
        0 - false {return 0}
        default {error "REQUEST_FAIL:boolean required: $value"}
    }
}

proc lite_ced::identity {cell} {
    # Exact cell paths only: hierarchy is allowed, wildcards are not. A
    # relay that adopts a generated design addresses cells inside hierarchies.
    if {![regexp {^/?[A-Za-z_][A-Za-z0-9_]*(/[A-Za-z_][A-Za-z0-9_]*)*$} $cell]} {
        error "REQUEST_FAIL:exact cell name or hierarchical path required"
    }
    return /[string trimleft $cell /]
}

proc lite_ced::persist {} {
    variable root; variable records
    if {$root eq ""} {error "STATE_FAIL:call lite_ced::init first"}
    ipcfg_ced::write_evidence [evidence_path ledger state] $records
}

proc lite_ced::evidence_path {cell label} {
    variable root; variable serial
    if {$root eq ""} {error "STATE_FAIL:call lite_ced::init first"}
    # The serial restarts with each Vivado session, but a reused project keeps the evidence
    # earlier sessions wrote; never collide with it.
    set name [string map {/ __} [string trimleft $cell /]]
    while 1 {
        incr serial
        set path [file join $root evidence [format %s-%s-%04d.tcldict $name $label $serial]]
        if {![file exists $path]} {return $path}
    }
}

proc lite_ced::partial_save {} {
    if {[catch {save_bd_design} error]} {return [list SAVE:FAIL $error]}
    return SAVE:OK
}

# Read only selected keys on the actual BD cell, not a default metadata instance.
# Reuse the native range parser on a managed mirror of this configuration,
# never a cached default instance. BD native validation remains authoritative.
# Which pins each proposed two-state switch adds, measured on a throwaway cell of the same IP
# in the open block design, which is deleted before returning.  Some IPs decide port presence
# in encrypted Tcl and publish no port gating, so only the cell's own pins show it.
proc lite_ced::port_effect {vlnv proposals} {
    if {[current_bd_design -quiet] eq ""} {error "PORT_EFFECT_FAIL:no open block design"}
    if {![regexp {^[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+$} $vlnv]} {
        error "PORT_EFFECT_FAIL:invalid VLNV"
    }
    if {[dict size $proposals] < 1 || [dict size $proposals] > 4} {error "PORT_EFFECT_FAIL:1..4 proposals"}
    set name lite_port_probe_[clock clicks]
    if {[catch {create_bd_cell -type ip -vlnv $vlnv $name} msg]} {error "PORT_EFFECT_FAIL:$msg"}
    set result {}
    if {[catch {
        set cell [get_bd_cells $name]
        set pins [lsort [get_property NAME [get_bd_pins -quiet -of_objects $cell]]]
        dict for {key value} $proposals {
            if {![regexp {^CONFIG\.[A-Za-z0-9_]+$} $key]} {continue}
            set before [get_property -quiet $key $cell]
            if {[catch {set_property $key $value $cell}]} {dict set result $key {}; continue}
            set now [lsort [get_property NAME [get_bd_pins -quiet -of_objects $cell]]]
            set added {}
            foreach pin $now {if {$pin ni $pins} {lappend added $pin}}
            dict set result $key $added
            catch {set_property $key $before $cell}
        }
    } msg]} {
        catch {delete_bd_objs [get_bd_cells -quiet $name]}
        error "PORT_EFFECT_FAIL:$msg"
    }
    delete_bd_objs [get_bd_cells -quiet $name]
    if {[llength [get_bd_cells -quiet $name]]} {error "PORT_EFFECT_FAIL:probe cell was not removed"}
    return [list PORT_EFFECT:OK effects $result]
}

# Whether a write sticks, alone and after each trial setting, on a throwaway cell of the same IP
# that is deleted before returning (issue #548: axi_noc2 accepts NUM_MC but keeps 0 until the
# memory-controller mode is set). trials: flat list {key value key value ...}, at most 8 pairs.
proc lite_ced::stick_effect {vlnv target value trials} {
    if {[current_bd_design -quiet] eq ""} {error "STICK_FAIL:no open block design"}
    if {![regexp {^[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+$} $vlnv]} {error "STICK_FAIL:invalid VLNV"}
    if {![regexp {^CONFIG\.[A-Za-z0-9_]+$} $target]} {error "STICK_FAIL:invalid target"}
    if {[llength $trials] % 2 || [llength $trials] > 16} {error "STICK_FAIL:1..8 trial pairs"}
    set name lite_stick_probe_[clock clicks]
    if {[catch {create_bd_cell -type ip -vlnv $vlnv $name} msg]} {error "STICK_FAIL:$msg"}
    set same {{a b} {expr {[string equal -nocase $a $b] || ([string is double -strict $a] && [string is double -strict $b] && $a == $b)}}}
    set results {}
    if {[catch {
        set cell [get_bd_cells $name]
        set target_default [get_property -quiet $target $cell]
        catch {set_property $target $value $cell}
        set alone [apply $same [get_property -quiet $target $cell] $value]
        catch {set_property $target $target_default $cell}
        if {!$alone} {
            foreach {key setting} $trials {
                if {![regexp {^CONFIG\.[A-Za-z0-9_]+$} $key]} {continue}
                set before [get_property -quiet $key $cell]
                set stuck 0
                if {![catch {set_property $key $setting $cell}] && ![catch {set_property $target $value $cell}]} {
                    set stuck [apply $same [get_property -quiet $target $cell] $value]
                }
                lappend results [list $key $setting $stuck]
                catch {set_property $target $target_default $cell}
                catch {set_property $key $before $cell}
                if {$stuck} {break}
            }
        }
    } msg]} {
        catch {delete_bd_objs [get_bd_cells -quiet $name]}
        error "STICK_FAIL:$msg"
    }
    delete_bd_objs [get_bd_cells -quiet $name]
    if {[llength [get_bd_cells -quiet $name]]} {error "STICK_FAIL:probe cell was not removed"}
    return [list STICK:OK alone $alone trials $results]
}

proc lite_ced::context_ranges {cell keys {proposed {}}} {
    variable root; variable serial; variable context_range_cache; variable context_range_unavailable
    set object [get_bd_cells $cell]
    set vlnv [get_property VLNV $object]
    set overrides {}
    foreach key [lsort [list_property $object]] {
        if {![regexp {^CONFIG\.[A-Za-z0-9_]+$} $key]} {continue}
        set source {};catch {set source [get_property ${key}.VALUE_SRC $object]}
        if {[string toupper $source] in {USER PROPAGATED}} {
            dict set overrides $key [get_property $key $object]
        }
    }
    dict for {key value} $proposed {dict set overrides $key $value}
    set identity [list [version] [get_property PART [current_project]] $cell $vlnv $overrides]
    set customizer [list [version] [get_property PART [current_project]] $vlnv]
    set unavailable [list $customizer *]
    if {![dict exists $context_range_unavailable $unavailable]} {
        set all_unavailable [expr {[llength $keys] > 0}]
        set reasons {}
        foreach key $keys {
            set marker [list $customizer $key]
            if {![dict exists $context_range_unavailable $marker]} {set all_unavailable 0;break}
            lappend reasons [dict get $context_range_unavailable $marker]
        }
        if {$all_unavailable} {set unavailable [list $customizer [lsort $keys]]}
    }
    set scope [expr {[dict size $proposed] ? "proposed_configuration_mirror" : "current_configuration_mirror"}]
    set rows {};set file {};set reason {}
    if {[info exists context_range_cache] && [dict exists $context_range_cache $identity]} {
        set file [dict get $context_range_cache $identity]
    }
    if {[lindex $unavailable 1] ne "*" && [llength [lindex $unavailable 1]] > 1} {
        set reason [join $reasons {; }]
    } elseif {[dict exists $context_range_unavailable $unavailable]} {
        set reason [dict get $context_range_unavailable $unavailable]
    } elseif {$file eq "" || ![file isfile $file]} {
        incr serial
        set name lite_ranges_$serial
        set directory [file join $root tmp ranges]
        file mkdir $directory
        set file [file join $root evidence ${name}.txt]
        set create_failed [catch {
            create_ip -vlnv $vlnv -module_name $name -dir $directory
            set managed [get_ips -quiet $name]
            if {[llength $managed] != 1} {error "no managed mirror"}
        } reason]
        if {$create_failed} {
            # A customizer that cannot create a managed default instance also
            # cannot provide configuration-sensitive ranges. Remember that
            # native fact for this build/part/VLNV so a later configure capture
            # does not repeat the same failing customizer and misclassify a
            # successful write as an application failure.
            dict set context_range_unavailable [list $customizer *] $reason
            set file {}
        } elseif {[catch {
            set properties [list_property $managed]
            foreach key [dict keys $overrides] {
                if {$key ni $properties} {error "unmapped mirror property $key"}
            }
            if {[dict size $overrides]} {set_property -dict $overrides $managed}
            set result [ipcfg_ced::param_deps $name $file]
            if {![string match PARAM_DEPS:* $result]} {error $result}
        } reason]} {
            set file {}
            # A failure while inspecting the unmodified mirror establishes
            # that this customizer cannot publish live ranges in this
            # session. Proposed values did not cause it, so later apply calls
            # must reuse the unavailable result instead of emitting the same
            # native errors inside their evidence capture.
            if {![dict size $proposed]} {
                dict set context_range_unavailable [list $customizer *] $reason
            }
        } else {
            dict set context_range_cache $identity $file
        }
        catch {remove_files [get_files -quiet [file join $directory $name ${name}.xci]]}
        catch {file delete -force [file join $directory $name]}
    }
    set blocks {}
    if {$file ne ""} {catch {set blocks [parameter_blocks $file]}}
    foreach key $keys {
        set range RANGE_NONE
        if {$file ne ""} {set range [ipcfg_ced::param_range $file [string range $key 7 end]]}
        set complete [expr {[string match RANGE_NUM:* $range] || [string match RANGE_LIST:* $range]}]
        set row [dict create range [string range $range 0 239] range_scope [expr {$complete ? $scope : "unavailable"}] range_complete $complete]
        if {[string length $range]>240} {dict set row range_complete 0}
        # Native enablement of the user parameter in this configuration mirror.
        set name [string range $key 7 end]
        if {[dict exists $blocks $name]} {
            set block [dict get $blocks $name]
            if {[regexp -line {^Disabled:} $block]} {
                dict set row enabled 0;dict set row enablement_scope $scope
            } elseif {[regexp -line {^Enabled:} $block]} {
                dict set row enabled 1;dict set row enablement_scope $scope
            }
        }
        if {$reason ne ""} {dict set row range_reason [string range $reason 0 159]}
        if {$file ne ""} {dict set row range_file $file}
        dict set rows $key $row
        if {![dict size $proposed] && !$complete} {
            dict set context_range_unavailable [list $customizer $key] \
                [expr {$reason ne "" ? $reason : "source range unavailable for $key"}]
        }
    }
    return $rows
}

proc lite_ced::live_parameters {cell keys} {
    if {![llength $keys] || [llength $keys] > 24} {error "LIVE_FAIL:select 1..24 keys"}
    set object [get_bd_cells -quiet [identity $cell]]
    if {[llength $object] != 1} {error "LIVE_FAIL:cell missing or ambiguous"}
    set properties [list_property $object];set rows {}
    set ranges {}
    catch {set ranges [context_ranges $cell $keys]}
    foreach key $keys {
        if {![regexp {^CONFIG\.[A-Za-z0-9_]+$} $key] || $key ni $properties} {error "LIVE_FAIL:unknown CONFIG key"}
        set value [get_property $key $object];set source {}
        catch {set source [get_property ${key}.VALUE_SRC $object]}
        set bounds [expr {[dict exists $ranges $key] ? [dict get $ranges $key] : [dict create range RANGE_NONE range_scope unavailable range_complete 0]}]
        set row [dict merge $bounds [dict create value [string range $value 0 239] value_src $source]]
        if {[string length $value] > 240} {dict set row truncated 1}
        dict set rows $key $row
    }
    return $rows
}

proc lite_ced::named_in_text {value text} {
    set value [string trim $value]
    if {$value eq "" || [string length $value] > 64} {return 0}
    regsub -all {[][{}()*+?.\\^$|]} $value {\\&} quoted
    return [regexp -nocase "(?:^|\[^A-Za-z0-9_.\])${quoted}(?!\[A-Za-z0-9_\]|\\.\[0-9\])" $text]
}

# Every pinned requirement text stays, and a value the text itself names stays wanted for it.
proc lite_ced::intent_change {pinned canonical} {
    set wanted [dict create]
    foreach r $canonical {
        dict lappend wanted [dict get $r text] [string tolower [string trim [dict get $r wanted]]]
        if {[dict exists $r covered_values]} {
            foreach value [dict get $r covered_values] {
                # The JSON adapter emits this only for an explicit token in
                # a selected source enum, with grounding retained in evidence.
                dict lappend wanted [dict get $r text] [string tolower [string trim $value]]
            }
        }
    }
    foreach r $pinned {
        set text [dict get $r text]
        if {![dict exists $wanted $text]} {return "requirement '$text' dropped"}
        set value [dict get $r wanted]
        if {[named_in_text $value $text] && [string tolower [string trim $value]] ni [dict get $wanted $text]} {
            return "requested value $value of '$text' must stay selected"
        }
    }
    return ""
}

proc lite_ced::register {cell requirements} {
    variable records
    set canonical [requirements_check $requirements]
    if {[dict exists $records $cell]} {
        set pinned [dict get $records $cell requirements]
        if {$pinned ne $canonical} {
            # After a failed apply only the parameter mapping and encodings may change; each
            # requirement text and every value it names stay immutable.
            if {![dict exists $records $cell remap_allowed] || ![dict get $records $cell remap_allowed]} {
                error "REQUIREMENTS_FAIL:immutable intent changed for $cell"
            }
            set change [intent_change $pinned $canonical]
            if {$change ne ""} {
                error "REQUIREMENTS_FAIL:immutable intent changed for $cell: $change"
            }
            dict set records $cell requirements $canonical
            dict set records $cell remap_allowed 0
            persist
        }
    } else {
        dict set records $cell [dict create requirements $canonical baseline {} baseline_captured 0 \
            attempts 0 failures {} capture_ok 0 evidence {}]
        persist
    }
    return $canonical
}

# A later prompt about a cell configured earlier in this session is a new intent: drop the
# cell's pinned requirements and attempt count so they are not compared with the old prompt's.
# Retries within one prompt never reach this (they keep the same ledger).
proc lite_ced::reset_cell {cell} {
    variable records; variable root
    if {$root eq ""} {error "STATE_FAIL:call lite_ced::init first"}
    # The ledger is keyed by the block-design path (/cell); begin passes the bare name.
    set found {}
    foreach key [list $cell /[string trimleft $cell /]] {
        if {[dict exists $records $key] && $key ni $found} {lappend found $key}
    }
    if {![llength $found]} {return [list RESET:NONE cell $cell]}
    foreach key $found {dict unset records $key}
    persist
    return [list RESET:OK cell [lindex $found 0]]
}

proc lite_ced::requirement_value {cell r} {
    set key [dict get $r key]
    if {$key eq ""} {error "unmapped requirement"}
    set objects [get_bd_cells -quiet $cell]
    if {[llength $objects] != 1} {error "cell missing or ambiguous"}
    if {$key eq "__CELL_NAME__"} {return [string trimleft $cell /]}
    if {$key ni [list_property $objects]} {error "missing property $key"}
    if {[dict exists $r read_key]} {set key [dict get $r read_key]}
    if {$key ni [list_property $objects]} {error "missing readback property $key"}
    set value [get_property $key $objects]
    if {[dict exists $r subkey]} {
        set subkey [dict get $r subkey]
        if {[dict exists $r read_subkey]} {set subkey [dict get $r read_subkey]}
        if {![dict exists $value $subkey]} {error "missing nested key $subkey"}
        set value [dict get $value $subkey]
    }
    return $value
}

proc lite_ced::inner_dictionary {value} {
    expr {[string length $value]<=240 && ![catch {dict size $value} size] && $size>1}
}

proc lite_ced::readback {cell requirements} {
    set observations {}; set failures {}
    foreach r $requirements {
        set id [dict get $r id]
        set want [dict get $r wanted]
        if {[catch {requirement_value $cell $r} actual]} {
            set primary_error $actual
            set equivalent_ok 0
            if {[string match {missing nested key *} $primary_error] &&
                [dict exists $r equivalent_key] && [dict exists $r equivalent_subkey] &&
                [dict exists $r equivalent_wanted] &&
                [dict get $r equivalence_scope] eq {same_container_exact_value_and_identifier_tokens}} {
                set alternate [dict create key [dict get $r equivalent_key] \
                    subkey [dict get $r equivalent_subkey]]
                if {![catch {requirement_value $cell $alternate} encoded] &&
                    [normal $encoded] eq [normal [dict get $r equivalent_wanted]]} {
                    set equivalent_ok 1
                    dict set observations $id [dict create pass 1 wanted $want actual $encoded \
                        propagated 1 observed_key [dict get $r equivalent_key] \
                        observed_subkey [dict get $r equivalent_subkey]]
                }
            }
            if {!$equivalent_ok} {
                lappend failures [list $id reason $primary_error wanted $want]
                dict set observations $id [dict create pass 0 reason $primary_error wanted $want]
            }
        } else {
            set ok [expr {[normal $actual] eq [normal $want]}]
            if {!$ok && [dict exists $r subkey] && [inner_dictionary $actual] && ![catch {dict size $want} size] && $size} {
                # A merged inner-dictionary write passes when every requested key reads back as requested.
                set ok 1
                dict for {k v} $want {
                    if {![dict exists $actual $k] || [normal [dict get $actual $k]] ne [normal $v]} {set ok 0}
                }
            }
            dict set observations $id [dict create pass $ok wanted $want actual $actual]
            if {!$ok} {lappend failures [list $id actual $actual wanted $want]}
        }
    }
    return [dict create observations $observations failures $failures]
}

proc lite_ced::prepare {hint cell requirements} {
    variable records
    set cell [identity $cell]
    register $cell $requirements
    set objects [get_bd_cells -quiet $cell]
    set resolution [resolve $hint]
    set vlnv [string range $resolution 5 end]
    if {![llength $objects]} {
        set result [ipcfg_ced::create_cell $vlnv [string trimleft $cell /]]
        if {![string match SUCCESS:* $result]} {error $result}
    } elseif {[llength $objects] != 1 || [get_property VLNV $objects] ne $vlnv} {
        error "DISCOVERY_FAIL:existing cell is not $vlnv"
    }
    dict set records $cell vlnv $vlnv
    return $cell
}

proc lite_ced::configure {hint cell config requirements} {
    variable records
    set cell [identity $cell]
    set config [canonical_dict $config]
    set requirements [register $cell $requirements]
    set record [dict get $records $cell]
    if {[dict get $record attempts] >= 3 ||
        ([dict exists $record last_failed_config] && [dict get $record last_failed_config] eq $config && [dict get $record repeated_failure_count] >= 2)} {
        set saved [partial_save]
        return [list CFG:STOP reason retry_limit saved $saved evidence [dict get $record evidence]]
    }
    dict incr record attempts
    dict set records $cell $record
    # Mutations and native warnings are captured; full output remains in scratch.
    set path [evidence_path $cell configure]
    set receipt [ipcfg_ced::capture $path [list lite_ced::apply_config $hint $cell $config $requirements] {APPLY:OK}]
    set rb [readback $cell $requirements]
    set bad [dict get $rb failures]
    if {[dict get $receipt status] ne "OK"} {
        lappend bad [list apply reason [dict get $receipt excerpt]]
    }
    dict set records $cell evidence $path
    dict set records $cell capture_ok [expr {[dict get $receipt status] eq "OK"}]
    dict set records $cell last_readback $rb
    if {[llength $bad]} {
        # Count repeated failed *data requests*, independent of superficial command rewriting.
        set pair [list $config $bad]
        set n 1
        if {[dict exists $records $cell last_failure_pair] && [dict get $records $cell last_failure_pair] eq $pair} {
            set n [expr {[dict get $records $cell repeated_failure_count] + 1}]
        }
        dict set records $cell last_failure_pair $pair
        dict set records $cell last_failed_config $config
        dict set records $cell repeated_failure_count $n
        dict set records $cell remap_allowed 1
        set saved [partial_save]
        persist
        return [list CFG:FIX failures $bad saved $saved evidence $path]
    }
    dict set records $cell remap_allowed 0
    persist
    return [list CFG:OK cell $cell vlnv [dict get $records $cell vlnv] verified [llength $requirements] evidence $path]
}

# Apply a verified subset while preserving the complete immutable ledger that
# the first configure call pinned. This is used only to recover from an atomic
# batch rejection; omitted requirements remain pinned and unresolved.
proc lite_ced::configure_subset {hint cell config requirements subset} {
    variable records
    set cell [identity $cell]
    set config [canonical_dict $config]
    set requirements [register $cell $requirements]
    set subset [requirements_check $subset]
    foreach r $subset {
        if {[lsearch -exact $requirements $r] < 0} {
            error "REQUIREMENTS_FAIL:subset entry is not in the immutable ledger"
        }
    }
    set path [evidence_path $cell configure-subset]
    set receipt [ipcfg_ced::capture $path [list lite_ced::apply_config $hint $cell $config $subset 0] {APPLY:OK}]
    set rb [readback $cell $subset]
    set bad [dict get $rb failures]
    if {[dict get $receipt status] ne "OK"} {lappend bad [list apply reason [dict get $receipt excerpt]]}
    if {[llength $bad]} {
        set saved [partial_save]
        return [list CFG:FIX failures $bad saved $saved evidence $path]
    }
    return [list CFG:OK cell $cell subset_verified [llength $subset] evidence $path]
}

# After independently verified subsets have all succeeded, promote the native
# record only when every original pinned requirement reads back correctly.
proc lite_ced::verify_pinned {cell requirements} {
    variable records
    set cell [identity $cell]
    set requirements [register $cell $requirements]
    set rb [readback $cell $requirements]
    set bad [dict get $rb failures]
    if {[llength $bad]} {
        dict set records $cell capture_ok 0
        persist
        return [list CFG:FIX failures $bad]
    }
    dict set records $cell capture_ok 1
    dict set records $cell last_readback $rb
    persist
    return [list CFG:OK cell $cell verified [llength $requirements]]
}

proc lite_ced::apply_config {hint cell config requirements {register_requirements 1}} {
    variable records
    if {$register_requirements} {
        prepare $hint $cell $requirements
    } else {
        set cell [identity $cell]
        set object [get_bd_cells -quiet $cell]
        set resolution [resolve $hint]
        set vlnv [string range $resolution 5 end]
        if {[llength $object] != 1 || [get_property VLNV $object] ne $vlnv || ![dict exists $records $cell]} {
            error "DISCOVERY_FAIL:subset requires the existing pinned cell"
        }
    }
    set object [get_bd_cells $cell]
    set properties [list_property $object]
    foreach key [dict keys $config] {
        if {![regexp {^CONFIG\.[A-Za-z0-9_]+$} $key] || $key ni $properties} {
            error "CONFIGURE_FAIL:unknown property $key"
        }
        set source {};catch {set source [get_property ${key}.VALUE_SRC $object]}
        if {[string toupper $source] in {PROPAGATED CONSTANT}} {
            error "CONFIGURE_FAIL:$key VALUE_SRC=$source; assert after propagation instead of writing"
        }
    }
    # Resolve against the entire proposed batch, including controlling settings.
    # Never reject using ranges from a default or a pre-change configuration.
    set ranges {}
    if {[dict size $config]} {catch {set ranges [context_ranges $cell [dict keys $config] $config]}}
    if {[dict size $ranges]} {
        dict for {key value} $config {
            set nested 0
            foreach r $requirements {
                if {[dict get $r key] eq $key && [dict exists $r subkey]} {set nested 1}
            }
            if {$nested || [string first , $value]>=0} {continue}
            set name [string range $key 7 end]
            set row [dict get $ranges $key]
            if {![dict get $row range_complete]} {continue}
            set range [dict get $row range]
            if {[string match RANGE_NUM:* $range] && ![ipcfg_ced::_is_num $value]} {
                error "VALUE_FAIL:$key expects a numeric value; legal=$range"
            }
            set checked [ipcfg_ced::check_value [dict get $row range_file] $name $value]
            if {[string match VAL_OUT_OF_RANGE:* $checked] || [string match VAL_NOT_IN_LIST:* $checked]} {
                error "VALUE_FAIL:$checked"
            }
        }
    }
    # A remapped requirement gets its baseline before its first write.
    set baseline [dict get $records $cell baseline]
    set captured [dict get $records $cell baseline_captured]
    foreach r $requirements {
        if {[dict exists $baseline [dict get $r id]]} {continue}
        if {[catch {requirement_value $cell $r} value]} {set value <unmapped>}
        dict set baseline [dict get $r id] $value
        set captured 0
    }
    if {!$captured} {
        dict set records $cell baseline $baseline
        dict set records $cell baseline_captured 1
        persist
    }
    # Validate and merge requested nested updates; unknown subkeys are never invented.
    dict for {key value} $config {
        set nested 0
        foreach r $requirements {
            if {[dict get $r key] eq $key && [dict exists $r subkey]} {set nested 1}
        }
        if {$nested} {
            set original [get_property $key $object]
            dict size $original
            dict for {subkey wanted} $value {
                if {![dict exists $original $subkey]} {
                    set allowed [known_subkeys $hint $key]
                    if {$subkey ni $allowed && ![documented_subkey $hint $object $key $subkey $requirements]} {
                        error "CONFIGURE_FAIL:unknown subkey $key/$subkey; absent from current value/graph; explicit write_doc and a known native/derived key mapping required"
                    }
                }
                if {[inner_dictionary [expr {[dict exists $original $subkey] ? [dict get $original $subkey] : ""}]] &&
                    ![catch {dict size $wanted}]} {
                    set wanted [dict merge [dict get $original $subkey] $wanted]
                }
                dict set original $subkey $wanted
            }
            dict set config $key $original
        }
    }
    # Source-declared controllers selected in this same batch are written
    # before dependent values. No controller value is guessed here. The final
    # batch and all pinned requirements are still verified after propagation.
    set prerequisite_keys {}
    foreach r $requirements {
        if {[dict exists $r prerequisites]} {
            foreach key [dict get $r prerequisites] {
                if {[dict exists $config $key]} {lappend prerequisite_keys $key}
            }
        }
    }
    set prerequisites {}
    foreach key [lsort -unique $prerequisite_keys] {dict set prerequisites $key [dict get $config $key]}
    if {[dict size $prerequisites]} {
        set result [ipcfg_ced::apply_dict $cell $prerequisites]
        if {![string match SUCCESS:* $result]} {error $result}
        # Refresh native enablement/ranges after prerequisite propagation.
        catch {context_ranges $cell [dict keys $config] $config}
    }
    set result [ipcfg_ced::apply_dict $cell $config]
    if {![string match SUCCESS:* $result]} {error $result}
    set reqmap {}
    foreach r $requirements {
        # Numeric comma vectors and nested subkeys need component-wise readback, not scalar string equality.
        if {[dict get $r key] eq "" || [dict exists $r subkey] || [dict exists $r read_key] ||
            ([dict exists $r mode] && [dict get $r mode] eq "assert") || [string first , [dict get $r wanted]] >= 0} {continue}
        lappend reqmap [dict get $r key] [dict get $r wanted] [dict get $records $cell baseline [dict get $r id]]
    }
    set intent [ipcfg_ced::verify_intent $cell $reqmap 0]
    if {[llength $intent]} {error "INTENT_FAIL:$intent"}
    set bad [dict get [readback $cell $requirements] failures]
    if {[llength $bad]} {error "READBACK_FAIL:$bad"}
    return APPLY:OK
}

proc lite_ced::finish {cell args} {
    variable records
    set cell [identity $cell]
    if {![dict exists $records $cell]} {error "FINISH_FAIL:no pinned requirements for $cell"}
    set pinned [dict get $records $cell requirements]
    if {[llength $args] > 1} {error "FINISH_FAIL:expected cell and optional requirements"}
    if {[llength $args]} {register $cell [lindex $args 0]}
    set saved [partial_save]
    set rb [readback $cell $pinned]
    set ledger {}
    foreach r $pinned {
        set observation [dict get $rb observations [dict get $r id]]
        set outcome unapplied
        if {[dict get $observation pass] && [dict get $records $cell capture_ok]} {set outcome applied}
        lappend ledger [dict get $r text] [dict get $r key] $outcome
    }
    set report [ipcfg_ced::coverage_report $ledger]
    set full [expr {![llength [dict get $rb failures]] && [dict get $records $cell capture_ok] && $saved eq "SAVE:OK"}]
    set path [evidence_path $cell finish]
    ipcfg_ced::write_evidence $path [dict create saved $saved readback $rb coverage $report verified_full $full]
    dict set records $cell final_readback $rb
    persist
    if {![dict get $records $cell capture_ok]} {
        dict lappend rb failures [list capture reason {native application capture failed} evidence [dict get $records $cell evidence]]
    }
    if {$saved ne "SAVE:OK"} {dict lappend rb failures [list save reason $saved]}
    if {$full} {return [list DONE:COVERAGE:FULL cell $cell verified [llength $pinned] evidence $path]}
    return [list DONE:COVERAGE:PARTIAL cell $cell failures [dict get $rb failures] saved $saved evidence $path]
}

proc lite_ced::verified_readback {cell} {
    variable records
    set cell [identity $cell]
    if {![dict exists $records $cell final_readback]} {error "READBACK_FAIL:finish first"}
    set observations [dict get $records $cell final_readback observations]
    set fields {}
    foreach r [dict get $records $cell requirements] {
        set observation [dict get $observations [dict get $r id]]
        set row [dict create requirement_id [dict get $r id] text [dict get $r text] \
            key [expr {[dict exists $observation observed_key] ? [dict get $observation observed_key] : [dict get $r key]}] \
            pass [dict get $observation pass]]
        if {[dict exists $observation observed_subkey]} {
            dict set row subkey [dict get $observation observed_subkey]
            dict set row propagated 1
        } elseif {[dict exists $r subkey]} {dict set row subkey [dict get $r subkey]}
        foreach field {actual reason} {
            if {[dict exists $observation $field]} {dict set row $field [string range [dict get $observation $field] 0 1023]}
        }
        lappend fields $row
    }
    return [dict create fields $fields]
}

proc lite_ced::known_subkeys {hint key} {
    if {![regexp {^CONFIG\.([A-Za-z0-9_]+)$} $key -> name]} {return {}}
    if {[catch {metadata $hint} record]} {return {}}
    set result [ipcfg_ced::dict_keys [dict get $record file] $name]
    set prefix "DK:$name:"
    if {[string first $prefix $result]!=0} {return {}}
    return [string range $result [string length $prefix] end]
}

# Full schemas stay in scratch. The JSON adapter reads this host-only receipt,
# then returns selected scalar fields rather than dictionary strings to a model.
proc lite_ced::nested_schema {hint cell} {
    set object [get_bd_cells [identity $cell]]
    set properties [list_property $object];set possible {}
    foreach key $properties {
        if {![string match CONFIG.* $key]} {continue}
        set value [get_property $key $object]
        if {[string match *_CONFIG $key] || (![catch {dict size $value} count] && $count>1)} {
            lappend possible $key
        }
    }
    if {![llength $possible]} {return [list NESTED:OK file {} containers 0]}
    set native {};set blocks {}
    if {![catch {metadata $hint} native]} {set blocks [parameter_blocks [dict get $native file]]}
    set entries {}
    foreach key $possible {
        set current [get_property $key $object];set fields {};set native_key {}
        if {![catch {dict size $current} size] && $size>0} {set fields $current} else {set current {}}
        set name [string range $key 7 end]
        if {[dict exists $blocks ${name}_INTERNAL]} {
            set resolved [field [dict get $blocks ${name}_INTERNAL] {Current Value}]
            if {![catch {dict size $resolved} size] && $size>0} {
                set fields [dict merge $resolved $fields]
                set native_key CONFIG.${name}_INTERNAL
            }
        }
        set selected {}
        dict for {subkey value} $fields {
            if {![regexp {^[A-Za-z_][A-Za-z0-9_]*$} $subkey] || [string length $value]>240} {continue}
            set scope live_dictionary
            if {![dict exists $current $subkey]} {set scope native_same_name_candidate}
            set entry [dict create default $value mapping_scope $scope]
            # Small inner dictionaries are written by merging requested keys; deeper levels stay opaque.
            if {![catch {dict size $value} size] && $size>1} {dict set entry shape dictionary}
            dict set selected $subkey $entry
        }
        if {[dict size $selected]} {
            dict set entries $key [dict create native_key $native_key fields $selected]
        }
    }
    if {![dict size $entries]} {return [list NESTED:OK file {} containers 0]}
    set path [evidence_path $cell nested-schema]
    ipcfg_ced::write_evidence $path [dict create parameters $entries native $native \
        scope {same-name resolved fields are provisional mappings; native apply/readback required}]
    return [list NESTED:OK file $path containers [dict size $entries]]
}

proc lite_ced::native_field_mapping {hint key subkey native_key} {
    if {$native_key ne "${key}_INTERNAL" || [catch {metadata $hint} record]} {return 0}
    set blocks [parameter_blocks [dict get $record file]]
    set name [string range $key 7 end]
    if {![dict exists $blocks $name] || ![dict exists $blocks ${name}_INTERNAL]} {return 0}
    set result [ipcfg_ced::param_dict [dict get $record file] ${name}_INTERNAL $subkey]
    return [string match PD:* $result]
}

# Metadata is generated on demand. No IP or parameter names are special-cased.
proc lite_ced::metadata {hint} {
    variable root; variable metadata_cache; variable serial
    if {$root eq ""} {error "STATE_FAIL:call lite_ced::init first"}
    set result [resolve $hint]
    set vlnv [string range $result 5 end]
    set identity [list [version] [get_property PART [current_project]] $vlnv]
    if {[dict exists $metadata_cache $identity]} {
        set cached [dict get $metadata_cache $identity]
        if {[file isfile [dict get $cached file]]} {return $cached}
    }
    incr serial
    set name lite_meta_$serial
    set file [file join $root evidence ${name}.txt]
    set result [ipcfg_ced::param_deps $name $file $vlnv [file join $root tmp metadata]]
    if {![string match PARAM_DEPS:* $result]} {error "METADATA_UNAVAILABLE:$result"}
    set ch [open $file];set text [read $ch];close $ch
    if {![regexp -line {^Parameter Name:} $text] && [regexp -line {^ERROR:[ \t]*(.*)$} $text -> message]} {
        error "METADATA_UNAVAILABLE:$message"
    }
    set record [dict create identity $identity vlnv $vlnv file $file]
    dict set metadata_cache $identity $record
    return $record
}

proc lite_ced::parameter_blocks {file} {
    set ch [open $file r]
    try {set text [read $ch]} finally {close $ch}
    set blocks {}; set current {}
    foreach line [split $text \n] {
        if {[regexp {^\*{5,}} $line]} {
            set block [join $current \n]
            if {[regexp -line {^Parameter ID:\s+PARAM_VALUE\.(\S+)\s*$} $block -> name]} {
                dict set blocks $name $block
            }
            set current {}
        } else {lappend current $line}
    }
    set block [join $current \n]
    if {[regexp -line {^Parameter ID:\s+PARAM_VALUE\.(\S+)\s*$} $block -> name]} {dict set blocks $name $block}
    return $blocks
}

proc lite_ced::field {block name} {
    # Exact field labels, never model-provided regex expressions.
    foreach line [split $block \n] {
        if {[string first "$name:" $line] == 0} {return [string trim [string range $line [expr {[string length $name]+1}] end]]}
    }
    return ""
}

proc lite_ced::inspect {hint keywords {limit 6} {offset 0}} {
    set keywords [keywords_check $keywords]
    if {![string is integer -strict $limit] || $limit < 1 || $limit > 12 ||
        ![string is integer -strict $offset] || $offset < 0} {error "INSPECT_FAIL:limit 1..12; offset >= 0"}
    set metadata [metadata $hint]
    set file [dict get $metadata file]
    set blocks [parameter_blocks $file]
    set matches {}; set found {}
    dict for {name block} $blocks {
        set searchable [string tolower "$name [field $block {Display Name}] [field $block Description]"]
        set hit 0
        foreach keyword $keywords {
            if {[string first [string tolower $keyword] $searchable] >= 0} {
                set hit 1;dict set found $keyword 1
            }
        }
        if {$hit} {lappend matches $name}
    }
    set matches [lsort $matches]
    set selected [lrange $matches $offset [expr {$offset+$limit-1}]]
    set detail {}; set candidates {}
    foreach name $selected {
        set block [dict get $blocks $name]
        set candidate [dict create key CONFIG.$name label [field $block {Display Name}] type [field $block Type] \
            default [field $block {Default Value}] current [field $block {Current Value}] enums [enum_labels $block] range [ipcfg_ced::param_range $file $name]]
        # Keep large dictionary defaults/ranges out of the model's inline context.
        dict for {key value} $candidate {
            if {[string length $value] > 160} {
                dict set candidate $key [string range $value 0 159]
                dict set candidate ${key}_truncated 1
            }
        }
        if {[regexp -line {^Disabled:} $block]} {dict set candidate state disabled} else {dict set candidate state metadata}
        lappend candidates $candidate
        dict set detail CONFIG.$name $candidate
    }
    # Dictionary-key evidence is separate from flat parameter matches.
    set nested {}
    foreach keyword $keywords {
        set result [ipcfg_ced::dict_find $file $keyword]
        set hits {}
        if {[string match DF:* $result] && $result ne "DF:none"} {set hits [string range $result 3 end]}
        # Native graphs can omit dictionary nodes; resolved values still publish candidate names.
        dict for {name block} $blocks {
            set current [field $block {Current Value}]
            if {[catch {dict keys $current} subkeys]} {continue}
            foreach subkey $subkeys {
                if {[string first [string tolower $keyword] [string tolower $subkey]]>=0} {lappend hits "$name<$subkey>"}
            }
        }
        set hits [lsort -unique $hits]
        if {[llength $hits]} {
            dict set found $keyword 1
            dict set nested $keyword [dict create matched [llength $hits] offset $offset \
                shown [lrange $hits $offset [expr {$offset+$limit-1}]] \
                more [expr {$offset+$limit < [llength $hits]}]]
        } else {dict set nested $keyword $result}
    }
    set unmatched {}
    foreach keyword $keywords {if {![dict exists $found $keyword]} {lappend unmatched $keyword}}
    set path [evidence_path metadata selected]
    ipcfg_ced::write_evidence $path [dict create metadata $metadata parameters $detail nested $nested]
    return [list META:OK vlnv [dict get $metadata vlnv] keywords $keywords matched [llength $matches] offset $offset \
        shown [llength $selected] more [expr {$offset+[llength $selected]<[llength $matches]}] \
        candidates $candidates unmatched $unmatched nested $nested evidence $path]
}

proc lite_ced::open_cell {hint cell} {
    set cell [identity $cell]
    set resolution [resolve $hint]
    set vlnv [string range $resolution 5 end]
    if {[part_available $vlnv] eq "WRONG_PART"} {
        error "DISCOVERY_FAIL:WRONG_PART:$vlnv is not supported on the current project part"
    }
    set existing [get_bd_cells -quiet $cell]
    if {[llength $existing] && [get_property VLNV $existing] ne $vlnv} {error "DISCOVERY_FAIL:cell identity mismatch"}
    if {[llength $existing]} {return [list CELL:OK cell $cell vlnv $vlnv existing 1]}
    set path [evidence_path $cell create]
    set receipt [ipcfg_ced::capture $path [list ipcfg_ced::create_cell $vlnv [string trimleft $cell /]] {SUCCESS:created}]
    if {[dict get $receipt status] ne "OK"} {return [list CELL:PARTIAL reason [dict get $receipt excerpt] evidence $path]}
    return [list CELL:OK cell $cell vlnv $vlnv evidence $path]
}

# Remove only the exact provisional cell opened by this structured session.
proc lite_ced::delete_cell {object} {
    delete_bd_objs $object
    return SUCCESS:deleted
}

proc lite_ced::discard_cell {cell expected_vlnv} {
    set cell [identity $cell]
    set found [get_bd_cells -quiet $cell]
    if {[llength $found] != 1} {error "DISCARD_FAIL:expected exactly one cell named $cell"}
    set actual [get_property VLNV $found]
    if {$actual ne $expected_vlnv} {error "DISCARD_FAIL:cell identity mismatch"}
    set path [evidence_path $cell discard]
    set receipt [ipcfg_ced::capture $path [list lite_ced::delete_cell $found] {SUCCESS:deleted}]
    if {[dict get $receipt status] ne "OK" || [llength [get_bd_cells -quiet $cell]]} {
        error "DISCARD_FAIL:[dict get $receipt excerpt]"
    }
    return [list DISCARD:OK cell $cell vlnv $actual evidence $path]
}

# Reversibly test one live scalar setting. This never saves the design.
proc lite_ced::probe_scalar {cell key wanted} {
    set cell [identity $cell]
    set object [get_bd_cells -quiet $cell]
    if {[llength $object] != 1 || ![regexp {^CONFIG\.[A-Za-z0-9_]+$} $key] ||
        $key ni [list_property $object]} {error "PROBE_FAIL:exact cell and CONFIG key required"}
    set source {}
    catch {set source [get_property ${key}.VALUE_SRC $object]}
    if {$source in {PROPAGATED CONSTANT}} {
        return [list PROBE:REJECTED key $key reason value_source value_src $source]
    }
    set before [get_property $key $object]
    set write_error ""
    if {[catch {set_property $key $wanted $object} write_error]} {
        return [list PROBE:REJECTED key $key reason write_failed detail [string range $write_error 0 239] before $before]
    }
    set actual [get_property $key $object]
    set accepted [expr {[normal $actual] eq [normal $wanted]}]
    set restore_error ""
    if {[catch {set_property $key $before $object} restore_error]} {
        return [list PROBE:DIRTY key $key reason restore_failed detail [string range $restore_error 0 239] before $before actual $actual]
    }
    set restored [get_property $key $object]
    if {[normal $restored] ne [normal $before]} {
        return [list PROBE:DIRTY key $key reason restore_readback_failed before $before actual $actual restored $restored]
    }
    if {!$accepted} {
        return [list PROBE:REJECTED key $key reason readback_mismatch before $before wanted $wanted actual $actual restored $restored]
    }
    return [list PROBE:OK key $key before $before wanted $wanted actual $actual restored $restored]
}

proc lite_ced::keywords_check {keywords} {
    if {![llength $keywords] || [llength $keywords]>8} {error "INSPECT_FAIL:provide 1..8 short feature keywords"}
    set normalized {}
    foreach keyword $keywords {
        # Models sometimes format Tcl data lists with prose trailing commas.
        # Normalize that punctuation only; do not invent semantic synonyms.
        set keyword [string trim [string trim $keyword] ,]
        if {$keyword eq "" || $keyword eq "*" || [string length $keyword]>48} {
            error "INSPECT_FAIL:each feature keyword must be nonempty, <=48 characters, and not *"
        }
        lappend normalized $keyword
    }
    return $normalized
}

proc lite_ced::inspect_cell {cell keywords {limit 6} {offset 0}} {
    set cell [identity $cell]
    set keywords [keywords_check $keywords]
    if {![string is integer -strict $limit] || $limit<1 || $limit>12 ||
        ![string is integer -strict $offset] || $offset<0} {error "INSPECT_FAIL:limit 1..12; offset >= 0"}
    set results {}
    foreach keyword $keywords {
        set result [ipcfg_ced::find_params $cell $keyword]
        if {[regexp {^FP:(FLAT|NESTED)} $result -> shape]} {
            set names [lindex $result end]
            set bounded [dict create shape $shape matched [llength $names] offset $offset                 names [lrange $names $offset [expr {$offset+$limit-1}]] more [expr {$offset+$limit<[llength $names]}]]
            if {[regexp {container=(\S+)} $result -> container]} {dict set bounded container $container}
            dict set results $keyword $bounded
        } else {dict set results $keyword $result}
    }
    set path [evidence_path $cell discovery]
    ipcfg_ced::write_evidence $path $results
    return [list LIVE_META:OK results $results evidence $path]
}

proc lite_ced::vector {current updates {encoding scalar}} {
    if {$encoding ni {scalar boolean}} {error "VECTOR_FAIL:encoding must be scalar or boolean"}
    set values [split $current ,]
    if {$current eq ""} {error "VECTOR_FAIL:nonempty current vector required"}
    dict for {index wanted} $updates {
        if {![string is integer -strict $index] || $index < 0 || $index >= [llength $values]} {
            error "VECTOR_FAIL:index $index outside length [llength $values]"
        }
        if {$encoding eq "boolean"} {set wanted [expr {[boolean $wanted] ? "true" : "false"}]}
        lset values $index $wanted
    }
    return [join $values ,]
}


# Preserve enum labels, not only their encoded values. Large lists remain explicit.
proc lite_ced::enum_labels {block {offset 0}} {
    set entries {}; set active 0
    foreach line [split $block \n] {
        if {[regexp {^Range List:\s*(.*)$} $line -> rest]} {set active 1} elseif {$active && [regexp {^\s+(\S.*)$} $line -> rest]} {
        } else {set active 0;continue}
        set rest [string trim $rest]
        if {$rest ne "" && [regexp {^(\S+)(?:\s+(.*))?$} $rest -> value label]} {
            lappend entries [list $value $label]
        }
    }
    return [dict create count [llength $entries] offset $offset shown [lrange $entries $offset [expr {$offset+5}]] more [expr {$offset+6<[llength $entries]}]]
}

# Exact selected keys, with optional exact dictionary entries, never a full dump.
proc lite_ced::packaged_parameters {hint keys} {
    variable root; variable help_cache; variable loaded_signature
    if {$root eq ""} {error "STATE_FAIL:call lite_ced::init first"}
    set resolution [resolve $hint]
    set vlnv [string range $resolution 5 end]
    set defs [get_ipdefs -all -quiet $vlnv]
    if {[llength $defs]!=1} {error "HELP_UNAVAILABLE:exact catalog definition required"}
    set xml [get_property XML_FILE_NAME [lindex $defs 0]]
    if {![file isfile $xml]} {error "HELP_UNAVAILABLE:catalog XML missing"}
    set names {}
    foreach key $keys {
        if {![regexp {^CONFIG\.([A-Za-z0-9_]+)$} $key -> name]} {error "DETAIL_FAIL:exact CONFIG key required"}
        lappend names $name
    }
    set identity [list [version] [get_property PART [current_project]] $vlnv $xml [file mtime $xml] [file size $xml] [lsort -unique $names]]
    if {![dict exists $help_cache $identity]} {
        # Vivado's native IP creation can source framework code into the caller
        # namespace and overwrite a generic variable named dir.
        set helper [file join [file dirname [lindex $loaded_signature 0]] .. scripts parameter_help.py]
        set data [exec python3 -B $helper $xml {*}$names]
        dict size $data
        dict set help_cache $identity [dict create identity $identity vlnv $vlnv xml $xml parameters $data]
    }
    return [dict get $help_cache $identity]
}

# Install before first customization in the live IP interpreter. Collect only
# selected tooltip text; no GUI handles, prior-run files, or answer recipes.
proc lite_ced::watch_gui_help {hint ip_interp keys} {
    variable gui_help_watches;variable serial
    if {![llength $keys] || [llength $keys]>4 || $ip_interp ni [interp slaves]} {error "HELP_FAIL:live IP interpreter and 1..4 exact keys required"}
    set names {}
    foreach key $keys {if {![regexp {^CONFIG\.([A-Za-z0-9_]+)$} $key -> name]} {error "HELP_FAIL:exact CONFIG key required"};lappend names $name}
    set vlnv [string range [resolve $hint] 5 end]
    set identity [list [version] [get_property PART [current_project]] $vlnv]
    set instance [interp eval $ip_interp {ipgui::current_instname}]
    set tag [list $identity $instance [incr serial]]
    set script {
        proc ::ipcfg_selected_help_capture {command code result operation} {
            set names @NAMES@
            if {$code!=0 || [string tolower [lindex $command 1]] ne "tooltip"} {return}
            catch {
                foreach name [lindex $command 3] {
                    if {$name ni $names} {continue}
                    set p [ipgui::get_guiparamspec -of [ipgui::current_inst] $name]
                    dict set ::ipcfg_selected_help_captured $name [get_property TOOLTIP $p]
                    set ::ipcfg_selected_help_tag @TAG@
                }
            }
            return
        }
        foreach setter {::set_property ::ipcust::set_property ::xit::set_property} {
            if {![llength [info commands $setter]]} {continue}
            catch {trace remove execution $setter leave ::ipcfg_selected_help_capture}
            trace add execution $setter leave ::ipcfg_selected_help_capture
        }
    }
    interp eval $ip_interp [string map [list @NAMES@ [list $names] @TAG@ [list $tag]] $script]
    interp eval $ip_interp {set ::ipcfg_selected_help_captured {}}
    dict set gui_help_watches $identity [dict create tag $tag instance $instance keys $keys]
    return [list HELP:WATCHING instance $instance keys $keys scope {open the actual GUI before requesting detail}]
}

proc lite_ced::selected_gui_help {hint} {
    variable gui_help_watches
    set identity [list [version] [get_property PART [current_project]] [string range [resolve $hint] 5 end]]
    if {![dict exists $gui_help_watches $identity]} {return {}}
    set watch [dict get $gui_help_watches $identity]
    set rows {}
    # GUI initialization can clone/replace IP interpreters. Read plain tagged
    # data only; querying current_instname on old native scopes can crash Vivado.
    foreach child [interp slaves] {
        if {[catch {interp eval $child {
            if {[info exists ::ipcfg_selected_help_tag] && [info exists ::ipcfg_selected_help_captured]} {
                list $::ipcfg_selected_help_tag $::ipcfg_selected_help_captured
            } else {list {} {}}
        }} data] || [lindex $data 0] ne [dict get $watch tag]} {continue}
        dict for {name text} [lindex $data 1] {
            dict set rows $name [dict create text $text instance [dict get $watch instance] scope gui_instance_at_capture]
        }
    }
    if {[dict size $rows]} {
        dict set gui_help_watches $identity snapshot $rows
    } elseif {[dict exists $watch snapshot]} {
        set rows [dict get $watch snapshot]
    }
    return $rows
}

proc lite_ced::detail {hint keys {subkeys {}} {enum_offset 0}} {
    if {![llength $keys] || [llength $keys]>4 || [llength $subkeys]>12 || ![string is integer -strict $enum_offset] || $enum_offset<0} {
        error "DETAIL_FAIL:select 1..4 exact CONFIG keys and at most 12 subkeys"
    }
    # Snapshot selected text before native creation can replace the GUI scope.
    set gui_help {};catch {set gui_help [selected_gui_help $hint]}
    set metadata [metadata $hint];set file [dict get $metadata file]
    set blocks [parameter_blocks $file];set selected {};set missing {};set full_help {}
    set help_record {};set help_status available
    if {[catch {packaged_parameters $hint $keys} help_record]} {set help_record {};set help_status unavailable}
    foreach key $keys {
        if {![regexp {^CONFIG\.([A-Za-z0-9_]+)$} $key -> name]} {error "DETAIL_FAIL:exact CONFIG key required"}
        if {![dict exists $blocks $name]} {lappend missing $key;continue}
        set block [dict get $blocks $name];set fields {}
        foreach field_name {{Display Name} Description Type {Default Value} {Current Value} Enabled Disabled} {
            set value [field $block $field_name]
            if {[string length $value]>240} {
                dict set fields ${field_name}_truncated 1
                set value [string range $value 0 239]
            }
            dict set fields $field_name $value
        }
        set range [ipcfg_ced::param_range $file $name]
        if {[string length $range]>240} {dict set fields range_truncated 1;set range [string range $range 0 239]}
        dict set fields range $range
        dict set fields range_source [expr {[string match RANGE_NUM:* $range] || [string match RANGE_LIST:* $range] ? "native_metadata" : "unavailable"}]
        dict set fields range_scope native_default_instance
        set help [field $block Description];set source native_description
        if {$help eq "" && [dict exists $help_record parameters $name]} {
            set help [dict get $help_record parameters $name help];set source packaged_description
        }
        if {[dict exists $gui_help $name] && [dict get $gui_help $name text] ne ""} {
            set help [dict get $gui_help $name text];set source gui_tooltip
            dict set fields help_scope [dict get $gui_help $name scope]
            dict set fields help_instance [dict get $gui_help $name instance]
        }
        if {$help eq ""} {set source unavailable}
        dict set full_help $key [dict create text $help source $source]
        dict set fields help_source $source
        dict set fields help $help
        if {[string length $help]>240} {dict set fields help_truncated 1;dict set fields help [string range $help 0 239]}
        dict set fields enums [enum_labels $block $enum_offset]
        foreach subkey $subkeys {
            set value [ipcfg_ced::param_dict $file $name $subkey]
            if {[string length $value]>240} {dict set fields subkey_truncated $subkey 1;set value [string range $value 0 239]}
            dict set fields subkeys $subkey $value
        }
        dict set selected $key $fields
    }
    set path [evidence_path metadata detail]
    ipcfg_ced::write_evidence $path [dict create metadata $metadata parameters $selected missing $missing full_help $full_help packaged_help $help_record help_status $help_status gui_help $gui_help]
    return [list DETAIL:OK parameters $selected missing $missing evidence $path]
}

# Unified selected help + ranges, including IPI-only IPs that cannot create_ip.
proc lite_ced::describe {hint keys args} {
    # Accept the documented key list and common separate-key calls without
    # relaxing exact-name or selection bounds. A final integer is enum paging.
    set enum_offset 0
    if {[llength $args] && [string is integer -strict [lindex $args end]]} {
        set enum_offset [lindex $args end];set args [lrange $args 0 end-1]
    }
    foreach key $args {
        if {![regexp {^CONFIG\.([A-Za-z0-9_]+)$} $key]} {error "DETAIL_FAIL:exact CONFIG keys in a Tcl list; optional integer enum_offset"}
        lappend keys $key
    }
    if {![llength $keys] || [llength $keys]>4 || ![string is integer -strict $enum_offset] || $enum_offset<0} {
        error "DETAIL_FAIL:select 1..4 exact CONFIG keys; enum_offset >= 0"
    }
    foreach key $keys {if {![regexp {^CONFIG\.([A-Za-z0-9_]+)$} $key]} {error "DETAIL_FAIL:exact CONFIG key required"}}
    if {![catch {detail $hint $keys {} $enum_offset} result options]} {return $result}
    if {![string match METADATA_UNAVAILABLE:* $result]} {return -options $options $result}
    set native_failure $result
    set record [packaged_parameters $hint $keys];set selected {};set missing {}
    set gui_help {};catch {set gui_help [selected_gui_help $hint]}
    foreach key $keys {
        set name [string range $key 7 end]
        if {![dict exists $record parameters $name]} {lappend missing $key;continue}
        set row [dict get $record parameters $name]
        if {[dict exists $gui_help $name] && [dict get $gui_help $name text] ne ""} {
            dict set row help [dict get $gui_help $name text];dict set row help_source gui_tooltip
            dict set row help_instance [dict get $gui_help $name instance];dict set row help_scope [dict get $gui_help $name scope]
        }
        set choices [dict get $row choices];dict unset row choices
        set total [llength $choices]
        set page [lmap c [lrange $choices $enum_offset [expr {$enum_offset+5}]] {list [dict get $c value] [dict get $c label]}]
        dict set row enums [dict create count $total offset $enum_offset shown $page more [expr {$enum_offset+6<$total}]]
        dict set row range_source [expr {$total ? "packaged_choices" : "unavailable"}]
        dict set row range [expr {$total ? "RANGE_LIST:[join [lmap c $page {lindex $c 0}] { }]" : "RANGE_NONE"}]
        dict set row range_scope packaged_not_live
        dict set row range_complete [expr {$enum_offset==0 && $total<=6}]
        dict set row native_only 1
        if {[dict get $row help] eq ""} {dict set row help_source unavailable}
        dict for {field value} $row {
            if {$field in {label help default range} && [string length $value]>240} {dict set row $field [string range $value 0 239];dict set row ${field}_truncated 1}
        }
        if {[dict exists $row range_truncated]} {dict set row range_complete 0}
        dict set selected $key $row
    }
    set path [evidence_path metadata describe]
    ipcfg_ced::write_evidence $path [dict create metadata $record native_unavailable $native_failure parameters $selected missing $missing gui_help $gui_help]
    return [list DETAIL:PACKAGED vlnv [dict get $record vlnv] parameters $selected missing $missing evidence $path]
}

# Direct graph predecessors only; missing publication is not proof of no gating.
proc lite_ced::gates {hint keywords {limit 6} {offset 0}} {
    set keywords [keywords_check $keywords]
    if {![string is integer -strict $limit] || $limit<1 || $limit>12 ||
        ![string is integer -strict $offset] || $offset<0} {error "GATES_FAIL:keywords, limit 1..12, offset >= 0 required"}
    set metadata [metadata $hint];set file [dict get $metadata file]
    set published [ipcfg_ced::enablement_ports $file]
    if {![string match PE_PORTS:* $published]} {return [list GATES:UNAVAILABLE reason $published]}
    if {$published eq "PE_PORTS:none"} {return [list GATES:UNAVAILABLE reason {IP publishes no port gating}]}
    set ranked {}
    foreach port [string range $published 9 end] {
        set score 0
        foreach keyword $keywords {
            if {[string equal -nocase $keyword $port]} {incr score 100}
            if {[string first [string tolower $keyword] [string tolower $port]]>=0} {incr score}
        }
        if {$score>0} {lappend ranked [list $score $port]}
    }
    # Exact requested interfaces precede their signal variants; richer matches
    # keep another requested interface from being crowded out by one family.
    set matches [lmap entry [lsort -integer -decreasing -index 0 $ranked] {lindex $entry 1}]
    set detail {}
    foreach port [lrange $matches $offset [expr {$offset+$limit-1}]] {dict set detail $port [ipcfg_ced::port_enablers $file $port]}
    set path [evidence_path metadata gates]
    ipcfg_ced::write_evidence $path [dict create metadata $metadata published_count [llength [string range $published 9 end]] dependencies $detail]
    return [list GATES:OK matched [llength $matches] offset $offset shown [dict size $detail] \
        more [expr {$offset+[dict size $detail]<[llength $matches]}] dependencies $detail evidence $path]
}


proc lite_ced::ip_name {hint} {
    set name [string trim $hint]
    # Catalog identity is a name, not vendor/library/version tokens. Accept the
    # pipe spelling sometimes supplied by clients without inventing aliases.
    foreach separator {: |} {
        set parts [split $name $separator]
        if {[llength $parts] in {3 4} && [regexp {^[0-9]+(\.[0-9]+)*$} [lindex $parts end]]} {
            set name [lindex $parts end-1]
            break
        }
    }
    return [string tolower [regsub -all {\s+} $name _]]
}

# Record release-database part support for one part, so part_available and supported_examples
# answer without reading SUPPORTED_PARTS (up to 100 s per definition). Unlisted IPs stay live.
proc lite_ced::seed_support {part table} {
    variable support_cache
    if {[string first / $part] >= 0 || $part eq ""} {error "SUPPORT_FAIL:exact part required"}
    dict set support_cache $part $table
    return [list SUPPORT:SEEDED part $part ips [dict size $table]]
}

proc lite_ced::cached_support {vlnv} {
    variable support_cache
    if {[catch {get_property PART [current_project -quiet]} part] || ![dict exists $support_cache $part $vlnv]} {return {}}
    return [dict get $support_cache $part $vlnv]
}

# The bundled ipcfg library, loaded on first use when lite_ced::init has not loaded it yet.
proc lite_ced::_need_ipcfg {} {
    if {[llength [info commands ::ipcfg_ced::part_status]]} {return}
    uplevel #0 [list source -notrace [file normalize [file join $::lite_ced::dir ipcfg ipcfg.tcl]]]
}

proc lite_ced::part_available {vlnv} {
    lite_ced::_need_ipcfg
    set cached [cached_support $vlnv]
    if {[llength $cached]} {return [lindex $cached 0]}
    # The published SUPPORTED_PARTS gate, remembered per Vivado build (ipcfg_ced::part_status): the
    # first read of some definitions takes minutes on a release with no database (2026.1).
    # Missing metadata is unknown, rather than evidence of incompatibility.
    if {[catch {
        set def [lindex [lsort -dictionary [get_ipdefs -all -quiet $vlnv]] end]
        set status [ipcfg_ced::part_status $def]
    }]} {return UNKNOWN}
    return $status
}

# Installed parts that an IP definition publishes as supported, one per device family.
proc lite_ced::supported_examples {vlnv {limit 4}} {
    set cached [cached_support $vlnv]
    if {[lindex $cached 0] eq "WRONG_PART"} {
        return [list supported_count [lindex $cached 1] example_parts [lrange [lindex $cached 2] 0 [expr {$limit-1}]]]
    }
    lite_ced::_need_ipcfg
    return [ipcfg_ced::part_examples [lindex [lsort -dictionary [get_ipdefs -all -quiet $vlnv]] end] $limit]
}

# Before any cell exists, retarget the project to a part the chosen IP supports.
proc lite_ced::set_part {vlnv part} {
    if {[llength [get_bd_cells -quiet *]]} {error "PART_FAIL:the block design already has cells"}
    if {![llength [get_ipdefs -all -quiet $vlnv]]} {error "PART_FAIL:unknown IP $vlnv"}
    if {$part ni [get_property -quiet SUPPORTED_PARTS [get_ipdefs -all -quiet $vlnv]]} {
        error "PART_FAIL:$vlnv does not support $part; use one of its example_parts"
    }
    if {![llength [get_parts -quiet $part]]} {error "PART_FAIL:$part is not installed"}
    set before [get_property PART [current_project]]
    set_property part $part [current_project]
    if {[get_property PART [current_project]] ne $part} {error "PART_FAIL:project part did not change"}
    return [list PART:OK part $part previous $before]
}

proc lite_ced::restore_part {part} {
    if {[llength [get_bd_cells -quiet *]]} {error "PART_FAIL:the block design still has cells"}
    if {![llength [get_parts -quiet $part]]} {error "PART_FAIL:$part is not installed"}
    set before [get_property PART [current_project]]
    set_property part $part [current_project]
    if {[get_property PART [current_project]] ne $part} {error "PART_FAIL:project part did not change"}
    return [list PART:RESTORED part $part previous $before]
}

proc lite_ced::display_names {} {
    # Lower-case catalog display name per visible IP definition, read once per session.
    variable display_names
    if {![info exists display_names]} {
        set display_names {}
        catch {
            set defs [get_ipdefs -quiet]
            set labels [get_property -quiet DISPLAY_NAME $defs]
            if {[llength $labels]==[llength $defs]} {
                foreach def $defs label $labels {dict set display_names $def [string tolower $label]}
            }
        }
    }
    return $display_names
}

proc lite_ced::latest_versions {vlnvs} {
    set latest {}
    foreach vlnv [lsort -dictionary -unique $vlnvs] {
        dict set latest [join [lrange [split $vlnv :] 0 2] :] $vlnv
    }
    return [dict values $latest]
}

proc lite_ced::catalog {hint {limit 6} {offset 0} {check_availability 1}} {
    if {[string trim $hint] eq "" || ![string is integer -strict $limit] || $limit<1 || $limit>12 ||
        ![string is integer -strict $offset] || $offset<0 || ![string is boolean -strict $check_availability]} {
        error "CATALOG_FAIL:hint, limit 1..12, offset >= 0 and boolean availability required"
    }
    if {[regexp {^[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+$} $hint] &&
        ![catch {get_ipdefs -all -quiet $hint} defs] && [llength $defs]==1 && [lindex $defs 0] eq $hint} {
        if {[part_available $hint] eq "WRONG_PART"} {return [list CATALOG:UNAVAILABLE vlnv $hint availability WRONG_PART {*}[supported_examples $hint]]}
        return [list CATALOG:EXACT vlnv $hint]
    }
    set name_hint [ip_name $hint]
    set result [ipcfg_ced::resolve_vlnv $name_hint]
    if {[string match VLNV:* $result]} {
        set vlnv [string range $result 5 end]
        if {[part_available $vlnv] eq "WRONG_PART"} {return [list CATALOG:UNAVAILABLE vlnv $vlnv availability WRONG_PART {*}[supported_examples $vlnv]]}
        return [list CATALOG:EXACT vlnv $vlnv]
    }
    # Users and models usually name an IP by its catalog display name.
    set labels [display_names]
    set wanted [regsub -all {[^a-z0-9]} $name_hint ""]
    set named [latest_versions [dict keys [dict filter $labels script {vlnv label} {
        expr {[regsub -all {[^a-z0-9]} $label ""] eq $wanted}
    }]]]
    if {[llength $named]==1 && [part_available [lindex $named 0]] ne "WRONG_PART"} {
        return [list CATALOG:EXACT vlnv [lindex $named 0]]
    }
    set candidates {}
    if {[string match VLNV_CANDIDATES:* $result]} {set candidates [lindex [string range $result 16 end] 0]}
    set terms [lsort -unique [lsearch -all -inline -regexp [regexp -all -inline {[[:alnum:]]+} $name_hint] {^[[:alnum:]]{2,}$}]]
    dict for {vlnv label} $labels {
        foreach term $terms {
            if {[string first $term $label]>=0} {lappend candidates $vlnv;break}
        }
    }
    if {![llength $candidates]} {return [list CATALOG:NONE hint $hint reason $result]}
    # A term found in many catalog labels (a family or bus name) identifies less than a rare one.
    set frequency {}
    foreach term $terms {
        set count 0
        dict for {vlnv label} $labels {if {[string first $term $label]>=0} {incr count}}
        dict set frequency $term [expr {max($count,1)}]
    }
    set ranked {}
    foreach vlnv [latest_versions $candidates] {
        set name [string tolower [lindex [split $vlnv :] 2]];set hits {};set weight 0.0
        set label [expr {[dict exists $labels $vlnv] ? [dict get $labels $vlnv] : ""}]
        foreach term $terms {
            if {[string first $term $name]>=0 || [string first $term $label]>=0} {
                lappend hits $term;set weight [expr {$weight+1.0/[dict get $frequency $term]}]
            }
        }
        set extra [llength [lsearch -all -inline -not -regexp [regexp -all -inline {[[:alnum:]]+} $label] ^([join $terms |])$]]
        lappend ranked [list [expr {-[llength $hits]}] [expr {-$weight}] $extra $vlnv $hits $label]
    }
    if {[llength [lsearch -all -index 0 -not $ranked 0]]} {set ranked [lsearch -all -inline -index 0 -not $ranked 0]}
    set ranked [lsort -integer -index 0 [lsort -real -index 1 [lsort -integer -index 2 [lsort -index 3 $ranked]]]]
    set ranked [lmap item $ranked {lrange $item 3 end}];set shown {}
    set shortlist [lrange $ranked $offset [expr {$offset+$limit-1}]]
    # SUPPORTED_PARTS can load an IP definition. Check only this ranked page;
    # the chosen identity is checked again before any cell is created.
    set unavailable {}
    foreach item $shortlist {
        set row [dict create vlnv [lindex $item 0] matched_terms [lindex $item 1]]
        if {[lindex $item 2] ne ""} {dict set row display_name [lindex $item 2]}
        if {$check_availability && [part_available [lindex $item 0]] eq "WRONG_PART"} {
            lappend unavailable [dict merge $row [supported_examples [lindex $item 0]]]
            continue
        }
        lappend shown $row
    }
    return [list CATALOG:CANDIDATES matched [llength $ranked] offset $offset shown $shown \
        more [expr {$offset+[llength $shortlist]<[llength $ranked]}] choice_required 1 \
        availability_scope [expr {$check_availability ? "ranked_page_only" : "deferred"}] \
        compatibility_checks [expr {$check_availability ? [llength $shortlist] : 0}] unavailable $unavailable]
}

# Check only the candidates that survive cross-query ranking. Loading
# SUPPORTED_PARTS is expensive for some definitions, so checking each fuzzy
# query page before it is merged wastes most of the begin budget.
# Without a release database: the supported IP in the unsupported IP's catalog category with
# the same interface set (the database's same_category, from the same TAXONOMY and INTERFACES
# properties). On Versal 2026.1 this is what offers SmartConnect for the AXI Protocol Converter.
# A list of {vlnv name display_name description} dicts, or empty when the pool exceeds 16.
proc lite_ced::same_category_live {vlnv {limit 16}} {
    lite_ced::_need_ipcfg
    set def [lindex [lsort -dictionary [get_ipdefs -all -quiet $vlnv]] end]
    if {$def eq ""} {return {}}
    set taxonomy [get_property -quiet TAXONOMY $def]
    if {$taxonomy eq ""} {return {}}
    set wanted [lsort -unique [split [string map {, " "} [get_property -quiet INTERFACES $def]]]]
    set own [lindex [split $def :] 2]
    set latest [dict create]
    foreach other [get_ipdefs -all -quiet *] {
        set name [lindex [split $other :] 2]
        if {$name eq $own || [get_property -quiet TAXONOMY $other] ne $taxonomy} {continue}
        if {[lsort -unique [split [string map {, " "} [get_property -quiet INTERFACES $other]]]] ne $wanted} {continue}
        if {![dict exists $latest $name] || [lindex [lsort -dictionary [list [dict get $latest $name] $other]] end] eq $other} {
            dict set latest $name $other
        }
    }
    set pool {}
    dict for {name other} $latest {
        if {[ipcfg_ced::part_status $other] ne "AVAILABLE"} {continue}
        lappend pool [dict create vlnv [join [lrange [split $other :] 0 2] :] name $name \
            display_name [get_property -quiet DISPLAY_NAME $other] \
            description [string range [get_property -quiet DESCRIPTION $other] 0 199]]
    }
    return [expr {[llength $pool] <= $limit ? $pool : {}}]
}

proc lite_ced::candidate_availability {vlnvs} {
    if {![llength $vlnvs] || [llength $vlnvs] > 6} {error "AVAILABILITY_FAIL:1..6 VLNVs required"}
    set rows {}
    foreach vlnv $vlnvs {
        if {![regexp {^[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+$} $vlnv]} {
            error "AVAILABILITY_FAIL:invalid VLNV"
        }
        set status [part_available $vlnv]
        set row [dict create vlnv $vlnv status $status]
        if {$status eq "WRONG_PART"} {
            set row [dict merge $row [supported_examples $vlnv]]
            set pool [same_category_live $vlnv]
            if {[llength $pool]} {dict set row same_category $pool}
        }
        lappend rows $row
    }
    return [list AVAILABILITY:OK rows $rows]
}

proc lite_ced::resolve {hint} {
    # Exact hidden IPI subcores are catalog metadata too; never expand a fuzzy hint here.
    if {[regexp {^[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+:[[:alnum:]_.-]+$} $hint] &&
        ![catch {get_ipdefs -all -quiet $hint} defs] && [llength $defs]==1 && [lindex $defs 0] eq $hint} {
        return VLNV:$hint
    }
    set result [ipcfg_ced::resolve_vlnv [ip_name $hint]]
    if {[string match VLNV:* $result]} {return $result}
    error "DISCOVERY_FAIL:[catalog $hint]; replace the IP hint with a chosen catalog VLNV, not different feature keywords"
}


# Naming convention only: CONFIG.<NAME>_CONFIG may hold the parameters of a
# catalog definition named <name>. Callers still validate overlap and readback.
proc lite_ced::sibling_definition {key} {
    if {![regexp {^CONFIG\.([A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)_CONFIG$} $key -> name]} {return SIBLING:NONE}
    set defs [lsort -dictionary [get_ipdefs -all -quiet "*:*:[string tolower $name]:*"]]
    if {![llength $defs]} {return SIBLING:NONE}
    set def [lindex $defs end]
    set xml [get_property XML_FILE_NAME $def]
    if {![file isfile $xml]} {return SIBLING:NONE}
    return [list SIBLING:OK vlnv [get_property VLNV $def] xml $xml]
}

proc lite_ced::sibling_field {key subkey vlnv} {
    set found [sibling_definition $key]
    if {[lindex $found 0] ne "SIBLING:OK" || [dict get [lrange $found 1 end] vlnv] ne $vlnv} {return 0}
    if {[catch {packaged_parameters $vlnv [list CONFIG.$subkey]} record]} {return 0}
    return [dict exists [dict get $record parameters] $subkey]
}

# A readable derived entry alone does not prove that it is writable.
proc lite_ced::documented_subkey {hint object key subkey requirements} {
    foreach r $requirements {
        if {[dict get $r key] ne $key || ![dict exists $r subkey] || [dict get $r subkey] ne $subkey} {continue}
        if {[dict exists $r mapping_scope] && [dict get $r mapping_scope] eq "native_same_name_candidate" &&
            [dict exists $r native_key] && [native_field_mapping $hint $key $subkey [dict get $r native_key]]} {
            # Validate the schema again; candidate generation is not a verdict.
            # Native capture and the pinned field readback still determine success.
            return 1
        }
        if {[dict exists $r mapping_scope] && [dict get $r mapping_scope] eq "sibling_definition_candidate" &&
            [dict exists $r source_vlnv] && [sibling_field $key $subkey [dict get $r source_vlnv]]} {
            return 1
        }
        if {
            ![dict exists $r write_doc] || [string trim [dict get $r write_doc]] eq ""} {continue}
        if {[dict exists $r read_key]} {
            set read_key [dict get $r read_key]
            if {![regexp {^CONFIG\.[A-Za-z0-9_]+$} $read_key] || $read_key ni [list_property $object]} {continue}
            set read_subkey $subkey
            if {[dict exists $r read_subkey]} {set read_subkey [dict get $r read_subkey]}
            set derived [get_property $read_key $object]
            if {![catch {dict exists $derived $read_subkey} exists] && $exists} {return 1}
        } elseif {[dict exists $r native_key]} {
            # Managed-IP metadata can expose a resolved dictionary absent from the BD.
            if {![regexp {^CONFIG\.([A-Za-z0-9_]+)$} [dict get $r native_key] -> name] ||
                [catch {metadata $hint} native]} {continue}
            set native_subkey $subkey
            if {[dict exists $r native_subkey]} {set native_subkey [dict get $r native_subkey]}
            set known [ipcfg_ced::param_dict [dict get $native file] $name $native_subkey]
            if {[string match PD:* $known]} {return 1}
        }
    }
    return 0
}


proc lite_ced::inspect_for {hint cell keywords {limit 6} {offset 0}} {
    set keywords [keywords_check $keywords]
    set cell [identity $cell]
    set created [open_cell $hint $cell]
    if {[lindex $created 0] ne "CELL:OK"} {return $created}
    if {[catch {inspect $hint $keywords $limit $offset} result]} {
        if {[string match METADATA_UNAVAILABLE:* $result]} {
            return [list LIVE_FALLBACK reason $result result [inspect_cell $cell $keywords $limit $offset]]
        }
        error $result
    }
    set data [lrange $result 1 end]
    set properties [list_property [get_bd_cells $cell]]
    set candidates {}
    foreach candidate [dict get $data candidates] {
        set key [dict get $candidate key]
        set exists [expr {$key in $properties}]
        dict set candidate bd_property_exists $exists
        if {$exists} {
            set current [get_property $key [get_bd_cells $cell]]
            catch {dict unset candidate current_truncated}
            if {[string length $current]>160} {dict set candidate current_truncated 1;set current [string range $current 0 159]}
            dict set candidate current $current
        }
        lappend candidates $candidate
    }
    dict set data candidates $candidates
    set dict_candidates {}
    foreach property $properties {
        if {![string match CONFIG.* $property]} {continue}
        set value [get_property $property [get_bd_cells $cell]]
        if {![catch {dict size $value} count] && $count>0} {lappend dict_candidates $property}
    }
    set dict_candidates [lsort $dict_candidates]
    dict set data live_dict_candidates [lrange $dict_candidates $offset [expr {$offset+$limit-1}]]
    dict set data live_dict_count [llength $dict_candidates]
    dict set data live_dict_more [expr {$offset+$limit<[llength $dict_candidates]}]
    dict set data cell $cell
    dict set data scope {native managed-IP metadata; bd_property_exists tests live visibility, not semantics}
    dict set data native_evidence [dict get $data evidence]
    set path [evidence_path $cell selected]
    dict set data evidence $path
    ipcfg_ced::write_evidence $path $data
    return [linsert $data 0 META:OK]
}

set ::lite_ced::loaded_signature $::lite_ced::source_candidate
unset ::lite_ced::source_candidate
