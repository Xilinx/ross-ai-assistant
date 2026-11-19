# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Board-preset drift check. A board preset owns the parameters it lists; other
# block-automation options (for example PL clock or reset counts) can silently
# overwrite them. ipcfg_ced::preset_drift compares every preset value with the
# cell's live configuration, including hierarchical sub-keys such as
# CONFIG.PS11_CONFIG(PS_NUM_FABRIC_RESETS). The parser is pure Tcl.

namespace eval ipcfg_ced {}
namespace eval ipcfg_ced::preset {}

proc ipcfg_ced::preset::unescape {text} {
    return [string map {&quot; \" &apos; ' &lt; < &gt; > &amp; &} $text]
}

proc ipcfg_ced::preset::attribute {tag name} {
    if {[regexp "\\m$name=\"(\[^\"\]*)\"" $tag -> value]} {return [ipcfg_ced::preset::unescape $value]}
    return ""
}

proc ipcfg_ced::preset::parse {xml} {
    # Returns {proc_name {ip <name> params {key value ...}}}. A hierarchical parameter keeps
    # its whole path: CONFIG.PS_PMC_CONFIG(PMC_SD1(CD_IO(PMC_MIO))). Nesting is tracked with a
    # stack -- a single parent dropped the outer level, lifted siblings that follow an inner
    # block to top-level CONFIG.* keys, and folded same-named leaves of different
    # peripherals onto one key (56 of 67 leaves on the VCK190 CIPS preset, issue #498).
    set result {}
    # Commented-out entries are not part of the preset (the KD240 SOM preset comments out 37).
    regsub -all {<!--.*?-->} $xml {} xml
    # Split on the closing tag: Tcl AREs take one greediness for the whole RE,
    # so a lazy .*? after a greedy [^>]* would still swallow later blocks.
    foreach chunk [split [string map {</ip_preset> \x01} $xml] \x01] {
        if {![regexp {<ip_preset\s[^>]*preset_proc_name="([^"]+)"} $chunk -> name]} {continue}
        set body [string range $chunk [string first <ip_preset $chunk] end]
        set ip ""
        regexp {<ip\s[^>]*\mname="([^"]+)"} $body -> ip
        set params {}
        set stack {}
        foreach tag [regexp -all -inline {<(?:/?user_hier_parameter|user_parameter)\y[^>]*>} $body] {
            if {[string match {</user_hier_parameter*} $tag]} {set stack [lrange $stack 0 end-1]; continue}
            set pname [ipcfg_ced::preset::attribute $tag name]
            if {[string match {<user_hier_parameter*} $tag]} {
                # A self-closing hierarchy tag opens and closes in one tag.
                if {![string match */> $tag]} {lappend stack $pname}
                continue
            }
            set value [ipcfg_ced::preset::attribute $tag value]
            dict set params [ipcfg_ced::preset::path_key [concat $stack [list $pname]]] $value
        }
        dict set result $name [dict create ip $ip params $params]
    }
    return $result
}

# {CONFIG.A B C} -> CONFIG.A(B(C)); the outermost name gets CONFIG. if it lacks it.
proc ipcfg_ced::preset::path_key {names} {
    set top [lindex $names 0]
    if {![string match CONFIG.* $top]} {set top CONFIG.$top}
    set key $top
    set close ""
    foreach name [lrange $names 1 end] {append key "($name"; append close ")"}
    return $key$close
}

# CONFIG.A(B(C)) -> {CONFIG.A B C}
proc ipcfg_ced::preset::key_path {key} {
    set names {}
    while {[regexp {^([^()]+)\((.*)\)$} $key -> head inner]} {lappend names $head; set key $inner}
    lappend names $key
    return $names
}

# One level of a live dictionary value. Top-level values are flat {K V K V}; nested ones are
# lists of pairs {{K V} {K V}}. Returns {found value}.
proc ipcfg_ced::preset::child {value name} {
    # A pin range is stored as {PMC_MIO 26 .. 36}: read as a dict its keys would include "..",
    # so a value whose would-be keys are not all identifiers is "name value..." instead.
    if {![catch {llength $value} n] && $n > 2 && [lindex $value 0] eq $name} {
        set keys [lmap {k v} $value {set k}]
        if {[llength [lsearch -all -inline -not -regexp $keys {^[A-Za-z_][A-Za-z0-9_]*$}]]} {
            return [list 1 [lrange $value 1 end]]
        }
    }
    if {![catch {dict exists $value $name} has] && $has} {return [list 1 [dict get $value $name]]}
    if {[catch {llength $value}]} {return {0 {}}}
    foreach item $value {
        if {![catch {llength $item} n] && $n == 2 && [lindex $item 0] eq $name} {return [list 1 [lindex $item 1]]}
    }
    return {0 {}}
}

proc ipcfg_ced::preset::normalize {value} {
    # Presets brace nested values ({ENABLE 1 ...}); live dict reads do not.
    set value [string trim $value]
    if {![catch {llength $value} count] && $count == 1} {set value [lindex $value 0]}
    return [string tolower [regsub -all {\s+} [string trim $value] " "]]
}

proc ipcfg_ced::preset::compare {expected actual_lookup} {
    # actual_lookup: command prefix called with a key; returns {found value}.
    set drift {}
    dict for {key want} $expected {
        # A preset entry with no value sets nothing, so there is nothing to compare.
        if {[string trim $want] eq ""} {continue}
        lassign [{*}$actual_lookup $key] found have
        if {!$found} {
            lappend drift [dict create key $key preset $want actual {} reason missing]
        } elseif {[ipcfg_ced::preset::normalize $want] ne [ipcfg_ced::preset::normalize $have]} {
            if {[ipcfg_ced::preset::ignored_on_input $key $have $actual_lookup]} {continue}
            # The IP marks a parameter it does not use in this configuration "NA" or "<Select>"
            # (DDR4 timing on an LPDDR4 board, DisplayPort clocks with DisplayPort off).
            if {[string tolower [string trim $have]] in {na <select>}} {continue}
            lappend drift [dict create key $key preset $want actual $have reason changed]
        }
    }
    return $drift
}

# A pin whose live DIRECTION is "in" has no output data; the IP stores OUTPUT_DATA "default"
# whatever the preset lists, so that difference is the IP's normalization, not drift.
proc ipcfg_ced::preset::ignored_on_input {key have actual_lookup} {
    set path [ipcfg_ced::preset::key_path $key]
    if {[lindex $path end] ne "OUTPUT_DATA" || [string tolower $have] ne "default"} {return 0}
    lassign [{*}$actual_lookup [ipcfg_ced::preset::path_key [lreplace $path end end DIRECTION]]] found direction
    return [expr {$found && [string tolower $direction] eq "in"}]
}

# --- Live adapters (Vivado) ---
# Caches: a traced build spent 205 s in 22 calls, because each call re-read the board files and,
# for each of ~270 preset keys, re-resolved the cell and re-listed all its properties.
namespace eval ipcfg_ced::preset {
    variable file_cache [dict create]      ;# board part -> preset file path
    variable parse_cache [dict create]     ;# preset path -> {stamp parsed}
    variable names_cache [dict create]     ;# "board|vlnv" -> preset proc names (unbound fallback)
    variable live [dict create]            ;# one preset_drift call: object, properties, values
}
# The preset file, parsed once per file content.
proc ipcfg_ced::preset::live_presets {path} {
    variable parse_cache
    set stamp [list [file mtime $path] [file size $path]]
    if {[dict exists $parse_cache $path] && [lindex [dict get $parse_cache $path] 0] eq $stamp} {
        return [lindex [dict get $parse_cache $path] 1]
    }
    set channel [open $path r]
    try {set presets [ipcfg_ced::preset::parse [read $channel]]} finally {close $channel}
    dict set parse_cache $path [list $stamp $presets]
    return $presets
}
proc ipcfg_ced::preset::live_file {} {
    variable file_cache
    set board [current_board_part -quiet]
    if {$board eq ""} {error "no board part is set"}
    if {[dict exists $file_cache $board]} {return [dict get $file_cache $board]}
    set path [get_property FILE_NAME [get_board_parts $board]]
    set directory [file dirname $path]
    # The board names its preset file (Kria SOMs: preset_file="preset_s4.xml"); preset.xml otherwise.
    set name preset.xml
    catch {
        set channel [open $path r]
        try {set text [read $channel]} finally {close $channel}
        regexp {<board\s[^>]*preset_file="([^"]+)"} $text -> name
    }
    set preset [file join $directory $name]
    if {![file isfile $preset]} {error "$name not found beside $path"}
    dict set file_cache $board $preset
    return $preset
}

# Read one top-level property for the current preset_drift call: the cell is resolved and its
# property names listed once per call, and each value is read once.
proc ipcfg_ced::preset::live_top {cell name} {
    variable live
    if {![dict exists $live cell] || [dict get $live cell] ne $cell} {
        set object [get_bd_cells $cell]
        set names [dict create]
        foreach property [list_property $object] {dict set names $property 1}
        set live [dict create cell $cell object $object names $names values [dict create]]
    }
    if {![dict exists $live names $name]} {return {0 {}}}
    if {![dict exists $live values $name]} {
        if {[catch {get_property $name [dict get $live object]} value]} {return {0 {}}}
        dict set live values $name $value
    }
    return [list 1 [dict get $live values $name]]
}
proc ipcfg_ced::preset::live_lookup {cell key} {
    set path [ipcfg_ced::preset::key_path $key]
    set top [lindex $path 0]
    if {[llength $path] == 1} {return [ipcfg_ced::preset::live_top $cell $key]}
    # Walk every level of the nested value, however deep the preset goes.
    lassign [ipcfg_ced::preset::live_top $cell $top] found value
    if {!$found} {return {0 {}}}
    foreach name [lrange $path 1 end] {
        lassign [ipcfg_ced::preset::child $value $name] found value
        if {!$found} {return {0 {}}}
    }
    return [list 1 $value]
}

proc ipcfg_ced::preset::live_proc_names {cell} {
    # Board interfaces bound on the cell map to preset procs through board.xml.
    set object [get_bd_cells $cell]
    set names {}
    foreach property [list_property $object CONFIG.*BOARD_INTERFACE*] {
        set interface [get_property $property $object]
        if {[string tolower $interface] in {"" custom}} {continue}
        set board [current_board_part -quiet]
        set xml [file join [file dirname [get_property FILE_NAME [get_board_parts $board]]] board.xml]
        set channel [open $xml r]
        try {set text [read $channel]} finally {close $channel}
        if {[regexp "name=\"[string map {. \\.} $interface]\"\[^>\]*preset_proc=\"(\[^\"\]+)\"" $text -> name]} {
            lappend names $name
        }
    }
    # No hook bound: some processing systems take the board preset without recording a
    # *_BOARD_INTERFACE (Zynq UltraScale+ leaves every hook "custom"). Use the preset of the
    # board interface whose preferred IP is this cell.
    if {![llength $names]} {
        variable names_cache
        set vlnv [join [lrange [split [get_property VLNV $object] :] 0 2] :]
        set board [current_board_part -quiet]
        if {[dict exists $names_cache "$board|$vlnv"]} {return [dict get $names_cache "$board|$vlnv"]}
        set xml [file join [file dirname [get_property FILE_NAME [get_board_parts $board]]] board.xml]
        set channel [open $xml r]
        try {set text [read $channel]} finally {close $channel}
        foreach interface [get_board_part_interfaces -quiet] {
            set match 0
            foreach preference [get_property -quiet PREFERENCES $interface] {
                if {[string match $preference $vlnv] || [join [lrange [split $preference :] 0 2] :] eq $vlnv} {set match 1}
            }
            set iname [get_property -quiet NAME $interface]
            if {$match && [regexp "name=\"[string map {. \\.} $iname]\"\[^>\]*preset_proc=\"(\[^\"\]+)\"" $text -> name]} {
                lappend names $name
            }
        }
        dict set names_cache "$board|$vlnv" $names
    }
    return $names
}

# Compare a board-bound cell's live values with its board preset; PRESET:OK or PRESET_DRIFT listing each changed key.
proc ipcfg_ced::preset_drift {cell} {
    # Returns PRESET:OK n=<checked> or PRESET_DRIFT:<n> <key>=<actual>(preset <value>) ...
    set ::ipcfg_ced::preset::live [dict create]
    set code [catch {
        set path [ipcfg_ced::preset::live_file]
        set presets [ipcfg_ced::preset::live_presets $path]
        set names [ipcfg_ced::preset::live_proc_names $cell]
        if {![llength $names]} {error "no board-bound preset on $cell"}
        set drift {}
        set checked 0
        foreach name $names {
            if {![dict exists $presets $name]} {error "preset $name not in $path"}
            set params [dict get $presets $name params]
            incr checked [dict size $params]
            lappend drift {*}[ipcfg_ced::preset::compare $params [list ipcfg_ced::preset::live_lookup $cell]]
        }
        set ::ipcfg_ced::last_preset_drift [dict create cell $cell presets $names checked $checked drift $drift]
    } detail]
    set ::ipcfg_ced::preset::live [dict create]
    if {$code} {return "PRESET_CHECK_ERROR:$detail"}
    if {![llength $drift]} {return "PRESET:OK n=$checked presets=[join $names ,]"}
    set items [lmap entry $drift {format %s=%s(preset\ %s) [dict get $entry key] [dict get $entry actual] [dict get $entry preset]}]
    return "PRESET_DRIFT:[llength $drift] [join $items { }]"
}
