# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# ila_help.tcl: show the human guide of the ILA insertion skill from inside a Vivado session (or plain tclsh).
# The text lives in ../references/help.md, next to this script's folder. Nothing here needs Vivado or the other library files,
# so it works for a user who only has the Vivado MCP tools and no way to read files.
#
#   ila::help               table of contents, then the "Where are you now?" section
#   ila::help <topic>       one section: what where inspect glossary items process options refusals clocks limits troubleshooting next
#   ila::help all           the whole guide
#
# The proc returns the text (it does not print it), so the Vivado MCP shows it as the command result.
# ila_insert.tcl sources this file at its end; source it by itself when you only want help.
#   ila::inspect            what a user can probe in the open design (ila_inspect.tcl, sourced at the end of this file)

namespace eval ila {
    # Folder of this script, read once at source time (info script is empty inside a proc).
    variable help_dir [file dirname [file normalize [info script]]]

    proc help_path {} {
        variable help_dir
        return [file join [file dirname $help_dir] references help.md]
    }

    # Split the guide into {topic title body} entries. A section starts at a line <!-- help-topic: NAME --> and runs to the next one.
    proc help_sections {text} {
        set out {}
        set topic ""
        set lines {}
        foreach l [split $text "\n"] {
            if {[regexp {^<!-- help-topic: ([a-z]+) -->$} $l -> t]} {
                if {$topic ne ""} { lappend out [help_entry $topic $lines] }
                set topic $t
                set lines {}
            } elseif {$topic ne ""} {
                lappend lines $l
            }
        }
        if {$topic ne ""} { lappend out [help_entry $topic $lines] }
        return $out
    }

    proc help_entry {topic lines} {
        set title $topic
        if {[regexp {^##\s+(.*)$} [lindex $lines 0] -> t]} { set title $t }
        return [list $topic $title [string trim [join $lines "\n"]]]
    }

    proc help {{topic ""}} {
        set path [help_path]
        if {[catch {set fh [open $path r]; set text [read $fh]; close $fh} err]} {
            return "ILA help: the guide file was not found or cannot be read ($path). Read references/help.md in the skill folder instead."
        }
        set secs [help_sections $text]
        set names [lmap s $secs {lindex $s 0}]
        set topic [string tolower [string trim $topic]]
        if {$topic eq "all"} { return [string trim $text] }
        if {$topic eq ""} {
            set toc "ILA insertion help. Ask for one topic to see its section (AI only: ila::help <topic>):"
            foreach s $secs { append toc "\n  [lindex $s 0]: [lindex $s 1]" }
            set where [lsearch -exact $names where]
            if {$where >= 0} { append toc "\n\n" [lindex $secs $where 2] }
            return $toc
        }
        set i [lsearch -exact $names $topic]
        if {$i < 0} {
            return "ILA help: no topic '$topic'. Topics: [join $names {, }], all."
        }
        return [lindex $secs $i 2]
    }
}

# ila::inspect (what can be probed in the open design). Optional: help works without it.
catch {source [file join [file dirname [info script]] ila_inspect.tcl]}
