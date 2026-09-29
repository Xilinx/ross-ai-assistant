# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Board-preset drift check. A board preset owns the parameters it lists; other
# block-automation options (for example PL clock or reset counts) can silently
# overwrite them. ipcfg::preset_drift compares every preset value with the
# cell's live configuration, including hierarchical sub-keys such as
# CONFIG.PS11_CONFIG(PS_NUM_FABRIC_RESETS). The parser is pure Tcl.

namespace eval ipcfg {}
namespace eval ipcfg::preset {}

proc ipcfg::preset::unescape {text} {
    return [string map {&quot; \" &apos; ' &lt; < &gt; > &amp; &} $text]
}

proc ipcfg::preset::attribute {tag name} {
    if {[regexp "\\m$name=\"(\[^\"\]*)\"" $tag -> value]} {return [ipcfg::preset::unescape $value]}
    return ""
}

proc ipcfg::preset::parse {xml} {
    # Returns {proc_name {ip <name> params {key value ...}}} with hierarchical
    # parameters flattened to CONFIG.PARENT(child) keys.
    set result {}
    # Split on the closing tag: Tcl AREs take one greediness for the whole RE,
    # so a lazy .*? after a greedy [^>]* would still swallow later blocks.
    foreach chunk [split [string map {</ip_preset> \x01} $xml] \x01] {
        if {![regexp {<ip_preset\s[^>]*preset_proc_name="([^"]+)"} $chunk -> name]} {continue}
        set body [string range $chunk [string first <ip_preset $chunk] end]
        set ip ""
        regexp {<ip\s[^>]*\mname="([^"]+)"} $body -> ip
        set params {}
        set parent ""
        foreach tag [regexp -all -inline {<(?:/?user_hier_parameter|user_parameter)\y[^>]*>} $body] {
            if {[string match {</user_hier_parameter*} $tag]} {set parent ""; continue}
            set key [ipcfg::preset::attribute $tag name]
            if {[string match {<user_hier_parameter*} $tag]} {set parent $key; continue}
            set value [ipcfg::preset::attribute $tag value]
            if {$parent ne ""} {set key "${parent}($key)"} elseif {![string match CONFIG.* $key]} {set key CONFIG.$key}
            dict set params $key $value
        }
        dict set result $name [dict create ip $ip params $params]
    }
    return $result
}

proc ipcfg::preset::normalize {value} {
    # Presets brace nested values ({ENABLE 1 ...}); live dict reads do not.
    set value [string trim $value]
    if {![catch {llength $value} count] && $count == 1} {set value [lindex $value 0]}
    return [string tolower [regsub -all {\s+} [string trim $value] " "]]
}

proc ipcfg::preset::compare {expected actual_lookup} {
    # actual_lookup: command prefix called with a key; returns {found value}.
    set drift {}
    dict for {key want} $expected {
        lassign [{*}$actual_lookup $key] found have
        if {!$found} {
            lappend drift [dict create key $key preset $want actual {} reason missing]
        } elseif {[ipcfg::preset::normalize $want] ne [ipcfg::preset::normalize $have]} {
            lappend drift [dict create key $key preset $want actual $have reason changed]
        }
    }
    return $drift
}

# --- Live adapters (Vivado) ---
proc ipcfg::preset::live_file {} {
    set board [current_board_part -quiet]
    if {$board eq ""} {error "no board part is set"}
    set path [get_property FILE_NAME [get_board_parts $board]]
    set directory [file dirname $path]
    set preset [file join $directory preset.xml]
    if {![file isfile $preset]} {error "preset.xml not found beside $path"}
    return $preset
}

proc ipcfg::preset::live_lookup {cell key} {
    set object [get_bd_cells $cell]
    if {[regexp {^(CONFIG\.[A-Za-z0-9_]+)\(([^)]+)\)$} $key -> parent child]} {
        if {[catch {get_property $parent $object} value] || [catch {dict exists $value $child} has] || !$has} {
            return {0 {}}
        }
        return [list 1 [dict get $value $child]]
    }
    if {$key ni [list_property $object]} {return {0 {}}}
    return [list 1 [get_property $key $object]]
}

proc ipcfg::preset::live_proc_names {cell} {
    # Board interfaces bound on the cell map to preset procs through board.xml.
    set object [get_bd_cells $cell]
    set names {}
    foreach property [list_property $object CONFIG.*BOARD_INTERFACE*] {
        set interface [get_property $property $object]
        if {$interface in {"" Custom}} {continue}
        set board [current_board_part -quiet]
        set xml [file join [file dirname [get_property FILE_NAME [get_board_parts $board]]] board.xml]
        set channel [open $xml r]
        try {set text [read $channel]} finally {close $channel}
        if {[regexp "name=\"[string map {. \\.} $interface]\"\[^>\]*preset_proc=\"(\[^\"\]+)\"" $text -> name]} {
            lappend names $name
        }
    }
    return $names
}

proc ipcfg::preset_drift {cell} {
    # Returns PRESET:OK n=<checked> or PRESET_DRIFT:<n> <key>=<actual>(preset <value>) ...
    set code [catch {
        set path [ipcfg::preset::live_file]
        set channel [open $path r]
        try {set presets [ipcfg::preset::parse [read $channel]]} finally {close $channel}
        set names [ipcfg::preset::live_proc_names $cell]
        if {![llength $names]} {error "no board-bound preset on $cell"}
        set drift {}
        set checked 0
        foreach name $names {
            if {![dict exists $presets $name]} {error "preset $name not in $path"}
            set params [dict get $presets $name params]
            incr checked [dict size $params]
            lappend drift {*}[ipcfg::preset::compare $params [list ipcfg::preset::live_lookup $cell]]
        }
        set ::ipcfg::last_preset_drift [dict create cell $cell presets $names checked $checked drift $drift]
    } detail]
    if {$code} {return "PRESET_CHECK_ERROR:$detail"}
    if {![llength $drift]} {return "PRESET:OK n=$checked presets=[join $names ,]"}
    set items [lmap entry $drift {format %s=%s(preset\ %s) [dict get $entry key] [dict get $entry actual] [dict get $entry preset]}]
    return "PRESET_DRIFT:[llength $drift] [join $items { }]"
}
