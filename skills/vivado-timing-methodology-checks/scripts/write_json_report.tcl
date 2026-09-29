# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# REVISION HISTORY #
####################
# v0.1 - Initial version - JSON report generation for Vivado violations
# v0.2 - Updated to match report_lookahead.tcl style and user experience
#
# write_json_report.tcl
# This script generates a JSON report for various Vivado violation types
# Supported types: methodology, qor_suggestion, drc, cdc

proc convert_to_time_format {seconds} {
# takes a time value formatted in vivado typical speak xx:xx:xx
# then returns a value that is number of seconds

    set error 0
    # Check validity of the seconds argument:
    # =======================================
    
    if {$error == 1} {
        puts "ERROR: There is $error error. Please correct it before continuing"
        return
    } elseif {$error != 0} {
        puts "ERROR: There are $error errors. Please correct then before continuing"
        return
    }
    
    set h [expr {$seconds/3600}]
    incr seconds [expr {$h*-3600}]
    set m [expr {$seconds/60}]
    set s [expr {$seconds%60}]
    return [format "%02.2d:%02.2d:%02.2d" $h $m $s]
}

# Procedure to escape JSON special characters
proc json_escape {str} {
    set str [string map {
        "\\" "\\\\"
        "\"" "\\\""
        "\n" "\\n"
        "\r" "\\r"
        "\t" "\\t"
        "\f" "\\f"
        "\b" "\\b"
    } $str]
    return $str
}

# Procedure to convert a property value to JSON format
proc property_to_json {value} {
    if {$value eq ""} {
        return "null"
    } elseif {[string is integer -strict $value] || [string is double -strict $value]} {
        return $value
    } elseif {[string is boolean -strict $value]} {
        return [string tolower $value]
    } else {
        return "\"[json_escape $value]\""
    }
}

# Procedure to get all properties of an object and convert to JSON
proc object_to_json {obj} {
    set json "\{\n"
    set props [list_property $obj]
    set first 1
    
    foreach prop $props {
        if {!$first} {
            append json ",\n"
        }
        set first 0
        
        set value [get_property $prop $obj]
        append json "    \"$prop\": [property_to_json $value]"
    }
    
    append json "\n  \}"
    return $json
}

# Procedure to process methodology violations
proc process_methodology_violations {} {
    set violations [get_methodology_violations]
    set json_list [list]
    
    foreach violation $violations {
        lappend json_list [object_to_json $violation]
    }
    
    return $json_list
}

# Procedure to process DRC violations
proc process_drc_violations {} {
    set violations [get_drc_violations]
    set json_list [list]
    
    foreach violation $violations {
        lappend json_list [object_to_json $violation]
    }
    
    return $json_list
}

# Procedure to process CDC violations
proc process_cdc_violations {} {
    set violations [get_cdc_violations]
    set json_list [list]
    
    foreach violation $violations {
        lappend json_list [object_to_json $violation]
    }
    
    return $json_list
}

# Procedure to process QoR suggestions
proc process_qor_suggestions {} {
    set suggestions [get_qor_suggestions]
    set json_list [list]
    
    foreach suggestion $suggestions {
        lappend json_list [object_to_json $suggestion]
    }
    
    return $json_list
}

# Main procedure to write the JSON report
proc write_json_report {args} {
    set start [clock seconds]
    set print_help 0
    set fn ""
    set filemode w
    set report_type ""
    set error 0
    
    while {[llength $args]} {
        set name [lshift args]
        switch -regexp -- $name {
            {^-append$} -
            {^-a(p(p(e(nd?)?)?)?)$}                { set filemode {a}}
            {^-file$} -
            {^-f(i(l(e?)?)?)$}                     { set fn [lshift args]}
            {^-report$} -
            {^-r(e(p(o(r(t?)?)?)?)?)$}             { set report_type [lshift args]}
            {^-help$} -
            {^-h(e(l(p?)?)?)$}                     { set print_help 1}
            default {
                if {[string match "-*" $name]} {
                    puts " -E- option '$name' is not a valid option."
                    incr error
                } else {
                    puts " -E- option '$name' is not a valid option."
                    incr error
                }
            }
        }
    }
    
    if {$print_help == 1} {
        set msg {
        Command:
        ========
        write_json_report
        
        Switches:
        =========
        -report <type>              Report type - one of: drc, methodology, cdc, suggestions (required)
        [-file <filename>]          Prints to the specified file. Otherwise prints to stdout.
        [-append]                   Appends the information to the file specified in -file arg. Ignored if no -file is set.
        
        Description
        ============
        Generates a JSON report for various Vivado violation types.
        Supported types: methodology, qor_suggestion (suggestions), drc, cdc
        
        Examples:
        =========
        The following generates a methodology JSON report to a file methodology.json
        
        write_json_report -report methodology -file methodology.json
        
        The following generates a DRC JSON report to stdout
        
        write_json_report -report drc
        }
        puts $msg
        return
    }
    
    # Validate report type
    if {$report_type eq ""} {
        puts " -E- -report is required"
        incr error
    }
    
    if {$report_type ni {drc methodology cdc suggestions}} {
        puts " -E- Invalid report type '$report_type'"
        puts "     Valid types are: drc, methodology, cdc, suggestions"
        incr error
    }
    
    if {$error > 0} {
        if {$error == 1} {
            puts "ERROR: There is $error error. Please correct it before continuing"
        } else {
            puts "ERROR: There are $error errors. Please correct them before continuing"
        }
        return -code error
    }
    
    # Set default output file if not specified
    if {$fn eq ""} {
        set fid stdout
    } else {
        if [catch {set fid [open $fn $filemode]} errmsg] {
            puts stderr "Unable to open the file: $fn \n $errmsg"
            return -code 2 "Unable to open the file: $fn \n $errmsg"
        }
    }
    
    set report_data "\[\n"
    
    switch -exact $report_type {
        "methodology" {
            puts "Processing methodology violations..."
            set violations [process_methodology_violations]
            append report_data [join $violations ",\n"]
        }
        "suggestions" {
            puts "Processing QoR suggestions..."
            set suggestions [process_qor_suggestions]
            append report_data [join $suggestions ",\n"]
        }
        "drc" {
            puts "Processing DRC violations..."
            set violations [process_drc_violations]
            append report_data [join $violations ",\n"]
        }
        "cdc" {
            puts "Processing CDC violations..."
            set violations [process_cdc_violations]
            append report_data [join $violations ",\n"]
        }
    }
    
    append report_data "\n\]"
    
    # Write to file
    puts $fid $report_data
    
    if {$fid ne "stdout"} {
        close $fid
        puts "JSON report written to: $fn"
    }
    
    set stop [clock seconds]
    puts "write_json_report: Time(s): [convert_to_time_format [expr $stop-$start]]"
}

# Helper procedure for argument parsing
proc lshift {inputlist} {
  upvar $inputlist argv
  set arg  [lindex $argv 0]
  set argv [lrange $argv 1 end]
  return $arg
}

# Run the report if called directly
# Uncomment the following line to run automatically when sourced
# write_json_report


