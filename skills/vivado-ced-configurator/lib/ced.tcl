# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Example-design (CED) helpers for the structured CED workflow. The server
# passes every value as data; these procs never evaluate model text. The CED's
# own Tcl (addOptions, updaters, createDesign) runs inside Vivado exactly as the
# New Project wizard runs it.
#
# Replies never carry Vivado first-class objects: freeing a stored object after
# the project changes corrupts Vivado's heap. Every returned value passes
# through cedcfg::plain.
namespace eval cedcfg {
    variable root ""
    variable native_log ""
}

# A fresh string with no Vivado object behind it.
proc cedcfg::plain {value} {
    return [encoding convertfrom utf-8 [encoding convertto utf-8 $value]]
}

# The run root holds evidence/, snapshots/ and proj/.
#
# Vivado's working directory is meant to be <run>/work, so the root is its
# parent. Take the parent only when the working directory is that `work`
# folder: started anywhere else, its parent is not ours to write in. On a test
# host it is the directory holding every case's workspace, so each run would
# build its project on top of the last one's. A working directory that is not
# `work` is its own root, which keeps every run inside it.
proc cedcfg::run_root {native_log} {
    set workdir [file dirname [file dirname $native_log]]
    if {[file tail $workdir] eq "work"} {set workdir [file dirname $workdir]}
    return [file normalize $workdir]
}

# The configured MCP places each native log under working_dir/.vivado-ai.
proc cedcfg::begin {native_log} {
    variable root
    if {[file tail [file dirname $native_log]] ne ".vivado-ai" || ![file isfile $native_log]} {
        error "INIT_FAIL:expected the exact log_file from vivado_start"
    }
    set candidate [cedcfg::run_root $native_log]
    if {$root ne "" && $root ne $candidate} {error "INIT_FAIL:already initialized for another scratch run"}
    if {$root eq "" && [current_project -quiet] ne ""} {error "INIT_FAIL:a project is already open; use a fresh session"}
    set root $candidate
    set ::cedcfg::native_log [file normalize $native_log]
    foreach d {evidence snapshots} {file mkdir [file join $root $d]}
    # Boards outside Vivado's own repository (a custom board such as the Rave2
    # Pro) resolve as targets once their board files are on the repo path.
    if {[info exists ::env(CED_BOARD_REPO)] && $::env(CED_BOARD_REPO) ne ""} {
        set paths [get_param board.repoPaths]
        foreach p [split $::env(CED_BOARD_REPO) :] {
            if {$p ne "" && [file isdirectory $p] && [lsearch -exact $paths $p] < 0} {lappend paths $p}
        }
        set_param board.repoPaths $paths
    }
    return [list BEGIN:OK root $root version [lindex [split [version] \n] 0]]
}

proc cedcfg::log_size {} {
    variable native_log
    if {$native_log eq "" || ![file isfile $native_log]} {return 0}
    return [file size $native_log]
}

# Native messages written since a log offset: counts plus the first few lines.
proc cedcfg::messages_since {offset} {
    variable native_log
    set counts [dict create errors 0 critical_warnings 0]
    set first {}
    if {$native_log eq "" || ![file isfile $native_log]} {return [dict create counts $counts first $first available 0]}
    set ch [open $native_log r]
    try {seek $ch $offset; set text [read $ch]} finally {close $ch}
    foreach line [split $text \n] {
        set kind ""
        if {[string match "ERROR:*" $line]} {set kind errors}
        if {[string match "CRITICAL WARNING:*" $line]} {set kind critical_warnings}
        if {$kind eq ""} {continue}
        dict incr counts $kind
        if {[llength $first] < 6} {lappend first [string range $line 0 299]}
    }
    return [dict create counts $counts first $first available 1]
}

# Board candidates by exact or substring board name, or one exact installed part.
proc cedcfg::targets {text} {
    set key [string tolower [string trim $text]]
    if {![regexp {^[a-z0-9_.:+-]{2,64}$} $key]} {error "TARGET_FAIL:use a board name such as zcu102 or an exact part name"}
    set parts [get_parts -quiet $key]
    if {[llength $parts] == 1} {
        set part [plain $parts]
        return [list [dict create kind part name $part part $part family [plain [get_property FAMILY $parts]]]]
    }
    set exact {}; set partial {}
    foreach object [get_board_parts -quiet -latest_file_version] {
        set bp [plain $object]
        set short [lindex [split $bp :] 1]
        set display [plain [get_property DISPLAY_NAME $object]]
        set row [dict create kind board name $bp board $short display $display part [plain [get_property PART_NAME $object]]]
        if {$short eq $key || [string match "${key}_som" $short]} {lappend exact $row
        } elseif {[string first $key $short] >= 0 || [string first $key [string tolower $display]] >= 0} {lappend partial $row}
    }
    if {[llength $exact]} {return $exact}
    return [lrange $partial 0 5]
}

proc cedcfg::project {part board_part} {
    variable root
    if {$root eq ""} {error "STATE_FAIL:call cedcfg::begin first"}
    if {[current_project -quiet] ne ""} {
        if {[get_property PART [current_project]] ne $part} {error "PROJECT_FAIL:project part differs"}
        return [list PROJECT:REUSED dir [get_property DIRECTORY [current_project]]]
    }
    create_project proj [file join $root proj] -part $part
    if {$board_part ne ""} {set_property BOARD_PART $board_part [current_project]}
    return [plain [list PROJECT:OK dir [file join $root proj] part $part board_part [get_property BOARD_PART [current_project]]]]
}

proc cedcfg::load_repo {path} {
    if {![file isdirectory $path]} {error "REPO_FAIL:missing $path"}
    add_example_design_repo -repo_path $path
    return [list REPO:OK designs [llength [get_example_designs]]]
}

# Live presence and target support for each VLNV. Hidden designs usually lack an installed part.
proc cedcfg::support {vlnvs} {
    set board [get_property BOARD_PART [current_project]]
    set board_key [join [lrange [split $board :] 0 1] :]
    set part [get_property PART [current_project]]
    set rows {}
    foreach vlnv $vlnvs {
        set ed [get_example_designs -quiet $vlnv]
        if {![llength $ed]} {
            lappend rows [dict create vlnv $vlnv present 0]
            continue
        }
        set boards [plain [get_property SUPPORTED_BOARDS $ed]]
        set parts [plain [get_property SUPPORTED_PARTS $ed]]
        set supported unknown
        set part_only [expr {![llength $boards] && [llength $parts]}]
        if {[llength $boards] || [llength $parts]} {
            set supported 0
            foreach b $boards {if {[join [lrange [split $b :] 0 1] :] eq $board_key} {set supported 1}}
            # A part match only counts in a part-targeted project. Versal
            # Extensible Embedded Platform ships in two variants: the board one
            # returns boards from getSupportedBoards, the part one returns ""
            # and takes PROJECT_PARAM.PART. The board's own part is in the part
            # variant's list, but Vivado still refuses it in a board project:
            # "does not support any boards. Please specify a part."
            if {$board_key eq ""} {
                foreach p $parts {if {[string match -nocase "*$part*" $p]} {set supported 1}}
            }
        }
        lappend rows [dict create vlnv $vlnv present 1 supported $supported part_only $part_only \
            boards [lmap b [lrange $boards 0 5] {lindex [split $b :] 1}] board_count [llength $boards] \
            part_count [llength $parts] repo [plain [get_property REPO_DIRECTORY $ed]]]
    }
    return [plain $rows]
}

proc cedcfg::namespace_of {vlnv} {
    set ns "::${vlnv}_example_design"
    if {![namespace exists $ns]} {error "OPTIONS_FAIL:design namespace not loaded for $vlnv"}
    return $ns
}

# Vivado's own evaluated option view for the current board, plus the CED's addOptions dicts.
proc cedcfg::options {vlnv} {
    variable root
    set ed [get_example_designs $vlnv]
    set ns [namespace_of $vlnv]
    set file [file join $root evidence [string map {: _} $vlnv]-options.txt]
    ced::debug_design_info -file_name $file $ed
    set added {}
    catch {set added [${ns}::addOptions $ed [plain [get_property BOARD_PART [current_project]]]]}
    return [plain [dict create file $file add_options $added board_part [get_property BOARD_PART [current_project]] \
        part [get_property PART [current_project]]]]
}

# Run the CED's updater and validater procs to a fixed point over a parameter state.
# updaters: {proc inputs outputs} rows from the CED's own updater headers.
proc cedcfg::evaluate {vlnv state updaters} {
    set ns [namespace_of $vlnv]
    set writes {}; set errors {}; set messages {}
    set pass 0
    while {$pass < 8} {
        incr pass
        set changed 0
        foreach u $updaters {
            lassign $u name inputs outputs
            set proc ${ns}::$name
            if {![llength [info procs $proc]]} {lappend errors [list $name missing_proc]; continue}
            set formal [info args $proc]
            if {$formal ne [concat $inputs $outputs]} {lappend errors [list $name argument_mismatch $formal]; continue}
            set values [lmap a $formal {expr {[dict exists $state $a] ? [dict get $state $a] : ""}}]
            if {[catch {$proc {*}$values} result]} {
                lappend errors [list $name error [string range $result 0 299]]
                continue
            }
            if {[catch {dict size $result}]} {continue}
            dict for {key value} $result {
                if {[string match *.ERRMSG $key]} {
                    if {$value ne ""} {dict set messages $key $value}
                    continue
                }
                if {![dict exists $state $key] || [dict get $state $key] ne $value} {
                    if {[string match *.VALUE $key] && [dict exists $state $key]} {
                        lappend writes [list $key [dict get $state $key] $value $name]
                    }
                    dict set state $key $value
                    set changed 1
                }
            }
        }
        if {!$changed} {break}
    }
    return [dict create state $state writes $writes errors $errors messages $messages passes $pass converged [expr {!$changed}]]
}

proc cedcfg::instantiate {vlnv options kind} {
    set offset [log_size]
    set bd ""
    if {$kind eq "IPI"} {
        if {[current_bd_design -quiet] eq ""} {create_bd_design design_1}
        set bd [current_bd_design]
        set code [catch {instantiate_example_design -design $bd -options $options $vlnv} result]
    } else {
        set code [catch {instantiate_example_design -options $options $vlnv} result]
    }
    set bd [plain [current_bd_design -quiet]]
    set validation none
    if {!$code && $bd ne ""} {
        set validation [expr {[catch {validate_bd_design -force} vresult] ? "FAIL" : "OK"}]
        if {[catch {save_bd_design} saved]} {set validation SAVE_FAIL}
    }
    return [plain [dict create status [expr {$code ? "FAIL" : "OK"}] result [string range $result 0 499] bd $bd \
        validation $validation messages [messages_since $offset]]]
}

proc cedcfg::validate {} {
    set offset [log_size]
    set status [expr {[catch {validate_bd_design -force} result] ? "FAIL" : "OK"}]
    return [dict create status $status result [string range $result 0 299] messages [messages_since $offset]]
}

# IP cells with the external interface ports they reach, for assigning design-level requests.
proc cedcfg::cells {} {
    set rows {}
    foreach cell [lsort [get_bd_cells -quiet -hierarchical]] {
        set object [get_bd_cells $cell]
        set vlnv [get_property VLNV $object]
        if {$vlnv eq "" || [string match *:inline_hdl:* $vlnv]} {continue}
        set ports {}
        foreach pin [get_bd_intf_pins -quiet -of_objects $object] {
            foreach port [get_bd_intf_ports -quiet -of_objects [get_bd_intf_nets -quiet -of_objects $pin]] {
                lappend ports [string trimleft $port /]
            }
        }
        set display ""
        catch {set display [get_property DISPLAY_NAME [lindex [get_ipdefs -quiet -all $vlnv] 0]]}
        lappend rows [plain [dict create path [string trimleft $cell /] vlnv $vlnv display $display ports [lrange [lsort -unique $ports] 0 2]]]
    }
    return [plain $rows]
}

# The cells that drive a cell's input clock pins (any input pin when it has no
# clock input): the place a frequency this cell receives is actually set.
# ced-gen_H3: clk_wiz_0 has no parameter bank, and "set on the cell that
# produces it" sent the model searching; it wrote the DDR4 controller instead
# of ps_e, which drives clk_in1 through pl_clk0.
proc cedcfg::drivers {path} {
    set cell [get_bd_cells -quiet /$path]
    if {![llength $cell]} {return {}}
    set inputs [get_bd_pins -quiet -of_objects $cell -filter {DIR == I && TYPE == clk}]
    if {![llength $inputs]} {set inputs [get_bd_pins -quiet -of_objects $cell -filter {DIR == I}]}
    set rows {}
    foreach pin $inputs {
        foreach source [find_bd_objs -quiet -relation connected_to -thru_hier $pin] {
            if {[get_property DIR $source] ne "O"} {continue}
            set owner [get_bd_cells -quiet -of_objects $source]
            if {![llength $owner]} {continue}
            lappend rows [dict create pin [lindex [split $pin /] end] cell [string trimleft $owner /] \
                source_pin [lindex [split $source /] end]]
        }
    }
    return [plain [lrange $rows 0 7]]
}

# The parameters of a cell that hold the frequency one of its output pins runs at.
# The move to a driving cell names the pin it drives through (ps_e/pl_clk0), and a
# model then searches for the pin's name: ced-gen_H3 (Gemma-31B) described ps_e four
# times as "pl_clk0 frequency", "PL_CLK0_FREQ" and so on, and never reached
# PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ, whose name does not say "clock" at all.
# Vivado already knows the answer: the pin carries FREQ_HZ, and the parameter that
# sets it holds the same number. This returns every frequency-named CONFIG value of
# the cell that matches the pin's frequency, in MHz or Hz, within half a percent (a
# PLL reaches 99.999001 for a requested 100). No IP is named here; choosing among
# several matches is the caller's job.
proc cedcfg::frequency_source {path pin} {
    set cell [get_bd_cells -quiet /$path]
    set object [get_bd_pins -quiet /$path/$pin]
    if {![llength $cell] || ![llength $object]} {return {}}
    set hz [get_property -quiet CONFIG.FREQ_HZ $object]
    if {![string is double -strict $hz] || $hz <= 0} {return {}}
    set mhz [expr {$hz / 1.0e6}]
    set rows {}
    foreach key [list_property $cell CONFIG.*] {
        if {[string match *.VALUE_SRC $key]} {continue}
        set name [string range $key 7 end]
        if {![regexp -nocase {FREQ|MHZ|HZ} $name]} {continue}
        set value [get_property -quiet $key $cell]
        if {![string is double -strict $value] || $value <= 0} {continue}
        foreach scale {1.0 1.0e-6} {
            if {abs($value * $scale - $mhz) <= 0.005 * $mhz} {
                lappend rows [dict create key $name value $value \
                    value_src [get_property -quiet ${key}.VALUE_SRC $cell]]
                break
            }
        }
    }
    return [plain [list pin_mhz [format %.6g $mhz] matches [lrange $rows 0 63]]]
}

# The dual of cedcfg::drivers: the cells this cell drives through its OWN output
# clock pins.  A cell with clock loads generates the clock it hands out, so a
# stated frequency belongs on it and not on whatever feeds its reference input.
# On 2026.2 clk_wiz publishes no parameter bank, so every hint the flow gives
# about it is built from topology rather than metadata; without this the flow
# read clk_wiz_1 as a cell that "does not set its own clock" and pushed the
# 200 MHz requirement upstream to the cell driving clk_in1, which cannot set it.
proc cedcfg::clock_loads {path} {
    set cell [get_bd_cells -quiet /$path]
    if {![llength $cell]} {return {}}
    set rows {}
    foreach pin [get_bd_pins -quiet -of_objects $cell -filter {DIR == O && TYPE == clk}] {
        foreach sink [find_bd_objs -quiet -relation connected_to -thru_hier $pin] {
            if {[get_property DIR $sink] ne "I"} {continue}
            set owner [get_bd_cells -quiet -of_objects $sink]
            if {![llength $owner] || [string trimleft $owner /] eq $path} {continue}
            lappend rows [dict create pin [lindex [split $pin /] end] cell [string trimleft $owner /] \
                sink_pin [lindex [split $sink /] end]]
        }
    }
    return [plain [lrange $rows 0 7]]
}

# Every CONFIG.* value of every IP cell, one base64 record per line, for diffing outside Tcl.
proc cedcfg::snapshot {file} {
    set ch [open $file w]
    set count 0
    try {
        foreach cell [lsort [get_bd_cells -quiet -hierarchical]] {
            set object [get_bd_cells $cell]
            if {[get_property VLNV $object] eq ""} {continue}
            foreach key [list_property $object CONFIG.*] {
                if {[string match *.VALUE_SRC $key]} {continue}
                set value [get_property $key $object]
                set source ""
                catch {set source [get_property $key.VALUE_SRC $object]}
                puts $ch "[string trimleft $cell /]\t$key\t$source\t[binary encode base64 -maxlen 0 [encoding convertto utf-8 $value]]"
                incr count
            }
        }
    } finally {close $ch}
    return [list SNAPSHOT:OK file $file values $count]
}

proc cedcfg::bd_dirs {} {
    set bd [plain [current_bd_design -quiet]]
    if {$bd eq ""} {error "STATE_FAIL:no open block design"}
    set file ""
    foreach f [get_files -quiet $bd.bd] {
        if {[string first .srcs/ $f] >= 0} {set file [plain $f]}
    }
    if {$file eq ""} {error "STATE_FAIL:block design file not found"}
    set project_dir [plain [get_property DIRECTORY [current_project]]]
    set gen [file join $project_dir [plain [get_property NAME [current_project]]].gen sources_1 bd $bd]
    return [list $bd $file [file dirname $file] $gen]
}

# Save the open block design and copy its source and generated folders to snapshots/<tag>.
proc cedcfg::checkpoint {tag} {
    variable root
    if {![regexp {^[A-Za-z0-9_-]+$} $tag]} {error "STATE_FAIL:bad snapshot tag"}
    save_bd_design
    lassign [bd_dirs] bd file src gen
    set target [file join $root snapshots $tag]
    file delete -force $target
    file mkdir $target
    file copy $src [file join $target srcs]
    if {[file isdirectory $gen]} {file copy $gen [file join $target gen]}
    return [list CHECKPOINT:OK tag $tag bd $bd]
}

# Replace the block design on disk with snapshots/<tag>; unsaved edits are discarded.
# Vivado rereads a block design from disk only when the project is reopened
# (closing the design alone keeps the in-memory copy).
proc cedcfg::restore {tag} {
    variable root
    set source [file join $root snapshots $tag]
    if {![regexp {^[A-Za-z0-9_-]+$} $tag] || ![file isdirectory [file join $source srcs]]} {error "STATE_FAIL:unknown snapshot $tag"}
    lassign [bd_dirs] bd file src gen
    set xpr [plain [file join [get_property DIRECTORY [current_project]] [get_property NAME [current_project]].xpr]]
    close_project
    file delete -force $src
    file copy [file join $source srcs] $src
    file delete -force $gen
    if {[file isdirectory [file join $source gen]]} {file copy [file join $source gen] $gen}
    open_project $xpr
    open_bd_design [get_files $file]
    set locked [get_ips -quiet -filter {IS_LOCKED}]
    return [list RESTORE:OK tag $tag bd [current_bd_design -quiet] locked_ips $locked]
}

proc cedcfg::save {} {
    if {[current_bd_design -quiet] eq ""} {return SAVE:NO_BD}
    if {[catch {save_bd_design} error]} {return [list SAVE:FAIL $error]}
    return SAVE:OK
}

# ---------------------------------------------------------------------------
# Port: build a CED on a board it supports (the reference) and carry it to the
# user's board, which it does not list. Only called when the user asked for a
# port. Every decision is made from Vivado's own data; see SKILL.md and
# references/port.md.

# Cells whose board binding is a memory, transceiver or host interface: these
# cannot be dropped as a peripheral can, so a port that would drop one refuses.
namespace eval cedcfg {
    variable target_ifaces {}
    variable port_unportable {*noc* *ddr* *lpddr* *hbm* *gt_* *gtwiz* *gt_quad* *pcie* *cpm* *qdma* *xdma* *mipi* *mrmac* *dcmac* *hsdp*}
}

proc cedcfg::board_key {name} {
    return [join [lrange [split $name :] 0 1] :]
}

# The installed board the design supports whose part is in the target's family.
# Prefers the same architecture, then the same package, then the name.
proc cedcfg::port_plan {vlnv} {
    set ed [get_example_designs -quiet $vlnv]
    if {![llength $ed]} {error "PORT_FAIL:design not loaded: $vlnv"}
    set target_part [get_parts [get_property PART [current_project]]]
    set family [get_property FAMILY $target_part]
    set arch [get_property ARCHITECTURE $target_part]
    set package [get_property PACKAGE $target_part]
    set best {}
    foreach supported [get_property SUPPORTED_BOARDS $ed] {
        set key [board_key $supported]
        foreach bp [get_board_parts -quiet -latest_file_version] {
            if {[board_key $bp] ne $key} {continue}
            set part [get_parts -quiet [get_property PART_NAME [get_board_parts $bp]]]
            if {![llength $part] || [get_property FAMILY $part] ne $family} {continue}
            set score [expr {([get_property ARCHITECTURE $part] eq $arch ? 0 : 2) + ([get_property PACKAGE $part] eq $package ? 0 : 1)}]
            lappend best [list $score [plain $bp] [plain $part]]
        }
    }
    if {![llength $best]} {
        return [plain [dict create found 0 family $family reason "no installed board that the design supports has a part of family $family"]]
    }
    lassign [lindex [lsort -index 0 -integer [lsort -index 1 $best]] 0] score bp part
    return [plain [dict create found 1 board $bp part $part family $family \
        why "same family ($family) as the target and supported by the design"]]
}

# Swap the (empty) target project for a reference project on the reference board.
proc cedcfg::port_reference {board part} {
    variable root
    variable target_ifaces
    set target_ifaces {}
    if {[current_project -quiet] ne ""} {
        # The target board's interfaces decide what a port keeps; read them now,
        # while the target project is open.
        set target_ifaces [lmap i [get_board_part_interfaces -quiet] {plain $i}]
        if {[llength [get_files -quiet *.bd]]} {error "PORT_FAIL:the target project already holds a block design"}
        set dir [get_property DIRECTORY [current_project]]
        close_project
        if {[file tail $dir] eq "proj"} {file delete -force $dir}
    }
    # -force: a choose_ced retried after a failure finds the folder from the first try.
    create_project proj [file join $root port-ref] -part $part -force
    set_property BOARD_PART $board [current_project]
    return [plain [list PORT_REFERENCE:OK dir [file join $root port-ref] board [get_property BOARD_PART [current_project]]]]
}

proc cedcfg::is_ps {cell} {
    set vlnv [get_property VLNV $cell]
    return [expr {[string match *:ps_wizard:* $vlnv] || [string match *:versal_cips:* $vlnv]}]
}

# CONFIG.*_BOARD_INTERFACE values that bind a cell to a board interface.
proc cedcfg::bindings {cell} {
    set rows {}
    foreach key [list_property $cell CONFIG.*BOARD_INTERFACE] {
        if {[string match *.VALUE_SRC $key]} {continue}
        set value [get_property $key $cell]
        if {$value ne "" && $value ne "Custom"} {lappend rows [list [plain $key] [plain $value]]}
    }
    return $rows
}

# The dictionary-valued CONFIG.*_CONFIG properties of a PS cell.
proc cedcfg::ps_dicts {cell} {
    set rows {}
    foreach key [list_property $cell CONFIG.*_CONFIG] {
        if {[string match *.VALUE_SRC $key]} {continue}
        set value [get_property $key $cell]
        if {[catch {dict size $value} n] || $n < 2} {continue}
        lappend rows [plain $key] [plain $value]
    }
    return $rows
}

# The board preset alone for a PS cell's IP: a throw-away cell in a throw-away
# block design with only the board interface set (Vivado applies the preset).
proc cedcfg::preset_of {vlnv bindings} {
    set main [current_bd_design]
    create_bd_design cedport_probe
    set probe [create_bd_cell -type ip -vlnv $vlnv cedport_probe_ps]
    foreach row $bindings {lassign $row key value; set_property $key $value $probe}
    set dicts [ps_dicts $probe]
    set file [get_files -quiet cedport_probe.bd]
    close_bd_design [get_bd_designs cedport_probe]
    if {[llength $file]} {
        set path [plain [lindex $file 0]]
        remove_files $file
        file delete -force [file dirname $path]
    }
    current_bd_design [get_bd_designs $main]
    current_bd_instance /
    return $dicts
}

# A port endpoint carries its CONFIG values, so it can be recreated if the
# port does not survive the move (ced-port-haiku3 lost gt_refclk0/1 and MMI_GT).
proc cedcfg::port_config {port} {
    set config {}
    foreach key [list_property $port CONFIG.*] {
        if {[string match *.VALUE_SRC $key]} {continue}
        if {[get_property -quiet $key.VALUE_SRC $port] in {DEFAULT}} {continue}
        lappend config [plain $key] [plain [get_property $key $port]]
    }
    return $config
}

proc cedcfg::endpoints {net self} {
    set rows {}
    foreach p [get_bd_pins -quiet -of_objects $net] {if {[string first "$self/" $p] != 0} {lappend rows [list pin [plain $p]]}}
    foreach p [get_bd_ports -quiet -of_objects $net] {lappend rows [list port [plain $p] [port_config $p]]}
    return $rows
}

proc cedcfg::intf_endpoints {net self} {
    set rows {}
    foreach p [get_bd_intf_pins -quiet -of_objects $net] {if {[string first "$self/" $p] != 0} {lappend rows [list pin [plain $p]]}}
    foreach p [get_bd_intf_ports -quiet -of_objects $net] {lappend rows [list port [plain $p] [port_config $p]]}
    return $rows
}

# The new PS pin made external under the original port's name and settings.
proc cedcfg::restore_port {pin row intf} {
    lassign $row kind path config
    set name [file tail $path]
    set made [expr {$intf ? [make_bd_intf_pins_external $pin] : [make_bd_pins_external $pin]}]
    set made [lindex $made 0]
    if {[string trimleft $made /] ne $name} {catch {set_property NAME $name $made}}
    set made [object_of [list port $name] $intf]
    foreach {key value} $config {catch {set_property $key $value $made}}
    return $made
}

# Ports are found by name, not by path: in a session driven through the Vivado
# MCP the path lookup missed ports that existed, and the port then made new
# ones beside them (ced-port-haiku4).
proc cedcfg::object_of {row intf} {
    lassign $row kind path
    if {$kind eq "pin"} {
        return [expr {$intf ? [get_bd_intf_pins -quiet $path] : [get_bd_pins -quiet $path]}]
    }
    set name [file tail $path]
    if {$intf} {return [get_bd_intf_ports -quiet -filter "NAME == $name"]}
    return [get_bd_ports -quiet -filter "NAME == $name"]
}

# Delete a cell with the nets and external ports that served only it.
proc cedcfg::drop_cell {name} {
    set cell [get_bd_cells /$name]
    set nets [get_bd_nets -quiet -of_objects [get_bd_pins -quiet -of_objects $cell]]
    set intf_nets [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins -quiet -of_objects $cell]]
    delete_bd_objs $cell
    foreach net $nets {
        set net [get_bd_nets -quiet $net]
        if {![llength $net] || [llength [get_bd_pins -quiet -of_objects $net]]} {continue}
        set ports [get_bd_ports -quiet -of_objects $net]
        delete_bd_objs $net
        if {[llength $ports]} {delete_bd_objs $ports}
    }
    foreach net $intf_nets {
        set net [get_bd_intf_nets -quiet $net]
        if {![llength $net] || [llength [get_bd_intf_pins -quiet -of_objects $net]]} {continue}
        set ports [get_bd_intf_ports -quiet -of_objects $net]
        delete_bd_objs $net
        if {[llength $ports]} {delete_bd_objs $ports}
    }
}

# Carry the built reference design to the target board. Runs in the reference
# project with its block design open; leaves the target project open at
# <root>/proj. Returns found=0 with a reason when it refuses; the reference
# project is then left as it was.
proc cedcfg::port_apply {target_board} {
    variable root
    variable port_unportable
    set offset [log_size]
    if {[llength [get_files -quiet *.bd]] != 1} {return [plain [dict create ok 0 reason "the design has more than one block design"]]}
    set bd [plain [current_bd_design]]
    current_bd_instance /
    set ref_board [plain [get_property BOARD_PART [current_project]]]
    set cells [get_bd_cells -quiet]
    set ps {}
    foreach c $cells {if {[is_ps $c]} {lappend ps $c}}
    if {[llength $ps] > 1} {return [plain [dict create ok 0 reason "the design has more than one processing system block"]]}

    # 1. Capture, in the reference.
    set kept {}; set ps_plan {}
    if {[llength $ps]} {
        set ps [lindex $ps 0]
        set name [string trimleft $ps /]
        set binds [bindings $ps]
        set built [ps_dicts $ps]
        set preset [preset_of [get_property VLNV $ps] $binds]
        set overrides {}
        foreach {key value} $built {
            set base [expr {[dict exists $preset $key] ? [dict get $preset $key] : {}}]
            dict for {k v} $value {
                if {![dict exists $base $k] || [dict get $base $k] ne $v} {dict set overrides $key $k $v}
            }
        }
        set nets {}; set intf_nets {}
        foreach pin [get_bd_pins -quiet -of_objects $ps] {
            set net [get_bd_nets -quiet -of_objects $pin]
            if {[llength $net]} {lappend nets [list [plain [lindex [split $pin /] end]] [endpoints $net $ps]]}
        }
        foreach pin [get_bd_intf_pins -quiet -of_objects $ps] {
            set net [get_bd_intf_nets -quiet -of_objects $pin]
            if {[llength $net]} {lappend intf_nets [list [plain [lindex [split $pin /] end]] [intf_endpoints $net $ps]]}
        }
        set ps_plan [dict create name [plain $name] vlnv [plain [get_property VLNV $ps]] bindings $binds overrides $overrides nets $nets intf_nets $intf_nets]
        set flat {}
        dict for {key d} $overrides {dict for {k v} $d {dict set flat $k $v}}
        dict set kept $name $flat
    }
    set bound {}
    foreach c $cells {
        if {[is_ps $c]} {continue}
        set binds [bindings $c]
        if {[llength $binds]} {lappend bound [list [plain [string trimleft $c /]] [plain [get_property VLNV $c]] $binds]}
    }

    # 2. A copy at <root>/proj. Everything leaving the design leaves it before the
    # board changes: an IP whose board binding no longer resolves is wrapped in
    # an upgrade hierarchy when the board switches, and that wrapper keeps driving
    # its nets.
    variable target_ifaces
    save_project_as proj [file join $root proj] -force
    open_bd_design [get_files $bd.bd]
    current_bd_instance /

    # 3. Board-bound peripherals: keep when the target has the same interface, else drop.
    set dropped {}; set rebound {}
    foreach row $bound {
        lassign $row name vlnv binds
        set missing {}
        foreach b $binds {if {[lsearch -exact $target_ifaces [lindex $b 1]] < 0} {lappend missing [lindex $b 1]}}
        if {![llength $missing]} {
            lappend rebound [dict create cell $name board_interfaces [lmap b $binds {lindex $b 1}]]
            continue
        }
        foreach pattern $port_unportable {
            if {[string match -nocase $pattern $vlnv] || [string match -nocase $pattern $name]} {
                close_project
                open_project [file join $root port-ref proj.xpr]
                return [plain [dict create ok 0 reason "$name ($vlnv) is bound to the reference board's [join $missing {, }], which the target lacks; a memory, transceiver or host interface is not dropped by a port" \
                    messages [messages_since $offset]]]
            }
        }
        drop_cell $name
        lappend dropped [dict create cell $name ip $vlnv board_interfaces $missing \
            reason "the target board has no interface named [join $missing {, }]"]
    }
    if {[llength $ps_plan]} {
        set name [dict get $ps_plan name]
        foreach b [dict get $ps_plan bindings] {
            if {[lsearch -exact $target_ifaces [lindex $b 1]] < 0} {
                close_project
                open_project [file join $root port-ref proj.xpr]
                return [plain [dict create ok 0 reason "the target board has no [lindex $b 1] interface for $name" messages [messages_since $offset]]]
            }
        }
        delete_bd_objs [get_bd_cells /$name]
    }
    save_bd_design
    close_bd_design [current_bd_design]
    set_property BOARD_PART $target_board [current_project]
    catch {upgrade_ip -quiet [get_ips -quiet -filter {IS_LOCKED}]}
    open_bd_design [get_files $bd.bd]
    current_bd_instance /

    # 4. The PS: a fresh cell with the target's board preset and the design's own settings on top.
    set tied {}
    if {[llength $ps_plan]} {
        set name [dict get $ps_plan name]
        set fresh [create_bd_cell -type ip -vlnv [dict get $ps_plan vlnv] $name]
        foreach b [dict get $ps_plan bindings] {set_property [lindex $b 0] [lindex $b 1] $fresh}
        set settings {}
        dict for {key d} [dict get $ps_plan overrides] {dict for {k v} $d {lappend settings ${key}($k) $v}}
        if {[llength $settings]} {set_property -dict $settings $fresh}
        set lost {}
        set trace {}
        foreach row [dict get $ps_plan intf_nets] {
            lassign $row pin ends
            set mine [get_bd_intf_pins -quiet /$name/$pin]
            set other {}
            foreach e $ends {set o [object_of $e 1]; if {[llength $o]} {set other $o; break}}
            if {![llength $mine]} {lappend lost $pin; continue}
            if {![llength $other]} {
                set port [lsearch -inline -index 0 $ends port]
                lappend trace [list $pin intf not_found $ends ports [lmap p [get_bd_intf_ports -quiet] {plain $p}] bd [plain [current_bd_design]] inst [plain [current_bd_instance .]]]
                if {[llength $port]} {restore_port $mine $port 1}
                continue
            }
            # An interface net is point to point and this one joined the old
            # cell; deleting the cell can leave it on the other end.
            set stale [get_bd_intf_nets -quiet -of_objects $other]
            if {[llength $stale]} {delete_bd_objs $stale}
            connect_bd_intf_net $mine $other
        }
        foreach row [dict get $ps_plan nets] {
            lassign $row pin ends
            set mine [get_bd_pins -quiet /$name/$pin]
            set others {}
            foreach e $ends {set o [object_of $e 0]; if {[llength $o]} {lappend others $o}}
            if {![llength $mine]} {lappend lost $pin; continue}
            if {![llength $others]} {
                set port [lsearch -inline -index 0 $ends port]
                if {[llength $port]} {restore_port $mine $port 0}
                continue
            }
            # The old net survives on its other ends without a driver: join it.
            set stale [get_bd_nets -quiet -of_objects [lindex $others 0]]
            if {[llength $stale]} {
                connect_bd_net -net $stale $mine
                foreach o [lrange $others 1 end] {
                    if {![llength [get_bd_nets -quiet -of_objects $o]]} {connect_bd_net -net $stale $o}
                }
            } else {
                connect_bd_net $mine {*}$others
            }
        }
        if {[llength $lost]} {
            return [plain [dict create ok 0 reason "with the target's preset, $name has no pin [join $lost {, }] that the design uses" messages [messages_since $offset]]]
        }
        # Every connection the PS had in the reference must be back.
        set open {}
        foreach row [dict get $ps_plan intf_nets] {
            if {![llength [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins -quiet /$name/[lindex $row 0]]]]} {lappend open [lindex $row 0]}
        }
        foreach row [dict get $ps_plan nets] {
            if {![llength [get_bd_nets -quiet -of_objects [get_bd_pins -quiet /$name/[lindex $row 0]]]]} {lappend open [lindex $row 0]}
        }
        if {[llength $open]} {
            return [plain [dict create ok 0 reason "could not reconnect $name [join $open {, }] as in the reference design" messages [messages_since $offset]]]
        }
        # 5. Clock inputs the target's preset adds and nothing drives: a constant, never a running clock.
        set n 0
        foreach pin [get_bd_pins -quiet -of_objects $fresh -filter {DIR == I && TYPE == clk}] {
            if {[llength [get_bd_nets -quiet -of_objects $pin]]} {continue}
            set c [create_bd_cell -type inline_hdl -vlnv xilinx.com:inline_hdl:ilconstant:* cedport_tie_[incr n]]
            connect_bd_net [get_bd_pins $c/dout] $pin
            lappend tied [dict create pin [string trimleft $pin /] to "ilconstant [string trimleft $c /]" \
                reason "clock input the design does not use"]
        }
    }

    # 6. Addresses, validation, wrapper.
    catch {assign_bd_address}
    set validation [expr {[catch {validate_bd_design -force} vresult] ? "FAIL" : "OK"}]
    save_bd_design
    set wrapper ""
    catch {
        set w [make_wrapper -files [get_files $bd.bd] -top -force]
        foreach f [get_files -quiet *_wrapper.v] {remove_files $f}
        add_files -norecurse $w
        set_property top [file rootname [file tail $w]] [current_fileset]
        set wrapper [plain $w]
    }
    update_compile_order -quiet -fileset sources_1
    return [plain [dict create ok 1 from $ref_board to [get_property BOARD_PART [current_project]] \
        part [get_property PART [current_project]] bd $bd kept_settings $kept dropped $dropped rebound $rebound \
        tied_off $tied reconnect_trace [expr {[info exists trace] ? $trace : {}}] validation $validation result [string range $vresult 0 299] wrapper $wrapper \
        dir [file join $root proj] messages [messages_since $offset]]]
}
