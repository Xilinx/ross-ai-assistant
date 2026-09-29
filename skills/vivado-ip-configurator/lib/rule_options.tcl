# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Live block-automation option schema. A rule's apply_rule validates the
# -config dict against the same widgets its GUI shows (get_rule_options), and
# reports only the FIRST missing/illegal key per attempt. Reading the widgets
# up front gives every key, its GUI default and the values legal in the
# current design context, without mutating the design.

namespace eval ipcfg {}
namespace eval ipcfg::ruleopts {}

# widgets: list of dicts {name value ?value_list? ?enabled? ?type?}; value_list
# holds {label value} pairs. Returns {key {default legal enabled} ...}.
proc ipcfg::ruleopts::parse_widgets {widgets} {
    set schema {}
    foreach widget $widgets {
        if {![dict exists $widget name]} continue
        set legal {}
        if {[dict exists $widget value_list]} {
            foreach pair [dict get $widget value_list] { lappend legal [lindex $pair end] }
        }
        set default [expr {[dict exists $widget value] ? [dict get $widget value] : ""}]
        set enabled [expr {[dict exists $widget enabled] ? [string is true -strict [dict get $widget enabled]] : 1}]
        dict set schema [dict get $widget name] [list $default $legal $enabled]
    }
    return $schema
}

# Returns {config <completed> defaulted {k v ..} unknown {k ..} illegal {k {v legal} ..}}.
proc ipcfg::ruleopts::check {config schema} {
    set completed $config
    set defaulted {}
    set unknown {}
    set illegal {}
    dict for {key value} $config {
        if {![dict exists $schema $key]} { lappend unknown $key; continue }
        lassign [dict get $schema $key] default legal enabled
        if {$enabled && [llength $legal] > 0 && [lsearch -exact $legal $value] < 0} {
            lappend illegal $key [list $value $legal]
        }
    }
    dict for {key spec} $schema {
        if {![dict exists $config $key]} {
            dict set completed $key [lindex $spec 0]
            lappend defaulted $key [lindex $spec 0]
        }
    }
    return [dict create config $completed defaulted $defaulted unknown $unknown illegal $illegal]
}

proc ipcfg::ruleopts::format_schema {schema} {
    set parts {}
    dict for {key spec} $schema {
        lassign $spec default legal enabled
        lappend parts "$key=[list $default][expr {[llength $legal] ? " legal=[list $legal]" : ""}][expr {$enabled ? "" : " disabled"}]"
    }
    return [join $parts "; "]
}

# Live adapter: the loaded rule namespace, e.g. ::xilinx.com:bd_rule:axi_noc2:1.0.
proc ipcfg::ruleopts::live_namespace {rule} {
    set found [namespace children :: "${rule}:*"]
    lappend found {*}[namespace children :: [string map {: _} $rule]]
    set found [lsort -dictionary [lsearch -all -inline -not $found ""]]
    if {![llength $found]} { return "" }
    return [lindex $found end]
}

proc ipcfg::ruleopts::live_widgets {cell rule} {
    set ns [live_namespace $rule]
    if {$ns eq "" || [info procs ${ns}::get_rule_options] eq ""} {
        error "rule $rule is not loaded (open the BD and create the target cell first)"
    }
    set options [${ns}::get_rule_options [get_bd_cells $cell]]
    if {![dict exists $options widgets]} { error "rule $rule returned no widgets" }
    return [dict get $options widgets]
}

# Public: RULE_OPTIONS:keys={..} defaults={k v ..} legal={k {..} ..} schema=<text>
proc ipcfg::automation_rule_options {cell rule} {
    if {[catch {ipcfg::ruleopts::parse_widgets [ipcfg::ruleopts::live_widgets $cell $rule]} schema]} {
        return "RULE_OPTIONS_ERROR:$schema"
    }
    set defaults {}
    set legal {}
    dict for {key spec} $schema {
        lappend defaults $key [lindex $spec 0]
        if {[llength [lindex $spec 1]]} { lappend legal $key [lindex $spec 1] }
    }
    set ::ipcfg::last_rule_options [dict create cell $cell rule $rule schema $schema]
    return "RULE_OPTIONS:keys={[dict keys $schema]} defaults={$defaults} legal={$legal} schema=[ipcfg::ruleopts::format_schema $schema]"
}
