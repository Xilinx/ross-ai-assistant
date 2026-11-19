# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# Evidence collection for the Stage 2 planner. Sourced after open_checkpoint on
# the current winner's routed checkpoint, with $run_dir set. Read-only: writes
# reports and $run_dir/evidence.json, never modifies the design.
#
# QoR suggestions are included (report + .rqs file the planner can apply).
# 2026.1 released segfaulted inside report_qor_suggestions on design D; newer
# builds are expected to be fine -- it runs last so every other report is
# already on disk if it does crash.

proc _ev_json_str {s} {
    return "\"[string map [list "\\" "\\\\" "\"" "\\\"" "\n" " " "\t" " "] $s]\""
}
proc _ev_num {v} {
    if {$v eq "" || ![string is double -strict $v]} { return null }
    return $v
}
proc _ev_prop {obj name} {
    if {[catch {get_property $name $obj} v]} { return "" }
    return $v
}

set _ev_max_paths 50
if {[info exists env(PLANNER_MAX_PATHS)]} { set _ev_max_paths $env(PLANNER_MAX_PATHS) }

# --- Summary reports (text, for the planner to read) ---
catch {report_timing_summary -max_paths 20 -file $run_dir/ev_timing_summary.rpt}
catch {report_timing -max_paths 30 -nworst 1 -sort_by slack -input_pins -file $run_dir/ev_timing_paths.rpt}
catch {report_design_analysis -timing -logic_level_distribution -max_paths 50 -file $run_dir/ev_design_analysis_timing.rpt}
catch {report_design_analysis -congestion -file $run_dir/ev_design_analysis_congestion.rpt}
catch {report_design_analysis -complexity -file $run_dir/ev_design_analysis_complexity.rpt}
catch {report_clock_utilization -file $run_dir/ev_clock_utilization.rpt}
catch {report_clock_interaction -delay_type max -significant_digits 3 -file $run_dir/ev_clock_interaction.rpt}
catch {report_high_fanout_nets -timing -max_nets 30 -file $run_dir/ev_high_fanout_nets.rpt}
catch {report_utilization -file $run_dir/ev_utilization.rpt}

# --- Per-path evidence (machine-readable) ---
set _ev_paths [get_timing_paths -setup -max_paths $_ev_max_paths -nworst 1 -slack_lesser_than 0 -quiet]
set _ev_items {}
foreach p $_ev_paths {
    set sp [_ev_prop $p STARTPOINT_PIN]
    set ep [_ev_prop $p ENDPOINT_PIN]
    set max_fo 0
    set max_fo_net ""
    foreach n [get_nets -quiet -of_objects $p] {
        set fo [_ev_prop $n FLAT_PIN_COUNT]
        if {[string is integer -strict $fo] && $fo > $max_fo} { set max_fo $fo; set max_fo_net $n }
    }
    set sslr ""; set eslr ""
    catch {set sslr [get_slrs -quiet -of_objects [get_cells -quiet -of_objects [get_pins -quiet $sp]]]}
    catch {set eslr [get_slrs -quiet -of_objects [get_cells -quiet -of_objects [get_pins -quiet $ep]]]}
    # Where the two ends sit (worst-endpoint targeting: LOC / pblock moves).
    set sloc ""; set eloc ""; set scr ""; set ecr ""
    catch {set sloc [get_property LOC [get_cells -quiet -of_objects [get_pins -quiet $sp]]]}
    catch {set eloc [get_property LOC [get_cells -quiet -of_objects [get_pins -quiet $ep]]]}
    catch {set scr [get_property CLOCK_REGION [get_sites -quiet $sloc]]}
    catch {set ecr [get_property CLOCK_REGION [get_sites -quiet $eloc]]}
    set item "\{"
    append item "\"slack\":[_ev_num [_ev_prop $p SLACK]],"
    append item "\"requirement\":[_ev_num [_ev_prop $p REQUIREMENT]],"
    append item "\"logic_levels\":[_ev_num [_ev_prop $p LOGIC_LEVELS]],"
    append item "\"datapath_delay\":[_ev_num [_ev_prop $p DATAPATH_DELAY]],"
    append item "\"logic_delay\":[_ev_num [_ev_prop $p DATAPATH_LOGIC_DELAY]],"
    append item "\"net_delay\":[_ev_num [_ev_prop $p DATAPATH_NET_DELAY]],"
    append item "\"skew\":[_ev_num [_ev_prop $p SKEW]],"
    append item "\"uncertainty\":[_ev_num [_ev_prop $p UNCERTAINTY]],"
    append item "\"src_clock\":[_ev_json_str [_ev_prop $p STARTPOINT_CLOCK]],"
    append item "\"dst_clock\":[_ev_json_str [_ev_prop $p ENDPOINT_CLOCK]],"
    append item "\"startpoint\":[_ev_json_str $sp],"
    append item "\"endpoint\":[_ev_json_str $ep],"
    append item "\"src_slr\":[_ev_json_str $sslr],"
    append item "\"dst_slr\":[_ev_json_str $eslr],"
    append item "\"src_loc\":[_ev_json_str $sloc],\"dst_loc\":[_ev_json_str $eloc],"
    append item "\"src_clock_region\":[_ev_json_str $scr],\"dst_clock_region\":[_ev_json_str $ecr],"
    append item "\"max_fanout\":$max_fo,"
    append item "\"max_fanout_net\":[_ev_json_str $max_fo_net]"
    append item "\}"
    lappend _ev_items $item
}

# --- Where the failing endpoints / startpoints concentrate (group_path, pblock targets) ---
# A wide slack wall is invisible in the worst 50 paths: sample up to
# PLANNER_MAX_ENDPOINTS failing setup paths (one per endpoint) and count them by
# hierarchy prefix at depths 1..5.
set _ev_max_ep 3000
if {[info exists env(PLANNER_MAX_ENDPOINTS)]} { set _ev_max_ep $env(PLANNER_MAX_ENDPOINTS) }
proc _ev_clusters {pins slacks} {
    set out {}
    for {set depth 1} {$depth <= 5} {incr depth} {
        set cnt [dict create]
        set worst [dict create]
        foreach pin $pins s $slacks {
            set parts [split $pin "/"]
            # drop the pin and the cell name: the hierarchy the cell lives in
            if {[llength $parts] < $depth + 2} { continue }
            set pre [join [lrange $parts 0 [expr {$depth - 1}]] "/"]
            dict incr cnt $pre
            if {![dict exists $worst $pre] || $s < [dict get $worst $pre]} {
                dict set worst $pre $s
            }
        }
        set pairs {}
        dict for {k v} $cnt { lappend pairs [list $k $v] }
        set top {}
        foreach kv [lrange [lsort -integer -decreasing -index 1 $pairs] 0 14] {
            lassign $kv k v
            lappend top "\{\"prefix\":[_ev_json_str $k],\"count\":$v,\"worst_slack\":[_ev_num [dict get $worst $k]]\}"
        }
        lappend out "\{\"depth\":$depth,\"top\":\[[join $top ,]\]\}"
    }
    return "\[[join $out ,]\]"
}
# Cell path with every [n] index removed: replicated generate instances and bus
# bits collapse into one stem (also the only grouping a flat netlist has).
proc _ev_stems {pins slacks} {
    set cnt [dict create]
    set worst [dict create]
    foreach pin $pins s $slacks {
        if {$pin eq ""} { continue }
        set stem [regsub -all {\[[0-9]+\]} [file dirname $pin] ""]
        dict incr cnt $stem
        if {![dict exists $worst $stem] || $s < [dict get $worst $stem]} {
            dict set worst $stem $s
        }
    }
    set pairs {}
    dict for {k v} $cnt { lappend pairs [list $k $v] }
    set top {}
    foreach kv [lrange [lsort -integer -decreasing -index 1 $pairs] 0 19] {
        lassign $kv k v
        lappend top "\{\"stem\":[_ev_json_str $k],\"count\":$v,\"worst_slack\":[_ev_num [dict get $worst $k]]\}"
    }
    return "\[[join $top ,]\]"
}
set _ev_eps {}; set _ev_sps {}; set _ev_sl {}
foreach p [get_timing_paths -setup -max_paths $_ev_max_ep -nworst 1 -unique_pins -slack_lesser_than 0 -quiet] {
    lappend _ev_eps [_ev_prop $p ENDPOINT_PIN]
    lappend _ev_sps [_ev_prop $p STARTPOINT_PIN]
    lappend _ev_sl [_ev_prop $p SLACK]
}
set _ev_ep_json "\{\"sampled\":[llength $_ev_eps],\"limit\":$_ev_max_ep,\"endpoints\":[_ev_clusters $_ev_eps $_ev_sl],\"startpoints\":[_ev_clusters $_ev_sps $_ev_sl],\"endpoint_stems\":[_ev_stems $_ev_eps $_ev_sl],\"startpoint_stems\":[_ev_stems $_ev_sps $_ev_sl]\}"

# --- Hold violators (a design can fail on WHS alone) ---
set _ev_hold_items {}
foreach p [get_timing_paths -hold -max_paths 20 -nworst 1 -slack_lesser_than 0 -quiet] {
    set item "\{"
    append item "\"slack\":[_ev_num [_ev_prop $p SLACK]],"
    append item "\"skew\":[_ev_num [_ev_prop $p SKEW]],"
    append item "\"logic_levels\":[_ev_num [_ev_prop $p LOGIC_LEVELS]],"
    append item "\"datapath_delay\":[_ev_num [_ev_prop $p DATAPATH_DELAY]],"
    append item "\"src_clock\":[_ev_json_str [_ev_prop $p STARTPOINT_CLOCK]],"
    append item "\"dst_clock\":[_ev_json_str [_ev_prop $p ENDPOINT_CLOCK]],"
    append item "\"startpoint\":[_ev_json_str [_ev_prop $p STARTPOINT_PIN]],"
    append item "\"endpoint\":[_ev_json_str [_ev_prop $p ENDPOINT_PIN]]"
    append item "\}"
    lappend _ev_hold_items $item
}
catch {report_timing -hold -max_paths 20 -nworst 1 -sort_by slack -file $run_dir/ev_hold_paths.rpt}

set _ev_wns [_ev_num [_ev_prop [lindex [get_timing_paths -setup -max_paths 1 -quiet] 0] SLACK]]
set _ev_whs [_ev_num [_ev_prop [lindex [get_timing_paths -hold -max_paths 1 -quiet] 0] SLACK]]
set _ev_part [_ev_prop [current_design] PART]
set _ev_nslr 0
catch {set _ev_nslr [llength [get_slrs -quiet]]}

set _ev_fh [open $run_dir/evidence.json w]
puts $_ev_fh "\{\"part\":[_ev_json_str $_ev_part],\"slr_count\":$_ev_nslr,\"wns\":$_ev_wns,\"whs\":$_ev_whs,\"failing_paths_sampled\":[llength $_ev_items],\"hold_paths_sampled\":[llength $_ev_hold_items],\"paths\":\[[join $_ev_items ,]\],\"hold_paths\":\[[join $_ev_hold_items ,]\],\"failing_clusters\":$_ev_ep_json\}"
close $_ev_fh
puts "INFO: planner evidence written: [llength $_ev_items] failing path(s) -> $run_dir/evidence.json"

# --- QoR suggestions last (see header) ---
file mkdir $run_dir/ev_rqs
catch {report_qor_suggestions -file $run_dir/ev_rqs.rpt}
catch {write_qor_suggestions -force $run_dir/ev_rqs/evidence.rqs}
puts "INFO: planner QoR suggestions: [expr {[file exists $run_dir/ev_rqs/evidence.rqs] ? {written} : {none}}]"
